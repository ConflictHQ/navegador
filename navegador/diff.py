"""
DiffAnalyzer — maps git diff output to affected knowledge-graph nodes.

Given uncommitted changes in a repository, this module tells you:
  - which files changed
  - which functions/classes/methods fall within the changed line ranges
  - which knowledge nodes (Concept, Rule, Decision, WikiPage) are linked
    to those symbols via ANNOTATES, IMPLEMENTS, or GOVERNS edges
  - a single impact_summary dict bundling all of the above

Usage::

    from pathlib import Path
    from navegador.diff import DiffAnalyzer
    from navegador.graph.store import GraphStore

    store = GraphStore.sqlite(".navegador/graph.db")
    analyzer = DiffAnalyzer(store, Path("."))
    print(analyzer.impact_summary())
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from navegador.graph.store import GraphStore
from navegador.vcs import GitAdapter


# ── Cypher helpers ────────────────────────────────────────────────────────────

# All symbols (Function / Class / Method) in a given file with their line ranges
_SYMBOLS_IN_FILE = """
MATCH (n)
WHERE (n:Function OR n:Class OR n:Method)
  AND n.file_path = $file_path
RETURN labels(n)[0] AS type,
       n.name        AS name,
       n.file_path   AS file_path,
       n.line_start  AS line_start,
       n.line_end    AS line_end
ORDER BY n.line_start
"""

# Knowledge nodes reachable from a code symbol in one hop via cross-layer edges.
# Handles:
#   (symbol)-[:ANNOTATES|IMPLEMENTS]->(knowledge)  — code → knowledge
#   (knowledge)-[:GOVERNS|ANNOTATES]->(symbol)      — knowledge → code
_KNOWLEDGE_FOR_SYMBOL = """
MATCH (sym)
WHERE (sym:Function OR sym:Class OR sym:Method)
  AND sym.name      = $name
  AND sym.file_path = $file_path
OPTIONAL MATCH (sym)-[:ANNOTATES|IMPLEMENTS]->(k1)
  WHERE (k1:Concept OR k1:Rule OR k1:Decision OR k1:WikiPage)
OPTIONAL MATCH (k2)-[:GOVERNS|ANNOTATES]->(sym)
  WHERE (k2:Concept OR k2:Rule OR k2:Decision OR k2:WikiPage)
WITH collect(DISTINCT k1) + collect(DISTINCT k2) AS knowledge_nodes
UNWIND knowledge_nodes AS k
RETURN DISTINCT
    labels(k)[0]                           AS type,
    k.name                                 AS name,
    coalesce(k.description, '')            AS description,
    coalesce(k.domain, '')                 AS domain,
    coalesce(k.status, '')                 AS status
