"""
Swarm observability dashboard.

Tracks agent heartbeats, task metrics, and graph statistics in Redis.
All data is keyed under the ``navegador:obs:`` namespace.
"""

from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from navegador.graph.store import GraphStore

logger = logging.getLogger(__name__)

_AGENT_PREFIX = "navegador:obs:agent:"
_TASK_METRICS_KEY = "navegador:obs:tasks"
_GRAPH_META_KEY = "navegador:obs:graph"


class SwarmDashboard:
    """
    Observability dashboard for a navegador agent swarm.

    All state is persisted in Redis so any process in the cluster can read it.

    Args:
        redis_url: Redis connection URL.
        _redis_client: Optional pre-built Redis client (for testing).
    """

    def __init__(self, redis_url: str, _redis_client: Any = None) -> None:
        self._url = redis_url
        self._redis: Any = _redis_client

    # ── Internal ──────────────────────────────────────────────────────────────

    def _client(self) -> Any:
        if self._redis is None:
            try:
                import redis  # type: ignore[import]
            except ImportError as exc:
                raise ImportError("Install redis: pip install redis") from exc
            self._redis = redis.from_url(self._url)
        return self._redis

    def _agent_key(self, agent_id: str) -> str:
        return f"{_AGENT_PREFIX}{agent_id}"

    # ── Public API ────────────────────────────────────────────────────────────

    def register_agent(self, agent_id: str, metadata: dict | None = None) -> None:
        """
        Register / refresh the heartbeat for an agent.

        Stores the agent's metadata and last-seen timestamp in Redis with a
        90-second TTL so stale agents expire automatically.

        Args:
            agent_id: Unique agent identifier.
            metadata: Optional dict of extra info (e.g. hostname, role).
        """
        client = self._client()
        payload = {
            "agent_id": agent_id,
            "last_seen": time.time(),
            "state": "active",
            **(metadata or {}),
        }
        client.setex(self._agent_key(agent_id), 90, json.dumps(payload))
        logger.debug("Agent heartbeat: %s", agent_id)

    def agent_status(self) -> list[dict]:
        """
        Return status for all registered (non-expired) agents.

        Returns:
            List of agent dicts, each containing at minimum:
            ``agent_id``, ``last_seen``, ``state``.
        """
        client = self._client()
        pattern = f"{_AGENT_PREFIX}*"
        keys = client.keys(pattern)
        agents = []
        for key in keys:
            raw = client.get(key)
            if raw:
                if isinstance(raw, bytes):
                    raw = raw.decode()
                agents.append(json.loads(raw))
        return agents

    def task_metrics(self) -> dict:
        """
        Return aggregate task counters.

        Returns:
            Dict with keys: ``pending``, ``active``, ``completed``, ``failed``.
        """
        client = self._client()
        raw = client.get(_TASK_METRICS_KEY)
        if raw:
            if isinstance(raw, bytes):
                raw = raw.decode()
            return json.loads(raw)
        return {"pending": 0, "active": 0, "completed": 0, "failed": 0}

    def update_task_metrics(self, **kwargs: int) -> None:
        """
        Overwrite specific task metric counters.

        Example::

            dashboard.update_task_metrics(pending=5, active=2)
        """
        client = self._client()
        current = self.task_metrics()
        current.update(kwargs)
        client.set(_TASK_METRICS_KEY, json.dumps(current))

    def graph_metrics(self, store: "GraphStore") -> dict:
        """
        Return graph statistics from the live GraphStore.

        Args:
            store: GraphStore instance to query.

        Returns:
            Dict with keys: ``node_count``, ``edge_count``, ``last_modified``.
        """
        node_count = store.node_count()
        edge_count = store.edge_count()
        ts = time.time()

        # Persist a snapshot so the dashboard can show it without a live store
        client = self._client()
        payload = {
            "node_count": node_count,
            "edge_count": edge_count,
            "last_modified": ts,
        }
        client.set(_GRAPH_META_KEY, json.dumps(payload))
        return payload

    def to_json(self) -> str:
        """
        Return a full dashboard snapshot as a JSON string.

        Includes: agents, task_metrics, and the last-known graph_metrics.
        """
        client = self._client()
        raw_graph = client.get(_GRAPH_META_KEY)
        graph_meta: dict = {}
        if raw_graph:
            if isinstance(raw_graph, bytes):
                raw_graph = raw_graph.decode()
            graph_meta = json.loads(raw_graph)

        snapshot = {
            "timestamp": time.time(),
            "agents": self.agent_status(),
            "task_metrics": self.task_metrics(),
            "graph_metrics": graph_meta,
        }
        return json.dumps(snapshot, indent=2)
