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
        mock_mcp_types.Tool = dict  # Tool(...) → dict so we can inspect fields
        mock_mcp_types.TextContent = dict  # TextContent(type=..., text=...) → dict

        with (
            patch.dict(
                "sys.modules",
                {
                    "mcp": MagicMock(),
                    "mcp.server": mock_mcp_server,
                    "mcp.types": mock_mcp_types,
                },
            ),
            patch("navegador.context.ContextLoader", return_value=self.loader),
        ):
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
    async def test_returns_twenty_three_tools(self):
        tools = await self.fx.list_tools_fn()
        assert len(tools) == 23

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
            "review_diff",
            "release_check",
            "apply_lens",
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
        cypher = "MATCH (n) RETURN n LIMIT 10"
        result = await self.fx.call_tool_fn("query_graph", {"cypher": cypher})
        self.fx.store.query.assert_called_once_with(cypher)
        data = json.loads(result[0]["text"])
        assert len(data) == 2

    @pytest.mark.asyncio
    async def test_empty_result_set(self):
        self.fx.store.query.return_value = MagicMock(result_set=[])
        result = await self.fx.call_tool_fn(
            "query_graph", {"cypher": "MATCH (n) RETURN n LIMIT 10"}
        )
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
        await self.fx.call_tool_fn("find_owners", {"name": "AuthService", "file_path": "auth.py"})
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


# ── call_tool — new handlers (lines 647–896) ────────────────────────────────


class TestCallToolBlastRadius:
    """blast_radius handler — ImpactAnalyzer.blast_radius()."""

    def setup_method(self):
        self.fx = _ServerFixture()

    @pytest.mark.asyncio
    async def test_returns_json_with_affected_data(self):
        from navegador.analysis.impact import ImpactResult

        mock_result = ImpactResult(
            name="foo",
            file_path="bar.py",
            depth=2,
            depth_reached=2,
            affected_nodes=[{"type": "Function", "name": "baz", "file_path": "baz.py"}],
            affected_files=["bar.py", "baz.py"],
        )
        mock_analyzer = MagicMock()
        mock_analyzer.blast_radius.return_value = mock_result

        with patch("navegador.analysis.impact.ImpactAnalyzer", return_value=mock_analyzer):
            result = await self.fx.call_tool_fn(
                "blast_radius", {"name": "foo", "file_path": "bar.py", "depth": 2}
            )

        mock_analyzer.blast_radius.assert_called_once_with(
            "foo", file_path="bar.py", depth=2
        )
        data = json.loads(result[0]["text"])
        assert data["name"] == "foo"
        assert "baz.py" in data["affected_files"]
        assert len(data["affected_nodes"]) == 1

    @pytest.mark.asyncio
    async def test_defaults_depth_to_three_and_file_path_to_empty(self):
        from navegador.analysis.impact import ImpactResult

        mock_result = ImpactResult(name="x", file_path="", depth=3)
        mock_analyzer = MagicMock()
        mock_analyzer.blast_radius.return_value = mock_result

        with patch("navegador.analysis.impact.ImpactAnalyzer", return_value=mock_analyzer):
            await self.fx.call_tool_fn("blast_radius", {"name": "x"})

        mock_analyzer.blast_radius.assert_called_once_with("x", file_path="", depth=3)


class TestCallToolMemoryList:
    """memory_list handler — raw Cypher MEMORY_LIST query."""

    def setup_method(self):
        self.fx = _ServerFixture()

    @pytest.mark.asyncio
    async def test_returns_memory_items_as_json(self):
        self.fx.store.query.return_value = MagicMock(
            result_set=[
                ["feedback", "my-rule", "domain1", "rule", "repo1", "content here"],
            ]
        )
        result = await self.fx.call_tool_fn("memory_list", {})
        data = json.loads(result[0]["text"])
        assert len(data) == 1
        assert data[0]["name"] == "my-rule"
        assert data[0]["content"] == "content here"
        assert data[0]["memory_type"] == "rule"

    @pytest.mark.asyncio
    async def test_empty_result_returns_no_memory_message(self):
        self.fx.store.query.return_value = MagicMock(result_set=[])
        result = await self.fx.call_tool_fn("memory_list", {})
        assert "No memory nodes found" in result[0]["text"]

    @pytest.mark.asyncio
    async def test_passes_scope_and_type_params(self):
        self.fx.store.query.return_value = MagicMock(result_set=[])
        await self.fx.call_tool_fn(
            "memory_list",
            {"type": "feedback", "scope": "workspace", "repo": "myrepo", "limit": 10},
        )
        call_args = self.fx.store.query.call_args
        params = call_args[0][1]
        assert params["type"] == "feedback"
        assert params["scope"] == "workspace"
        assert params["repo"] == "myrepo"
        assert params["limit"] == 10


