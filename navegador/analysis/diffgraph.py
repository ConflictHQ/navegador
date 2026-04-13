"""
PR / branch diff graph — structural comparison between two git refs.

Unlike raw line diffs, this compares graph snapshots: what new CALLS, IMPORTS,
INHERITS, or ownership edges did this branch introduce? What symbols changed
blast radius? Which domains, rules, docs, or tests are newly affected?

Usage::

    from navegador.analysis.diffgraph import DiffGraphAnalyzer

    analyzer = DiffGraphAnalyzer(store, repo_path="/path/to/repo")

    # Compare working tree against HEAD (current uncommitted changes)
    report = analyzer.diff_working_tree()

    # Compare two refs (branch, tag, commit SHA)
    report = analyzer.diff_refs(base="main", head="feature/new-auth")

    print(report.to_markdown())
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from navegador.graph import GraphStore


@dataclass
class StructuralChange:
    kind: str  # "added_call" | "removed_call" | "new_symbol" | "removed_symbol"
    # "new_import" | "changed_blast_radius" | "affected_knowledge"
    # "added" | "removed" | "moved" (snapshot diff)
    symbol: str
    file_path: str = ""
    detail: str = ""  # target of a call/import, or blast-radius delta info
    line_start: int | None = None


@dataclass
class DiffGraphReport:
    base_ref: str
    head_ref: str

    # Structural changes detected
    new_symbols: list[StructuralChange] = field(default_factory=list)
    changed_symbols: list[StructuralChange] = field(default_factory=list)
    affected_files: list[str] = field(default_factory=list)
    affected_knowledge: list[dict[str, str]] = field(default_factory=list)
    blast_radius_summary: dict[str, Any] = field(default_factory=dict)

    # Snapshot-backed diff fields
    added_nodes: list[StructuralChange] = field(default_factory=list)
    removed_nodes: list[StructuralChange] = field(default_factory=list)
    moved_nodes: list[StructuralChange] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        def _sc(lst: list[StructuralChange]) -> list[dict[str, Any]]:
            return [s.__dict__ for s in lst]

        return {
            "base": self.base_ref,
            "head": self.head_ref,
            "new_symbols": _sc(self.new_symbols),
            "changed_symbols": _sc(self.changed_symbols),
            "affected_files": self.affected_files,
            "affected_knowledge": self.affected_knowledge,
            "blast_radius_summary": self.blast_radius_summary,
            "added_nodes": _sc(self.added_nodes),
            "removed_nodes": _sc(self.removed_nodes),
            "moved_nodes": _sc(self.moved_nodes),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    def to_markdown(self) -> str:
        lines = [f"# Structural Diff — `{self.base_ref}` → `{self.head_ref}`\n"]

        if self.affected_files:
            lines.append(f"\n## Changed Files ({len(self.affected_files)})\n")
            for f in self.affected_files:
                lines.append(f"- `{f}`")

        if self.new_symbols:
            lines.append(f"\n## New / Modified Symbols ({len(self.new_symbols)})\n")
            for s in self.new_symbols:
                loc = f":{s.line_start}" if s.line_start else ""
                detail = f" — {s.detail}" if s.detail else ""
                lines.append(f"- **{s.kind}** `{s.symbol}` `{s.file_path}`{loc}{detail}")

        if self.changed_symbols:
            lines.append(f"\n## Structurally Changed Symbols ({len(self.changed_symbols)})\n")
            for s in self.changed_symbols:
                lines.append(f"- **{s.kind}** `{s.symbol}` `{s.file_path}` — {s.detail}")

        if self.affected_knowledge:
            lines.append(f"\n## Affected Knowledge ({len(self.affected_knowledge)})\n")
            for k in self.affected_knowledge:
                lines.append(f"- **{k['type']}** `{k['name']}`")

        if self.added_nodes:
            lines.append(f"\n## Added Symbols ({len(self.added_nodes)})\n")
            for s in self.added_nodes:
                loc = f":{s.line_start}" if s.line_start else ""
                lines.append(f"- **added** `{s.symbol}` `{s.file_path}`{loc}")

        if self.removed_nodes:
            lines.append(f"\n## Removed Symbols ({len(self.removed_nodes)})\n")
            for s in self.removed_nodes:
                loc = f":{s.line_start}" if s.line_start else ""
                lines.append(f"- **removed** `{s.symbol}` `{s.file_path}`{loc}")

        if self.moved_nodes:
            lines.append(f"\n## Moved Symbols ({len(self.moved_nodes)})\n")
            for s in self.moved_nodes:
                lines.append(f"- **moved** `{s.symbol}` — {s.detail}")

        if self.blast_radius_summary:
            br = self.blast_radius_summary
            lines.append("\n## Blast Radius Summary\n")
            lines.append(f"- Affected nodes: {br.get('total_affected', 0)}")
            lines.append(f"- Affected files: {br.get('affected_files', 0)}")
            lines.append(f"- Affected knowledge: {br.get('affected_knowledge', 0)}")

        return "\n".join(lines)


class DiffGraphAnalyzer:
    """
    Computes structural graph changes between two git refs.

    Strategy:
    1. Use git diff to find changed files and affected line ranges.
    2. Query the current graph for symbols in those files/ranges.
    3. For each affected symbol, run blast-radius analysis.
    4. Report: new symbols, structurally changed symbols, blast radius summary,
       affected knowledge nodes.

    Note: for true before/after comparison, callers should ingest both refs
    into separate graphs and compare node sets. This analyzer works against the
    currently-ingested graph, treating changed-lines symbols as the delta.
    """

    def __init__(self, store: GraphStore, repo_path: str | Path) -> None:
        self.store = store
        self.repo_path = Path(repo_path)

    def diff_working_tree(self) -> DiffGraphReport:
        """Structural diff of working tree vs HEAD."""
        return self._build_report("HEAD", "working tree")

    def diff_refs(self, base: str, head: str = "HEAD") -> DiffGraphReport:
        """
        Structural diff between two git refs (branches, tags, SHAs).

        Runs ``git diff <base>...<head>`` to find changed files/lines,
        then maps those to graph symbols.
        """
        return self._build_report(base, head)

    def diff_snapshots(self, base_ref: str, head_ref: str) -> DiffGraphReport:
        """
        True graph diff between two snapshot refs.

        Requires both refs to have been snapshotted via HistoryStore.snapshot().
        Falls back to diff_refs() heuristic if either snapshot is missing.
        """
        from navegador.history import HistoryStore

        h = HistoryStore(self.store, self.repo_path)

        snaps = {s.ref for s in h.list_snapshots()}
        if base_ref not in snaps or head_ref not in snaps:
            return self.diff_refs(base=base_ref, head=head_ref)

        delta = h.diff_snapshots(base_ref, head_ref)

        report = DiffGraphReport(base_ref=base_ref, head_ref=head_ref)

        for e in delta["added"]:
            report.added_nodes.append(
                StructuralChange(
                    kind="added",
                    symbol=e["name"],
                    file_path=e.get("file_path") or "",
                    line_start=e.get("line_start"),
                )
            )

        for e in delta["removed"]:
            report.removed_nodes.append(
                StructuralChange(
                    kind="removed",
                    symbol=e["name"],
                    file_path=e.get("file_path") or "",
                    line_start=e.get("line_start"),
                )
            )

        for e in delta["moved"]:
            report.moved_nodes.append(
                StructuralChange(
                    kind="moved",
                    symbol=e["name"],
                    file_path=e["to"],
                    detail=f"{e['from']} \u2192 {e['to']}",
                )
            )

        # Collect all unique affected file paths
        all_files: set[str] = set()
        for node in report.added_nodes:
            if node.file_path:
                all_files.add(node.file_path)
        for node in report.removed_nodes:
            if node.file_path:
                all_files.add(node.file_path)
        for node in report.moved_nodes:
            if node.file_path:
                all_files.add(node.file_path)
            # Also include the source file from moved detail
            for e in delta["moved"]:
                if e["name"] == node.symbol and e["from"]:
                    all_files.add(e["from"])
        report.affected_files = sorted(all_files)

        # Blast radius for added symbols
        from navegador.analysis.impact import ImpactAnalyzer

        impact = ImpactAnalyzer(self.store)
        all_affected_nodes: list[dict[str, Any]] = []
        all_affected_knowledge: list[dict[str, str]] = []

        for node in report.added_nodes:
            result = impact.blast_radius(node.symbol, file_path=node.file_path, depth=2)
            all_affected_nodes.extend(result.affected_nodes)
            all_affected_knowledge.extend(result.affected_knowledge)

        # Dedupe knowledge
        seen_k: set[str] = set()
        for k in all_affected_knowledge:
            key = f"{k['type']}:{k['name']}"
            if key not in seen_k:
                seen_k.add(key)
                report.affected_knowledge.append(k)

        # Aggregate blast radius summary
        unique_files: set[str] = {n["file_path"] for n in all_affected_nodes if n.get("file_path")}
        report.blast_radius_summary = {
            "total_affected": len(all_affected_nodes),
            "affected_files": len(unique_files),
            "affected_knowledge": len(report.affected_knowledge),
        }

        return report

    # ── Internals ──────────────────────────────────────────────────────────────

    def _build_report(self, base: str, head: str) -> DiffGraphReport:
        report = DiffGraphReport(base_ref=base, head_ref=head)

        # Get changed files and line ranges
        changed_files, line_map = self._get_changes(base, head)
        report.affected_files = sorted(changed_files)

        # Map changed lines to graph symbols
        from navegador.analysis.impact import ImpactAnalyzer
        from navegador.diff import _SYMBOLS_IN_FILE, _lines_overlap

        impact = ImpactAnalyzer(self.store)
        all_affected_nodes: list[dict[str, Any]] = []
        all_affected_knowledge: list[dict[str, str]] = []
        seen_symbols: set[str] = set()

        for file_path in changed_files:
            changed_ranges = line_map.get(file_path, [(1, 999_999)])
            rows = self.store.query(_SYMBOLS_IN_FILE, {"file_path": file_path}).result_set or []

            for row in rows:
                _sym_type, sym_name, sym_file, line_start, line_end = (
                    row[0],
                    row[1],
                    row[2],
                    row[3],
                    row[4],
                )
                if not _lines_overlap(changed_ranges, line_start, line_end):
                    continue

                key = f"{sym_name}:{sym_file}"
                if key in seen_symbols:
                    continue
                seen_symbols.add(key)

                kind = "new_symbol" if self._is_new(sym_name, sym_file, base) else "changed_symbol"
                change = StructuralChange(
                    kind=kind,
                    symbol=sym_name,
                    file_path=sym_file or "",
                    line_start=line_start,
                )
                if kind == "new_symbol":
                    report.new_symbols.append(change)
                else:
                    report.changed_symbols.append(change)

                # Blast radius per affected symbol
                result = impact.blast_radius(sym_name, file_path=sym_file or "", depth=2)
                all_affected_nodes.extend(result.affected_nodes)
                all_affected_knowledge.extend(result.affected_knowledge)

        # Dedupe knowledge
        seen_k: set[str] = set()
        for k in all_affected_knowledge:
            key = f"{k['type']}:{k['name']}"
            if key not in seen_k:
                seen_k.add(key)
                report.affected_knowledge.append(k)

        # Aggregate blast radius summary
        unique_files: set[str] = {n["file_path"] for n in all_affected_nodes if n["file_path"]}
        report.blast_radius_summary = {
            "total_affected": len(all_affected_nodes),
            "affected_files": len(unique_files),
            "affected_knowledge": len(report.affected_knowledge),
        }

        return report

    def _get_changes(
        self, base: str, head: str
    ) -> tuple[list[str], dict[str, list[tuple[int, int]]]]:
        """Return (changed_files, line_map) for the given ref range."""
        from navegador.diff import _parse_unified_diff_hunks

        if base == "HEAD" and head == "working tree":
            # Working tree diff
            diff_args = ["git", "diff", "-U0", "HEAD"]
            names_args = ["git", "diff", "HEAD", "--name-only"]
        else:
            diff_args = ["git", "diff", "-U0", f"{base}...{head}"]
            names_args = ["git", "diff", f"{base}...{head}", "--name-only"]

        names_result = subprocess.run(
            names_args, cwd=self.repo_path, capture_output=True, text=True, check=False
        )
        changed_files = [f.strip() for f in names_result.stdout.splitlines() if f.strip()]

        diff_result = subprocess.run(
            diff_args, cwd=self.repo_path, capture_output=True, text=True, check=False
        )
        if diff_result.stdout.strip():
            line_map = _parse_unified_diff_hunks(diff_result.stdout)
        else:
            line_map = {f: [(1, 999_999)] for f in changed_files}

        return changed_files, line_map

    def _is_new(self, name: str, file_path: str, base: str) -> bool:
        """
        Heuristic: check if the symbol existed in the base ref by looking
        at whether the file itself is new (git status).
        """
        if not file_path:
            return False
        result = subprocess.run(
            ["git", "show", f"{base}:{file_path}"],
            cwd=self.repo_path,
            capture_output=True,
            check=False,
        )
        return result.returncode != 0  # file didn't exist in base → symbol is new
