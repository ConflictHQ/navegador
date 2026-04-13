"""
Tests for navegador.cluster — ClusterManager, GraphNotifier, TaskQueue,
WorkPartitioner, and SessionManager.

All Redis operations are mocked; no real Redis instance is required.
"""

from __future__ import annotations

import json
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_redis_mock():
    """Return a MagicMock that behaves like a Redis client."""
    r = MagicMock()
    pipe = MagicMock()
    pipe.execute.return_value = [True, True, True]
    r.pipeline.return_value = pipe
    return r, pipe


# ===========================================================================
# #20 — ClusterManager
# ===========================================================================

class TestClusterManagerStatus:
    def test_in_sync_when_versions_equal(self):
        from navegador.cluster.core import ClusterManager

        r, _ = _make_redis_mock()
        r.get.return_value = b"5"
        mgr = ClusterManager("redis://localhost:6379", redis_client=r)

        # Patch _local_version to return the same value
        with patch.object(mgr, "_local_version", return_value=5):
            s = mgr.status()

        assert s["shared_version"] == 5
        assert s["local_version"] == 5
        assert s["in_sync"] is True

    def test_out_of_sync_when_versions_differ(self):
        from navegador.cluster.core import ClusterManager

        r, _ = _make_redis_mock()
        r.get.return_value = b"10"
        mgr = ClusterManager("redis://localhost:6379", redis_client=r)

        with patch.object(mgr, "_local_version", return_value=3):
            s = mgr.status()

        assert s["shared_version"] == 10
        assert s["local_version"] == 3
        assert s["in_sync"] is False

    def test_zero_versions_when_no_data(self):
        from navegador.cluster.core import ClusterManager

        r, _ = _make_redis_mock()
        r.get.return_value = None  # no version key in Redis
        mgr = ClusterManager("redis://localhost:6379", redis_client=r)

        with patch.object(mgr, "_local_version", return_value=0):
            s = mgr.status()

        assert s["shared_version"] == 0
        assert s["local_version"] == 0
        assert s["in_sync"] is True


class TestClusterManagerSnapshotToLocal:
    def test_no_op_when_no_snapshot_in_redis(self, caplog):
        import logging

        from navegador.cluster.core import ClusterManager

        r, _ = _make_redis_mock()
        r.get.return_value = None
        mgr = ClusterManager("redis://localhost:6379", redis_client=r)

        with caplog.at_level(logging.WARNING, logger="navegador.cluster.core"):
            mgr.snapshot_to_local()

        assert "No shared snapshot" in caplog.text

    def test_calls_import_and_sets_version(self):
        from navegador.cluster.core import _SNAPSHOT_KEY, _VERSION_KEY, ClusterManager

        r, _ = _make_redis_mock()
        snapshot_data = json.dumps({"nodes": [], "edges": []})

        def _get_side(key):
            if key == _SNAPSHOT_KEY:
                return snapshot_data.encode()
            if key == _VERSION_KEY:
                return b"7"
            return None

        r.get.side_effect = _get_side
        mgr = ClusterManager("redis://localhost:6379", redis_client=r)

        with patch.object(mgr, "_import_to_local_graph") as mock_import, \
             patch.object(mgr, "_set_local_version") as mock_set_ver:
            mgr.snapshot_to_local()

        mock_import.assert_called_once_with({"nodes": [], "edges": []})
        mock_set_ver.assert_called_once_with(7)


class TestClusterManagerPushToShared:
    def test_exports_and_writes_to_redis(self):
        from navegador.cluster.core import ClusterManager

        r, pipe = _make_redis_mock()
        r.get.return_value = b"3"  # current shared version

        mgr = ClusterManager("redis://localhost:6379", redis_client=r)
        export_data = {"nodes": [{"labels": ["Function"], "properties": {"name": "f"}}], "edges": []}

        with patch.object(mgr, "_export_local_graph", return_value=export_data), \
             patch.object(mgr, "_set_local_version") as mock_set:
            mgr.push_to_shared()

        # Pipeline should have been used
        r.pipeline.assert_called()
        pipe.execute.assert_called()
        mock_set.assert_called_once_with(4)  # incremented from 3


