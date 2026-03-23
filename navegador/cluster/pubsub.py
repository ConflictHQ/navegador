"""
GraphNotifier — real-time graph change notifications via Redis pub/sub.

Agents can publish change events when they mutate graph nodes or edges and
subscribe to receive those events, enabling reactive coordination in a swarm.

Usage:
    notifier = GraphNotifier("redis://localhost:6379")

    # Publisher side
    notifier.publish(EventType.NODE_CREATED, {"label": "Function", "name": "my_fn"})

    # Subscriber side (blocking — run in a thread)
    def handler(event_type, data):
        print(f"Event: {event_type}, data: {data}")

    notifier.subscribe([EventType.NODE_CREATED, EventType.EDGE_CREATED], handler)
"""

from __future__ import annotations

import json
import logging
import threading
from enum import Enum
from typing import Any, Callable

logger = logging.getLogger(__name__)

_CHANNEL_PREFIX = "navegador:events"


class EventType(str, Enum):
    """Graph change event types."""

    NODE_CREATED = "node_created"
    NODE_UPDATED = "node_updated"
    NODE_DELETED = "node_deleted"
    EDGE_CREATED = "edge_created"
    EDGE_UPDATED = "edge_updated"
    EDGE_DELETED = "edge_deleted"
    GRAPH_CLEARED = "graph_cleared"
    SNAPSHOT_PUSHED = "snapshot_pushed"


def _channel_name(event_type: EventType | str) -> str:
    val = event_type.value if isinstance(event_type, EventType) else str(event_type)
    return f"{_CHANNEL_PREFIX}:{val}"


class GraphNotifier:
    """
    Publish and subscribe to graph change events over Redis pub/sub.

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
        self._subscriptions: list[threading.Thread] = []

    # ── Internal ──────────────────────────────────────────────────────────────

    @staticmethod
    def _connect_redis(url: str) -> Any:
        try:
            import redis  # type: ignore[import]
        except ImportError as exc:
            raise ImportError("Install redis: pip install redis") from exc
        return redis.from_url(url)

    # ── Public API ────────────────────────────────────────────────────────────

    def publish(self, event_type: EventType | str, data: dict[str, Any]) -> int:
        """
        Publish a change event to all subscribers.

        Parameters
        ----------
        event_type:
            One of the ``EventType`` enum values (or a raw string).
        data:
            Arbitrary JSON-serialisable payload describing the change.

        Returns
        -------
        int
            Number of clients that received the message.
        """
        channel = _channel_name(event_type)
        payload = json.dumps(
            {
                "event_type": event_type.value if isinstance(event_type, EventType) else event_type,
                "data": data,
            }
        )
        result = self._redis.publish(channel, payload)
        logger.debug("Published %s to channel %s (%d receivers)", event_type, channel, result)
        return result

    def subscribe(
        self,
        event_types: list[EventType | str],
        callback: Callable[[str, dict[str, Any]], None],
        *,
        run_in_thread: bool = False,
    ) -> threading.Thread | None:
        """
        Subscribe to one or more event types and invoke *callback* for each.

        Parameters
        ----------
        event_types:
            List of ``EventType`` values to listen for.
        callback:
            Callable receiving ``(event_type: str, data: dict)``.
        run_in_thread:
            If ``True``, run the blocking listen loop in a daemon thread and
            return that thread.  If ``False`` (default), block the calling
            thread.

        Returns
        -------
        threading.Thread | None
            The daemon thread if ``run_in_thread=True``, else ``None``.
        """
        channels = [_channel_name(et) for et in event_types]
        pubsub = self._redis.pubsub()
        pubsub.subscribe(*channels)
        logger.info("Subscribed to channels: %s", channels)

        def _listen() -> None:
            for message in pubsub.listen():
                if message["type"] != "message":
                    continue
                try:
                    payload = json.loads(message["data"])
                    et = payload.get("event_type", "")
                    d = payload.get("data", {})
                    callback(et, d)
                except (json.JSONDecodeError, TypeError, KeyError) as exc:
                    logger.warning("Failed to decode pubsub message: %s", exc)

        if run_in_thread:
            t = threading.Thread(target=_listen, daemon=True)
            t.start()
            self._subscriptions.append(t)
            return t

        _listen()
        return None

    def close(self) -> None:
        """Close the Redis connection."""
        try:
            self._redis.close()
        except Exception:
            pass
