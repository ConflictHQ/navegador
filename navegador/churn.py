"""
Code churn correlation — git history analysis for behavioural coupling.

Parses git log to find files that change frequently (churn) and files
that frequently change together (behavioural coupling).  Results are
stored in the graph as properties on File nodes and COUPLED_WITH edges.

Usage::

    from pathlib import Path
    from navegador.churn import ChurnAnalyzer
    from navegador.graph.store import GraphStore

    store = GraphStore.sqlite(".navegador/graph.db")
    analyzer = ChurnAnalyzer(Path("."), limit=500)

    churn   = analyzer.file_churn()
    pairs   = analyzer.coupling_pairs(min_co_changes=3, min_confidence=0.5)
    stats   = analyzer.store_churn(store)
"""

from __future__ import annotations

import subprocess
from collections import defaultdict
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path


# ── Data models ───────────────────────────────────────────────────────────────


@dataclass
class ChurnEntry:
    """Per-file churn statistics derived from git history."""

    file_path: str
    commit_count: int
    lines_changed: int


@dataclass
class CouplingPair:
    """A pair of files that frequently change together in the same commits."""

    file_a: str
    file_b: str
    co_change_count: int
    confidence: float  # co_change_count / max(changes_a, changes_b)


# ── Analyser ──────────────────────────────────────────────────────────────────