class TestCallToolMemoryGet:
    """memory_get handler — single memory lookup."""

    def setup_method(self):
        self.fx = _ServerFixture()

    @pytest.mark.asyncio
    async def test_returns_memory_node_json(self):
        self.fx.store.query.return_value = MagicMock(
            result_set=[
                ["Rule", "my-rule", "a desc", "feedback", "repo1", "rule body"],
            ]
        )
        result = await self.fx.call_tool_fn("memory_get", {"name": "my-rule"})
        data = json.loads(result[0]["text"])
        assert data["name"] == "my-rule"
        assert data["label"] == "Rule"
        assert data["content"] == "rule body"

    @pytest.mark.asyncio
    async def test_not_found_returns_message(self):
        self.fx.store.query.return_value = MagicMock(result_set=[])
        result = await self.fx.call_tool_fn("memory_get", {"name": "missing"})
        assert "No memory node found" in result[0]["text"]
        assert "missing" in result[0]["text"]


class TestCallToolMemoryForFile:
    """memory_for_file handler — memories linked to a file's symbols."""

    def setup_method(self):
        self.fx = _ServerFixture()

    @pytest.mark.asyncio
    async def test_returns_linked_memories(self):
        self.fx.store.query.return_value = MagicMock(
            result_set=[
                ["Rule", "auth-rule", "enforce auth", "feedback", "repo1", "always check auth"],
            ]
        )
        result = await self.fx.call_tool_fn("memory_for_file", {"path": "app/auth.py"})
        data = json.loads(result[0]["text"])
        assert len(data) == 1
        assert data[0]["name"] == "auth-rule"
        assert data[0]["content"] == "always check auth"

    @pytest.mark.asyncio
    async def test_no_linked_memories(self):
        self.fx.store.query.return_value = MagicMock(result_set=[])
        result = await self.fx.call_tool_fn("memory_for_file", {"path": "utils.py"})
        assert "No memory nodes linked to this file" in result[0]["text"]


class TestCallToolDiffGraph:
    """diff_graph handler — DiffGraphAnalyzer dispatch."""

    def setup_method(self):
        self.fx = _ServerFixture()

    def _mock_report(self, md_text="# diff report", json_text='{"ok": true}'):
        report = MagicMock()
        report.to_markdown.return_value = md_text
        report.to_json.return_value = json_text
        report.new_symbols = []
        report.changed_symbols = []
        report.affected_files = []
        return report

    @pytest.mark.asyncio
    async def test_defaults_call_diff_working_tree(self):
        """base=HEAD, head=working tree (defaults) dispatches to diff_working_tree."""
        report = self._mock_report(md_text="## working tree diff")
        mock_analyzer = MagicMock()
        mock_analyzer.diff_working_tree.return_value = report

        with patch(
            "navegador.analysis.diffgraph.DiffGraphAnalyzer",
            return_value=mock_analyzer,
        ):
            result = await self.fx.call_tool_fn("diff_graph", {})

        mock_analyzer.diff_working_tree.assert_called_once()
        assert "working tree diff" in result[0]["text"]

    @pytest.mark.asyncio
    async def test_explicit_refs_call_diff_refs(self):
        """Non-default base/head dispatches to diff_refs."""
        report = self._mock_report(json_text='{"base": "main", "head": "HEAD"}')
        mock_analyzer = MagicMock()
        mock_analyzer.diff_refs.return_value = report

        with patch(
            "navegador.analysis.diffgraph.DiffGraphAnalyzer",
            return_value=mock_analyzer,
        ):
            result = await self.fx.call_tool_fn(
                "diff_graph", {"base": "main", "head": "HEAD", "format": "json"}
            )

        mock_analyzer.diff_refs.assert_called_once_with(base="main", head="HEAD")
        data = json.loads(result[0]["text"])
        assert data["base"] == "main"

    @pytest.mark.asyncio
    async def test_snapshot_mode_calls_diff_snapshots(self):
        report = self._mock_report(md_text="## snapshot diff")
        mock_analyzer = MagicMock()
        mock_analyzer.diff_snapshots.return_value = report

        with patch(
            "navegador.analysis.diffgraph.DiffGraphAnalyzer",
            return_value=mock_analyzer,
        ):
            result = await self.fx.call_tool_fn(
                "diff_graph",
                {"base": "v1.0", "head": "v2.0", "snapshot_mode": True},
            )

        mock_analyzer.diff_snapshots.assert_called_once_with(
            base_ref="v1.0", head_ref="v2.0"
        )
        assert "snapshot diff" in result[0]["text"]


