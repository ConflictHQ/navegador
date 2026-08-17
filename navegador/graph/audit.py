"""
Find graphs on a shared server that have stopped describing anything real.

Once every project shares one server (1.5.0), the graph list accumulates and
nothing notices when an entry goes bad. An audit of a live server holding 41
graphs found all three failure modes at once: a 47 MB graph whose 15,291 file
paths had **none** still resolving on disk, the same repository indexed under
three different names, and nine empty graphs named ``1)`` through ``9)`` left
behind by shell output being fed back in as arguments.

None of these break a query. They quietly answer the wrong question, or waste
memory that a shared server is supposed to be conserving (#181).

A graph is only judged where there is ground truth: resolution is measured
against a checkout on disk, and a graph with no known checkout is reported as
``unknown`` rather than guessed at. Nothing is deleted without being named
first.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# A graph name we would never have written. `1)` and `telemetry{9)}` come from
# redis-cli's numbered list output being pasted back in as an argument.
JUNK_NAME = re.compile(r"^\d+\)$|[)\}\{]|^\s*$")

# Below this share of file paths resolving on disk, the graph is describing a
# checkout that no longer exists. Not zero: a little drift is normal as files
# are deleted between ingests, and calling that stale would be false alarm.
STALE_RESOLUTION = 0.05

# An empty falkordblite snapshot is a few hundred bytes.
EMPTY_NODES = 0


@dataclass
class GraphAudit:
    """One graph on the server and what is wrong with it, if anything."""

    name: str
    nodes: int = 0
    edges: int = 0
    size_bytes: int = 0
    files_total: int = 0
    files_resolved: int = 0
    root: Path | None = None
    repositories: list[str] = field(default_factory=list)
    duplicate_of: str | None = None

    @property
    def resolution(self) -> float | None:
        """Share of file paths that still exist, or None without a checkout."""
        if self.root is None or self.files_total == 0:
            return None
        return self.files_resolved / self.files_total

    @property
    def verdict(self) -> str:
        if JUNK_NAME.search(self.name):
            return "junk"
        if self.nodes <= EMPTY_NODES:
            return "empty"
        if self.duplicate_of:
            return "duplicate"
        share = self.resolution
        if share is None:
            return "unknown"
        if share <= STALE_RESOLUTION:
            return "stale"
        return "healthy"

    @property
    def reclaimable(self) -> bool:
        """Safe to delete without losing anything a re-ingest could not rebuild."""
        return self.verdict in {"junk", "empty", "stale"}

    def explain(self) -> str:
        if self.verdict == "junk":
            return "name is not one we would have written; likely shell output fed back in"
        if self.verdict == "empty":
            return "no nodes"
        if self.verdict == "duplicate":
            return f"same repository as {self.duplicate_of}"
        if self.verdict == "stale":
            resolved = f"{self.files_resolved}/{self.files_total}"
            return f"only {resolved} file paths still exist on disk"
        if self.verdict == "unknown":
            return "no checkout found to check against"
        return "ok"

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "verdict": self.verdict,
            "explanation": self.explain(),
            "nodes": self.nodes,
            "edges": self.edges,
            "size_bytes": self.size_bytes,
            "files_total": self.files_total,
            "files_resolved": self.files_resolved,
            "resolution": self.resolution,
            "root": str(self.root) if self.root else None,
            "duplicate_of": self.duplicate_of,
            "reclaimable": self.reclaimable,
        }


def graph_size_bytes(connection, name: str) -> int:
    """
    Byte length of the serialised graph.

    ``MEMORY USAGE`` returns a useless 48 bytes for a graph key because Redis
    cannot size a module type, so DUMP is the only honest measure available
    without vendor-specific commands.
    """
    try:
        blob = connection.dump(name)
    except Exception:
        return 0
    return len(blob) if blob else 0


def _counts(graph) -> tuple[int, int]:
    nodes = graph.ro_query("MATCH (n) RETURN count(n)").result_set
    edges = graph.ro_query("MATCH ()-[r]->() RETURN count(r)").result_set
    return (
        int(nodes[0][0]) if nodes else 0,
        int(edges[0][0]) if edges else 0,
    )


def _file_paths(graph, limit: int) -> list[str]:
    rows = graph.ro_query(f"MATCH (f:File) RETURN f.path LIMIT {int(limit)}").result_set
    return [row[0] for row in (rows or []) if row and row[0]]


def _repositories(graph) -> list[str]:
    rows = graph.ro_query("MATCH (r:Repository) RETURN r.name").result_set
    return sorted({row[0] for row in (rows or []) if row and row[0]})


def audit_graph(
    db,
    connection,
    name: str,
    root: Path | None = None,
    sample: int = 2000,
) -> GraphAudit:
    """
    Inspect one graph, checking its file paths against *root* when given.

    Only a sample of paths is checked: resolution is a ratio, and stat-ing
    50,000 files to learn what 2,000 already say is a waste of a scan.
    """
    report = GraphAudit(name=name, root=root)
    if JUNK_NAME.search(name):
        # Do not query a junk key; it may not even be a graph.
        report.size_bytes = graph_size_bytes(connection, name)
        return report

    graph = db.select_graph(name)
    try:
        report.nodes, report.edges = _counts(graph)
    except Exception:
        return report
    report.size_bytes = graph_size_bytes(connection, name)
    if report.nodes == 0:
        return report

    report.repositories = _repositories(graph)
    if root is None:
        return report

    paths = _file_paths(graph, sample)
    report.files_total = len(paths)
    report.files_resolved = sum(1 for p in paths if (root / p).exists())
    return report


def mark_duplicates(reports: list[GraphAudit]) -> None:
    """
    Flag graphs describing a repository another graph already covers.

    Reported, never merged: two graphs of one repository may differ by ingest
    settings or age, and picking a winner is the operator's call. The larger
    graph keeps its identity and the smaller is flagged.
    """
    by_repo: dict[str, GraphAudit] = {}
    # Node count decides, name breaks ties, so the same server always yields
    # the same answer rather than depending on dict ordering.
    for report in sorted(reports, key=lambda r: (-r.nodes, r.name)):
        if report.verdict in {"junk", "empty"} or not report.repositories:
            continue
        key = "|".join(report.repositories)
        if key in by_repo:
            report.duplicate_of = by_repo[key].name
        else:
            by_repo[key] = report


def audit_server(url: str, roots: dict[str, Path] | None = None) -> list[GraphAudit]:
    """
    Audit every graph on the server at *url*.

    *roots* maps graph name to the checkout it describes — normally built from
    ``navegador scan``. A graph absent from the mapping is reported as
    ``unknown`` rather than assumed stale, because a graph we cannot check is
    not the same as a graph we know is wrong.
    """
    import falkordb
    import redis as redis_lib

    db = falkordb.FalkorDB.from_url(url)
    connection = redis_lib.from_url(url)
    roots = roots or {}

    # GRAPH.LIST returns bytes on a connection that is not decoding responses,
    # and str() on bytes yields "b'name'" — which matches no graph, so every
    # lookup misses and every graph is reported empty. The connection stays
    # undecoded because DUMP must return raw bytes to be measured.
    names = [
        n.decode() if isinstance(n, bytes) else str(n)
        for n in connection.execute_command("GRAPH.LIST")
    ]
    reports = [audit_graph(db, connection, name, roots.get(name)) for name in sorted(names)]
    mark_duplicates(reports)
    return reports


def prune(
    target: str | object, reports: list[GraphAudit], include_stale: bool = False
) -> list[str]:
    """
    Delete the graphs named in *reports* that are safe to remove.

    Junk and empty graphs go without ceremony. Stale ones only when asked
    explicitly: "the checkout moved" and "the checkout is gone" look identical
    from here, and one of those is recoverable by re-ingesting while the other
    throws away the only copy.

    *target* is a server URL or an existing Redis connection. Accepting a
    connection is what lets this be tested against an embedded store, which is
    a real Redis over a unix socket and has no ``redis://`` URL to pass.
    """
    if isinstance(target, str):
        import redis as redis_lib

        connection = redis_lib.from_url(target)
    else:
        connection = target
    removed = []
    for report in reports:
        if report.verdict in {"junk", "empty"} or (include_stale and report.verdict == "stale"):
            try:
                connection.delete(report.name)
                removed.append(report.name)
            except Exception:
                continue
    return removed