class TestClusterManagerSync:
    def test_pulls_when_shared_is_newer(self):
        from navegador.cluster.core import ClusterManager

        r, _ = _make_redis_mock()
        r.get.return_value = b"10"
        mgr = ClusterManager("redis://localhost:6379", redis_client=r)

        with patch.object(mgr, "_local_version", return_value=2), \
             patch.object(mgr, "snapshot_to_local") as mock_pull, \
             patch.object(mgr, "push_to_shared") as mock_push:
            mgr.sync()

        mock_pull.assert_called_once()
        mock_push.assert_not_called()

    def test_pushes_when_local_is_current(self):
        from navegador.cluster.core import ClusterManager

        r, _ = _make_redis_mock()
        r.get.return_value = b"5"
        mgr = ClusterManager("redis://localhost:6379", redis_client=r)

        with patch.object(mgr, "_local_version", return_value=5), \
             patch.object(mgr, "snapshot_to_local") as mock_pull, \
             patch.object(mgr, "push_to_shared") as mock_push:
            mgr.sync()

        mock_push.assert_called_once()
        mock_pull.assert_not_called()


# ===========================================================================
# #32 — GraphNotifier
# ===========================================================================

class TestGraphNotifierPublish:
    def test_publishes_to_correct_channel(self):
        from navegador.cluster.pubsub import EventType, GraphNotifier

        r, _ = _make_redis_mock()
        r.publish.return_value = 1
        notifier = GraphNotifier("redis://localhost:6379", redis_client=r)

        count = notifier.publish(EventType.NODE_CREATED, {"name": "MyFunc"})

        assert count == 1
        channel_arg = r.publish.call_args[0][0]
        assert "node_created" in channel_arg

    def test_payload_is_json_with_event_type_and_data(self):
        from navegador.cluster.pubsub import EventType, GraphNotifier

        r, _ = _make_redis_mock()
        r.publish.return_value = 0
        notifier = GraphNotifier("redis://localhost:6379", redis_client=r)

        notifier.publish(EventType.EDGE_CREATED, {"src": "A", "dst": "B"})

        payload_str = r.publish.call_args[0][1]
        payload = json.loads(payload_str)
        assert payload["event_type"] == "edge_created"
        assert payload["data"] == {"src": "A", "dst": "B"}

    def test_publish_with_string_event_type(self):
        from navegador.cluster.pubsub import GraphNotifier

        r, _ = _make_redis_mock()
        r.publish.return_value = 0
        notifier = GraphNotifier("redis://localhost:6379", redis_client=r)

        notifier.publish("custom_event", {"key": "val"})

        channel_arg = r.publish.call_args[0][0]
        assert "custom_event" in channel_arg

    def test_all_event_types_exist(self):
        from navegador.cluster.pubsub import EventType

        for expected in ["node_created", "node_updated", "node_deleted",
                         "edge_created", "edge_updated", "edge_deleted",
                         "graph_cleared", "snapshot_pushed"]:
            assert any(e.value == expected for e in EventType)