class TestCallToolDriftCheck:
    """drift_check handler — DriftChecker.check()."""

    def setup_method(self):
        self.fx = _ServerFixture()

    @pytest.mark.asyncio
    async def test_returns_markdown_by_default(self):
        report = MagicMock()
        report.to_markdown.return_value = "## Drift — 0 violations"
        mock_checker = MagicMock()
        mock_checker.check.return_value = report

        with patch("navegador.analysis.drift.DriftChecker", return_value=mock_checker):
            result = await self.fx.call_tool_fn("drift_check", {})

        mock_checker.check.assert_called_once()
        assert "Drift" in result[0]["text"]

    @pytest.mark.asyncio
    async def test_returns_json_when_requested(self):
        report = MagicMock()
        report.to_json.return_value = '{"checks_run": 5, "violations": []}'
        mock_checker = MagicMock()
        mock_checker.check.return_value = report

        with patch("navegador.analysis.drift.DriftChecker", return_value=mock_checker):
            result = await self.fx.call_tool_fn("drift_check", {"format": "json"})

        data = json.loads(result[0]["text"])
        assert data["checks_run"] == 5


class TestCallToolBlastRadiusCrossRepo:
    """blast_radius_cross_repo handler — CrossRepoImpactAnalyzer."""

    def setup_method(self):
        self.fx = _ServerFixture()

    @pytest.mark.asyncio
    async def test_passes_args_and_returns_markdown(self):
        report = MagicMock()
        report.to_markdown.return_value = "## Cross-repo blast: foo"
        mock_analyzer = MagicMock()
        mock_analyzer.blast_radius.return_value = report

        with patch(
            "navegador.analysis.crossrepo.CrossRepoImpactAnalyzer",
            return_value=mock_analyzer,
        ):
            result = await self.fx.call_tool_fn(
                "blast_radius_cross_repo",
                {"name": "foo", "file_path": "bar.py", "repo": "myrepo", "depth": 4},
            )

        mock_analyzer.blast_radius.assert_called_once_with(
            "foo", file_path="bar.py", repo="myrepo", depth=4
        )
        assert "Cross-repo blast" in result[0]["text"]

    @pytest.mark.asyncio
    async def test_json_format(self):
        report = MagicMock()
        report.to_json.return_value = '{"name": "foo", "affected_repos": ["a"]}'
        mock_analyzer = MagicMock()
        mock_analyzer.blast_radius.return_value = report

        with patch(
            "navegador.analysis.crossrepo.CrossRepoImpactAnalyzer",
            return_value=mock_analyzer,
        ):
            result = await self.fx.call_tool_fn(
                "blast_radius_cross_repo",
                {"name": "foo", "format": "json"},
            )

        data = json.loads(result[0]["text"])
        assert data["name"] == "foo"
        assert "a" in data["affected_repos"]


