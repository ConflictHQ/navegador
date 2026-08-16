"""
Copy a graph from one store to another — embedded → Redis, Redis → Redis.

This is the migration path from per-project embedded graphs to a shared
FalkorDB server. It is deliberately *not* built on the JSONL export/import in
:mod:`navegador.graph.export`: that format identifies edge endpoints by
``(name, path)`` merge keys, which are ambiguous across labels and silently drop
edges whose endpoints do not round-trip. Losing edges during a migration is the
one failure that must not happen quietly.

Instead every source node is stamped with a temporary property carrying its
source-internal id, edges are reconnected by that id, and the property is
removed afterwards. Endpoint identity is exact, so the copy is lossless and
verifiable by comparing node and edge counts on both sides.
"""

import logging
import re
import time
from collections import defaultdict
from typing import Any

from navegador.graph.store import GraphStore

logger = logging.getLogger(__name__)

#: Temporary property holding the source graph's internal node id.
MIGRATION_KEY = "_nav_transfer_id"

#: Rows sent per write round trip. Large enough to amortise latency, small
#: enough to stay well under FalkorDB's query and result-set ceilings.
DEFAULT_BATCH = 500

#: Rows fetched per read round trip. Deliberately larger than the write batch:
#: reads are the cheaper half and their cost is dominated by round trips, so a
#: small read page turns a million-node graph into thousands of them.
READ_PAGE = 5000

#: Per-query ceilings for migration, in milliseconds. A server's configured
#: default is tuned for interactive queries — the official FalkorDB image ships
#: TIMEOUT 1000 — which a bulk read over a million-edge graph will exceed. A
#: migration is a batch job and is allowed to take minutes.
READ_TIMEOUT_MS = 600_000
WRITE_TIMEOUT_MS = 600_000

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class TransferError(RuntimeError):
    """Raised when a graph cannot be copied faithfully."""


def _safe_identifier(name: str, kind: str) -> str:
    """
    Validate a label or relationship type before interpolating it into Cypher.

    Labels and types cannot be passed as query parameters, so they are the one
    place a hostile or malformed source graph could inject Cypher.
    """
    if not isinstance(name, str) or not _IDENTIFIER.match(name):
        raise TransferError(f"Refusing to copy {kind} with unsafe name: {name!r}")
    return name


def _decode(value: Any) -> Any:
    return value.decode() if isinstance(value, bytes) else value