class TestGraphNotifierSubscribe:
    def test_subscribe_uses_pubsub_and_calls_callback(self):
        from navegador.cluster.pubsub import EventType, GraphNotifier

        r, _ = _make_redis_mock()
        pubsub_mock = MagicMock()

        # Two messages: one subscription confirmation, one real message
        messages = [
            {"type": "subscribe", "data": 1},
            {
                "type": "message",
                "data": json.dumps({
                    "event_type": "node_created",
                    "data": {"name": "Foo"},
                }).encode(),
            },
        ]

        # Make listen() yield one message then raise StopIteration
        pubsub_mock.listen.return_value = iter(messages)
        r.pubsub.return_value = pubsub_mock

        notifier = GraphNotifier("redis://localhost:6379", redis_client=r)
        received: list = []

        def handler(event_type, data):
            received.append((event_type, data))

        # run_in_thread=False so we block until messages exhausted
        notifier.subscribe([EventType.NODE_CREATED], handler, run_in_thread=False)

        assert len(received) == 1
        assert received[0] == ("node_created", {"name": "Foo"})

    def test_subscribe_in_thread_returns_thread(self):
        from navegador.cluster.pubsub import EventType, GraphNotifier

        r, _ = _make_redis_mock()
        pubsub_mock = MagicMock()
        pubsub_mock.listen.return_value = iter([])  # immediately empty
        r.pubsub.return_value = pubsub_mock

        notifier = GraphNotifier("redis://localhost:6379", redis_client=r)
        t = notifier.subscribe([EventType.NODE_CREATED], lambda *_: None, run_in_thread=True)

        assert isinstance(t, threading.Thread)
        assert t.daemon is True

    def test_malformed_message_does_not_raise(self):
        from navegador.cluster.pubsub import EventType, GraphNotifier

        r, _ = _make_redis_mock()
        pubsub_mock = MagicMock()
        messages = [
            {"type": "message", "data": b"not valid json"},
        ]
        pubsub_mock.listen.return_value = iter(messages)
        r.pubsub.return_value = pubsub_mock

        notifier = GraphNotifier("redis://localhost:6379", redis_client=r)
        # Should not raise
        notifier.subscribe([EventType.NODE_DELETED], lambda *_: None, run_in_thread=False)


# ===========================================================================
# #46 — TaskQueue
# ===========================================================================

class TestTaskQueueEnqueue:
    def test_returns_task_id_string(self):
        from navegador.cluster.taskqueue import TaskQueue

        r, pipe = _make_redis_mock()
        queue = TaskQueue("redis://localhost:6379", redis_client=r)

        task_id = queue.enqueue("ingest_file", {"path": "src/main.py"})

        assert isinstance(task_id, str)
        assert len(task_id) > 0

    def test_stores_task_hash_and_pushes_to_list(self):
        from navegador.cluster.taskqueue import _QUEUE_KEY, TaskQueue

        r, pipe = _make_redis_mock()
        queue = TaskQueue("redis://localhost:6379", redis_client=r)

        task_id = queue.enqueue("ingest_file", {"path": "src/main.py"})

        pipe.hset.assert_called_once()
        pipe.rpush.assert_called_once()
        rpush_args = pipe.rpush.call_args[0]
        assert rpush_args[0] == _QUEUE_KEY
        assert rpush_args[1] == task_id

    def test_two_enqueues_produce_different_ids(self):
        from navegador.cluster.taskqueue import TaskQueue

        r, _ = _make_redis_mock()
        queue = TaskQueue("redis://localhost:6379", redis_client=r)

        id1 = queue.enqueue("type_a", {})
        id2 = queue.enqueue("type_b", {})

        assert id1 != id2


class TestTaskQueueDequeue:
    def _setup_dequeue(self, task_id: str, task_type: str = "ingest"):
        from navegador.cluster.taskqueue import Task

        r, pipe = _make_redis_mock()
        r.lpop.return_value = task_id.encode()

        task = Task(id=task_id, type=task_type, payload={"x": 1})
        stored = task.to_dict()
        # Convert back to bytes as Redis would return
        r.hgetall.return_value = {k.encode(): v.encode() for k, v in stored.items()}
        return r, pipe

    def test_returns_task_when_queue_has_items(self):
        from navegador.cluster.taskqueue import TaskQueue

        task_id = "test-task-001"
        r, pipe = self._setup_dequeue(task_id)
        queue = TaskQueue("redis://localhost:6379", redis_client=r)

        task = queue.dequeue("agent-1")

        assert task is not None
        assert task.id == task_id

    def test_returns_none_when_queue_empty(self):
        from navegador.cluster.taskqueue import TaskQueue

        r, _ = _make_redis_mock()
        r.lpop.return_value = None
        queue = TaskQueue("redis://localhost:6379", redis_client=r)

        task = queue.dequeue("agent-1")

        assert task is None

    def test_updates_status_to_in_progress(self):
        from navegador.cluster.taskqueue import TaskQueue, TaskStatus

        task_id = "test-task-002"
        r, pipe = self._setup_dequeue(task_id)
        queue = TaskQueue("redis://localhost:6379", redis_client=r)

        queue.dequeue("agent-1")

        # The pipeline hset should include in_progress status
        hset_call = pipe.hset.call_args
        mapping = hset_call[1]["mapping"]
        assert mapping["status"] == TaskStatus.IN_PROGRESS.value

    def test_sets_agent_id_on_task(self):
        from navegador.cluster.taskqueue import TaskQueue

        task_id = "test-task-003"
        r, pipe = self._setup_dequeue(task_id)
        queue = TaskQueue("redis://localhost:6379", redis_client=r)

        queue.dequeue("my-agent")

        mapping = pipe.hset.call_args[1]["mapping"]
        assert mapping["agent_id"] == "my-agent"