class TestCallToolBuildTaskPack:
    """build_task_pack handler — TaskPackBuilder dispatch."""

    def setup_method(self):
        self.fx = _ServerFixture()

    def _mock_builder(self, md="## TaskPack", js='{"target_name": "x"}'):
        pack = MagicMock()
        pack.to_markdown.return_value = md
        pack.to_json.return_value = js
        builder = MagicMock()
        builder.for_symbol.return_value = pack
        builder.for_file.return_value = pack
        return builder

    @pytest.mark.asyncio
    async def test_symbol_mode_when_plain_name(self):
        """A plain name (no slash, no file extension) calls for_symbol."""
        builder = self._mock_builder(md="## Pack for AuthService")

        with patch("navegador.taskpack.TaskPackBuilder", return_value=builder):
            result = await self.fx.call_tool_fn(
                "build_task_pack",
                {"target": "AuthService", "file_path": "auth.py", "depth": 3},
            )

        builder.for_symbol.assert_called_once_with(
            "AuthService", file_path="auth.py", depth=3, mode="implement"
        )
        assert "AuthService" in result[0]["text"]

    @pytest.mark.asyncio
    async def test_file_mode_with_slash(self):
        """A target containing '/' routes to for_file."""
        builder = self._mock_builder()

        with patch("navegador.taskpack.TaskPackBuilder", return_value=builder):
            await self.fx.call_tool_fn(
                "build_task_pack", {"target": "app/auth/service.py"}
            )

        builder.for_file.assert_called_once_with(
            "app/auth/service.py", mode="implement"
        )

    @pytest.mark.asyncio
    async def test_file_mode_with_extension(self):
        """A target ending with a known extension routes to for_file."""
        builder = self._mock_builder()

        with patch("navegador.taskpack.TaskPackBuilder", return_value=builder):
            await self.fx.call_tool_fn(
                "build_task_pack", {"target": "service.py"}
            )

        builder.for_file.assert_called_once()

    @pytest.mark.asyncio
    async def test_json_format(self):
        builder = self._mock_builder(js='{"target_name": "x", "mode": "review"}')

        with patch("navegador.taskpack.TaskPackBuilder", return_value=builder):
            result = await self.fx.call_tool_fn(
                "build_task_pack",
                {"target": "MyClass", "format": "json", "mode": "review"},
            )

        builder.for_symbol.assert_called_once_with(
            "MyClass", file_path="", depth=2, mode="review"
        )
        data = json.loads(result[0]["text"])
        assert data["target_name"] == "x"


class TestCallToolSymbolHistory:
    """symbol_history handler — HistoryStore with mode dispatch."""

    def setup_method(self):
        self.fx = _ServerFixture()

    @pytest.mark.asyncio
    async def test_history_mode_default(self):
        report = MagicMock()
        report.to_markdown.return_value = "# History -- foo"
        mock_hs = MagicMock()
        mock_hs.history.return_value = report

        with patch("navegador.history.HistoryStore", return_value=mock_hs):
            result = await self.fx.call_tool_fn(
                "symbol_history", {"name": "foo", "file_path": "bar.py"}
            )

        mock_hs.history.assert_called_once_with("foo", file_path="bar.py")
        assert "History" in result[0]["text"]

    @pytest.mark.asyncio
    async def test_lineage_mode(self):
        report = MagicMock()
        report.to_json.return_value = '{"symbol": "foo", "chain": []}'
        mock_hs = MagicMock()
        mock_hs.lineage.return_value = report

        with patch("navegador.history.HistoryStore", return_value=mock_hs):
            result = await self.fx.call_tool_fn(
                "symbol_history",
                {"name": "foo", "mode": "lineage", "format": "json"},
            )

        mock_hs.lineage.assert_called_once_with("foo", file_path="")
        data = json.loads(result[0]["text"])
        assert data["symbol"] == "foo"

    @pytest.mark.asyncio
    async def test_symbols_at_mode_markdown(self):
        """symbols_at returns a formatted list in markdown."""
        entry = MagicMock()
        entry.label = "Function"
        entry.name = "do_stuff"
        entry.file_path = "stuff.py"
        entry.__dict__ = {"label": "Function", "name": "do_stuff", "file_path": "stuff.py"}
        mock_hs = MagicMock()
        mock_hs.symbols_at.return_value = [entry]

        with patch("navegador.history.HistoryStore", return_value=mock_hs):
            result = await self.fx.call_tool_fn(
                "symbol_history",
                {"name": "irrelevant", "mode": "symbols_at", "ref": "abc123"},
            )

        mock_hs.symbols_at.assert_called_once_with("abc123")
        text = result[0]["text"]
        assert "do_stuff" in text
        assert "Function" in text

    @pytest.mark.asyncio
    async def test_symbols_at_mode_json(self):
        entry = MagicMock()
        entry.__dict__ = {"label": "Class", "name": "Foo", "file_path": "foo.py"}
        mock_hs = MagicMock()
        mock_hs.symbols_at.return_value = [entry]

        with patch("navegador.history.HistoryStore", return_value=mock_hs):
            result = await self.fx.call_tool_fn(
                "symbol_history",
                {"name": "x", "mode": "symbols_at", "ref": "HEAD", "format": "json"},
            )

        data = json.loads(result[0]["text"])
        assert len(data) == 1
        assert data[0]["name"] == "Foo"

    @pytest.mark.asyncio
    async def test_symbols_at_defaults_to_head(self):
        """When ref is empty, symbols_at defaults to HEAD."""
        mock_hs = MagicMock()
        mock_hs.symbols_at.return_value = []

        with patch("navegador.history.HistoryStore", return_value=mock_hs):
            await self.fx.call_tool_fn(
                "symbol_history", {"name": "x", "mode": "symbols_at"}
            )

        mock_hs.symbols_at.assert_called_once_with("HEAD")