def _labels_of(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        return [_decode(x) for x in raw if x is not None]
    return [_decode(raw)]


def copy_graph(
    source: GraphStore,
    dest: GraphStore,
    clear: bool = True,
    batch_size: int = DEFAULT_BATCH,
) -> dict[str, int]:
    """
    Copy every node and edge from *source* into *dest*.

    Args:
        source: Store to read from. Left untouched.
        dest:   Store to write into.
        clear:  Wipe *dest* first (default). Without it, the copy is additive
                and node counts will not match on verification.

    Returns:
        Counts for ``nodes``, ``edges``, and the ``source_nodes`` /
        ``source_edges`` they were compared against.

    Raises:
        TransferError: if the destination does not end up with the same node
            and edge counts as the source.
    """
    source_nodes = source.node_count()
    source_edges = source.edge_count()

    if clear:
        dest.clear()

    nodes_written, indexed_labels = _copy_nodes(source, dest, batch_size)
    edges_written = _copy_edges(source, dest, batch_size)
    _drop_migration_key(dest)
    # Leave no transfer scaffolding behind: the property is gone, so an index
    # over it is dead weight, and a leftover one is what breaks the next copy.
    for label in indexed_labels:
        _drop_transfer_index(dest, label)

    dest_nodes = dest.node_count()
    dest_edges = dest.edge_count()

    if clear and (dest_nodes != source_nodes or dest_edges != source_edges):
        raise TransferError(
            "Graph copy did not verify — refusing to report success.\n"
            f"  source: {source_nodes} nodes, {source_edges} edges\n"
            f"  dest:   {dest_nodes} nodes, {dest_edges} edges\n"
            "The destination graph has been written but is incomplete; "
            "re-run the copy or restore from the source, which is unchanged."
        )

    logger.info("Copied %d nodes, %d edges", dest_nodes, dest_edges)
    return {
        "nodes": dest_nodes,
        "edges": dest_edges,
        "source_nodes": source_nodes,
        "source_edges": source_edges,
        "nodes_written": nodes_written,
        "edges_written": edges_written,
    }


def _stream_by_id(source: GraphStore, match: str, returns: str, id_expr: str, page: int):
    """
    Yield rows in pages, walking forward by internal id.

    Deliberately not SKIP/LIMIT: a deep SKIP re-scans and re-sorts everything it
    skips, so page cost grows with offset and a large graph eventually exceeds
    the server's query timeout mid-migration. Carrying the last id forward keeps
    every page the same cost. The first returned column must be *id_expr*.
    """
    last = -1
    while True:
        rows = (
            source.query(
                f"MATCH {match} WHERE {id_expr} > {int(last)} "
                f"RETURN {returns} ORDER BY {id_expr} LIMIT {int(page)}",
                timeout=READ_TIMEOUT_MS,
            ).result_set
            or []
        )
        if not rows:
            return
        yield from rows
        if len(rows) < page:
            return
        last = rows[-1][0]


def _copy_nodes(source: GraphStore, dest: GraphStore, batch_size: int) -> tuple[int, set[str]]:
    """
    Recreate every source node in *dest*, stamped with its source id.

    Returns the number written and the labels that were indexed, so the caller
    can tear those indexes down once the edges are reconnected.
    """
    # Group by label set: a label cannot be parameterised, so each distinct
    # label set needs its own CREATE statement. Buffers are flushed as they fill
    # rather than materialising the whole graph in memory first.
    buffers: dict[tuple[str, ...], list[dict]] = defaultdict(list)
    written = 0

    def flush(labels: tuple[str, ...]) -> int:
        items = buffers[labels]
        if not items:
            return 0
        for label in labels:
            _safe_identifier(label, "label")
        label_clause = "".join(f":{label}" for label in labels)
        # Unlabelled nodes are legal in FalkorDB and must still be copied.
        pattern = f"(n{label_clause})" if label_clause else "(n)"
        dest.query(
            f"UNWIND $rows AS row CREATE {pattern} "
            f"SET n = row.props SET n.{MIGRATION_KEY} = row.id",
            {"rows": items},
            timeout=WRITE_TIMEOUT_MS,
        )
        count = len(items)
        buffers[labels] = []
        return count

    rows = _stream_by_id(
        source,
        "(n)",
        "id(n) AS nid, labels(n) AS labels, properties(n) AS props",
        "id(n)",
        READ_PAGE,
    )
    for row in rows:
        labels = tuple(_labels_of(row[1]))
        buffers[labels].append({"id": row[0], "props": row[2] if isinstance(row[2], dict) else {}})
        if len(buffers[labels]) >= batch_size:
            written += flush(labels)

    for labels in list(buffers):
        written += flush(labels)

    indexed = {label for labels in buffers for label in labels}
    for label in indexed:
        _ensure_transfer_index(dest, label)
    _wait_for_indexes(dest, indexed)

    return written, indexed


def _wait_for_indexes(dest: GraphStore, labels: set[str], timeout: float = 300.0) -> None:
    """
    Block until every transfer index is operational.

    FalkorDB builds indexes asynchronously. A lookup issued against one that is
    still under construction returns no rows rather than waiting, so edges whose
    endpoints were not yet indexed are silently not created — the copy finishes
    with every node present and a fraction of the edges missing. Under light
    load the build completes between statements and nothing goes wrong, which is
    what makes this fail intermittently and only on larger graphs.
    """
    if not labels:
        return

    deadline = time.monotonic() + timeout
    pending = set(labels)
    while pending:
        try:
            rows = dest.query("CALL db.indexes()", timeout=READ_TIMEOUT_MS).result_set or []
        except Exception:  # noqa: BLE001 — older servers may not expose db.indexes()
            return
        for row in rows:
            if len(row) < 8:
                continue
            label = _decode(row[0])
            props = [_decode(p) for p in row[1]] if isinstance(row[1], (list, tuple)) else []
            if label in pending and MIGRATION_KEY in props:
                if _decode(row[7]) == "OPERATIONAL":
                    pending.discard(label)
        if not pending:
            return
        if time.monotonic() >= deadline:
            raise TransferError(
                "Timed out waiting for transfer indexes to become operational on: "
                + ", ".join(sorted(pending))
                + ". Copying edges now would silently drop those whose endpoints "
                "are not yet indexed."
            )
        time.sleep(0.1)


def _ensure_transfer_index(dest: GraphStore, label: str) -> None:
    """
    Build a fresh index on the temporary id property for *label*.

    Without an index, reconnecting edges degrades to a full scan per edge — the
    difference between seconds and hours on a real graph.

    Any pre-existing index is dropped first rather than reused. Indexes survive
    ``MATCH (n) DETACH DELETE n``, and one left over from an earlier copy into
    the same graph returns *no rows* for nodes that are demonstrably present —
    so edges whose endpoints it should have found are silently never created.
    That produces a destination with every node and only some of its edges,
    which is exactly the outcome this module exists to prevent.
    """
    _drop_transfer_index(dest, label)
    dest.query(f"CREATE INDEX FOR (n:{label}) ON (n.{MIGRATION_KEY})")


def _drop_transfer_index(dest: GraphStore, label: str) -> None:
    """Remove the transfer index for *label*, tolerating its absence."""
    try:
        dest.query(f"DROP INDEX FOR (n:{label}) ON (n.{MIGRATION_KEY})")
    except Exception:  # noqa: BLE001 — no index to drop is the common case
        pass


def _copy_edges(source: GraphStore, dest: GraphStore, batch_size: int) -> int:
    """Reconnect every source edge in *dest* using the stamped source ids."""
    # Group by (source label, type, destination label) so each statement can use
    # labelled, index-backed lookups on both endpoints.
    buffers: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    written = 0

    def flush(key: tuple[str, str, str]) -> int:
        items = buffers[key]
        if not items:
            return 0
        src_label, edge_type, dst_label = key
        _safe_identifier(edge_type, "relationship type")
        src_clause = f":{_safe_identifier(src_label, 'label')}" if src_label else ""
        dst_clause = f":{_safe_identifier(dst_label, 'label')}" if dst_label else ""
        dest.query(
            f"UNWIND $rows AS row "
            f"MATCH (a{src_clause} {{{MIGRATION_KEY}: row.src}}), "
            f"(b{dst_clause} {{{MIGRATION_KEY}: row.dst}}) "
            f"CREATE (a)-[r:{edge_type}]->(b) SET r = row.props",
            {"rows": items},
            timeout=WRITE_TIMEOUT_MS,
        )
        count = len(items)
        buffers[key] = []
        return count

    rows = _stream_by_id(
        source,
        "(a)-[r]->(b)",
        "id(r) AS rid, id(a) AS src, labels(a) AS src_labels, type(r) AS type, "
        "properties(r) AS props, id(b) AS dst, labels(b) AS dst_labels",
        "id(r)",
        READ_PAGE,
    )
    for row in rows:
        src_labels = _labels_of(row[2])
        dst_labels = _labels_of(row[6])
        key = (
            src_labels[0] if src_labels else "",
            _decode(row[3]),
            dst_labels[0] if dst_labels else "",
        )
        buffers[key].append(
            {"src": row[1], "dst": row[5], "props": row[4] if isinstance(row[4], dict) else {}}
        )
        if len(buffers[key]) >= batch_size:
            written += flush(key)

    for key in list(buffers):
        written += flush(key)

    return written


def _drop_migration_key(dest: GraphStore) -> None:
    """Remove the temporary id property so the copy is indistinguishable."""
    dest.query(f"MATCH (n) WHERE n.{MIGRATION_KEY} IS NOT NULL REMOVE n.{MIGRATION_KEY}")


def _chunks(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]
