# Copyright CONFLICT LLC 2026 (weareconflict.com)
"""
ExplorerServer — lightweight HTTP server for graph visualisation.

Runs in a daemon thread so it does not block the caller.  Uses only
Python's built-in http.server and json modules (no Flask/FastAPI).

Routes
------
GET /               — self-contained HTML visualisation page
GET /api/graph      — full graph as {nodes: [...], edges: [...]}
GET /api/search?q=  — search nodes by name (case-insensitive substring)
GET /api/node/<name>— node details + immediate neighbours
GET /api/stats      — {nodes: N, edges: M, node_types: {...}, edge_types: {...}}
"""

from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, unquote, urlparse

from .templates import HTML_TEMPLATE

if TYPE_CHECKING:
    from navegador.graph.store import GraphStore

logger = logging.getLogger(__name__)

# ── Cypher helpers ─────────────────────────────────────────────────────────


def _query(store: "GraphStore", cypher: str, params: dict[str, Any] | None = None) -> list:
    """Run a Cypher query and return the result_set list (or [])."""
    try:
        result = store.query(cypher, params or {})
        return result.result_set or []
    except Exception as exc:
        logger.warning("Graph query failed: %s", exc)
        return []


def _get_all_nodes(store: "GraphStore") -> list[dict]:
    rows = _query(
        store,
        "MATCH (n) RETURN id(n) AS id, labels(n)[0] AS label, "
        "n.name AS name, properties(n) AS props",
    )
    result = []
    for row in rows:
        nid, label, name, props = row[0], row[1], row[2], row[3]
        node_props = dict(props) if isinstance(props, dict) else {}
        result.append(
            {
                "id": str(nid),
                "label": label or "default",
                "name": name or str(nid),
                "props": node_props,
            }
        )
    return result


def _get_all_edges(store: "GraphStore") -> list[dict]:
    rows = _query(
        store,
        "MATCH (a)-[r]->(b) RETURN id(a) AS src, id(b) AS tgt, type(r) AS rel",
    )
    result = []
    for row in rows:
        src, tgt, rel = row[0], row[1], row[2]
        result.append({"source": str(src), "target": str(tgt), "type": rel or ""})
    return result


def _search_nodes(store: "GraphStore", query: str, limit: int = 50) -> list[dict]:
    q = query.lower()
    rows = _query(
        store,
        "MATCH (n) WHERE toLower(n.name) CONTAINS $q "
        "RETURN labels(n)[0] AS label, n.name AS name, "
        "coalesce(n.file_path, '') AS file_path, "
        "coalesce(n.domain, '') AS domain "
        "LIMIT $limit",
        {"q": q, "limit": limit},
    )
    result = []
    for row in rows:
        result.append(
            {
                "label": row[0] or "",
                "name": row[1] or "",
                "file_path": row[2] or "",
                "domain": row[3] or "",
            }
        )
    return result


def _get_node_detail(store: "GraphStore", name: str) -> dict:
    # Node properties
    rows = _query(
        store,
        "MATCH (n) WHERE n.name = $name "
        "RETURN labels(n)[0] AS label, properties(n) AS props "
        "LIMIT 1",
        {"name": name},
    )
    if not rows:
        return {"name": name, "label": "", "props": {}, "neighbors": []}

    label = rows[0][0] or ""
    props = dict(rows[0][1]) if isinstance(rows[0][1], dict) else {}

    # Outbound neighbours
    out_rows = _query(
        store,
        "MATCH (n)-[r]->(nb) WHERE n.name = $name "
        "RETURN labels(nb)[0] AS nb_label, nb.name AS nb_name, type(r) AS rel "
        "LIMIT 100",
        {"name": name},
    )
    # Inbound neighbours
    in_rows = _query(
        store,
        "MATCH (nb)-[r]->(n) WHERE n.name = $name "
        "RETURN labels(nb)[0] AS nb_label, nb.name AS nb_name, type(r) AS rel "
        "LIMIT 100",
        {"name": name},
    )

    seen: set[str] = set()
    neighbors = []
    for row in list(out_rows) + list(in_rows):
        nb_label, nb_name, rel = row[0] or "", row[1] or "", row[2] or ""
        key = f"{nb_name}|{rel}"
        if key not in seen:
            seen.add(key)
            neighbors.append({"label": nb_label, "name": nb_name, "rel": rel})

    return {"name": name, "label": label, "props": props, "neighbors": neighbors}


