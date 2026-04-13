"""Tests for navegador.mcp.server — create_mcp_server and all tool handlers."""

import json
from unittest.mock import MagicMock, patch

import pytest

from navegador.context.loader import ContextBundle, ContextNode

# ── Helpers ───────────────────────────────────────────────────────────────────

def _node(name="foo", type_="Function", file_path="app.py"):
    return ContextNode(name=name, type=type_, file_path=file_path)


def _bundle(name="target"):
    return ContextBundle(target=_node(name), nodes=[])


def _mock_store():
    store = MagicMock()
    store.query.return_value = MagicMock(result_set=[])
    store.node_count.return_value = 5
    store.edge_count.return_value = 3
    return store


class _ServerFixture:
    """
    Builds a navegador MCP server with mocked mcp module and captures the
    list_tools and call_tool async handlers so they can be invoked directly.
    """

    def __init__(self, loader=None):
        self.store = _mock_store()
        self.loader = loader or self._default_loader()
        self.list_tools_fn = None
        self.call_tool_fn = None
        self._build()

    def _default_loader(self):
        from navegador.context import ContextLoader
        loader = MagicMock(spec=ContextLoader)
        loader.store = self.store
        loader.load_file.return_value = _bundle("file_target")
        loader.load_function.return_value = _bundle("fn_target")
        loader.load_class.return_value = _bundle("cls_target")
        loader.load_decision.return_value = _bundle("decision_target")
        loader.search.return_value = []
        loader.find_owners.return_value = []
        loader.search_knowledge.return_value = []
        return loader

    def _build(self):
        list_holder = {}
        call_holder = {}

        def list_tools_decorator():
            def decorator(fn):
                list_holder["fn"] = fn
                return fn
            return decorator

        def call_tool_decorator():
            def decorator(fn):
                call_holder["fn"] = fn
                return fn
            return decorator

        mock_server = MagicMock()
        mock_server.list_tools = list_tools_decorator
        mock_server.call_tool = call_tool_decorator

        mock_mcp_server = MagicMock()
        mock_mcp_server.Server.return_value = mock_server

        mock_mcp_types = MagicMock()
        mock_mcp_types.Tool = dict          # Tool(...) → dict so we can inspect fields
        mock_mcp_types.TextContent = dict   # TextContent(type=..., text=...) → dict

        with patch.dict("sys.modules", {
            "mcp": MagicMock(),
            "mcp.server": mock_mcp_server,
            "mcp.types": mock_mcp_types,
        }), patch("navegador.context.ContextLoader", return_value=self.loader):
            from importlib import reload

            import navegador.mcp.server as srv
            reload(srv)
            self.server = srv.create_mcp_server(lambda: self.store)

        self.list_tools_fn = list_holder["fn"]
        self.call_tool_fn = call_holder["fn"]


# ── Import guard ──────────────────────────────────────────────────────────────

class TestCreateMcpServerImport:
    def test_raises_import_error_if_mcp_not_installed(self):
        with patch.dict("sys.modules", {"mcp": None, "mcp.server": None, "mcp.types": None}):
            from importlib import reload

            import navegador.mcp.server as srv
            reload(srv)
            with pytest.raises(ImportError, match="mcp"):
                srv.create_mcp_server(lambda: _mock_store())


# ── list_tools ────────────────────────────────────────────────────────────────

