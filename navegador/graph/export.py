"""
Text-based graph export and import for navegador.

Exports the full graph to a deterministic JSON Lines format (.jsonl)
suitable for committing to version control. Each line is a self-contained
JSON object — either a node or an edge.

Format:
  {"kind": "node", "label": "Function", "props": {"name": "foo", ...}}
  {"kind": "edge", "type": "CALLS", "from": {...}, "to": {...}}
"""

import json
import logging
from pathlib import Path

from navegador.graph.store import GraphStore

logger = logging.getLogger(__name__)


def export_graph(store: GraphStore, output_path: str | Path) -> dict[str, int]:
    """
    Export the full graph to a JSONL file.

    Returns:
        Dict with counts: nodes, edges.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    nodes = _export_nodes(store)
    edges = _export_edges(store)

    # Sort for deterministic output
    nodes.sort(key=lambda n: (n["label"], json.dumps(n["props"], sort_keys=True)))
    edges.sort(
        key=lambda e: (
            e["type"],
            json.dumps(e["from"], sort_keys=True),
            json.dumps(e["to"], sort_keys=True),
        )
    )

    with output_path.open("w", encoding="utf-8") as f:
        for node in nodes:
            f.write(json.dumps(node, sort_keys=True) + "\n")
        for edge in edges:
            f.write(json.dumps(edge, sort_keys=True) + "\n")

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
        Dict with counts: nodes, edges.
    """
    input_path = Path(input_path)
    if not input_path.exists():
        raise FileNotFoundError(f"Export file not found: {input_path}")

    if clear:
        store.clear()

    node_count = 0
    edge_count = 0

    with input_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)

            if record["kind"] == "node":
                _import_node(store, record)
                node_count += 1
            elif record["kind"] == "edge":
                _import_edge(store, record)
                edge_count += 1

    logger.info("Imported %d nodes, %d edges from %s", node_count, edge_count, input_path)
    return {"nodes": node_count, "edges": edge_count}


def _export_nodes(store: GraphStore) -> list[dict]:
    """Export all nodes with their labels and properties."""
    result = store.query("MATCH (n) RETURN labels(n)[0] AS label, properties(n) AS props")
    nodes = []
    for row in result.result_set or []:
        label = row[0]
        props = row[1] if isinstance(row[1], dict) else {}
        nodes.append({"kind": "node", "label": label, "props": props})
    return nodes


def _export_edges(store: GraphStore) -> list[dict]:
    """Export all edges with type and endpoint identifiers."""
    result = store.query(
        "MATCH (a)-[r]->(b) "
        "RETURN type(r) AS type, labels(a)[0] AS from_label, "
        "a.name AS from_name, coalesce(a.file_path, a.path, '') AS from_path, "
        "labels(b)[0] AS to_label, b.name AS to_name, "
        "coalesce(b.file_path, b.path, '') AS to_path"
    )
    edges = []
    for row in result.result_set or []:
        edges.append(
            {
                "kind": "edge",
                "type": row[0],
                "from": {"label": row[1], "name": row[2], "path": row[3]},
                "to": {"label": row[4], "name": row[5], "path": row[6]},
            }
        )
    return edges


def _import_node(store: GraphStore, record: dict) -> None:
    """Create a node from an export record."""
    label = record["label"]
    props = record["props"]
    # Ensure required merge keys exist
    if "name" not in props:
        props["name"] = ""
    if "file_path" not in props and "path" not in props:
        props["file_path"] = ""

    prop_str = ", ".join(f"n.{k} = ${k}" for k in props)
    # Use name + file_path or path for merge key
    if "file_path" in props:
        cypher = f"MERGE (n:{label} {{name: $name, file_path: $file_path}}) SET {prop_str}"
    else:
        cypher = f"MERGE (n:{label} {{name: $name, path: $path}}) SET {prop_str}"
    store.query(cypher, props)


def _import_edge(store: GraphStore, record: dict) -> None:
    """Create an edge from an export record."""
    edge_type = record["type"]
    from_info = record["from"]
    to_info = record["to"]

    from_key = "name: $from_name"
    to_key = "name: $to_name"

    params = {
        "from_name": from_info["name"],
        "to_name": to_info["name"],
    }

    if from_info.get("path"):
        from_key += ", file_path: $from_path"
        params["from_path"] = from_info["path"]

    if to_info.get("path"):
        to_key += ", path: $to_path"
        params["to_path"] = to_info["path"]

    cypher = (
        f"MATCH (a:{from_info['label']} {{{from_key}}}), "
        f"(b:{to_info['label']} {{{to_key}}}) "
        f"MERGE (a)-[r:{edge_type}]->(b)"
    )
    store.query(cypher, params)
