"""
Agent-to-agent async messaging via Redis.

Messages are queued in Redis lists, one list per recipient agent.  A broadcast
copies the message to every currently-known agent queue.

All queues live under the ``navegador:msg:`` namespace.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

_QUEUE_PREFIX = "navegador:msg:queue:"
_ACK_PREFIX = "navegador:msg:ack:"
_ALL_AGENTS_KEY = "navegador:msg:agents"


@dataclass
class Message:
    """A single agent-to-agent message."""

    id: str
    from_agent: str
    to_agent: str
    type: str
    payload: dict
    timestamp: float
    acknowledged: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Message":
        return cls(
            id=data["id"],
            from_agent=data["from_agent"],
            to_agent=data["to_agent"],
            type=data["type"],
            payload=data.get("payload", {}),
            timestamp=data["timestamp"],
            acknowledged=data.get("acknowledged", False),
        )


class MessageBus:
    """
    Async message bus for agent-to-agent communication.

    Messages are stored in per-agent Redis lists.  Acknowledged messages are
    tracked in a separate key set; unacknowledged messages remain in the queue.

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

    def _queue_key(self, agent_id: str) -> str:
        return f"{_QUEUE_PREFIX}{agent_id}"

    def _ack_key(self, agent_id: str) -> str:
        return f"{_ACK_PREFIX}{agent_id}"

    def _register_agent(self, agent_id: str) -> None:
        """Track the agent so broadcasts can find it."""
        self._client().sadd(_ALL_AGENTS_KEY, agent_id)

    def _push_message(self, message: Message) -> None:
        """Push a serialised message onto the recipient's queue."""
        client = self._client()
        self._register_agent(message.to_agent)
        client.rpush(self._queue_key(message.to_agent), json.dumps(message.to_dict()))

    # ── Public API ────────────────────────────────────────────────────────────

    def send(
        self,
        from_agent: str,
        to_agent: str,
        message_type: str,
        payload: dict,
    ) -> str:
        """
        Send a message from one agent to another.

        Args:
            from_agent: Sender agent ID.
            to_agent: Recipient agent ID.
            message_type: Semantic type label for the message.
            payload: Arbitrary JSON-serialisable dict.

        Returns:
            Unique message ID (UUID4 string).
        """
        msg = Message(
            id=str(uuid.uuid4()),
            from_agent=from_agent,
            to_agent=to_agent,
            type=message_type,
            payload=payload,
            timestamp=time.time(),
        )
        self._push_message(msg)
        logger.debug("Message sent: %s -> %s [%s]", from_agent, to_agent, msg.id)
        return msg.id

    def receive(self, agent_id: str, limit: int = 10) -> list[Message]:
        """
        Retrieve pending (unacknowledged) messages for an agent.

        Messages remain in the queue until acknowledged via :meth:`acknowledge`.

        Args:
            agent_id: The receiving agent's ID.
            limit: Maximum number of messages to return (default 10).

        Returns:
            List of :class:`Message` objects, oldest first.
        """
        client = self._client()
        self._register_agent(agent_id)
        raw_items = client.lrange(self._queue_key(agent_id), 0, limit - 1)
        acked_ids: set[str] = set(
            i.decode() if isinstance(i, bytes) else i
            for i in client.smembers(self._ack_key(agent_id))
        )

        messages = []
        for raw in raw_items:
            if isinstance(raw, bytes):
                raw = raw.decode()
            data = json.loads(raw)
            msg = Message.from_dict(data)
            if msg.id not in acked_ids:
                messages.append(msg)
        return messages

    def acknowledge(self, message_id: str, agent_id: str | None = None) -> None:
        """
        Mark a message as read.

        Because messages stay in the queue list (for replay), acknowledgement
        is stored in a separate Redis set.  If *agent_id* is not supplied the
        method scans all known agent ack-sets (less efficient).

        Args:
            message_id: The message ID to acknowledge.
            agent_id: Optionally scope the acknowledgement to one agent's set.
        """
        client = self._client()
        if agent_id:
            client.sadd(self._ack_key(agent_id), message_id)
        else:
            # Best-effort: acknowledge in all known agent sets
            all_agents = client.smembers(_ALL_AGENTS_KEY)
            for a in all_agents:
                if isinstance(a, bytes):
                    a = a.decode()
                client.sadd(self._ack_key(a), message_id)
        logger.debug("Message acknowledged: %s", message_id)

    def broadcast(
        self,
        from_agent: str,
        message_type: str,
        payload: dict,
    ) -> list[str]:
        """
        Send a message to every registered agent.

        Args:
            from_agent: Sender agent ID.
            message_type: Semantic type label.
            payload: Arbitrary JSON-serialisable dict.

        Returns:
            List of message IDs (one per recipient).
        """
        client = self._client()
        all_agents = client.smembers(_ALL_AGENTS_KEY)
        message_ids = []
        for agent_bytes in all_agents:
            to_agent = agent_bytes.decode() if isinstance(agent_bytes, bytes) else agent_bytes
            if to_agent == from_agent:
                continue
            mid = self.send(from_agent, to_agent, message_type, payload)
            message_ids.append(mid)
        logger.debug(
            "Broadcast from %s [%s]: %d recipients", from_agent, message_type, len(message_ids)
        )
        return message_ids
