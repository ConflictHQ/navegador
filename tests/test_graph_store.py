"""Tests for navegador.graph.store.GraphStore."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from navegador.graph.store import GraphStore


def _mock_client():
    """Create a mock FalkorDB client."""
    client = MagicMock()
    graph = MagicMock()
    graph.query.return_value = MagicMock(result_set=None)
    client.select_graph.return_value = graph
    return client, graph


# ── Constructor ───────────────────────────────────────────────────────────────

class TestGraphStoreInit:
    def test_calls_select_graph(self):
        client, graph = _mock_client()
        GraphStore(client)
        client.select_graph.assert_called_once_with(GraphStore.GRAPH_NAME)

    def test_stores_graph(self):
        client, graph = _mock_client()
        store = GraphStore(client)
        assert store._graph is graph

    def test_graph_name_constant(self):
        assert GraphStore.GRAPH_NAME == "navegador"


# ── sqlite() classmethod ──────────────────────────────────────────────────────

class TestSqliteConstructor:
    def test_creates_db_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "sub" / "graph.db"
            mock_client = MagicMock()
            mock_graph = MagicMock()
            mock_graph.query.return_value = MagicMock(result_set=None)
            mock_client.select_graph.return_value = mock_graph
            mock_falkordb = MagicMock(return_value=mock_client)

            with patch.dict("sys.modules", {"redislite": MagicMock(FalkorDB=mock_falkordb)}):
                store = GraphStore.sqlite(str(db_path))
                assert isinstance(store, GraphStore)
                mock_falkordb.assert_called_once_with(str(db_path))

    def test_raises_import_error_if_not_installed(self):
        with patch.dict("sys.modules", {"redislite": None}):
            with pytest.raises(ImportError, match="falkordblite"):
                GraphStore.sqlite("/tmp/test.db")


# ── redis() classmethod ───────────────────────────────────────────────────────

class TestRedisConstructor:
    def test_creates_redis_store(self):
        mock_client = MagicMock()
        mock_graph = MagicMock()
        mock_graph.query.return_value = MagicMock(result_set=None)
        mock_client.select_graph.return_value = mock_graph

        mock_falkordb_module = MagicMock()
        mock_falkordb_module.FalkorDB.from_url.return_value = mock_client

        with patch.dict("sys.modules", {"falkordb": mock_falkordb_module}):
            store = GraphStore.redis("redis://localhost:6379")
            assert isinstance(store, GraphStore)
            mock_falkordb_module.FalkorDB.from_url.assert_called_once_with("redis://localhost:6379")

    def test_raises_import_error_if_not_installed(self):
        with patch.dict("sys.modules", {"falkordb": None}):
            with pytest.raises(ImportError, match="falkordb"):
                GraphStore.redis("redis://localhost:6379")


# ── query() ───────────────────────────────────────────────────────────────────

class TestQuery:
    def test_delegates_to_graph(self):
        client, graph = _mock_client()
        graph.query.return_value = MagicMock(result_set=[["a", "b"]])
        store = GraphStore(client)
        result = store.query("MATCH (n) RETURN n", {"x": 1})
        graph.query.assert_called_once_with("MATCH (n) RETURN n", {"x": 1})
        assert result.result_set == [["a", "b"]]

    def test_passes_empty_dict_when_no_params(self):
        client, graph = _mock_client()
        store = GraphStore(client)
        store.query("MATCH (n) RETURN n")
        graph.query.assert_called_once_with("MATCH (n) RETURN n", {})


# ── create_node() ─────────────────────────────────────────────────────────────

class TestCreateNode:
    def test_generates_merge_cypher(self):
        client, graph = _mock_client()
        store = GraphStore(client)
        store.create_node("Function", {"name": "foo", "file_path": "a.py", "docstring": "doc"})
        call_args = graph.query.call_args
        cypher = call_args[0][0]
        assert "MERGE" in cypher
        assert "Function" in cypher
        assert "name" in cypher
        assert "file_path" in cypher

    def test_passes_props_as_params(self):
        client, graph = _mock_client()
        store = GraphStore(client)
        props = {"name": "bar", "file_path": "b.py"}
        store.create_node("Class", props)
        call_params = graph.query.call_args[0][1]
        assert call_params["name"] == "bar"
        assert call_params["file_path"] == "b.py"


# ── create_edge() ─────────────────────────────────────────────────────────────

class TestCreateEdge:
    def test_generates_match_merge_cypher(self):
        client, graph = _mock_client()
        store = GraphStore(client)
        store.create_edge(
            "Function", {"name": "foo"},
            "CALLS",
            "Function", {"name": "bar"},
        )
        call_args = graph.query.call_args
        cypher = call_args[0][0]
        assert "MATCH" in cypher
        assert "MERGE" in cypher
        assert "CALLS" in cypher

    def test_passes_from_and_to_params(self):
        client, graph = _mock_client()
        store = GraphStore(client)
        store.create_edge(
            "Function", {"name": "foo"},
            "CALLS",
            "Function", {"name": "bar"},
        )
        params = graph.query.call_args[0][1]
        assert params["from_name"] == "foo"
        assert params["to_name"] == "bar"

    def test_includes_edge_props_when_provided(self):
        client, graph = _mock_client()
        store = GraphStore(client)
        store.create_edge(
            "Class", {"name": "A"},
            "INHERITS",
            "Class", {"name": "B"},
            props={"weight": 1},
        )
        call_args = graph.query.call_args
        cypher = call_args[0][0]
        params = call_args[0][1]
        assert "SET" in cypher
        assert params["p_weight"] == 1

    def test_no_set_clause_without_props(self):
        client, graph = _mock_client()
        store = GraphStore(client)
        store.create_edge("A", {"name": "x"}, "REL", "B", {"name": "y"})
        cypher = graph.query.call_args[0][0]
        assert "SET" not in cypher


# ── clear() ───────────────────────────────────────────────────────────────────

class TestClear:
    def test_executes_delete_query(self):
        client, graph = _mock_client()
        store = GraphStore(client)
        store.clear()
        cypher = graph.query.call_args[0][0]
        assert "DETACH DELETE" in cypher


# ── node_count / edge_count ───────────────────────────────────────────────────

class TestCounts:
    def test_node_count_returns_value(self):
        client, graph = _mock_client()
        graph.query.return_value = MagicMock(result_set=[[42]])
        store = GraphStore(client)
        assert store.node_count() == 42

    def test_node_count_returns_zero_on_empty(self):
        client, graph = _mock_client()
        graph.query.return_value = MagicMock(result_set=[])
        store = GraphStore(client)
        assert store.node_count() == 0

    def test_edge_count_returns_value(self):
        client, graph = _mock_client()
        graph.query.return_value = MagicMock(result_set=[[7]])
        store = GraphStore(client)
        assert store.edge_count() == 7

    def test_edge_count_returns_zero_on_empty(self):
        client, graph = _mock_client()
        graph.query.return_value = MagicMock(result_set=[])
        store = GraphStore(client)
        assert store.edge_count() == 0