class TestListTools:
    def setup_method(self):
        self.fx = _ServerFixture()

    @pytest.mark.asyncio
    async def test_returns_twenty_tools(self):
        tools = await self.fx.list_tools_fn()
        assert len(tools) == 20

    @pytest.mark.asyncio
    async def test_tool_names(self):
        tools = await self.fx.list_tools_fn()
        names = {t["name"] for t in tools}
        assert names == {
            "ingest_repo",
            "load_file_context",
            "load_function_context",
            "load_class_context",
            "search_symbols",
            "query_graph",
            "graph_stats",
            "get_rationale",
            "find_owners",
            "search_knowledge",
            "blast_radius",
            "memory_list",
            "memory_get",
            "memory_for_file",
            "build_task_pack",
            "blast_radius_cross_repo",
            "drift_check",
            "diff_graph",
            "symbol_history",
            "suggest_doc_links",
        }

    @pytest.mark.asyncio
    async def test_ingest_repo_requires_path(self):
        tools = await self.fx.list_tools_fn()
        t = next(t for t in tools if t["name"] == "ingest_repo")
        assert "path" in t["inputSchema"]["required"]

    @pytest.mark.asyncio
    async def test_load_function_context_requires_name_and_file_path(self):
        tools = await self.fx.list_tools_fn()
        t = next(t for t in tools if t["name"] == "load_function_context")
        assert "name" in t["inputSchema"]["required"]
        assert "file_path" in t["inputSchema"]["required"]


# ── call_tool — ingest_repo ───────────────────────────────────────────────────

class TestCallToolIngestRepo:
    def setup_method(self):
        self.fx = _ServerFixture()

    @pytest.mark.asyncio
    async def test_calls_ingester_and_returns_json(self):
        mock_ingester = MagicMock()
        mock_ingester.ingest.return_value = {"files": 3, "functions": 10, "classes": 2, "edges": 15}

        with patch("navegador.ingestion.RepoIngester", return_value=mock_ingester):
            result = await self.fx.call_tool_fn("ingest_repo", {"path": "/some/repo"})

        assert len(result) == 1
        data = json.loads(result[0]["text"])
        assert data["files"] == 3
        assert data["functions"] == 10

    @pytest.mark.asyncio
    async def test_passes_clear_flag(self):
        mock_ingester = MagicMock()
        mock_ingester.ingest.return_value = {"files": 0, "functions": 0, "classes": 0, "edges": 0}

        with patch("navegador.ingestion.RepoIngester", return_value=mock_ingester):
            await self.fx.call_tool_fn("ingest_repo", {"path": "/repo", "clear": True})

        mock_ingester.ingest.assert_called_once_with("/repo", clear=True)

    @pytest.mark.asyncio
    async def test_clear_defaults_to_false(self):
        mock_ingester = MagicMock()
        mock_ingester.ingest.return_value = {"files": 0, "functions": 0, "classes": 0, "edges": 0}

        with patch("navegador.ingestion.RepoIngester", return_value=mock_ingester):
            await self.fx.call_tool_fn("ingest_repo", {"path": "/repo"})

        mock_ingester.ingest.assert_called_once_with("/repo", clear=False)


# ── call_tool — load_file_context ─────────────────────────────────────────────

class TestCallToolLoadFileContext:
    def setup_method(self):
        self.fx = _ServerFixture()

    @pytest.mark.asyncio
    async def test_returns_markdown_by_default(self):
        result = await self.fx.call_tool_fn("load_file_context", {"file_path": "src/main.py"})
        self.fx.loader.load_file.assert_called_once_with("src/main.py")
        assert "file_target" in result[0]["text"]

    @pytest.mark.asyncio
    async def test_returns_json_when_requested(self):
        result = await self.fx.call_tool_fn(
            "load_file_context", {"file_path": "src/main.py", "format": "json"}
        )
        data = json.loads(result[0]["text"])
        assert data["target"]["name"] == "file_target"

    @pytest.mark.asyncio
    async def test_markdown_format_explicit(self):
        result = await self.fx.call_tool_fn(
            "load_file_context", {"file_path": "src/main.py", "format": "markdown"}
        )
        assert "file_target" in result[0]["text"]


# ── call_tool — load_function_context ────────────────────────────────────────

