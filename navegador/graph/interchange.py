"""
conflict-kg/v1 — canonical KG interchange format shared across Conflict tools.

One contract, two encodings (same field names):

JSON (small/medium graphs):
  {
    "format": "conflict-kg/v1",
    "nodes": [{"id": "str-unique", "name": "display", "type": "NodeType", "props": {}}],
    "edges": [{"source": "node-id", "target": "node-id", "type": "REL", "props": {}}]
  }

SQLite (large graphs):
  nodes(id TEXT PRIMARY KEY, name TEXT, type TEXT, props JSON)
  edges(source TEXT, target TEXT, type TEXT, props JSON)
  with indexes on edges(source) and edges(target)

Node ids are content-derived (type + path + name) so they are stable across
re-exports; edges reference node ids, never property dicts.
"""

import json
import logging
import sqlite3
from pathlib import Path

from navegador.graph.store import GraphStore, paged_query

logger = logging.getLogger(__name__)

FORMAT = "conflict-kg/v1"

_SQLITE_EXTENSIONS = {".db", ".sqlite", ".sqlite3"}

_SQLITE_SCHEMA = [
    "CREATE TABLE nodes (id TEXT PRIMARY KEY, name TEXT, type TEXT, props JSON)",
    "CREATE TABLE edges (source TEXT, target TEXT, type TEXT, props JSON)",
    "CREATE INDEX idx_edges_source ON edges(source)",
    "CREATE INDEX idx_edges_target ON edges(target)",
]


def collect_graph(store: GraphStore) -> tuple[list[dict], list[dict]]:
    """
    Read the full graph into canonical conflict-kg/v1 node and edge dicts.

    Returns:
        (nodes, edges) — deterministically ordered, edges referencing node ids.
    """
    rows = paged_query(
        store, "MATCH (n) RETURN id(n) AS iid, labels(n)[0] AS label, properties(n) ORDER BY iid"
    )
    raw_nodes = []
    for row in rows:
        iid, label = row[0], row[1] or "default"
        props = dict(row[2]) if isinstance(row[2], dict) else {}
        name = str(props.pop("name", "") or "")
        path = str(props.get("file_path") or props.get("path") or "")
        raw_nodes.append((label, path, name, iid, props))

    # Deterministic order, then content-derived ids with collision suffixes
    raw_nodes.sort(key=lambda n: (n[0], n[1], n[2], n[3]))
    nodes = []
    id_map: dict[int, str] = {}
    seen: dict[str, int] = {}
    for label, path, name, iid, props in raw_nodes:
        node_id = f"{label}:{path}:{name}"
        count = seen.get(node_id, 0)
        seen[node_id] = count + 1
        if count:
            node_id = f"{node_id}#{count}"
        id_map[iid] = node_id
        nodes.append({"id": node_id, "name": name, "type": label, "props": props})

    rows = paged_query(
        store,
        "MATCH (a)-[r]->(b) RETURN id(a) AS src, id(b) AS tgt, type(r) AS type, properties(r) "
        "ORDER BY id(r)",
    )
    edges = []
    for row in rows:
        src, tgt = id_map.get(row[0]), id_map.get(row[1])
        if src is None or tgt is None:
            continue
        props = dict(row[3]) if isinstance(row[3], dict) else {}
        edges.append({"source": src, "target": tgt, "type": row[2] or "", "props": props})
    edges.sort(key=lambda e: (e["type"], e["source"], e["target"]))

    return nodes, edges