class TestTaskQueueComplete:
    def test_marks_task_done(self):
        from navegador.cluster.taskqueue import TaskQueue, TaskStatus

        r, pipe = _make_redis_mock()
        queue = TaskQueue("redis://localhost:6379", redis_client=r)

        queue.complete("task-123", result={"output": "ok"})

        mapping = pipe.hset.call_args[1]["mapping"]
        assert mapping["status"] == TaskStatus.DONE.value

    def test_complete_with_no_result(self):
        from navegador.cluster.taskqueue import TaskQueue, TaskStatus

        r, pipe = _make_redis_mock()
        queue = TaskQueue("redis://localhost:6379", redis_client=r)

        queue.complete("task-456")

        mapping = pipe.hset.call_args[1]["mapping"]
        assert mapping["status"] == TaskStatus.DONE.value
        assert mapping["result"] == ""

    def test_removes_from_inprogress_set(self):
        from navegador.cluster.taskqueue import _INPROGRESS_KEY, TaskQueue

        r, pipe = _make_redis_mock()
        queue = TaskQueue("redis://localhost:6379", redis_client=r)

        queue.complete("task-789")

        pipe.srem.assert_called_once_with(_INPROGRESS_KEY, "task-789")


class TestTaskQueueFail:
    def test_marks_task_failed(self):
        from navegador.cluster.taskqueue import TaskQueue, TaskStatus

        r, pipe = _make_redis_mock()
        queue = TaskQueue("redis://localhost:6379", redis_client=r)

        queue.fail("task-999", "something went wrong")

        mapping = pipe.hset.call_args[1]["mapping"]
        assert mapping["status"] == TaskStatus.FAILED.value
        assert mapping["error"] == "something went wrong"

    def test_removes_from_inprogress_set(self):
        from navegador.cluster.taskqueue import _INPROGRESS_KEY, TaskQueue

        r, pipe = _make_redis_mock()
        queue = TaskQueue("redis://localhost:6379", redis_client=r)

        queue.fail("task-000", "oops")

        pipe.srem.assert_called_once_with(_INPROGRESS_KEY, "task-000")


class TestTaskQueueStatus:
    def test_returns_status_dict_for_existing_task(self):
        from navegador.cluster.taskqueue import Task, TaskQueue, TaskStatus

        r, _ = _make_redis_mock()
        task = Task(id="t1", type="analyze", payload={})
        r.hgetall.return_value = {k.encode(): v.encode() for k, v in task.to_dict().items()}
        queue = TaskQueue("redis://localhost:6379", redis_client=r)

        info = queue.status("t1")

        assert info["id"] == "t1"
        assert info["type"] == "analyze"
        assert info["status"] == TaskStatus.PENDING.value

    def test_raises_key_error_for_missing_task(self):
        from navegador.cluster.taskqueue import TaskQueue

        r, _ = _make_redis_mock()
        r.hgetall.return_value = {}
        queue = TaskQueue("redis://localhost:6379", redis_client=r)

        with pytest.raises(KeyError, match="not found"):
            queue.status("nonexistent")


