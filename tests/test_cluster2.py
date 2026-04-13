"""
Tests for navegador v0.6 cluster issues:

  #49 — DistributedLock (locking.py)
  #50 — CheckpointManager (checkpoint.py)
  #51 — SwarmDashboard (observability.py)
  #52 — MessageBus (messaging.py)
  #57 — FossilLiveAdapter (fossil_live.py)

All Redis and Fossil operations are mocked so no real infrastructure is needed.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_store():
    store = MagicMock()
    store.query.return_value = MagicMock(result_set=[])
    store.node_count.return_value = 5
    store.edge_count.return_value = 3
    return store


def _make_redis():
    """Return a MagicMock that behaves like a minimal Redis client."""
    r = MagicMock()
    _store: dict = {}
    _sets: dict = {}
    _lists: dict = {}
    _expiry: dict = {}

    def _set(key, value, nx=False, ex=None):
        if nx and key in _store:
            return False
        _store[key] = value
        if ex is not None:
            _expiry[key] = ex
        return True

    def _setex(key, ttl, value):
        _store[key] = value
        _expiry[key] = ttl
        return True

    def _get(key):
        return _store.get(key)

    def _delete(key):
        _store.pop(key, None)

    def _keys(pattern):
        # Very simple glob: support trailing *
        prefix = pattern.rstrip("*")
        return [k for k in _store if k.startswith(prefix)]

    def _sadd(key, *members):
        _sets.setdefault(key, set()).update(members)

    def _smembers(key):
        return _sets.get(key, set())

    def _rpush(key, value):
        _lists.setdefault(key, []).append(value)

    def _lrange(key, start, end):
        items = _lists.get(key, [])
        if end == -1:
            return items[start:]
        return items[start: end + 1]

    r.set.side_effect = _set
    r.setex.side_effect = _setex
    r.get.side_effect = _get
    r.delete.side_effect = _delete
    r.keys.side_effect = _keys
    r.sadd.side_effect = _sadd
    r.smembers.side_effect = _smembers
    r.rpush.side_effect = _rpush
    r.lrange.side_effect = _lrange
    return r


# =============================================================================
# #49 — DistributedLock
# =============================================================================


class TestDistributedLock:
    def test_acquire_release(self):
        from navegador.cluster.locking import DistributedLock

        r = _make_redis()
        lock = DistributedLock("redis://localhost", "my-lock", _redis_client=r)
        acquired = lock.acquire()
        assert acquired is True
        assert lock._token is not None
        lock.release()
        assert lock._token is None
        # Key should have been deleted
        assert r.get("navegador:lock:my-lock") is None

    def test_acquire_twice_fails(self):
        from navegador.cluster.locking import DistributedLock

        r = _make_redis()
        lock1 = DistributedLock("redis://localhost", "shared", _redis_client=r)
        lock2 = DistributedLock("redis://localhost", "shared", _redis_client=r)

        assert lock1.acquire() is True
        assert lock2.acquire() is False  # lock1 holds it
        lock1.release()

    def test_context_manager_acquires_and_releases(self):
        from navegador.cluster.locking import DistributedLock

        r = _make_redis()
        lock = DistributedLock("redis://localhost", "ctx-lock", _redis_client=r)
        with lock:
            assert lock._token is not None
        assert lock._token is None

    def test_context_manager_raises_lock_timeout(self):
        from navegador.cluster.locking import DistributedLock, LockTimeout

        r = _make_redis()
        # Pre-occupy the lock
        holder = DistributedLock("redis://localhost", "busy-lock", timeout=1, _redis_client=r)
        holder.acquire()

        waiter = DistributedLock(
            "redis://localhost", "busy-lock", timeout=1, retry_interval=0.05, _redis_client=r
        )
        with pytest.raises(LockTimeout):
            with waiter:
                pass

    def test_release_noop_when_not_holding(self):
        from navegador.cluster.locking import DistributedLock

        r = _make_redis()
        lock = DistributedLock("redis://localhost", "noop", _redis_client=r)
        lock.release()  # should not raise

    def test_lock_uses_setnx_semantics(self):
        """set() should be called with nx=True."""
        from navegador.cluster.locking import DistributedLock

        r = _make_redis()
        lock = DistributedLock("redis://localhost", "nx-test", _redis_client=r)
        lock.acquire()
        call_kwargs = r.set.call_args[1]
        assert call_kwargs.get("nx") is True


# =============================================================================
# #50 — CheckpointManager
# =============================================================================


class TestCheckpointManager:
    def test_create_returns_id(self, tmp_path):
        from navegador.cluster.checkpoint import CheckpointManager

        store = _make_store()
        with patch("navegador.cluster.checkpoint.export_graph") as mock_export:
            mock_export.return_value = {"nodes": 4, "edges": 2}
            mgr = CheckpointManager(store, tmp_path / "checkpoints")
            cid = mgr.create(label="before-refactor")
        assert isinstance(cid, str) and len(cid) == 36  # UUID4

    def test_create_writes_index(self, tmp_path):
        from navegador.cluster.checkpoint import CheckpointManager

        store = _make_store()
        ckdir = tmp_path / "ckpts"
        with patch("navegador.cluster.checkpoint.export_graph") as mock_export:
            mock_export.return_value = {"nodes": 3, "edges": 1}
            mgr = CheckpointManager(store, ckdir)
            cid = mgr.create(label="snap1")

        index_path = ckdir / "checkpoints.json"
        assert index_path.exists()
        index = json.loads(index_path.read_text())
        assert len(index) == 1
        assert index[0]["id"] == cid
        assert index[0]["label"] == "snap1"
        assert index[0]["node_count"] == 3

    def test_list_checkpoints(self, tmp_path):
        from navegador.cluster.checkpoint import CheckpointManager

        store = _make_store()
        ckdir = tmp_path / "ckpts"
        with patch("navegador.cluster.checkpoint.export_graph") as mock_export:
            mock_export.return_value = {"nodes": 2, "edges": 0}
            mgr = CheckpointManager(store, ckdir)
            id1 = mgr.create(label="first")
            id2 = mgr.create(label="second")

        checkpoints = mgr.list_checkpoints()
        assert len(checkpoints) == 2
        ids = [c["id"] for c in checkpoints]
        assert id1 in ids and id2 in ids

    def test_restore(self, tmp_path):
        from navegador.cluster.checkpoint import CheckpointManager

        store = _make_store()
        ckdir = tmp_path / "ckpts"

        def _fake_export(store, path):
            # Create the file so restore can find it
            Path(path).touch()
            return {"nodes": 5, "edges": 2}

        with patch("navegador.cluster.checkpoint.export_graph", side_effect=_fake_export), \
             patch("navegador.cluster.checkpoint.import_graph") as mock_import:
            mock_import.return_value = {"nodes": 5, "edges": 2}
            mgr = CheckpointManager(store, ckdir)
            cid = mgr.create(label="snapshot")
            mgr.restore(cid)
            mock_import.assert_called_once()
            call_args = mock_import.call_args
            assert call_args[0][0] is store
            assert call_args[1].get("clear", True) is True

    def test_restore_unknown_id_raises(self, tmp_path):
        from navegador.cluster.checkpoint import CheckpointManager

        store = _make_store()
        mgr = CheckpointManager(store, tmp_path / "ckpts")
        with pytest.raises(KeyError):
            mgr.restore("nonexistent-id")

    def test_delete_removes_from_index(self, tmp_path):
        from navegador.cluster.checkpoint import CheckpointManager

        store = _make_store()
        ckdir = tmp_path / "ckpts"
        with patch("navegador.cluster.checkpoint.export_graph") as mock_export:
            mock_export.return_value = {"nodes": 1, "edges": 0}
            mgr = CheckpointManager(store, ckdir)
            cid = mgr.create()
            mgr.delete(cid)

        assert len(mgr.list_checkpoints()) == 0

    def test_delete_unknown_id_raises(self, tmp_path):
        from navegador.cluster.checkpoint import CheckpointManager

        mgr = CheckpointManager(_make_store(), tmp_path / "ckpts")
        with pytest.raises(KeyError):
            mgr.delete("ghost-id")


# =============================================================================
# #51 — SwarmDashboard
# =============================================================================


class TestSwarmDashboard:
    def test_register_and_agent_status(self):
        from navegador.cluster.observability import SwarmDashboard

        r = _make_redis()
        dash = SwarmDashboard("redis://localhost", _redis_client=r)
        dash.register_agent("agent-1", {"role": "ingestor"})
        dash.register_agent("agent-2")

        agents = dash.agent_status()
        ids = {a["agent_id"] for a in agents}
        assert "agent-1" in ids
        assert "agent-2" in ids

    def test_agent_status_empty(self):
        from navegador.cluster.observability import SwarmDashboard

        r = _make_redis()
        dash = SwarmDashboard("redis://localhost", _redis_client=r)
        assert dash.agent_status() == []

    def test_task_metrics_default(self):
        from navegador.cluster.observability import SwarmDashboard

        r = _make_redis()
        dash = SwarmDashboard("redis://localhost", _redis_client=r)
        metrics = dash.task_metrics()
        assert metrics == {"pending": 0, "active": 0, "completed": 0, "failed": 0}

    def test_update_task_metrics(self):
        from navegador.cluster.observability import SwarmDashboard

        r = _make_redis()
        dash = SwarmDashboard("redis://localhost", _redis_client=r)
        dash.update_task_metrics(pending=3, active=1)
        m = dash.task_metrics()
        assert m["pending"] == 3
        assert m["active"] == 1

    def test_graph_metrics(self):
        from navegador.cluster.observability import SwarmDashboard

        r = _make_redis()
        dash = SwarmDashboard("redis://localhost", _redis_client=r)
        store = _make_store()
        gm = dash.graph_metrics(store)
        assert gm["node_count"] == 5
        assert gm["edge_count"] == 3
        assert "last_modified" in gm

    def test_to_json_contains_all_sections(self):
        from navegador.cluster.observability import SwarmDashboard

        r = _make_redis()
        dash = SwarmDashboard("redis://localhost", _redis_client=r)
        dash.register_agent("a1")
        dash.update_task_metrics(completed=7)
        store = _make_store()
        dash.graph_metrics(store)

        snapshot = json.loads(dash.to_json())
        assert "agents" in snapshot
        assert "task_metrics" in snapshot
        assert "graph_metrics" in snapshot
        assert snapshot["task_metrics"]["completed"] == 7


# =============================================================================
# #52 — MessageBus
# =============================================================================


class TestMessageBus:
    def test_send_returns_message_id(self):
        from navegador.cluster.messaging import MessageBus

        r = _make_redis()
        bus = MessageBus("redis://localhost", _redis_client=r)
        mid = bus.send("alice", "bob", "task.assign", {"task_id": "t1"})
        assert isinstance(mid, str) and len(mid) == 36

    def test_receive_pending_messages(self):
        from navegador.cluster.messaging import MessageBus

        r = _make_redis()
        bus = MessageBus("redis://localhost", _redis_client=r)
        bus.send("alice", "bob", "greeting", {"text": "hello"})
        bus.send("alice", "bob", "greeting", {"text": "world"})

        msgs = bus.receive("bob", limit=10)
        assert len(msgs) == 2
        assert msgs[0].from_agent == "alice"
        assert msgs[0].to_agent == "bob"
        assert msgs[0].payload["text"] == "hello"

    def test_acknowledge_removes_from_pending(self):
        from navegador.cluster.messaging import MessageBus

        r = _make_redis()
        bus = MessageBus("redis://localhost", _redis_client=r)
        mid = bus.send("alice", "bob", "ping", {})
        bus.acknowledge(mid, agent_id="bob")

        # Message was acked — receive should return empty for bob
        msgs = bus.receive("bob")
        assert all(m.id != mid for m in msgs)

    def test_broadcast_reaches_all_agents(self):
        from navegador.cluster.messaging import MessageBus

        r = _make_redis()
        bus = MessageBus("redis://localhost", _redis_client=r)
        # Register recipients
        bus.receive("carol")  # touching the queue registers the agent
        bus.receive("dave")

        _mids = bus.broadcast("alice", "announcement", {"msg": "deploy"})
        # carol and dave should each have the message
        carol_msgs = bus.receive("carol")
        dave_msgs = bus.receive("dave")
        assert any(m.type == "announcement" for m in carol_msgs)
        assert any(m.type == "announcement" for m in dave_msgs)

    def test_broadcast_excludes_sender(self):
        from navegador.cluster.messaging import MessageBus

        r = _make_redis()
        bus = MessageBus("redis://localhost", _redis_client=r)
        bus.receive("carol")  # register carol

        bus.broadcast("alice", "news", {"x": 1})
        # alice should not have received the broadcast
        alice_msgs = bus.receive("alice")
        assert all(m.from_agent != "alice" or m.type != "news" for m in alice_msgs)

    def test_message_fields(self):
        from navegador.cluster.messaging import MessageBus

        r = _make_redis()
        bus = MessageBus("redis://localhost", _redis_client=r)
        bus.send("sender", "receiver", "status.update", {"code": 42})
        msgs = bus.receive("receiver")
        assert len(msgs) == 1
        m = msgs[0]
        assert m.from_agent == "sender"
        assert m.to_agent == "receiver"
        assert m.type == "status.update"
        assert m.payload == {"code": 42}
        assert m.acknowledged is False
        assert m.timestamp > 0


# =============================================================================
# #57 — FossilLiveAdapter
# =============================================================================


class TestFossilLiveAdapter:
    def _make_sqlite_conn(self, rows_event=None, rows_ticket=None):
        """Create a mock sqlite3 connection."""
        import sqlite3

        conn = MagicMock(spec=sqlite3.Connection)
        cursor = MagicMock()
        cursor.fetchall.return_value = rows_event or []
        cursor.description = [
            ("type",), ("mtime",), ("objid",), ("uid",), ("user",),
            ("euser",), ("comment",), ("ecomment",),
        ]

        ticket_cursor = MagicMock()
        ticket_cursor.fetchall.return_value = rows_ticket or []
        ticket_cursor.description = [
            ("tkt_uuid",), ("title",), ("status",), ("type",), ("tkt_mtime",),
        ]

        # execute returns event cursor by default; ticket cursor when queried
        def _execute(sql, params=()):
            if "ticket" in sql:
                return ticket_cursor
            return cursor

        conn.execute.side_effect = _execute
        return conn

    def test_query_timeline_returns_rows(self):
        from navegador.cluster.fossil_live import FossilLiveAdapter

        raw_rows = [
            ("ci", 2460000.0, 12345, 1, "alice", "alice", "initial commit", ""),
        ]
        conn = self._make_sqlite_conn(rows_event=raw_rows)
        adapter = FossilLiveAdapter("/fake/repo.fossil", _sqlite_conn=conn)
        rows = adapter.query_timeline(limit=10)
        assert len(rows) == 1
        conn.execute.assert_called()

    def test_query_tickets_returns_rows(self):
        from navegador.cluster.fossil_live import FossilLiveAdapter

        ticket_rows = [("abc123", "Bug in login", "open", "defect", 2460001.0)]
        conn = self._make_sqlite_conn(rows_ticket=ticket_rows)
        adapter = FossilLiveAdapter("/fake/repo.fossil", _sqlite_conn=conn)
        tickets = adapter.query_tickets()
        assert len(tickets) == 1

    def test_query_tickets_exception_returns_empty(self):
        from navegador.cluster.fossil_live import FossilLiveAdapter

        conn = MagicMock()
        conn.execute.side_effect = Exception("no ticket table")
        adapter = FossilLiveAdapter("/fake/repo.fossil", _sqlite_conn=conn)
        result = adapter.query_tickets()
        assert result == []

    def test_sync_to_graph_imports_commits(self):
        from navegador.cluster.fossil_live import FossilLiveAdapter

        conn = MagicMock()
        cursor = MagicMock()
        cursor.fetchall.return_value = [
            ("ci", 2460000.0, 9999, 1, "bob", "bob", "fix bug", ""),
            ("w", 2460001.0, 1000, 2, "carol", "carol", "wiki edit", ""),  # skipped
        ]
        cursor.description = [
            ("type",), ("mtime",), ("objid",), ("uid",), ("user",),
            ("euser",), ("comment",), ("ecomment",),
        ]
        ticket_cursor = MagicMock()
        ticket_cursor.fetchall.return_value = []
        ticket_cursor.description = [("tkt_uuid",), ("title",), ("status",), ("type",), ("tkt_mtime",)]

        def _execute(sql, params=()):
            if "ticket" in sql:
                return ticket_cursor
            return cursor

        conn.execute.side_effect = _execute
        store = _make_store()
        adapter = FossilLiveAdapter("/fake/repo.fossil", _sqlite_conn=conn)
        result = adapter.sync_to_graph(store)
        # Only "ci" type events should be imported
        assert result["commits"] == 1
        assert result["tickets"] == 0

    def test_sync_to_graph_imports_tickets(self):
        from navegador.cluster.fossil_live import FossilLiveAdapter

        conn = MagicMock()
        event_cursor = MagicMock()
        event_cursor.fetchall.return_value = []
        event_cursor.description = [
            ("type",), ("mtime",), ("objid",), ("uid",), ("user",),
            ("euser",), ("comment",), ("ecomment",),
        ]
        ticket_cursor = MagicMock()
        ticket_cursor.fetchall.return_value = [
            ("ticket-uuid-1", "Login fails", "open", "defect", 2460002.0),
        ]
        ticket_cursor.description = [
            ("tkt_uuid",), ("title",), ("status",), ("type",), ("tkt_mtime",),
        ]

        def _execute(sql, params=()):
            if "ticket" in sql:
                return ticket_cursor
            return event_cursor

        conn.execute.side_effect = _execute
        store = _make_store()
        adapter = FossilLiveAdapter("/fake/repo.fossil", _sqlite_conn=conn)
        result = adapter.sync_to_graph(store)
        assert result["tickets"] == 1

    def test_attach_calls_attach_database_on_sqlite_conn(self):
        import sqlite3

        from navegador.cluster.fossil_live import FossilLiveAdapter

        conn = MagicMock(spec=sqlite3.Connection)
        conn.execute = MagicMock()
        store = _make_store()
        store._client = MagicMock()
        store._client._db = conn

        adapter = FossilLiveAdapter("/fake/repo.fossil")
        adapter.attach(store)
        # Should have called ATTACH DATABASE
        call_args = conn.execute.call_args
        assert "ATTACH" in call_args[0][0].upper()
        assert adapter._attached is True

    def test_attach_fallback_when_no_sqlite(self, tmp_path):
        """When the store is Redis-backed, adapter falls back gracefully."""
        import sqlite3

        from navegador.cluster.fossil_live import FossilLiveAdapter

        # Create a real (tiny) Fossil-like sqlite db so the fallback connect works
        fossil_path = tmp_path / "repo.fossil"
        db = sqlite3.connect(str(fossil_path))
        db.execute(
            "CREATE TABLE event (type TEXT, mtime REAL, objid INT, uid INT, "
            "user TEXT, euser TEXT, comment TEXT, ecomment TEXT)"
        )
        db.commit()
        db.close()

        store = _make_store()
        store._client = MagicMock()
        # No _db attribute — simulates Redis backend
        del store._client._db

        adapter = FossilLiveAdapter(fossil_path)
        adapter.attach(store)  # should not raise
        assert adapter._attached is False  # fallback path: no attachment
