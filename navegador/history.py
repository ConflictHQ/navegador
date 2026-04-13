"""
Time-travel graph and historical symbol lineage.

Stores lightweight graph snapshots for selected git refs (commits, tags,
branches) so you can query how the codebase structure changed over time.

Snapshots are stored inside the same FalkorDB graph using ``Snapshot`` nodes.
Each snapshot records which symbols (Function/Class/Method) existed at that
ref — without duplicating full node data — via ``SNAPSHOT_OF`` edges.

Lineage detection walks the snapshot chain and matches symbols across refs:
  - Same name, same file → identical, possibly changed body
  - Same name, different file → likely moved/renamed
  - Different name, same file, high bigram overlap → likely renamed
  - No match → new symbol or deleted symbol

Usage::

    from navegador.history import HistoryStore

    h = HistoryStore(store, repo_path=".")

    # Ingest snapshots for a list of refs
    h.snapshot("v1.0.0")
    h.snapshot("v2.0.0")
    h.snapshot("HEAD")

    # Query history for a symbol
    timeline = h.history("AuthService", file_path="app/auth.py")
    for entry in timeline:
        print(entry.ref, entry.event, entry.detail)

    # Dump all symbols at a ref
    symbols = h.symbols_at("v1.0.0")

    # Trace lineage (rename/move detection)
    chain = h.lineage("AuthService", file_path="app/auth.py")
    for step in chain:
        print(step.ref, step.name, step.file_path, step.event)
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from navegador.graph import GraphStore


@dataclass
class SnapshotEntry:
    """A symbol as it existed at a specific snapshot ref."""

    ref: str
    name: str
    label: str        # Function | Class | Method
    file_path: str
    line_start: int | None = None
    line_end: int | None = None


@dataclass
class HistoryEvent:
    """One event in a symbol's history across snapshot refs."""

    ref: str
    event: str        # "first_seen" | "moved" | "renamed" | "changed" | "removed" | "seen"
    name: str
    file_path: str
    detail: str = ""


@dataclass
class SnapshotInfo:
    ref: str
    commit_sha: str = ""
    committed_at: str = ""
    symbol_count: int = 0


@dataclass
class LineageStep:
    ref: str
    name: str
    file_path: str
    label: str
    event: str        # "created" | "moved" | "renamed" | "continued" | "removed"
    detail: str = ""


@dataclass
class HistoryReport:
    symbol: str
    file_path: str
    events: list[HistoryEvent] = field(default_factory=list)

    def to_markdown(self) -> str:
        lines = [f"# History — `{self.symbol}`"]
        if self.file_path:
            lines[0] += f" (`{self.file_path}`)"
        lines.append("")
        if not self.events:
            lines.append("_No snapshot history found._")
            return "\n".join(lines)
        lines.append("| Ref | Event | Detail |")
        lines.append("| --- | ----- | ------ |")
        for e in self.events:
            lines.append(f"| `{e.ref}` | **{e.event}** | {e.detail} |")
        return "\n".join(lines)

    def to_json(self) -> str:
        return json.dumps(
            {
                "symbol": self.symbol,
                "file_path": self.file_path,
                "events": [e.__dict__ for e in self.events],
            },
            indent=2,
        )


@dataclass
class LineageReport:
    symbol: str
    chain: list[LineageStep] = field(default_factory=list)

    def to_markdown(self) -> str:
        lines = [f"# Lineage — `{self.symbol}`\n"]
        if not self.chain:
            lines.append("_No lineage data found._")
            return "\n".join(lines)
        for step in self.chain:
            tag = f"**{step.event}**" if step.event != "continued" else step.event
            detail = f" — {step.detail}" if step.detail else ""
            lines.append(
                f"- `{step.ref}` {tag} `{step.name}` "
                f"`{step.file_path}`{detail}"
            )
        return "\n".join(lines)

    def to_json(self) -> str:
        return json.dumps(
            {"symbol": self.symbol, "chain": [s.__dict__ for s in self.chain]},
            indent=2,
        )


# ── Internal helpers ──────────────────────────────────────────────────────────

def _bigrams(s: str) -> set[str]:
    s = s.lower()
    return {s[i : i + 2] for i in range(len(s) - 1)}


def _name_similarity(a: str, b: str) -> float:
    if a == b:
        return 1.0
    bg_a, bg_b = _bigrams(a), _bigrams(b)
    if not bg_a or not bg_b:
        return 0.0
    return len(bg_a & bg_b) / len(bg_a | bg_b)