class TestCallToolSuggestDocLinks:
    """suggest_doc_links handler — DocLinker.suggest_links()."""

    def setup_method(self):
        self.fx = _ServerFixture()

    @pytest.mark.asyncio
    async def test_no_candidates_returns_message(self):
        mock_linker = MagicMock()
        mock_linker.suggest_links.return_value = []

        with patch(
            "navegador.intelligence.doclink.DocLinker", return_value=mock_linker
        ):
            result = await self.fx.call_tool_fn("suggest_doc_links", {})

        assert "No link candidates found." in result[0]["text"]

    @pytest.mark.asyncio
    async def test_with_candidates_contains_source_target(self):
        candidate = MagicMock()
        candidate.source_name = "AuthDoc"
        candidate.target_name = "AuthService"
        candidate.target_file = "auth.py"
        candidate.strategy = "EXACT_NAME"
        candidate.confidence = 0.95
        candidate.rationale = "Exact name match"
        candidate.__dict__ = {
            "source_name": "AuthDoc",
            "target_name": "AuthService",
            "target_file": "auth.py",
            "strategy": "EXACT_NAME",
            "confidence": 0.95,
            "rationale": "Exact name match",
        }
        mock_linker = MagicMock()
        mock_linker.suggest_links.return_value = [candidate]

        with patch(
            "navegador.intelligence.doclink.DocLinker", return_value=mock_linker
        ):
            result = await self.fx.call_tool_fn("suggest_doc_links", {})

        text = result[0]["text"]
        assert "AuthDoc" in text
        assert "AuthService" in text
        assert "EXACT_NAME" in text

    @pytest.mark.asyncio
    async def test_strategy_filter(self):
        """Passing strategy param filters candidates by strategy field."""
        exact = MagicMock()
        exact.strategy = "EXACT_NAME"
        exact.source_name = "AuthDoc"
        exact.target_name = "AuthService"
        exact.target_file = "auth.py"
        exact.confidence = 0.95
        exact.rationale = "Exact match"

        fuzzy = MagicMock()
        fuzzy.strategy = "FUZZY"
        fuzzy.source_name = "Doc2"
        fuzzy.target_name = "FuzzyThing"
        fuzzy.target_file = "fuz.py"
        fuzzy.confidence = 0.60
        fuzzy.rationale = "Fuzzy match"

        mock_linker = MagicMock()
        mock_linker.suggest_links.return_value = [exact, fuzzy]

        with patch(
            "navegador.intelligence.doclink.DocLinker", return_value=mock_linker
        ):
            result = await self.fx.call_tool_fn(
                "suggest_doc_links", {"strategy": "EXACT_NAME"}
            )

        text = result[0]["text"]
        # The EXACT_NAME candidate should be present, FUZZY should be filtered out
        assert "AuthDoc" in text
        assert "FuzzyThing" not in text

    @pytest.mark.asyncio
    async def test_json_format(self):
        candidate = MagicMock()
        candidate.strategy = "EXACT_NAME"
        candidate.__dict__ = {
            "source_name": "AuthDoc",
            "target_name": "AuthService",
            "strategy": "EXACT_NAME",
            "confidence": 0.95,
        }
        mock_linker = MagicMock()
        mock_linker.suggest_links.return_value = [candidate]

        with patch(
            "navegador.intelligence.doclink.DocLinker", return_value=mock_linker
        ):
            result = await self.fx.call_tool_fn(
                "suggest_doc_links", {"format": "json"}
            )

        data = json.loads(result[0]["text"])
        assert len(data) == 1
        assert data[0]["source_name"] == "AuthDoc"


