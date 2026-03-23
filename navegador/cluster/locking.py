"""
Distributed locking for critical code sections.

Uses Redis SETNX with expiry for lock implementation.  Each lock is stored
as a Redis key with a TTL; SETNX guarantees only one holder at a time.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

logger = logging.getLogger(__name__)

_LOCK_PREFIX = "navegador:lock:"


class LockTimeout(Exception):
    """Raised when a distributed lock cannot be acquired within the deadline."""


class DistributedLock:
    """
    A distributed mutex backed by Redis SETNX.

    Usage (context manager)::

        lock = DistributedLock("redis://localhost:6379", "my-lock")
        with lock:
            # only one process runs this at a time
            ...

    Usage (explicit)::

        lock = DistributedLock("redis://localhost:6379", "my-lock", timeout=10)
        if lock.acquire():
            try:
                ...
            finally:
                lock.release()

    Args:
        redis_url: Redis connection URL.
        name: Logical lock name (unique per resource).
        timeout: Lock expiry in seconds (default 30).  Also used as the
            maximum time to wait when acquiring (via __enter__).
        retry_interval: Seconds to sleep between acquire retries (default 0.1).
    """

    def __init__(
        self,
        redis_url: str,
        name: str,
        timeout: int = 30,
        retry_interval: float = 0.1,
        _redis_client: Any = None,
    ) -> None:
        self._url = redis_url
        self._name = name
        self._timeout = timeout
        self._retry_interval = retry_interval
        self._token: str | None = None  # unique value stored in Redis to own the lock
        self._redis: Any = _redis_client  # injected in tests; lazily created otherwise

    # ── Internal ──────────────────────────────────────────────────────────────

    def _client(self) -> Any:
        if self._redis is None:
            try:
                import redis  # type: ignore[import]
            except ImportError as exc:
                raise ImportError("Install redis: pip install redis") from exc
            self._redis = redis.from_url(self._url)
        return self._redis

    @property
    def _key(self) -> str:
        return f"{_LOCK_PREFIX}{self._name}"

    # ── Public API ────────────────────────────────────────────────────────────

    def acquire(self, blocking: bool = False, deadline: float | None = None) -> bool:
        """
        Try to acquire the lock.

        Args:
            blocking: If True, keep retrying until the lock is acquired or
                *deadline* is reached.
            deadline: Absolute time (``time.monotonic()``) after which
                acquisition fails.  Only used when *blocking* is True.

        Returns:
            True if the lock was acquired, False otherwise.
        """
        client = self._client()
        token = str(uuid.uuid4())
        while True:
            acquired = client.set(self._key, token, nx=True, ex=self._timeout)
            if acquired:
                self._token = token
                logger.debug("Lock acquired: %s (%s)", self._name, token)
                return True
            if not blocking:
                return False
            if deadline is not None and time.monotonic() >= deadline:
                return False
            time.sleep(self._retry_interval)

    def release(self) -> None:
        """Release the lock.  No-op if this instance does not hold it."""
        if self._token is None:
            return
        client = self._client()
        stored = client.get(self._key)
        # Decode bytes if necessary
        if isinstance(stored, bytes):
            stored = stored.decode()
        if stored == self._token:
            client.delete(self._key)
            logger.debug("Lock released: %s", self._name)
        self._token = None

    # ── Context manager ───────────────────────────────────────────────────────

    def __enter__(self) -> "DistributedLock":
        deadline = time.monotonic() + self._timeout
        acquired = self.acquire(blocking=True, deadline=deadline)
        if not acquired:
            raise LockTimeout(f"Could not acquire lock '{self._name}' within {self._timeout}s")
        return self

    def __exit__(self, *_: object) -> None:
        self.release()