class TestCallToolLoadFunctionContext:
    def setup_method(self):
        self.fx = _ServerFixture()

    @pytest.mark.asyncio
    async def test_calls_loader_with_depth(self):
        await self.fx.call_tool_fn(
            "load_function_context",
            {"name": "parse", "file_path": "parser.py", "depth": 3},
        )
        self.fx.loader.load_function.assert_called_once_with("parse", "parser.py", depth=3)

    @pytest.mark.asyncio
    async def test_depth_defaults_to_two(self):
        await self.fx.call_tool_fn(
            "load_function_context", {"name": "parse", "file_path": "parser.py"}
        )
        self.fx.loader.load_function.assert_called_once_with("parse", "parser.py", depth=2)

    @pytest.mark.asyncio
    async def test_returns_json_when_requested(self):
        result = await self.fx.call_tool_fn(
            "load_function_context",
            {"name": "parse", "file_path": "parser.py", "format": "json"},
        )
        data = json.loads(result[0]["text"])
        assert data["target"]["name"] == "fn_target"


# ── call_tool — load_class_context ───────────────────────────────────────────

class TestCallToolLoadClassContext:
    def setup_method(self):
        self.fx = _ServerFixture()

    @pytest.mark.asyncio
    async def test_calls_loader_with_name_and_file(self):
        await self.fx.call_tool_fn(
            "load_class_context", {"name": "AuthService", "file_path": "auth.py"}
        )
        self.fx.loader.load_class.assert_called_once_with("AuthService", "auth.py")

    @pytest.mark.asyncio
    async def test_returns_markdown_by_default(self):
        result = await self.fx.call_tool_fn(
            "load_class_context", {"name": "AuthService", "file_path": "auth.py"}
        )
        assert "cls_target" in result[0]["text"]

    @pytest.mark.asyncio
    async def test_returns_json_when_requested(self):
        result = await self.fx.call_tool_fn(
            "load_class_context",
            {"name": "AuthService", "file_path": "auth.py", "format": "json"},
        )
        data = json.loads(result[0]["text"])
        assert data["target"]["name"] == "cls_target"


# ── call_tool — search_symbols ───────────────────────────────────────────────

class TestCallToolSearchSymbols:
    def setup_method(self):
        self.fx = _ServerFixture()

    @pytest.mark.asyncio
    async def test_returns_no_results_message_on_empty(self):
        result = await self.fx.call_tool_fn("search_symbols", {"query": "xyz"})
        assert result[0]["text"] == "No results."

    @pytest.mark.asyncio
    async def test_formats_results_as_bullet_list(self):
        hit = ContextNode(name="do_thing", type="Function", file_path="utils.py", line_start=10)
        self.fx.loader.search.return_value = [hit]
        result = await self.fx.call_tool_fn("search_symbols", {"query": "do"})
        text = result[0]["text"]
        assert "do_thing" in text
        assert "utils.py" in text
        assert "Function" in text

    @pytest.mark.asyncio
    async def test_passes_limit(self):
        await self.fx.call_tool_fn("search_symbols", {"query": "foo", "limit": 5})
        self.fx.loader.search.assert_called_once_with("foo", limit=5)

    @pytest.mark.asyncio
    async def test_limit_defaults_to_twenty(self):
        await self.fx.call_tool_fn("search_symbols", {"query": "foo"})
        self.fx.loader.search.assert_called_once_with("foo", limit=20)


# ── call_tool — query_graph ───────────────────────────────────────────────────

class TestCallToolQueryGraph:
    def setup_method(self):
        self.fx = _ServerFixture()

    @pytest.mark.asyncio
    async def test_executes_cypher_and_returns_json(self):
        self.fx.store.query.return_value = MagicMock(result_set=[["node_a"], ["node_b"]])
        result = await self.fx.call_tool_fn("query_graph", {"cypher": "MATCH (n) RETURN n LIMIT 10"})
        self.fx.store.query.assert_called_once_with("MATCH (n) RETURN n LIMIT 10")
        data = json.loads(result[0]["text"])
        assert len(data) == 2

    @pytest.mark.asyncio
    async def test_empty_result_set(self):
        self.fx.store.query.return_value = MagicMock(result_set=[])
        result = await self.fx.call_tool_fn("query_graph", {"cypher": "MATCH (n) RETURN n LIMIT 10"})
        data = json.loads(result[0]["text"])
        assert data == []


# ── call_tool — graph_stats ───────────────────────────────────────────────────