def _git_sha(repo_path: Path, ref: str) -> str:
    """Resolve a ref to its commit SHA (best effort)."""
    result = subprocess.run(
        ["git", "rev-parse", "--short", ref],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _git_commit_date(repo_path: Path, ref: str) -> str:
    result = subprocess.run(
        ["git", "log", "-1", "--format=%ci", ref],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


# ── Cypher constants ──────────────────────────────────────────────────────────

_CREATE_SNAPSHOT = (
    "MERGE (s:Snapshot {ref: $ref}) "
    "SET s.commit_sha = $sha, s.committed_at = $date, s.symbol_count = $count"
)

_SNAPSHOT_LINK = (
    "MATCH (s:Snapshot {ref: $ref}), (n) "
    "WHERE (n:Function OR n:Class OR n:Method) "
    "AND n.name = $name AND n.file_path = $file_path "
    "MERGE (s)-[:SNAPSHOT_OF]->(n)"
)

_SNAPSHOT_LINK_BATCH = (
    "MATCH (s:Snapshot {ref: $ref}) "
    "WITH s "
    "MATCH (n) WHERE (n:Function OR n:Class OR n:Method) "
    "MERGE (s)-[:SNAPSHOT_OF]->(n)"
)

_LIST_SNAPSHOTS = (
    "MATCH (s:Snapshot) "
    "RETURN s.ref, s.commit_sha, s.committed_at, s.symbol_count "
    "ORDER BY s.committed_at"
)

_SYMBOLS_AT_SNAPSHOT = (
    "MATCH (s:Snapshot {ref: $ref})-[:SNAPSHOT_OF]->(n) "
    "RETURN labels(n)[0], n.name, n.file_path, n.line_start, n.line_end "
    "ORDER BY n.file_path, n.name"
)

_SNAPSHOTS_FOR_SYMBOL = (
    "MATCH (s:Snapshot)-[:SNAPSHOT_OF]->(n) "
    "WHERE n.name = $name AND ($file_path = '' OR n.file_path = $file_path) "
    "RETURN s.ref, s.committed_at, labels(n)[0], n.name, n.file_path "
    "ORDER BY s.committed_at"
)


# ── Main class ────────────────────────────────────────────────────────────────

class HistoryStore:
    """
    Manages graph snapshots for historical symbol lineage queries.

    Snapshots link a ``Snapshot`` node (keyed by git ref) to all
    ``Function``, ``Class``, and ``Method`` nodes visible in the graph
    at the time of snapshotting.  Multiple snapshots build a timeline
    that can be walked to detect renames, moves, and structural drift.
    """

    RENAME_THRESHOLD = 0.70  # bigram similarity for rename detection

    def __init__(self, store: GraphStore, repo_path: str | Path = ".") -> None:
        self.store = store
        self.repo_path = Path(repo_path)

    # ── Public API ────────────────────────────────────────────────────────────

    def snapshot(self, ref: str = "HEAD") -> SnapshotInfo:
        """
        Record a snapshot of the current graph state for *ref*.

        Does not re-ingest the codebase — links whatever symbols are
        currently in the graph to this snapshot node.  For a proper
        before/after comparison, ingest the ref first with ``ingest_repo``
        then call ``snapshot(ref)``.
        """
        sha = _git_sha(self.repo_path, ref)
        date = _git_commit_date(self.repo_path, ref)

        # Count current code symbols
        count_result = self.store.query(
            "MATCH (n) WHERE (n:Function OR n:Class OR n:Method) RETURN count(n)"
        )
        count = 0
        if count_result.result_set:
            count = count_result.result_set[0][0] or 0

        self.store.query(_CREATE_SNAPSHOT, {"ref": ref, "sha": sha, "date": date, "count": count})

        # Link all current code symbols to this snapshot
        self.store.query(_SNAPSHOT_LINK_BATCH, {"ref": ref})

        return SnapshotInfo(ref=ref, commit_sha=sha, committed_at=date, symbol_count=count)

    def list_snapshots(self) -> list[SnapshotInfo]:
        """Return all snapshots ordered by commit date."""
        rows = self.store.query(_LIST_SNAPSHOTS).result_set or []
        return [
            SnapshotInfo(
                ref=r[0] or "",
                commit_sha=r[1] or "",
                committed_at=r[2] or "",
                symbol_count=r[3] or 0,
            )
            for r in rows
        ]

    def symbols_at(self, ref: str) -> list[SnapshotEntry]:
        """Return all symbols captured in a snapshot."""
        rows = self.store.query(_SYMBOLS_AT_SNAPSHOT, {"ref": ref}).result_set or []
        return [
            SnapshotEntry(
                ref=ref,
                label=r[0] or "Function",
                name=r[1] or "",
                file_path=r[2] or "",
                line_start=r[3],
                line_end=r[4],
            )
            for r in rows
        ]

    def history(self, name: str, file_path: str = "") -> HistoryReport:
        """
        Return the history of a symbol across all snapshots.

        Events: first_seen, seen, moved, removed.
        """
        rows = self.store.query(
            _SNAPSHOTS_FOR_SYMBOL, {"name": name, "file_path": file_path}
        ).result_set or []

        events: list[HistoryEvent] = []
        prev_file: str | None = None

        for i, row in enumerate(rows):
            ref = row[0] or ""
            cur_file = row[4] or ""

            if i == 0:
                event = "first_seen"
                detail = f"in `{cur_file}`"
            elif prev_file and cur_file != prev_file:
                event = "moved"
                detail = f"`{prev_file}` → `{cur_file}`"
            else:
                event = "seen"
                detail = f"in `{cur_file}`"

            events.append(HistoryEvent(
                ref=ref,
                event=event,
                name=name,
                file_path=cur_file,
                detail=detail,
            ))
            prev_file = cur_file

        # Detect removal: symbol in penultimate snapshot but not latest
        snapshots = self.list_snapshots()
        if snapshots and rows:
            latest_ref = snapshots[-1].ref
            latest_refs = {r[0] for r in rows}
            if latest_ref not in latest_refs:
                events.append(HistoryEvent(
                    ref=latest_ref,
                    event="removed",
                    name=name,
                    file_path=prev_file or "",
                    detail="not present in latest snapshot",
                ))

        return HistoryReport(symbol=name, file_path=file_path, events=events)

    def lineage(self, name: str, file_path: str = "") -> LineageReport:
        """
        Trace a symbol's lineage across snapshots, detecting renames and moves.

        Walks snapshot pairs; at each step tries to match the symbol (or a
        renamed/moved variant) in the next snapshot.
        """
        snapshots = self.list_snapshots()
        if not snapshots:
            return LineageReport(symbol=name, chain=[])

        chain: list[LineageStep] = []
        current_name = name
        current_file = file_path

        for snap in snapshots:
            symbols = self.symbols_at(snap.ref)
            sym_map: dict[tuple[str, str], SnapshotEntry] = {
                (s.name, s.file_path): s for s in symbols
            }

            # 1. Exact match
            key = (current_name, current_file)
            if key in sym_map:
                s = sym_map[key]
                event = "created" if not chain else "continued"
                chain.append(LineageStep(
                    ref=snap.ref, name=s.name, file_path=s.file_path,
                    label=s.label, event=event,
                ))
                continue

            # 2. Same name, different file (move)
            name_matches = [s for s in symbols if s.name == current_name]
            if name_matches:
                s = name_matches[0]
                event = "created" if not chain else "moved"
                detail = (
                    f"moved from `{current_file}` to `{s.file_path}`"
                    if chain else ""
                )
                chain.append(LineageStep(
                    ref=snap.ref, name=s.name, file_path=s.file_path,
                    label=s.label, event=event, detail=detail,
                ))
                current_file = s.file_path
                continue

            # 3. Similar name in same file (rename)
            if current_file:
                file_syms = [s for s in symbols if s.file_path == current_file]
                best: SnapshotEntry | None = None
                best_score = 0.0
                for s in file_syms:
                    score = _name_similarity(current_name, s.name)
                    if score > best_score:
                        best_score = score
                        best = s
                if best and best_score >= self.RENAME_THRESHOLD:
                    chain.append(LineageStep(
                        ref=snap.ref, name=best.name, file_path=best.file_path,
                        label=best.label, event="renamed",
                        detail=(
                            f"`{current_name}` → `{best.name}` "
                            f"(similarity={best_score:.2f})"
                        ),
                    ))
                    current_name = best.name
                    continue

            # 4. Not found — removed
            if chain:
                chain.append(LineageStep(
                    ref=snap.ref, name=current_name, file_path=current_file,
                    label="", event="removed",
                    detail="not found in this snapshot",
                ))
                break

        return LineageReport(symbol=name, chain=chain)

    def diff_snapshots(
        self, base_ref: str, head_ref: str
    ) -> dict[str, Any]:
        """
        Compare two snapshots: what symbols were added, removed, or moved?

        Returns a dict with keys: added, removed, moved.
        """
        base_syms = {(s.name, s.file_path): s for s in self.symbols_at(base_ref)}
        head_syms = {(s.name, s.file_path): s for s in self.symbols_at(head_ref)}

        added = [s.__dict__ for k, s in head_syms.items() if k not in base_syms]
        removed = [s.__dict__ for k, s in base_syms.items() if k not in head_syms]

        # Moved: same name appears at different path
        base_by_name: dict[str, str] = {s.name: s.file_path for s in base_syms.values()}
        head_by_name: dict[str, str] = {s.name: s.file_path for s in head_syms.values()}
        moved = []
        for name_key, head_file in head_by_name.items():
            base_file = base_by_name.get(name_key)
            if base_file and base_file != head_file:
                moved.append({"name": name_key, "from": base_file, "to": head_file})

        return {"added": added, "removed": removed, "moved": moved}
