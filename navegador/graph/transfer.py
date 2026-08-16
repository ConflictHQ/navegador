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
from collections import defaultdict
from typing import Any

from navegador.graph.store import GraphStore, paged_query

logger = logging.getLogger(__name__)

#: Temporary property holding the source graph's internal node id.
MIGRATION_KEY = "_nav_transfer_id"

#: Rows sent per Cypher round trip. Large enough to amortise latency, small
#: enough to stay well under FalkorDB's query and result-set ceilings.
DEFAULT_BATCH = 500

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

    nodes_written = _copy_nodes(source, dest, batch_size)
    edges_written = _copy_edges(source, dest, batch_size)
    _drop_migration_key(dest)

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


def _copy_nodes(source: GraphStore, dest: GraphStore, batch_size: int) -> int:
    """Recreate every source node in *dest*, stamped with its source id."""
    rows = paged_query(
        source,
        "MATCH (n) RETURN id(n) AS nid, labels(n) AS labels, properties(n) AS props ORDER BY id(n)",
    )

    # Group by label set: a label cannot be parameterised, so each distinct
    # label set needs its own CREATE statement.
    grouped: dict[tuple[str, ...], list[dict]] = defaultdict(list)
    for row in rows:
        node_id, raw_labels, props = row[0], row[1], row[2]
        labels = tuple(_labels_of(raw_labels))
        grouped[labels].append({"id": node_id, "props": props if isinstance(props, dict) else {}})

    written = 0
    for labels, items in grouped.items():
        for label in labels:
            _safe_identifier(label, "label")
        label_clause = "".join(f":{label}" for label in labels)
        # Unlabelled nodes are legal in FalkorDB and must still be copied.
        pattern = f"(n{label_clause})" if label_clause else "(n)"
        cypher = (
            f"UNWIND $rows AS row CREATE {pattern} SET n = row.props SET n.{MIGRATION_KEY} = row.id"
        )
        for chunk in _chunks(items, batch_size):
            dest.query(cypher, {"rows": chunk})
            written += len(chunk)

        for label in labels:
            _ensure_transfer_index(dest, label)

    return written


def _ensure_transfer_index(dest: GraphStore, label: str) -> None:
    """
    Index the temporary id property for *label*.

    Without an index, reconnecting edges degrades to a full scan per edge — the
    difference between seconds and hours on a real graph. FalkorDB has no
    ``IF NOT EXISTS`` for index creation and indexes survive a graph clear, so
    an already-present index is an expected, benign outcome.
    """
    try:
        dest.query(f"CREATE INDEX FOR (n:{label}) ON (n.{MIGRATION_KEY})")
    except Exception as e:  # noqa: BLE001 — only "already indexed" is tolerable
        if "already indexed" not in str(e).lower():
            raise


def _copy_edges(source: GraphStore, dest: GraphStore, batch_size: int) -> int:
    """Reconnect every source edge in *dest* using the stamped source ids."""
    rows = paged_query(
        source,
        "MATCH (a)-[r]->(b) "
        "RETURN id(a) AS src, labels(a) AS src_labels, type(r) AS type, "
        "properties(r) AS props, id(b) AS dst, labels(b) AS dst_labels "
        "ORDER BY id(r)",
    )

    # Group by (source label, type, destination label) so each statement can use
    # labelled, index-backed lookups on both endpoints.
    grouped: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for row in rows:
        src_labels = _labels_of(row[1])
        dst_labels = _labels_of(row[5])
        key = (
            src_labels[0] if src_labels else "",
            _decode(row[2]),
            dst_labels[0] if dst_labels else "",
        )
        grouped[key].append(
            {"src": row[0], "dst": row[4], "props": row[3] if isinstance(row[3], dict) else {}}
        )

    written = 0
    for (src_label, edge_type, dst_label), items in grouped.items():
        _safe_identifier(edge_type, "relationship type")
        src_clause = f":{_safe_identifier(src_label, 'label')}" if src_label else ""
        dst_clause = f":{_safe_identifier(dst_label, 'label')}" if dst_label else ""
        cypher = (
            f"UNWIND $rows AS row "
            f"MATCH (a{src_clause} {{{MIGRATION_KEY}: row.src}}), "
            f"(b{dst_clause} {{{MIGRATION_KEY}: row.dst}}) "
            f"CREATE (a)-[r:{edge_type}]->(b) SET r = row.props"
        )
        for chunk in _chunks(items, batch_size):
            dest.query(cypher, {"rows": chunk})
            written += len(chunk)

    return written


def _drop_migration_key(dest: GraphStore) -> None:
    """Remove the temporary id property so the copy is indistinguishable."""
    dest.query(f"MATCH (n) WHERE n.{MIGRATION_KEY} IS NOT NULL REMOVE n.{MIGRATION_KEY}")


def _chunks(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]
