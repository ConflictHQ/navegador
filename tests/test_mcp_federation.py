# Copyright CONFLICT LLC 2026 (weareconflict.com)
"""
Tests for MCP multi-graph routing over a federated super-graph.

Two seeded repo graphs are rolled up with SuperGraphAggregator into a real
central store; MCP tools are exercised through the captured call_tool handler
against that store — no graph mocks.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from navegador.cli.commands import _parse_repo_sources
from navegador.federation import SuperGraphAggregator
from navegador.graph.store import GraphStore

# ── Server harness (mocked mcp SDK, real store + loader) ───────────────────


class _ServerFixture:
    """Capture list_tools/call_tool from create_mcp_server bound to a real store."""

    def __init__(self, store: GraphStore, read_only: bool = False):
        self.store = store
        self.call_tool_fn = None
        self.list_tools_fn = None
        self._build(read_only)

    def _build(self, read_only: bool):
        list_holder: dict = {}
        call_holder: dict = {}

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
        mock_mcp_types.Tool = dict
        mock_mcp_types.TextContent = dict

        with patch.dict(
            "sys.modules",
            {
                "mcp": MagicMock(),
                "mcp.server": mock_mcp_server,
                "mcp.types": mock_mcp_types,
            },
        ):
            from importlib import reload

            import navegador.mcp.server as srv

            reload(srv)
            srv.create_mcp_server(lambda: self.store, read_only=read_only)

        self.list_tools_fn = list_holder["fn"]
        self.call_tool_fn = call_holder["fn"]


def _text(result) -> str:
    return result[0]["text"]


# ── Fixtures ───────────────────────────────────────────────────────────────


def _seed_repo(store: GraphStore, suffix: str) -> None:
    store.create_node("File", {"name": "app.py", "path": "app.py", "language": "python"})
    store.create_node("Function", {"name": "process", "file_path": "app.py", "line_start": 1})
    store.create_node(
        "Function", {"name": f"helper_{suffix}", "file_path": "app.py", "line_start": 9}
    )
    store.create_node("Concept", {"name": "Payments"})
    store.create_edge(
        "File", {"path": "app.py"}, "CONTAINS", "Function", {"name": "process", "file_path": "app.py"}
    )
    store.create_edge(
        "Function",
        {"name": "process", "file_path": "app.py"},
        "CALLS",
        "Function",
        {"name": f"helper_{suffix}", "file_path": "app.py"},
    )
    store.create_edge(
        "Function",
        {"name": "process", "file_path": "app.py"},
        "IMPLEMENTS",
        "Concept",
        {"name": "Payments"},
    )


@pytest.fixture(scope="module")
def central(tmp_path_factory):
    """A real federated super-graph: repo-a and repo-b rolled up centrally."""
    central = GraphStore.sqlite(str(tmp_path_factory.mktemp("mcp-central") / "graph.db"))
    for repo in ("repo-a", "repo-b"):
        source = GraphStore.sqlite(str(tmp_path_factory.mktemp(f"mcp-{repo}") / "graph.db"))
        _seed_repo(source, repo.replace("repo-", ""))
        SuperGraphAggregator(central).aggregate({repo: source})
        source.close()
    yield central
    central.close()


@pytest.fixture(scope="module")
def call_tool(central):
    return _ServerFixture(central).call_tool_fn


@pytest.fixture(scope="module")
def call_tool_readonly(central):
    return _ServerFixture(central, read_only=True).call_tool_fn


# ── list_repos ─────────────────────────────────────────────────────────────


class TestListRepos:
    @pytest.mark.asyncio
    async def test_lists_federated_namespaces(self, call_tool):
        result = await call_tool("list_repos", {})
        assert json.loads(_text(result)) == ["repo-a", "repo-b"]


# ── search_symbols ─────────────────────────────────────────────────────────


class TestSearchSymbols:
    @pytest.mark.asyncio
    async def test_spans_all_repos_by_default(self, call_tool):
        result = await call_tool("search_symbols", {"query": "helper"})
        text = _text(result)
        assert "helper_a" in text
        assert "helper_b" in text

    @pytest.mark.asyncio
    async def test_repo_arg_scopes(self, call_tool):
        result = await call_tool("search_symbols", {"query": "helper", "repo": "repo-a"})
        text = _text(result)
        assert "helper_a" in text
        assert "helper_b" not in text


# ── search_knowledge ───────────────────────────────────────────────────────


class TestSearchKnowledge:
    @pytest.mark.asyncio
    async def test_shared_concept_matches_contributing_repo(self, call_tool):
        result = await call_tool("search_knowledge", {"query": "Payments", "repo": "repo-a"})
        assert "Payments" in _text(result)

    @pytest.mark.asyncio
    async def test_unknown_repo_matches_nothing(self, call_tool):
        result = await call_tool("search_knowledge", {"query": "Payments", "repo": "repo-c"})
        assert _text(result) == "No results."


# ── query_graph across the federation ──────────────────────────────────────


class TestQueryGraph:
    @pytest.mark.asyncio
    async def test_who_implements_concept_across_repos(self, call_tool):
        result = await call_tool(
            "query_graph",
            {
                "cypher": (
                    "MATCH (f:Function)-[:IMPLEMENTS]->(:Concept {name: 'Payments'}) "
                    "RETURN f.repo ORDER BY f.repo"
                )
            },
        )
        assert json.loads(_text(result)) == [["repo-a"], ["repo-b"]]

    @pytest.mark.asyncio
    async def test_read_only_blocks_writes_on_supergraph(self, call_tool_readonly):
        result = await call_tool_readonly(
            "query_graph", {"cypher": "CREATE (n:Function {name: 'evil'})"}
        )
        assert _text(result).startswith("Error:")


# ── blast_radius ───────────────────────────────────────────────────────────


class TestBlastRadius:
    @pytest.mark.asyncio
    async def test_spans_workspace_without_repo(self, call_tool):
        result = await call_tool("blast_radius", {"name": "process"})
        affected = {n["name"] for n in json.loads(_text(result))["affected_nodes"]}
        assert {"helper_a", "helper_b"} <= affected

    @pytest.mark.asyncio
    async def test_repo_arg_scopes_root(self, call_tool):
        result = await call_tool("blast_radius", {"name": "process", "repo": "repo-b"})
        affected = {n["name"] for n in json.loads(_text(result))["affected_nodes"]}
        assert "helper_b" in affected
        assert "helper_a" not in affected


# ── graph_stats ────────────────────────────────────────────────────────────


class TestGraphStats:
    @pytest.mark.asyncio
    async def test_repo_scoped_counts(self, call_tool):
        total = json.loads(_text(await call_tool("graph_stats", {})))
        scoped = json.loads(_text(await call_tool("graph_stats", {"repo": "repo-a"})))
        assert scoped["repo"] == "repo-a"
        assert 0 < scoped["nodes"] < total["nodes"]


# ── context tools ──────────────────────────────────────────────────────────


class TestContextScoping:
    @pytest.mark.asyncio
    async def test_load_file_context_with_repo(self, call_tool):
        result = await call_tool(
            "load_file_context", {"file_path": "app.py", "repo": "repo-a", "format": "json"}
        )
        payload = json.loads(_text(result))
        names = json.dumps(payload)
        assert "helper_a" in names
        assert "helper_b" not in names


# ── CLI source parsing (used by mcp --federate and aggregate) ──────────────


class TestParseRepoSources:
    def test_named_and_positional(self, tmp_path):
        repo = tmp_path / "myrepo"
        repo.mkdir()
        sources = _parse_repo_sources((f"backend={repo}", str(repo)))
        assert sources == {"backend": str(repo), "myrepo": str(repo)}