class TestTaskQueuePendingCount:
    def test_returns_llen_value(self):
        from navegador.cluster.taskqueue import TaskQueue

        r, _ = _make_redis_mock()
        r.llen.return_value = 7
        queue = TaskQueue("redis://localhost:6379", redis_client=r)

        assert queue.pending_count() == 7

    def test_returns_zero_when_empty(self):
        from navegador.cluster.taskqueue import TaskQueue

        r, _ = _make_redis_mock()
        r.llen.return_value = 0
        queue = TaskQueue("redis://localhost:6379", redis_client=r)

        assert queue.pending_count() == 0


# ===========================================================================
# #47 — WorkPartitioner
# ===========================================================================

def _mock_store_with_files(file_paths: list[str]) -> MagicMock:
    store = MagicMock()
    result = MagicMock()
    result.result_set = [[fp] for fp in file_paths]
    store.query.return_value = result
    return store


class TestWorkPartitionerPartition:
    def test_returns_n_partitions(self):
        from navegador.cluster.partitioning import WorkPartitioner

        store = _mock_store_with_files(["a.py", "b.py", "c.py", "d.py"])
        wp = WorkPartitioner(store)

        partitions = wp.partition(2)

        assert len(partitions) == 2

    def test_agent_ids_are_sequential(self):
        from navegador.cluster.partitioning import WorkPartitioner

        store = _mock_store_with_files(["a.py", "b.py", "c.py"])
        wp = WorkPartitioner(store)

        partitions = wp.partition(3)

        assert [p.agent_id for p in partitions] == ["agent-0", "agent-1", "agent-2"]

    def test_all_files_are_covered(self):
        from navegador.cluster.partitioning import WorkPartitioner

        files = ["a.py", "b.py", "c.py", "d.py", "e.py"]
        store = _mock_store_with_files(files)
        wp = WorkPartitioner(store)

        partitions = wp.partition(2)

        covered = [fp for p in partitions for fp in p.file_paths]
        assert sorted(covered) == sorted(files)

    def test_no_file_appears_twice(self):
        from navegador.cluster.partitioning import WorkPartitioner

        files = ["a.py", "b.py", "c.py", "d.py"]
        store = _mock_store_with_files(files)
        wp = WorkPartitioner(store)

        partitions = wp.partition(3)
        covered = [fp for p in partitions for fp in p.file_paths]

        assert len(covered) == len(set(covered))

    def test_estimated_work_equals_file_count(self):
        from navegador.cluster.partitioning import WorkPartitioner

        store = _mock_store_with_files(["a.py", "b.py", "c.py"])
        wp = WorkPartitioner(store)

        for p in wp.partition(3):
            assert p.estimated_work == len(p.file_paths)

    def test_empty_graph_produces_empty_partitions(self):
        from navegador.cluster.partitioning import WorkPartitioner

        store = MagicMock()
        result = MagicMock()
        result.result_set = []
        store.query.return_value = result
        wp = WorkPartitioner(store)

        partitions = wp.partition(3)

        assert len(partitions) == 3
        assert all(p.file_paths == [] for p in partitions)

    def test_more_agents_than_files(self):
        from navegador.cluster.partitioning import WorkPartitioner

        store = _mock_store_with_files(["only.py"])
        wp = WorkPartitioner(store)

        partitions = wp.partition(5)

        assert len(partitions) == 5
        non_empty = [p for p in partitions if p.file_paths]
        assert len(non_empty) == 1

    def test_raises_for_zero_agents(self):
        from navegador.cluster.partitioning import WorkPartitioner

        store = _mock_store_with_files(["a.py"])
        wp = WorkPartitioner(store)

        with pytest.raises(ValueError, match="n_agents"):
            wp.partition(0)

    def test_partition_to_dict(self):
        from navegador.cluster.partitioning import Partition

        p = Partition(agent_id="agent-0", file_paths=["x.py"], estimated_work=1)
        d = p.to_dict()
        assert d["agent_id"] == "agent-0"
        assert d["file_paths"] == ["x.py"]
        assert d["estimated_work"] == 1

    def test_single_agent_gets_all_files(self):
        from navegador.cluster.partitioning import WorkPartitioner

        files = ["a.py", "b.py", "c.py"]
        store = _mock_store_with_files(files)
        wp = WorkPartitioner(store)

        partitions = wp.partition(1)

        assert len(partitions) == 1
        assert sorted(partitions[0].file_paths) == sorted(files)


