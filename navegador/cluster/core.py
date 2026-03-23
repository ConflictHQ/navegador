"""
ClusterManager — shared Redis graph with local SQLite snapshot workflow.

Supports agent swarms sharing a central FalkorDB graph over Redis while
allowing individual agents to maintain a local SQLite snapshot for offline
or low-latency operation.

Usage:
    manager = ClusterManager("redis://localhost:6379", local_db_path=".navegador/graph.db")
    manager.snapshot_to_local()   # pull Redis -> SQLite
    manager.push_to_shared()      # push SQLite -> Redis
    info = manager.status()       # {"local_version": ..., "shared_version": ..., "in_sync": ...}
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_VERSION_KEY = "navegador:graph:version"
_META_KEY = "navegador:graph:meta"
_SNAPSHOT_KEY = "navegador:graph:snapshot"


class ClusterManager:
    """
    Coordinates graph state between a shared Redis FalkorDB instance and a
    local SQLite snapshot.

    Parameters
    ----------
    redis_url:
        URL of the Redis server hosting the shared FalkorDB graph.
    local_db_path:
        Path to the local SQLite (falkordblite) database file.
        Defaults to ``.navegador/graph.db``.
    redis_client:
        Optional pre-built Redis client (used for testing / dependency injection).
    """

    def __init__(
        self,
        redis_url: str,
        local_db_path: str | Path | None = None,
        *,
        redis_client: Any = None,
    ) -> None:
        self.redis_url = redis_url
        self.local_db_path = Path(local_db_path) if local_db_path else Path(".navegador/graph.db")
        self._redis = redis_client or self._connect_redis(redis_url)

    # ── Internal helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _connect_redis(url: str) -> Any:
        try:
            import redis  # type: ignore[import]
        except ImportError as exc:
            raise ImportError("Install redis: pip install redis") from exc
        return redis.from_url(url)

    def _redis_version(self) -> int:
        raw = self._redis.get(_VERSION_KEY)
        return int(raw) if raw is not None else 0

    def _local_version(self) -> int:
        meta_path = self.local_db_path.parent / "cluster_meta.json"
        if not meta_path.exists():
            return 0
        try:
            data = json.loads(meta_path.read_text())
            return int(data.get("version", 0))
        except (json.JSONDecodeError, OSError):
            return 0

    def _set_local_version(self, version: int) -> None:
        meta_path = self.local_db_path.parent / "cluster_meta.json"
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        existing: dict[str, Any] = {}
        if meta_path.exists():
            try:
                existing = json.loads(meta_path.read_text())
            except (json.JSONDecodeError, OSError):
                pass
        existing["version"] = version
        existing["updated_at"] = time.time()
        meta_path.write_text(json.dumps(existing, indent=2))

    def _export_local_graph(self) -> dict[str, Any]:
        """Read all nodes and edges from the local SQLite graph and return as dict."""
        from navegador.graph.store import GraphStore

        store = GraphStore.sqlite(self.local_db_path)
        nodes_result = store.query("MATCH (n) RETURN n")
        edges_result = store.query("MATCH (a)-[r]->(b) RETURN a, type(r), r, b")

        nodes = []
        if nodes_result.result_set:
            for row in nodes_result.result_set:
                node = row[0]
                nodes.append({"labels": list(node.labels), "properties": dict(node.properties)})

        edges = []
        if edges_result.result_set:
            for row in edges_result.result_set:
                src, rel_type, rel, dst = row
                edges.append({
                    "src_labels": list(src.labels),
                    "src_props": dict(src.properties),
                    "rel_type": rel_type,
                    "rel_props": dict(rel.properties) if rel.properties else {},
                    "dst_labels": list(dst.labels),
                    "dst_props": dict(dst.properties),
                })

        return {"nodes": nodes, "edges": edges}

    def _import_to_local_graph(self, data: dict[str, Any]) -> None:
        """Write snapshot data into the local SQLite graph."""
        from navegador.graph.store import GraphStore

        store = GraphStore.sqlite(self.local_db_path)
        store.clear()

        for node in data.get("nodes", []):
            label = node["labels"][0] if node["labels"] else "Node"
            props = node["properties"]
            store.create_node(label, props)

        for edge in data.get("edges", []):
            src_label = edge["src_labels"][0] if edge["src_labels"] else "Node"
            dst_label = edge["dst_labels"][0] if edge["dst_labels"] else "Node"
            rel_type = edge["rel_type"]
            src_props = edge["src_props"]
            dst_props = edge["dst_props"]
            rel_props = edge.get("rel_props") or None

            src_key = {k: v for k, v in src_props.items() if k in ("name", "file_path")}
            dst_key = {k: v for k, v in dst_props.items() if k in ("name", "file_path")}

            if src_key and dst_key:
                store.create_edge(src_label, src_key, rel_type, dst_label, dst_key, rel_props)

    # ── Public API ────────────────────────────────────────────────────────────

    def snapshot_to_local(self) -> None:
        """Pull the shared Redis graph down into the local SQLite snapshot."""
        raw = self._redis.get(_SNAPSHOT_KEY)
        if raw is None:
            logger.warning("No shared snapshot found in Redis; local graph unchanged.")
            return

        data = json.loads(raw)
        self._import_to_local_graph(data)
        shared_ver = self._redis_version()
        self._set_local_version(shared_ver)
        logger.info("Snapshot pulled from Redis (version %d) to %s", shared_ver, self.local_db_path)

    def push_to_shared(self) -> None:
        """Push the local SQLite graph up to the shared Redis instance."""
        data = self._export_local_graph()
        serialized = json.dumps(data)
        pipe = self._redis.pipeline()
        pipe.set(_SNAPSHOT_KEY, serialized)
        new_version = self._redis_version() + 1
        pipe.set(_VERSION_KEY, new_version)
        pipe.hset(_META_KEY, mapping={
            "last_push": time.time(),
            "node_count": len(data["nodes"]),
            "edge_count": len(data["edges"]),
        })
        pipe.execute()
        self._set_local_version(new_version)
        logger.info(
            "Pushed local graph to Redis (version %d): %d nodes, %d edges",
            new_version,
            len(data["nodes"]),
            len(data["edges"]),
        )

    def sync(self) -> None:
        """
        Bidirectional sync.

        Strategy: if the shared version is newer, pull it down first; if
        local is ahead (or equal), push local up.  This is a last-write-wins
        merge — suitable for most single-writer swarm topologies.
        """
        local_ver = self._local_version()
        shared_ver = self._redis_version()

        if shared_ver > local_ver:
            logger.info("Shared graph is newer (%d > %d); pulling.", shared_ver, local_ver)
            self.snapshot_to_local()
        else:
            logger.info("Local graph is current or ahead (%d >= %d); pushing.", local_ver, shared_ver)
            self.push_to_shared()

    def status(self) -> dict[str, Any]:
        """
        Return a dict describing the sync state.

        Keys: ``local_version``, ``shared_version``, ``in_sync``.
        """
        local_ver = self._local_version()
        shared_ver = self._redis_version()
        return {
            "local_version": local_ver,
            "shared_version": shared_ver,
            "in_sync": local_ver == shared_ver,
        }