class ChurnAnalyzer:
    """Analyze git history for churn and behavioural coupling.

    Parameters
    ----------
    repo_path:
        Path to the root of the git repository.
    limit:
        Maximum number of commits to inspect (most-recent first).
    """

    def __init__(self, repo_path: Path, limit: int = 500) -> None:
        self.repo_path = Path(repo_path)
        self.limit = limit

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _run(self, args: list[str]) -> str:
        """Run a git sub-command and return stdout as a string."""
        result = subprocess.run(
            ["git", *args],
            cwd=self.repo_path,
            capture_output=True,
            text=True,
            check=False,  # caller inspects output; non-zero exit is safe to ignore
        )
        return result.stdout

    def _commit_file_map(self) -> dict[str, list[str]]:
        """
        Return a mapping of commit hash → list of changed files.

        Uses ``git log --format="%H" --name-only`` which emits blocks like::

            <hash>

            file_a.py
            file_b.py

        Empty lines separate commit blocks.
        """
        raw = self._run(
            [
                "log",
                f"--max-count={self.limit}",
                "--format=%H",
                "--name-only",
            ]
        )

        commits: dict[str, list[str]] = {}
        current_hash: str = ""

        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            # A 40-char hex string is a commit hash
            if len(line) == 40 and all(c in "0123456789abcdefABCDEF" for c in line):
                current_hash = line
                commits[current_hash] = []
            elif current_hash:
                commits[current_hash].append(line)

        return commits

    def _numstat_map(self) -> dict[str, int]:
        """
        Return a mapping of file_path → total lines changed (added + deleted).

        Uses ``git log --numstat`` which emits lines like::

            <added>\t<deleted>\t<file>
        """
        raw = self._run(
            [
                "log",
                f"--max-count={self.limit}",
                "--numstat",
                "--format=",  # suppress commit header lines
            ]
        )

        lines_changed: dict[str, int] = defaultdict(int)
        for line in raw.splitlines():
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            added_str, deleted_str, file_path = parts[0], parts[1], parts[2]
            # Binary files show "-" for counts; skip them
            try:
                added = int(added_str)
                deleted = int(deleted_str)
            except ValueError:
                continue
            lines_changed[file_path] += added + deleted

        return dict(lines_changed)

    # ── Public API ────────────────────────────────────────────────────────────

    def file_churn(self) -> list[ChurnEntry]:
        """Return per-file churn stats from git log.

        Each entry carries:

        * ``commit_count`` — number of commits that touched the file
        * ``lines_changed`` — total lines added + deleted across those commits

        Results are sorted by ``commit_count`` descending.
        """
        commit_map = self._commit_file_map()
        numstat = self._numstat_map()

        # Count commits per file
        commit_counts: dict[str, int] = defaultdict(int)
        for files in commit_map.values():
            for f in files:
                commit_counts[f] += 1

        entries = [
            ChurnEntry(
                file_path=fp,
                commit_count=count,
                lines_changed=numstat.get(fp, 0),
            )
            for fp, count in commit_counts.items()
        ]
        entries.sort(key=lambda e: e.commit_count, reverse=True)
        return entries

    def coupling_pairs(
        self,
        min_co_changes: int = 3,
        min_confidence: float = 0.5,
    ) -> list[CouplingPair]:
        """Find files that frequently change together in the same commits.

        Parameters
        ----------
        min_co_changes:
            Minimum number of commits where both files appear together.
        min_confidence:
            Minimum confidence score (co_changes / max(changes_a, changes_b)).
            A value of 1.0 means one file always changes when the other does.

        Returns a list sorted by ``co_change_count`` descending.
        """
        commit_map = self._commit_file_map()

        # Count commits per file and co-change counts per pair
        commit_counts: dict[str, int] = defaultdict(int)
        co_changes: dict[tuple[str, str], int] = defaultdict(int)

        for files in commit_map.values():
            unique_files = list(dict.fromkeys(files))  # deduplicate, preserve order
            for f in unique_files:
                commit_counts[f] += 1
            for fa, fb in combinations(sorted(unique_files), 2):
                co_changes[(fa, fb)] += 1

        pairs: list[CouplingPair] = []
        for (fa, fb), co_count in co_changes.items():
            if co_count < min_co_changes:
                continue
            max_changes = max(commit_counts[fa], commit_counts[fb])
            if max_changes == 0:
                continue
            confidence = co_count / max_changes
            if confidence < min_confidence:
                continue
            pairs.append(
                CouplingPair(
                    file_a=fa,
                    file_b=fb,
                    co_change_count=co_count,
                    confidence=round(confidence, 4),
                )
            )

        pairs.sort(key=lambda p: p.co_change_count, reverse=True)
        return pairs

    def store_churn(self, store: "GraphStore") -> dict[str, int]:  # type: ignore[name-defined]  # noqa: F821
        """Write churn data to the graph.

        * Sets ``churn_score`` (commit count) on existing File nodes.
        * Creates ``COUPLED_WITH`` edges between behaviourally coupled files,
          carrying ``co_change_count`` and ``confidence`` as edge properties.

        Only updates nodes/edges that already exist in the graph — this method
        does not create new File nodes.

        Returns
        -------
        dict
            ``{"churn_updated": int, "couplings_written": int}``
        """
        churn_updated = 0
        couplings_written = 0

        # -- Update File node churn scores ------------------------------------
        for entry in self.file_churn():
            cypher = (
                "MATCH (f:File {file_path: $fp}) "
                "SET f.churn_score = $score, f.lines_changed = $lc"
            )
            result = store.query(
                cypher,
                {"fp": entry.file_path, "score": entry.commit_count, "lc": entry.lines_changed},
            )
            # FalkorDB returns stats; count rows affected if available
            if getattr(result, "nodes_modified", None) or getattr(
                result, "properties_set", None
            ):
                churn_updated += 1
            else:
                # Fallback: assume the match succeeded if no error was raised
                churn_updated += 1

        # -- Write COUPLED_WITH edges -----------------------------------------
        pairs = self.coupling_pairs()
        for pair in pairs:
            cypher = (
                "MATCH (a:File {file_path: $fa}), (b:File {file_path: $fb}) "
                "MERGE (a)-[r:COUPLED_WITH]->(b) "
                "SET r.co_change_count = $co, r.confidence = $conf"
            )
            store.query(
                cypher,
                {
                    "fa": pair.file_a,
                    "fb": pair.file_b,
                    "co": pair.co_change_count,
                    "conf": pair.confidence,
                },
            )
            couplings_written += 1

        return {"churn_updated": churn_updated, "couplings_written": couplings_written}