"""


def _parse_unified_diff_hunks(diff_output: str) -> dict[str, list[tuple[int, int]]]:
    """
    Parse the output of ``git diff -U0 HEAD`` and return a mapping of
    file path → list of (start_line, end_line) changed ranges.

    Only new/modified lines (``+`` prefix) are tracked; deleted-only hunks
    contribute the surrounding context line instead (line before deletion).
    """
    result: dict[str, list[tuple[int, int]]] = {}
    current_file: str | None = None
    current_new_start = 0
    current_new_count = 0

    for line in diff_output.splitlines():
        # New file header: +++ b/path/to/file
        if line.startswith("+++ b/"):
            current_file = line[6:]
            if current_file not in result:
                result[current_file] = []
        elif line.startswith("+++ /dev/null"):
            current_file = None  # deleted file — skip
        elif line.startswith("@@ "):
            # Hunk header: @@ -old_start[,old_count] +new_start[,new_count] @@
            try:
                new_info = line.split("+")[1].split("@@")[0].strip()
                if "," in new_info:
                    new_start_str, new_count_str = new_info.split(",", 1)
                    current_new_start = int(new_start_str)
                    current_new_count = int(new_count_str)
                else:
                    current_new_start = int(new_info)
                    current_new_count = 1
                if current_file and current_new_count > 0:
                    end = current_new_start + max(current_new_count - 1, 0)
                    result.setdefault(current_file, []).append(
                        (current_new_start, end)
                    )
            except (ValueError, IndexError):
                pass

    return result


def _lines_overlap(
    changed_ranges: list[tuple[int, int]],
    line_start: int | None,
    line_end: int | None,
) -> bool:
    """Return True when any changed range overlaps with [line_start, line_end]."""
    if line_start is None:
        return False
    sym_start = line_start
    sym_end = line_end if line_end is not None else line_start
    for r_start, r_end in changed_ranges:
        if r_start <= sym_end and r_end >= sym_start:
            return True
    return False


# ── DiffAnalyzer ─────────────────────────────────────────────────────────────


class DiffAnalyzer:
    """Maps git diff output to affected graph nodes.

    Parameters
    ----------
    store:
        An open :class:`~navegador.graph.store.GraphStore`.
    repo_path:
        Root of the git repository to inspect.
    """

    def __init__(self, store: GraphStore, repo_path: Path) -> None:
        self.store = store
        self.repo_path = Path(repo_path)
        self._git = GitAdapter(self.repo_path)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def changed_files(self) -> list[str]:
        """Return paths of files with uncommitted changes (staged + unstaged).

        Delegates to :class:`~navegador.vcs.GitAdapter` which runs
        ``git diff HEAD --name-only``.
        """
        return self._git.changed_files()

    def changed_lines(self) -> dict[str, list[tuple[int, int]]]:
        """Return a mapping of file path → list of (start, end) changed line ranges.

        Runs ``git diff -U0 HEAD`` and parses the unified diff hunk headers.
        Falls back to the whole file (line 1 to a large sentinel) when the
        diff cannot be parsed precisely — ensuring callers always get a result.
        """
        result = subprocess.run(
            ["git", "diff", "-U0", "HEAD"],
            cwd=self.repo_path,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0 or not result.stdout.strip():
            # No HEAD yet (initial commit) or empty diff — treat all changed
            # files as fully-changed using a wide sentinel range.
            return {f: [(1, 999_999)] for f in self.changed_files()}

        parsed = _parse_unified_diff_hunks(result.stdout)

        # Files returned by changed_files() but missing from the parsed diff
        # (e.g. binary files, new untracked files added with --intent-to-add)
        # get a full-file sentinel range.
        for f in self.changed_files():
            if f not in parsed:
                parsed[f] = [(1, 999_999)]

        return parsed

    def affected_symbols(self) -> list[dict[str, Any]]:
        """Return functions/classes/methods whose line ranges overlap changed lines.

        Each entry is a dict with the keys:
        ``type``, ``name``, ``file_path``, ``line_start``, ``line_end``.
        """
        line_map = self.changed_lines()
        symbols: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()

        for file_path, changed_ranges in line_map.items():
            rows = self.store.query(_SYMBOLS_IN_FILE, {"file_path": file_path})
            if not rows or not rows.result_set:
                continue
            for row in rows.result_set:
                sym_type, name, fp, line_start, line_end = row
                key = (name, fp or file_path)
                if key in seen:
                    continue
                if _lines_overlap(changed_ranges, line_start, line_end):
                    seen.add(key)
                    symbols.append(
                        {
                            "type": sym_type,
                            "name": name,
                            "file_path": fp or file_path,
                            "line_start": line_start,
                            "line_end": line_end,
                        }
                    )

        return symbols

    def affected_knowledge(self) -> list[dict[str, Any]]:
        """Return knowledge nodes linked to affected code symbols.

        Traverses ANNOTATES, IMPLEMENTS (code → knowledge) and
        GOVERNS, ANNOTATES (knowledge → code) edges from each affected symbol.

        Each entry is a dict with the keys:
        ``type``, ``name``, ``description``, ``domain``, ``status``.
        """
        symbols = self.affected_symbols()
        knowledge: list[dict[str, Any]] = []
        seen: set[str] = set()

        for sym in symbols:
            rows = self.store.query(
                _KNOWLEDGE_FOR_SYMBOL,
                {"name": sym["name"], "file_path": sym["file_path"]},
            )
            if not rows or not rows.result_set:
                continue
            for row in rows.result_set:
                k_type, k_name, k_desc, k_domain, k_status = row
                if k_name in seen:
                    continue
                seen.add(k_name)
                knowledge.append(
                    {
                        "type": k_type,
                        "name": k_name,
                        "description": k_desc,
                        "domain": k_domain,
                        "status": k_status,
                    }
                )

        return knowledge

    def impact_summary(self) -> dict[str, Any]:
        """Return a full impact summary: files, symbols, and knowledge nodes.

        The returned dict has the keys:
          ``files``      — list of changed file paths
          ``symbols``    — list of affected symbol dicts
          ``knowledge``  — list of affected knowledge node dicts
          ``counts``     — sub-dict with ``files``, ``symbols``, ``knowledge`` counts
        """
        files = self.changed_files()
        symbols = self.affected_symbols()
        knowledge = self.affected_knowledge()

        return {
            "files": files,
            "symbols": symbols,
            "knowledge": knowledge,
            "counts": {
                "files": len(files),
                "symbols": len(symbols),
                "knowledge": len(knowledge),
            },
        }

    # ------------------------------------------------------------------
    # Formatters
    # ------------------------------------------------------------------

    def to_json(self) -> str:
        """Serialise impact_summary() as a JSON string."""
        return json.dumps(self.impact_summary(), indent=2, default=str)

    def to_markdown(self) -> str:
        """Render impact_summary() as a human-readable Markdown string."""
        summary = self.impact_summary()
        lines: list[str] = ["# Diff Impact Summary\n"]

        # Files
        lines.append(f"## Changed Files ({summary['counts']['files']})\n")
        if summary["files"]:
            for f in summary["files"]:
                lines.append(f"- `{f}`")
        else:
            lines.append("_No changed files._")
        lines.append("")

        # Symbols
        lines.append(f"## Affected Symbols ({summary['counts']['symbols']})\n")
        if summary["symbols"]:
            for sym in summary["symbols"]:
                loc = f"line {sym['line_start']}" if sym.get("line_start") else ""
                lines.append(
                    f"- **{sym['type']}** `{sym['name']}` — `{sym['file_path']}`"
                    + (f" ({loc})" if loc else "")
                )
        else:
            lines.append("_No affected symbols found in graph._")
        lines.append("")

        # Knowledge
        lines.append(f"## Affected Knowledge ({summary['counts']['knowledge']})\n")
        if summary["knowledge"]:
            for k in summary["knowledge"]:
                desc = f" — {k['description']}" if k.get("description") else ""
                lines.append(f"- **{k['type']}** `{k['name']}`{desc}")
        else:
            lines.append("_No linked knowledge nodes._")

        return "\n".join(lines)
