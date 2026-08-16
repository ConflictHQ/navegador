"""
Text-based graph export and import for navegador.

Exports the full graph to a deterministic JSON Lines format (.jsonl) suitable
for committing to version control. Each line is a self-contained JSON object —
either a node or an edge.

Format (conflict-kg/v1 identity, one record per line)::

  {"kind": "node", "id": "Function::foo", "type": "Function",
   "name": "foo", "props": {...}}
  {"kind": "edge", "type": "CALLS", "source": "Function::foo",
   "target": "Function::bar", "props": {...}}

Node ids are content-derived (``type:path:name``, with a ``#n`` suffix on
collision) and edges reference them, so a round trip preserves every edge
exactly. This is the same identity scheme as the conflict-kg interchange
format, deliberately: two serializations of one canonical shape rather than two
formats that disagree about what a node is.

Legacy exports — which identified endpoints by ``(name, path)`` merge keys —
are still readable; see :func:`_import_legacy_edge`.
"""

import json
import logging
from pathlib import Path

from navegador.graph.interchange import collect_graph, merge_key
from navegador.graph.store import GraphStore

logger = logging.getLogger(__name__)


class ExportError(RuntimeError):
    """Raised when an import cannot reproduce the exported graph."""


def export_graph(store: GraphStore, output_path: str | Path) -> dict[str, int]:
    """
    Export the full graph to a JSONL file.

    Returns:
        Dict with counts: nodes, edges.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    nodes, edges = collect_graph(store)

    with output_path.open("w", encoding="utf-8") as f:
        for node in nodes:
            f.write(
                json.dumps(
                    {
                        "kind": "node",
                        "id": node["id"],
                        "type": node["type"],
                        "name": node["name"],
                        "props": node["props"],
                    },
                    sort_keys=True,
                )
                + "\n"
            )
        for edge in edges:
            f.write(
                json.dumps(
                    {
                        "kind": "edge",
                        "type": edge["type"],
                        "source": edge["source"],
                        "target": edge["target"],
                        "props": edge["props"],
                    },
                    sort_keys=True,
                )
                + "\n"
            )

    logger.info("Exported %d nodes, %d edges to %s", len(nodes), len(edges), output_path)
    return {"nodes": len(nodes), "edges": len(edges)}


def import_graph(store: GraphStore, input_path: str | Path, clear: bool = True) -> dict[str, int]:
    """
    Import a graph from a JSONL file.

    Args:
        store: Target GraphStore.
        input_path: Path to the JSONL file.
        clear: If True (default), wipe the graph before importing.

    Returns:
        Dict with counts of what was actually created — not of lines read.

    Raises:
        ExportError: when a fresh import creates fewer edges than the file
            describes. An import that silently drops relationships leaves a pile
            of disconnected nodes that answers every structural query with an
            empty result, which reads as a valid negative.
    """
    input_path = Path(input_path)
    if not input_path.exists():
        raise FileNotFoundError(f"Export file not found: {input_path}")

    node_records: list[dict] = []
    edge_records: list[dict] = []
    with input_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            if record.get("kind") == "node":
                node_records.append(record)
            elif record.get("kind") == "edge":
                edge_records.append(record)

    if clear:
        store.clear()

    # id -> (label, merge key) for endpoint resolution
    key_map: dict[str, tuple[str, dict]] = {}
    for record in node_records:
        label, props, node_id = _node_from_record(record)
        store.create_node(label, props)
        if node_id is not None:
            key_map[node_id] = (label, merge_key(label, props))

    created = 0
    skipped: list[dict] = []
    for record in edge_records:
        if _import_edge(store, record, key_map):
            created += 1
        else:
            skipped.append(record)

    for record in skipped[:5]:
        logger.warning("Skipped edge with unresolvable endpoint: %s", record)
    if len(skipped) > 5:
        logger.warning("… and %d more unresolvable edges", len(skipped) - 5)

    logger.info("Imported %d nodes, %d edges from %s", len(node_records), created, input_path)

    if clear and created != len(edge_records):
        raise ExportError(
            f"Import did not reproduce the export — refusing to report success.\n"
            f"  file describes: {len(node_records)} nodes, {len(edge_records)} edges\n"
            f"  created:        {store.node_count()} nodes, {created} edges\n"
            f"  {len(skipped)} edge(s) had endpoints that could not be resolved.\n"
            f"The graph has been written but is incomplete; structural queries "
            f"against it will return empty results rather than errors."
        )

    return {"nodes": len(node_records), "edges": created}


def _node_from_record(record: dict) -> tuple[str, dict, str | None]:
    """
    Read a node record in either the current or the legacy shape.

    Legacy records carry ``label`` and no ``id``; current ones carry ``type``,
    ``name`` and a content-derived ``id``.
    """
    if "label" in record and "type" not in record:
        props = dict(record.get("props") or {})
        return record["label"], props, None

    props = dict(record.get("props") or {})
    if record.get("name") is not None:
        props.setdefault("name", record["name"])
    return record["type"], props, record.get("id")


def _import_edge(store: GraphStore, record: dict, key_map: dict[str, tuple[str, dict]]) -> bool:
    """Create one edge, returning whether it was actually created."""
    source, target = record.get("source"), record.get("target")
    if isinstance(source, str) and isinstance(target, str):
        src, tgt = key_map.get(source), key_map.get(target)
        if src is None or tgt is None:
            return False
        return store.create_edge(
            src[0], src[1], record["type"], tgt[0], tgt[1], record.get("props") or None
        )

    return _import_legacy_edge(store, record)


def _import_legacy_edge(store: GraphStore, record: dict) -> bool:
    """
    Create an edge from a pre-id export record.

    Those records identify each endpoint by ``{label, name, path}``, where
    ``path`` was written from ``coalesce(file_path, path, '')``. The original
    importer matched the source on ``file_path`` and the target on ``path``,
    which meant any edge pointing at a code symbol matched nothing at all. Both
    endpoints are now keyed the way the node's own label is keyed.
    """
    from_info, to_info = record.get("from"), record.get("to")
    if not isinstance(from_info, dict) or not isinstance(to_info, dict):
        return False

    def endpoint(info: dict) -> tuple[str, dict]:
        label = info.get("label") or ""
        props = {"name": info.get("name", "")}
        if info.get("path"):
            # A path-keyed label stores it as `path`; everything else as `file_path`.
            if label in GraphStore._PATH_KEYED_LABELS:
                props["path"] = info["path"]
            else:
                props["file_path"] = info["path"]
        return label, merge_key(label, props)

    src_label, src_key = endpoint(from_info)
    tgt_label, tgt_key = endpoint(to_info)
    if not src_label or not tgt_label:
        return False

    return store.create_edge(
        src_label, src_key, record["type"], tgt_label, tgt_key, record.get("props") or None
    )