# ===========================================================================
# #48 — SessionManager
# ===========================================================================

class TestSessionManagerCreate:
    def test_returns_session_id_string(self):
        from navegador.cluster.sessions import SessionManager

        r, pipe = _make_redis_mock()
        r.hget.return_value = None
        mgr = SessionManager("redis://localhost:6379", redis_client=r)

        session_id = mgr.create_session("main", "agent-0")

        assert isinstance(session_id, str)
        assert len(session_id) > 0

    def test_saves_to_redis(self):
        from navegador.cluster.sessions import SessionManager

        r, pipe = _make_redis_mock()
        mgr = SessionManager("redis://localhost:6379", redis_client=r)

        mgr.create_session("feature/foo", "agent-1")

        pipe.hset.assert_called_once()
        pipe.sadd.assert_called_once()

    def test_session_data_contains_branch_and_agent(self):
        from navegador.cluster.sessions import SessionManager

        r, pipe = _make_redis_mock()
        mgr = SessionManager("redis://localhost:6379", redis_client=r)

        session_id = mgr.create_session("release/1.0", "agent-2")

        # Retrieve the JSON that was saved
        hset_call = pipe.hset.call_args
        saved_json = hset_call[1]["mapping"] if "mapping" in hset_call[1] else hset_call[0][2]
        # hset(key, field, value) — value is the JSON string
        if isinstance(saved_json, dict):
            # Called as hset(key, field, value) positionally — find the JSON value
            args = hset_call[0]
            saved_json = args[2] if len(args) >= 3 else list(hset_call[1].values())[-1]
        data = json.loads(saved_json)

        assert data["branch"] == "release/1.0"
        assert data["agent_id"] == "agent-2"
        assert data["session_id"] == session_id
        assert data["status"] == "active"

    def test_two_sessions_have_different_ids(self):
        from navegador.cluster.sessions import SessionManager

        r, _ = _make_redis_mock()
        mgr = SessionManager("redis://localhost:6379", redis_client=r)

        id1 = mgr.create_session("main", "agent-0")
        id2 = mgr.create_session("main", "agent-1")

        assert id1 != id2


class TestSessionManagerGet:
    def _setup_get(self, session_id: str, branch: str = "main", agent_id: str = "agent-0"):
        from navegador.cluster.sessions import _graph_name_from_session_id

        r, _ = _make_redis_mock()
        data = {
            "session_id": session_id,
            "branch": branch,
            "agent_id": agent_id,
            "graph_name": _graph_name_from_session_id(session_id),
            "created_at": time.time(),
            "status": "active",
        }
        r.hget.return_value = json.dumps(data).encode()
        return r, data

    def test_returns_session_dict(self):
        from navegador.cluster.sessions import SessionManager

        r, data = self._setup_get("sess-001")
        mgr = SessionManager("redis://localhost:6379", redis_client=r)

        result = mgr.get_session("sess-001")

        assert result["session_id"] == "sess-001"
        assert result["branch"] == "main"

    def test_raises_key_error_for_missing_session(self):
        from navegador.cluster.sessions import SessionManager

        r, _ = _make_redis_mock()
        r.hget.return_value = None
        mgr = SessionManager("redis://localhost:6379", redis_client=r)

        with pytest.raises(KeyError, match="not found"):
            mgr.get_session("does-not-exist")