class TestCallToolReviewDiff:
    """review_diff handler — DiffGraphAnalyzer + ReviewGenerator."""

    def setup_method(self):
        self.fx = _ServerFixture()

    def _mock_diff_and_review(self, review_md="## Review", review_json='{"comments": []}'):
        sc = MagicMock()
        sc.symbol = "changed_fn"
        sc.file_path = "app.py"
        diff_report = MagicMock()
        diff_report.new_symbols = [sc]
        diff_report.changed_symbols = []
        diff_report.affected_files = ["app.py"]

        review_report = MagicMock()
        review_report.to_markdown.return_value = review_md
        review_report.to_json.return_value = review_json
        review_report.comments = []

        return diff_report, review_report

    @pytest.mark.asyncio
    async def test_markdown_format(self):
        diff_report, review_report = self._mock_diff_and_review(
            review_md="## Review -- 0 comment(s)"
        )
        mock_analyzer = MagicMock()
        mock_analyzer.diff_refs.return_value = diff_report
        mock_gen = MagicMock()
        mock_gen.review_diff.return_value = review_report

        with (
            patch(
                "navegador.analysis.diffgraph.DiffGraphAnalyzer",
                return_value=mock_analyzer,
            ),
            patch(
                "navegador.analysis.review.ReviewGenerator", return_value=mock_gen
            ),
        ):
            result = await self.fx.call_tool_fn("review_diff", {})

        mock_analyzer.diff_refs.assert_called_once_with(base="main", head="HEAD")
        mock_gen.review_diff.assert_called_once()
        assert "Review" in result[0]["text"]

    @pytest.mark.asyncio
    async def test_json_format(self):
        diff_report, review_report = self._mock_diff_and_review(
            review_json='{"comments": [], "changed_files": ["app.py"]}'
        )
        mock_analyzer = MagicMock()
        mock_analyzer.diff_refs.return_value = diff_report
        mock_gen = MagicMock()
        mock_gen.review_diff.return_value = review_report

        with (
            patch(
                "navegador.analysis.diffgraph.DiffGraphAnalyzer",
                return_value=mock_analyzer,
            ),
            patch(
                "navegador.analysis.review.ReviewGenerator", return_value=mock_gen
            ),
        ):
            result = await self.fx.call_tool_fn(
                "review_diff", {"format": "json", "base": "dev", "head": "feature"}
            )

        mock_analyzer.diff_refs.assert_called_once_with(base="dev", head="feature")
        data = json.loads(result[0]["text"])
        assert "comments" in data

    @pytest.mark.asyncio
    async def test_confidence_filter_drops_low_confidence_comments(self):
        """Comments below min_confidence are dropped."""
        sc = MagicMock()
        sc.symbol = "fn"
        sc.file_path = "a.py"
        diff_report = MagicMock()
        diff_report.new_symbols = [sc]
        diff_report.changed_symbols = []
        diff_report.affected_files = ["a.py"]

        high = MagicMock()
        high.confidence = 0.9
        low = MagicMock()
        low.confidence = 0.2
        review_report = MagicMock()
        review_report.comments = [high, low]
        review_report.to_markdown.return_value = "## Review"

        mock_analyzer = MagicMock()
        mock_analyzer.diff_refs.return_value = diff_report
        mock_gen = MagicMock()
        mock_gen.review_diff.return_value = review_report

        with (
            patch(
                "navegador.analysis.diffgraph.DiffGraphAnalyzer",
                return_value=mock_analyzer,
            ),
            patch(
                "navegador.analysis.review.ReviewGenerator", return_value=mock_gen
            ),
        ):
            await self.fx.call_tool_fn(
                "review_diff", {"min_confidence": 0.5}
            )

        # The low-confidence comment should have been removed
        assert review_report.comments == [high]