def export_conflict_kg(store: GraphStore, output_path: str | Path) -> dict[str, int | str]:
    """
    Export the graph in conflict-kg/v1 format.

    Encoding is chosen by extension: .db/.sqlite/.sqlite3 → SQLite,
    anything else → canonical JSON.

    Returns:
        Dict with counts: nodes, edges, and the encoding used.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    nodes, edges = collect_graph(store)

    if output_path.suffix.lower() in _SQLITE_EXTENSIONS:
        encoding = "sqlite"
        _write_sqlite(output_path, nodes, edges)
    else:
        encoding = "json"
        payload = {"format": FORMAT, "nodes": nodes, "edges": edges}
        output_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    logger.info(
        "Exported %d nodes, %d edges to %s (%s, %s)",
        len(nodes),
        len(edges),
        output_path,
        FORMAT,
        encoding,
    )
    return {"nodes": len(nodes), "edges": len(edges), "encoding": encoding}


def import_conflict_kg(
    store: GraphStore, input_path: str | Path, clear: bool = True
) -> dict[str, int]:
    """
    Import a conflict-kg/v1 graph (JSON or SQLite encoding) into the store.

    Args:
        store: Target GraphStore.
        input_path: Path to a conflict-kg/v1 .json or .db file.
        clear: If True (default), wipe the graph before importing.

    Returns:
        Dict with counts: nodes, edges.
    """
    input_path = Path(input_path)
    if not input_path.exists():
        raise FileNotFoundError(f"Interchange file not found: {input_path}")

    if is_sqlite_file(input_path):
        nodes, edges = _read_sqlite(input_path)
    else:
        payload = json.loads(input_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("format") != FORMAT:
            raise ValueError(f"Not a {FORMAT} file: {input_path}")
        nodes, edges = payload.get("nodes", []), payload.get("edges", [])

    if clear:
        store.clear()

    key_map: dict[str, tuple[str, dict]] = {}
    for node in nodes:
        label = node["type"]
        props = {"name": node.get("name", ""), **node.get("props", {})}
        store.create_node(label, props)
        key_map[node["id"]] = (label, _merge_key(label, props))

    edge_count = 0
    for edge in edges:
        src, tgt = key_map.get(edge["source"]), key_map.get(edge["target"])
        if src is None or tgt is None:
            logger.warning("Skipping edge with unknown endpoint: %s", edge)
            continue
        store.create_edge(src[0], src[1], edge["type"], tgt[0], tgt[1], edge.get("props") or None)
        edge_count += 1

    logger.info("Imported %d nodes, %d edges from %s", len(nodes), edge_count, input_path)
    return {"nodes": len(nodes), "edges": edge_count}


def is_sqlite_file(path: str | Path) -> bool:
    """True if the file starts with the SQLite magic header."""
    with Path(path).open("rb") as f:
        return f.read(16) == b"SQLite format 3\x00"


def is_conflict_kg_json(path: str | Path) -> bool:
    """True if the file is a conflict-kg/v1 JSON document."""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return False
    return isinstance(payload, dict) and payload.get("format") == FORMAT


def _merge_key(label: str, props: dict) -> dict:
    """Merge-key props for a node, mirroring GraphStore.create_node key selection."""
    if label in GraphStore._PATH_KEYED_LABELS:
        return {"path": props.get("path", "")}
    if props.get("memory_type", "") and props.get("repo", ""):
        return {"name": props.get("name", ""), "repo": props["repo"]}
    if props.get("file_path", ""):
        return {"name": props.get("name", ""), "file_path": props["file_path"]}
    return {"name": props.get("name", "")}


def _write_sqlite(path: Path, nodes: list[dict], edges: list[dict]) -> None:
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(path)
    try:
        for stmt in _SQLITE_SCHEMA:
            conn.execute(stmt)
        conn.executemany(
            "INSERT INTO nodes (id, name, type, props) VALUES (?, ?, ?, ?)",
            [
                (n["id"], n["name"], n["type"], json.dumps(n["props"], sort_keys=True))
                for n in nodes
            ],
        )
        conn.executemany(
            "INSERT INTO edges (source, target, type, props) VALUES (?, ?, ?, ?)",
            [
                (e["source"], e["target"], e["type"], json.dumps(e["props"], sort_keys=True))
                for e in edges
            ],
        )
        conn.commit()
    finally:
        conn.close()


def _read_sqlite(path: Path) -> tuple[list[dict], list[dict]]:
    conn = sqlite3.connect(path)
    try:
        nodes = [
            {
                "id": r[0],
                "name": r[1] or "",
                "type": r[2] or "default",
                "props": json.loads(r[3] or "{}"),
            }
            for r in conn.execute("SELECT id, name, type, props FROM nodes")
        ]
        edges = [
            {"source": r[0], "target": r[1], "type": r[2] or "", "props": json.loads(r[3] or "{}")}
            for r in conn.execute("SELECT source, target, type, props FROM edges")
        ]
    finally:
        conn.close()
    return nodes, edges