class TestSessionManagerList:
    def test_returns_list_of_sessions(self):
        from navegador.cluster.sessions import SessionManager, _graph_name_from_session_id

        r, _ = _make_redis_mock()
        ids = ["sess-a", "sess-b"]
        r.smembers.return_value = {sid.encode() for sid in ids}

        def _hget_side(key, field):
            sid = field.decode() if isinstance(field, bytes) else field
            return json.dumps({
                "session_id": sid,
                "branch": "main",
                "agent_id": "agent-0",
                "graph_name": _graph_name_from_session_id(sid),
                "created_at": 0.0,
                "status": "active",
            }).encode()

        r.hget.side_effect = _hget_side
        mgr = SessionManager("redis://localhost:6379", redis_client=r)

        sessions = mgr.list_sessions()

        assert len(sessions) == 2
        session_ids = {s["session_id"] for s in sessions}
        assert session_ids == set(ids)

    def test_empty_list_when_no_sessions(self):
        from navegador.cluster.sessions import SessionManager

        r, _ = _make_redis_mock()
        r.smembers.return_value = set()
        mgr = SessionManager("redis://localhost:6379", redis_client=r)

        assert mgr.list_sessions() == []


class TestSessionManagerEnd:
    def test_end_session_updates_status_to_ended(self):
        from navegador.cluster.sessions import SessionManager, _graph_name_from_session_id

        r, pipe = _make_redis_mock()
        session_id = "sess-end-me"
        existing = {
            "session_id": session_id,
            "branch": "main",
            "agent_id": "agent-0",
            "graph_name": _graph_name_from_session_id(session_id),
            "created_at": time.time(),
            "status": "active",
        }
        r.hget.return_value = json.dumps(existing).encode()
        mgr = SessionManager("redis://localhost:6379", redis_client=r)

        mgr.end_session(session_id)

        # The second hset call (via _save_session after end) should contain "ended"
        saved_json = pipe.hset.call_args[0][2]
        updated = json.loads(saved_json)
        assert updated["status"] == "ended"
        assert "ended_at" in updated

    def test_end_nonexistent_session_raises_key_error(self):
        from navegador.cluster.sessions import SessionManager

        r, _ = _make_redis_mock()
        r.hget.return_value = None
        mgr = SessionManager("redis://localhost:6379", redis_client=r)

        with pytest.raises(KeyError):
            mgr.end_session("ghost-session")


class TestSessionManagerGraphName:
    def test_graph_name_is_namespaced(self):
        from navegador.cluster.sessions import SessionManager, _graph_name_from_session_id

        r, _ = _make_redis_mock()
        session_id = "my-session-123"
        data = {
            "session_id": session_id,
            "branch": "dev",
            "agent_id": "a",
            "graph_name": _graph_name_from_session_id(session_id),
            "created_at": 0.0,
            "status": "active",
        }
        r.hget.return_value = json.dumps(data).encode()
        mgr = SessionManager("redis://localhost:6379", redis_client=r)

        name = mgr.session_graph_name(session_id)

        assert name.startswith("navegador:sess:")

    def test_graph_name_is_deterministic(self):
        from navegador.cluster.sessions import _graph_name_from_session_id

        sid = "fixed-id"
        assert _graph_name_from_session_id(sid) == _graph_name_from_session_id(sid)

    def test_different_sessions_have_different_graph_names(self):
        from navegador.cluster.sessions import _graph_name_from_session_id

        assert _graph_name_from_session_id("a") != _graph_name_from_session_id("b")


# ===========================================================================
# __init__ re-exports
# ===========================================================================

class TestClusterInit:
    def test_all_public_symbols_importable(self):
        from navegador.cluster import (
            ClusterManager,
            EventType,
            GraphNotifier,
            Partition,
            SessionManager,
            Task,
            TaskQueue,
            TaskStatus,
            WorkPartitioner,
        )

        assert ClusterManager is not None
        assert EventType is not None
        assert GraphNotifier is not None
        assert Partition is not None
        assert SessionManager is not None
        assert Task is not None
        assert TaskQueue is not None
        assert TaskStatus is not None
        assert WorkPartitioner is not None