def _get_stats(store: "GraphStore") -> dict:
    node_count = store.node_count()
    edge_count = store.edge_count()

    node_type_rows = _query(
        store, "MATCH (n) RETURN labels(n)[0] AS type, count(n) AS c ORDER BY c DESC"
    )
    edge_type_rows = _query(
        store, "MATCH ()-[r]->() RETURN type(r) AS type, count(r) AS c ORDER BY c DESC"
    )

    return {
        "nodes": node_count,
        "edges": edge_count,
        "node_types": {r[0]: r[1] for r in node_type_rows if r[0]},
        "edge_types": {r[0]: r[1] for r in edge_type_rows if r[0]},
    }


# ── Request handler ────────────────────────────────────────────────────────


def _make_handler(store: "GraphStore"):
    """Return a BaseHTTPRequestHandler subclass bound to *store*."""

    class _Handler(BaseHTTPRequestHandler):
        _store = store

        # silence default access log to keep CLI output clean
        def log_message(self, fmt, *args):
            logger.debug(fmt, *args)

        def _send_json(self, data: Any, status: int = 200) -> None:
            body = json.dumps(data, default=str).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        def _send_html(self, html: str) -> None:
            body = html.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            qs = parse_qs(parsed.query)

            # ── Root — HTML page
            if path == "/":
                self._send_html(HTML_TEMPLATE)

            # ── Full graph
            elif path == "/api/graph":
                nodes = _get_all_nodes(self._store)
                edges = _get_all_edges(self._store)
                self._send_json({"nodes": nodes, "edges": edges})

            # ── Search
            elif path == "/api/search":
                q = qs.get("q", [""])[0]
                results = _search_nodes(self._store, q) if q else []
                self._send_json({"nodes": results})

            # ── Node detail — /api/node/<name>
            elif path.startswith("/api/node/"):
                raw_name = path[len("/api/node/") :]
                name = unquote(raw_name)
                detail = _get_node_detail(self._store, name)
                self._send_json(detail)

            # ── Stats
            elif path == "/api/stats":
                self._send_json(_get_stats(self._store))

            else:
                self._send_json({"error": "not found"}, 404)

    return _Handler


# ── ExplorerServer ─────────────────────────────────────────────────────────


class ExplorerServer:
    """
    Lightweight HTTP server that serves the Navegador graph explorer UI.

    Args:
        store:  A ``GraphStore`` instance (SQLite or Redis-backed).
        host:   Bind address (default ``127.0.0.1``).
        port:   TCP port (default ``8080``).

    Example::

        server = ExplorerServer(store, port=8080)
        server.start()
        # … do other work or sleep …
        server.stop()
    """

    def __init__(self, store: "GraphStore", host: str = "127.0.0.1", port: int = 8080) -> None:
        self.store = store
        self.host = host
        self.port = port
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def start(self) -> None:
        """Start the HTTP server in a background daemon thread."""
        if self._server is not None:
            raise RuntimeError("ExplorerServer is already running")

        handler_class = _make_handler(self.store)
        self._server = HTTPServer((self.host, self.port), handler_class)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="navegador-explorer",
            daemon=True,
        )
        self._thread.start()
        logger.info("ExplorerServer started at %s", self.url)

    def stop(self) -> None:
        """Shut down the HTTP server and wait for the thread to finish."""
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._server = None
        self._thread = None
        logger.info("ExplorerServer stopped")

    def __enter__(self) -> "ExplorerServer":
        self.start()
        return self

    def __exit__(self, *_) -> None:
        self.stop()
