"""
TaskQueue — work assignment for agent swarms via Redis.

Agents enqueue tasks (e.g. "ingest file X") and other agents dequeue and
claim them.  Tasks move through PENDING -> IN_PROGRESS -> DONE | FAILED.

Usage:
    queue = TaskQueue("redis://localhost:6379")

    task_id = queue.enqueue("ingest_file", {"path": "src/main.py"})

    task = queue.dequeue("agent-1")   # atomically claim next pending task
    if task:
        try:
            result = do_work(task)
            queue.complete(task.id, result)
        except Exception as e:
            queue.fail(task.id, str(e))

    info = queue.status(task_id)
    n = queue.pending_count()
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)

_QUEUE_KEY = "navegador:taskqueue:pending"          # Redis list (RPUSH/BLPOP)
_TASK_KEY_PREFIX = "navegador:task:"                # Hash per task
_INPROGRESS_KEY = "navegador:taskqueue:inprogress"  # Set of in-progress task IDs


class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    FAILED = "failed"


@dataclass
class Task:
    """A unit of work in the task queue."""

    id: str
    type: str
    payload: dict[str, Any]
    status: TaskStatus = TaskStatus.PENDING
    agent_id: str | None = None
    result: Any = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, str]:
        """Serialise to a flat string dict suitable for Redis HSET."""
        return {
            "id": self.id,
            "type": self.type,
            "payload": json.dumps(self.payload),
            "status": self.status.value,
            "agent_id": self.agent_id or "",
            "result": json.dumps(self.result) if self.result is not None else "",
            "error": self.error or "",
            "created_at": str(self.created_at),
            "updated_at": str(self.updated_at),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Task":
        # Redis hgetall returns bytes; decode if necessary.
        decoded: dict[str, Any] = {}
        for k, v in d.items():
            key = k.decode() if isinstance(k, bytes) else k
            val = v.decode() if isinstance(v, bytes) else v
            decoded[key] = val

        payload = json.loads(decoded.get("payload", "{}") or "{}")
        result_raw = decoded.get("result", "")
        result = json.loads(result_raw) if result_raw else None
        status_raw = decoded.get("status", TaskStatus.PENDING.value)
        status = TaskStatus(status_raw)

        return cls(
            id=decoded["id"],
            type=decoded["type"],
            payload=payload,
            status=status,
            agent_id=decoded.get("agent_id") or None,
            result=result,
            error=decoded.get("error") or None,
            created_at=float(decoded.get("created_at", 0)),
            updated_at=float(decoded.get("updated_at", 0)),
        )


def _task_key(task_id: str) -> str:
    return f"{_TASK_KEY_PREFIX}{task_id}"


class TaskQueue:
    """
    Redis-backed task queue for coordinating work across agent swarms.

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

    # ── Public API ────────────────────────────────────────────────────────────

    def enqueue(self, task_type: str, payload: dict[str, Any]) -> str:
        """
        Add a new task to the queue.

        Returns
        -------
        str
            The newly created task ID.
        """
        task_id = str(uuid.uuid4())
        task = Task(id=task_id, type=task_type, payload=payload)
        pipe = self._redis.pipeline()
        pipe.hset(_task_key(task_id), mapping=task.to_dict())
        pipe.rpush(_QUEUE_KEY, task_id)
        pipe.execute()
        logger.debug("Enqueued task %s (type=%s)", task_id, task_type)
        return task_id

    def dequeue(self, agent_id: str) -> Task | None:
        """
        Atomically claim the next pending task for *agent_id*.

        Returns ``None`` when the queue is empty.
        """
        task_id_raw = self._redis.lpop(_QUEUE_KEY)
        if task_id_raw is None:
            return None

        task_id = task_id_raw.decode() if isinstance(task_id_raw, bytes) else task_id_raw
        now = time.time()
        pipe = self._redis.pipeline()
        pipe.hset(_task_key(task_id), mapping={
            "status": TaskStatus.IN_PROGRESS.value,
            "agent_id": agent_id,
            "updated_at": now,
        })
        pipe.sadd(_INPROGRESS_KEY, task_id)
        pipe.execute()

        raw = self._redis.hgetall(_task_key(task_id))
        task = Task.from_dict(raw)
        logger.debug("Agent %s claimed task %s", agent_id, task_id)
        return task

    def complete(self, task_id: str, result: Any = None) -> None:
        """Mark a task as successfully completed."""
        result_encoded = json.dumps(result) if result is not None else ""
        pipe = self._redis.pipeline()
        pipe.hset(_task_key(task_id), mapping={
            "status": TaskStatus.DONE.value,
            "result": result_encoded,
            "updated_at": time.time(),
        })
        pipe.srem(_INPROGRESS_KEY, task_id)
        pipe.execute()
        logger.debug("Task %s completed", task_id)

    def fail(self, task_id: str, error: str) -> None:
        """Mark a task as failed with an error message."""
        pipe = self._redis.pipeline()
        pipe.hset(_task_key(task_id), mapping={
            "status": TaskStatus.FAILED.value,
            "error": error,
            "updated_at": time.time(),
        })
        pipe.srem(_INPROGRESS_KEY, task_id)
        pipe.execute()
        logger.debug("Task %s failed: %s", task_id, error)

    def status(self, task_id: str) -> dict[str, Any]:
        """
        Return a status dict for a task.

        Raises ``KeyError`` if the task does not exist.
        """
        raw = self._redis.hgetall(_task_key(task_id))
        if not raw:
            raise KeyError(f"Task not found: {task_id}")
        task = Task.from_dict(raw)
        return {
            "id": task.id,
            "type": task.type,
            "status": task.status.value,
            "agent_id": task.agent_id,
            "result": task.result,
            "error": task.error,
            "created_at": task.created_at,
            "updated_at": task.updated_at,
        }

    def pending_count(self) -> int:
        """Return the number of tasks currently waiting in the queue."""
        return self._redis.llen(_QUEUE_KEY)