class TestCallToolReleaseCheck:
    """release_check handler — ReleaseChecker.check()."""

    def setup_method(self):
        self.fx = _ServerFixture()

    @pytest.mark.asyncio
    async def test_markdown_format(self):
        report = MagicMock()
        report.passed = True
        report.to_markdown.return_value = "## Release Check -- PASS"
        mock_checker = MagicMock()
        mock_checker.check.return_value = report

        with patch(
            "navegador.analysis.release.ReleaseChecker", return_value=mock_checker
        ):
            result = await self.fx.call_tool_fn("release_check", {})

        mock_checker.check.assert_called_once_with(base="main", head="HEAD")
        assert "PASS" in result[0]["text"]

    @pytest.mark.asyncio
    async def test_json_format(self):
        report = MagicMock()
        report.to_json.return_value = '{"passed": false, "items": [{"severity": "error"}]}'
        mock_checker = MagicMock()
        mock_checker.check.return_value = report

        with patch(
            "navegador.analysis.release.ReleaseChecker", return_value=mock_checker
        ):
            result = await self.fx.call_tool_fn(
                "release_check",
                {"base": "v1.0", "head": "v1.1", "format": "json"},
            )

        mock_checker.check.assert_called_once_with(base="v1.0", head="v1.1")
        data = json.loads(result[0]["text"])
        assert data["passed"] is False

    @pytest.mark.asyncio
    async def test_passes_repo_path(self):
        report = MagicMock()
        report.to_markdown.return_value = "ok"
        mock_checker = MagicMock()
        mock_checker.check.return_value = report

        with patch(
            "navegador.analysis.release.ReleaseChecker", return_value=mock_checker
        ) as cls:
            await self.fx.call_tool_fn(
                "release_check", {"repo_path": "/my/repo"}
            )

        # ReleaseChecker is instantiated with store and repo_path
        cls.assert_called_once()
        assert cls.call_args[0][1] == "/my/repo"


class TestCallToolApplyLens:
    """apply_lens handler — LensEngine.apply()."""

    def setup_method(self):
        self.fx = _ServerFixture()

    @pytest.mark.asyncio
    async def test_applies_lens_and_returns_markdown(self):
        lens_result = MagicMock()
        lens_result.to_markdown.return_value = "## request_path lens"
        mock_engine = MagicMock()
        mock_engine.apply.return_value = lens_result

        with patch("navegador.lenses.LensEngine", return_value=mock_engine):
            result = await self.fx.call_tool_fn(
                "apply_lens",
                {"lens": "request_path", "symbol": "handle_request"},
            )

        mock_engine.apply.assert_called_once_with(
            "request_path",
            symbol="handle_request",
            domain="",
            file_path="",
            label="",
        )
        assert "request_path" in result[0]["text"]

    @pytest.mark.asyncio
    async def test_json_format(self):
        lens_result = MagicMock()
        lens_result.to_json.return_value = '{"lens": "ownership_map", "nodes": []}'
        mock_engine = MagicMock()
        mock_engine.apply.return_value = lens_result

        with patch("navegador.lenses.LensEngine", return_value=mock_engine):
            result = await self.fx.call_tool_fn(
                "apply_lens", {"lens": "ownership_map", "format": "json"}
            )

        data = json.loads(result[0]["text"])
        assert data["lens"] == "ownership_map"

    @pytest.mark.asyncio
    async def test_value_error_returns_error_message(self):
        """Unknown lens name raises ValueError, returned as Error: text."""
        mock_engine = MagicMock()
        mock_engine.apply.side_effect = ValueError("Unknown lens: bad_lens")

        with patch("navegador.lenses.LensEngine", return_value=mock_engine):
            result = await self.fx.call_tool_fn(
                "apply_lens", {"lens": "bad_lens"}
            )

        assert result[0]["text"] == "Error: Unknown lens: bad_lens"

    @pytest.mark.asyncio
    async def test_passes_all_params(self):
        lens_result = MagicMock()
        lens_result.to_markdown.return_value = "ok"
        mock_engine = MagicMock()
        mock_engine.apply.return_value = lens_result

        with patch("navegador.lenses.LensEngine", return_value=mock_engine):
            await self.fx.call_tool_fn(
                "apply_lens",
                {
                    "lens": "domain_boundaries",
                    "symbol": "sym",
                    "domain": "auth",
                    "file_path": "auth.py",
                    "label": "Function",
                },
            )

        mock_engine.apply.assert_called_once_with(
            "domain_boundaries",
            symbol="sym",
            domain="auth",
            file_path="auth.py",
            label="Function",
        )
