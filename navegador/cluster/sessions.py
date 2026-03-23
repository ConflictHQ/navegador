"""
SessionManager — branch-isolated session namespacing for agent swarms.

Each session is tied to a git branch and owns a uniquely named FalkorDB graph
so that multiple branches can be ingested and queried concurrently without
graph data bleeding across branches.

Usage:
    mgr = SessionManager("redis://localhost:6379")

    session_id = mgr.create_session("feature/my-branch", "agent-1")
    info = mgr.get_session(session_id)
    graph_name = mgr.session_graph_name(session_id)  # e.g. "navegador:sess:abc123"

    for s in mgr.list_sessions():
        print(s["session_id"], s["branch"])

    mgr.end_session(session_id)
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from typing import Any

logger = logging.getLogger(__name__)

_SESSIONS_KEY = "navegador:sessions"          # Redis hash: session_id -> JSON
_SESSION_INDEX_KEY = "navegador:sessions:ids"  # Redis set: all session IDs


def _make_session_id() -> str:
    return str(uuid.uuid4())


def _graph_name_from_session_id(session_id: str) -> str:
    """Return a short, deterministic graph name for a session ID."""
    short = hashlib.sha1(session_id.encode()).hexdigest()[:12]
    return f"navegador:sess:{short}"


class SessionManager:
    """
    Manage branch-isolated sessions for agent swarms.

    Each session has a unique graph namespace so agents working on different
    branches do not share graph state.

    Parameters
    ----------
    redis_url:
        URL of the Redis server.
    redis_client:
        Optional pre-built Redis client (for testing / DI).
    """

    def __init__(self, redis_url: str, *, redis_client: Any = None) -> None:
        self.redis_url = redis_url
        self._redis = redis_client or self._connect_redis(redis_url)

    # ── Internal ──────────────────────────────────────────────────────────────

    @staticmethod
    def _connect_redis(url: str) -> Any:
        try:
            import redis  # type: ignore[import]
        except ImportError as exc:
            raise ImportError("Install redis: pip install redis") from exc
        return redis.from_url(url)

    def _save_session(self, session_id: str, data: dict[str, Any]) -> None:
        pipe = self._redis.pipeline()
        pipe.hset(_SESSIONS_KEY, session_id, json.dumps(data))
        pipe.sadd(_SESSION_INDEX_KEY, session_id)
        pipe.execute()

    def _load_session(self, session_id: str) -> dict[str, Any] | None:
        raw = self._redis.hget(_SESSIONS_KEY, session_id)
        if raw is None:
            return None
        text = raw.decode() if isinstance(raw, bytes) else raw
        return json.loads(text)

    # ── Public API ────────────────────────────────────────────────────────────

    def create_session(self, branch: str, agent_id: str) -> str:
        """
        Create a new isolated session for *agent_id* working on *branch*.

        Returns
        -------
        str
            The new session ID.
        """
        session_id = _make_session_id()
        data: dict[str, Any] = {
            "session_id": session_id,
            "branch": branch,
            "agent_id": agent_id,
            "graph_name": _graph_name_from_session_id(session_id),
            "created_at": time.time(),
            "status": "active",
        }
        self._save_session(session_id, data)
        logger.info("Created session %s for branch %s / agent %s", session_id, branch, agent_id)
        return session_id

    def get_session(self, session_id: str) -> dict[str, Any]:
        """
        Retrieve session metadata.

        Raises ``KeyError`` if the session does not exist.
        """
        data = self._load_session(session_id)
        if data is None:
            raise KeyError(f"Session not found: {session_id}")
        return data

    def list_sessions(self) -> list[dict[str, Any]]:
        """Return all sessions (active and ended)."""
        raw_ids = self._redis.smembers(_SESSION_INDEX_KEY)
        sessions = []
        for raw_id in raw_ids:
            sid = raw_id.decode() if isinstance(raw_id, bytes) else raw_id
            data = self._load_session(sid)
            if data is not None:
                sessions.append(data)
        sessions.sort(key=lambda s: s.get("created_at", 0))
        return sessions

    def end_session(self, session_id: str) -> None:
        """Mark a session as ended (does not delete graph data)."""
        data = self.get_session(session_id)  # raises KeyError if missing
        data["status"] = "ended"
        data["ended_at"] = time.time()
        self._save_session(session_id, data)
        logger.info("Session %s ended", session_id)

    def session_graph_name(self, session_id: str) -> str:
        """
        Return the namespaced FalkorDB graph name for a session.

        The name is deterministic and safe to use as a FalkorDB graph key.
        """
        data = self.get_session(session_id)
        return data["graph_name"]