class TestCallToolGraphStats:
    def setup_method(self):
        self.fx = _ServerFixture()

    @pytest.mark.asyncio
    async def test_returns_node_and_edge_counts(self):
        self.fx.store.node_count.return_value = 42
        self.fx.store.edge_count.return_value = 17
        result = await self.fx.call_tool_fn("graph_stats", {})
        data = json.loads(result[0]["text"])
        assert data["nodes"] == 42
        assert data["edges"] == 17


# ── call_tool — unknown tool ──────────────────────────────────────────────────

# ── call_tool — get_rationale ────────────────────────────────────────────────

class TestCallToolGetRationale:
    def setup_method(self):
        self.fx = _ServerFixture()

    @pytest.mark.asyncio
    async def test_returns_markdown_by_default(self):
        result = await self.fx.call_tool_fn("get_rationale", {"name": "Use FalkorDB"})
        self.fx.loader.load_decision.assert_called_once_with("Use FalkorDB")
        assert "decision_target" in result[0]["text"]

    @pytest.mark.asyncio
    async def test_returns_json_when_requested(self):
        result = await self.fx.call_tool_fn(
            "get_rationale", {"name": "Use FalkorDB", "format": "json"}
        )
        data = json.loads(result[0]["text"])
        assert data["target"]["name"] == "decision_target"


# ── call_tool — find_owners ──────────────────────────────────────────────────

class TestCallToolFindOwners:
    def setup_method(self):
        self.fx = _ServerFixture()

    @pytest.mark.asyncio
    async def test_returns_no_owners_message(self):
        result = await self.fx.call_tool_fn("find_owners", {"name": "AuthService"})
        assert result[0]["text"] == "No owners found."

    @pytest.mark.asyncio
    async def test_formats_owners(self):
        owner = ContextNode(name="Alice", type="Person", description="role=lead, team=auth")
        self.fx.loader.find_owners.return_value = [owner]
        result = await self.fx.call_tool_fn("find_owners", {"name": "AuthService"})
        assert "Alice" in result[0]["text"]
        assert "role=lead" in result[0]["text"]

    @pytest.mark.asyncio
    async def test_passes_file_path(self):
        await self.fx.call_tool_fn(
            "find_owners", {"name": "AuthService", "file_path": "auth.py"}
        )
        self.fx.loader.find_owners.assert_called_once_with("AuthService", file_path="auth.py")


# ── call_tool — search_knowledge ─────────────────────────────────────────────

class TestCallToolSearchKnowledge:
    def setup_method(self):
        self.fx = _ServerFixture()

    @pytest.mark.asyncio
    async def test_returns_no_results_message(self):
        result = await self.fx.call_tool_fn("search_knowledge", {"query": "xyz"})
        assert result[0]["text"] == "No results."

    @pytest.mark.asyncio
    async def test_formats_results(self):
        hit = ContextNode(name="JWT", type="Concept", description="Stateless auth token")
        self.fx.loader.search_knowledge.return_value = [hit]
        result = await self.fx.call_tool_fn("search_knowledge", {"query": "JWT"})
        assert "JWT" in result[0]["text"]
        assert "Concept" in result[0]["text"]

    @pytest.mark.asyncio
    async def test_passes_limit(self):
        await self.fx.call_tool_fn("search_knowledge", {"query": "auth", "limit": 5})
        self.fx.loader.search_knowledge.assert_called_once_with("auth", limit=5)

    @pytest.mark.asyncio
    async def test_limit_defaults_to_twenty(self):
        await self.fx.call_tool_fn("search_knowledge", {"query": "auth"})
        self.fx.loader.search_knowledge.assert_called_once_with("auth", limit=20)


# ── call_tool — unknown tool ──────────────────────────────────────────────────

class TestCallToolUnknown:
    def setup_method(self):
        self.fx = _ServerFixture()

    @pytest.mark.asyncio
    async def test_returns_unknown_tool_message(self):
        result = await self.fx.call_tool_fn("nonexistent_tool", {})
        assert "Unknown tool" in result[0]["text"]
        assert "nonexistent_tool" in result[0]["text"]
