"""Tests for navegador.mcp.security and read-only / complexity enforcement in the MCP server."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from navegador.mcp.security import (
    QueryComplexityError,
    QueryValidationError,
    check_complexity,
    validate_cypher,
)


# ── validate_cypher ────────────────────────────────────────────────────────────


class TestValidateCypherBlocksWrites:
    """validate_cypher must reject all write-operation keywords."""

    @pytest.mark.parametrize(
        "query",
        [
            "CREATE (n:Node {name: 'bad'})",
            "MERGE (n:Node {name: 'x'}) ON CREATE SET n.created = true",
            "MATCH (n) SET n.flag = true",
            "MATCH (n) DELETE n",
            "MATCH (n) REMOVE n.prop",
            "DROP INDEX ON :Node(name)",
            # Case-insensitive variants
            "create (n:Node {name: 'bad'})",
            "merge (n) return n",
            "match (n) set n.x = 1",
            "match (n) delete n",
            "match (n) remove n.p",
            "drop constraint ON (n:Node) ASSERT n.id IS UNIQUE",
            # Mixed case
            "Create (n:Node)",
            "MeRgE (n:Node)",
        ],
    )
    def test_raises_for_write_keyword(self, query):
        with pytest.raises(QueryValidationError):
            validate_cypher(query)

    def test_error_message_names_keyword(self):
        with pytest.raises(QueryValidationError, match="CREATE"):
            validate_cypher("CREATE (n:Node)")

    def test_call_procedure_is_blocked(self):
        with pytest.raises(QueryValidationError, match="CALL"):
            validate_cypher("CALL db.labels()")

    def test_call_case_insensitive(self):
        with pytest.raises(QueryValidationError):
            validate_cypher("call db.labels()")

    def test_nested_subquery_blocked(self):
        with pytest.raises(QueryValidationError):
            validate_cypher("MATCH (n) WHERE { MATCH (m) RETURN m } RETURN n")


class TestValidateCypherAllowsReads:
    """validate_cypher must pass clean read-only queries."""

    @pytest.mark.parametrize(
        "query",
        [
            "MATCH (n) RETURN n LIMIT 10",
            "MATCH (n:Function) WHERE n.name = 'parse' RETURN n",
            "MATCH (a)-[:CALLS]->(b) RETURN a, b LIMIT 50",
            "MATCH (n) RETURN count(n)",
            "MATCH (n) WITH n ORDER BY n.name RETURN n LIMIT 20",
        ],
    )
    def test_valid_read_query_passes(self, query):
        # Should not raise
        validate_cypher(query)

    def test_match_return_without_write_passes(self):
        validate_cypher("MATCH (n:Class) RETURN n.name LIMIT 100")

    def test_comment_stripped_before_check(self):
        # A comment containing a keyword should not trigger validation
        query = "// CREATE would be bad\nMATCH (n) RETURN n LIMIT 5"
        validate_cypher(query)


# ── check_complexity ───────────────────────────────────────────────────────────


class TestCheckComplexityDeepPaths:
    """check_complexity must reject variable-length paths that exceed max_depth."""

    def test_exceeds_default_max_depth(self):
        with pytest.raises(QueryComplexityError, match="depth"):
            check_complexity("MATCH (a)-[*1..100]->(b) RETURN a, b LIMIT 10")

    def test_exceeds_custom_max_depth(self):
        with pytest.raises(QueryComplexityError):
            check_complexity("MATCH (a)-[*1..3]->(b) RETURN a, b LIMIT 10", max_depth=2)

    def test_open_ended_upper_bound_is_rejected(self):
        with pytest.raises(QueryComplexityError, match="no upper bound"):
            check_complexity("MATCH (a)-[*1..]->(b) RETURN a LIMIT 10")

    def test_exact_repetition_exceeds_depth(self):
        with pytest.raises(QueryComplexityError):
            check_complexity("MATCH (a)-[*10]->(b) RETURN a LIMIT 10", max_depth=5)

    def test_path_at_exact_max_depth_is_allowed(self):
        # *1..5 with max_depth=5 should be fine
        check_complexity("MATCH (a)-[*1..5]->(b) RETURN a, b LIMIT 10", max_depth=5)

    def test_shallow_path_is_allowed(self):
        check_complexity("MATCH (a)-[*1..2]->(b) RETURN a, b LIMIT 10")


class TestCheckComplexityUnbounded:
    """check_complexity must reject queries that could return unbounded results."""

    def test_match_return_without_limit_is_rejected(self):
        with pytest.raises(QueryComplexityError, match="LIMIT"):
            check_complexity("MATCH (n) RETURN n")

    def test_match_return_with_limit_is_allowed(self):
        check_complexity("MATCH (n) RETURN n LIMIT 100")

    def test_count_aggregation_is_allowed_without_limit(self):
        # COUNT() aggregation is inherently bounded
        check_complexity("MATCH (n) RETURN count(n)")

    def test_no_match_clause_is_allowed(self):
        # Pure RETURN with no MATCH is fine
        check_complexity("RETURN 1")

    def test_complex_valid_query_passes(self):
        check_complexity(
            "MATCH (n:Function)-[:CALLS]->(m) RETURN n.name, m.name LIMIT 50"
        )


# ── MCP server read-only integration ──────────────────────────────────────────


def _mock_store():
    store = MagicMock()
    store.query.return_value = MagicMock(result_set=[])
    store.node_count.return_value = 0
    store.edge_count.return_value = 0
    return store


class _ServerFixture:
    """
    Minimal fixture that builds a navegador MCP server (mocked mcp SDK) and
    exposes call_tool_fn for direct invocation in tests.
    """

    def __init__(self, read_only: bool = False):
        self.store = _mock_store()
        self.read_only = read_only
        self.call_tool_fn = None
        self._build()

    def _build(self):
        from navegador.context import ContextLoader

        loader = MagicMock(spec=ContextLoader)
        loader.store = self.store
        self.loader = loader

        call_holder: dict = {}

        def call_tool_decorator():
            def decorator(fn):
                call_holder["fn"] = fn
                return fn
            return decorator

        def list_tools_decorator():
            def decorator(fn):
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

        with patch.dict("sys.modules", {
            "mcp": MagicMock(),
            "mcp.server": mock_mcp_server,
            "mcp.types": mock_mcp_types,
        }), patch("navegador.context.ContextLoader", return_value=loader):
            from importlib import reload

            import navegador.mcp.server as srv
            reload(srv)
            srv.create_mcp_server(lambda: self.store, read_only=self.read_only)

        self.call_tool_fn = call_holder["fn"]


class TestReadOnlyModeBlocksIngest:
    """In read-only mode, ingest_repo must return an error and never call the ingester."""

    def setup_method(self):
        self.fx = _ServerFixture(read_only=True)

    @pytest.mark.asyncio
    async def test_ingest_repo_returns_error_in_read_only(self):
        result = await self.fx.call_tool_fn("ingest_repo", {"path": "/some/repo"})
        assert len(result) == 1
        assert "read-only" in result[0]["text"].lower()
        assert "Error" in result[0]["text"]

    @pytest.mark.asyncio
    async def test_ingest_repo_does_not_call_ingester(self):
        with patch("navegador.ingestion.RepoIngester") as mock_cls:
            await self.fx.call_tool_fn("ingest_repo", {"path": "/some/repo"})
        mock_cls.assert_not_called()


class TestReadOnlyModeBlocksWriteQueries:
    """In read-only mode, query_graph must reject write-operation Cypher."""

    def setup_method(self):
        self.fx = _ServerFixture(read_only=True)

    @pytest.mark.asyncio
    async def test_create_query_returns_error(self):
        result = await self.fx.call_tool_fn(
            "query_graph", {"cypher": "CREATE (n:Node {name: 'x'})"}
        )
        assert "Error" in result[0]["text"]
        self.fx.store.query.assert_not_called()

    @pytest.mark.asyncio
    async def test_delete_query_returns_error(self):
        result = await self.fx.call_tool_fn(
            "query_graph", {"cypher": "MATCH (n) DELETE n"}
        )
        assert "Error" in result[0]["text"]
        self.fx.store.query.assert_not_called()

    @pytest.mark.asyncio
    async def test_merge_query_returns_error(self):
        result = await self.fx.call_tool_fn(
            "query_graph", {"cypher": "MERGE (n:Node {name: 'x'})"}
        )
        assert "Error" in result[0]["text"]

    @pytest.mark.asyncio
    async def test_read_query_passes_validation(self):
        self.fx.store.query.return_value = MagicMock(result_set=[["result"]])
        result = await self.fx.call_tool_fn(
            "query_graph", {"cypher": "MATCH (n) RETURN n LIMIT 10"}
        )
        # Should be valid JSON, not an error message
        data = json.loads(result[0]["text"])
        assert isinstance(data, list)


class TestNormalModeAllowsEverything:
    """In normal (non-read-only) mode, write queries and ingest_repo should work."""

    def setup_method(self):
        self.fx = _ServerFixture(read_only=False)

    @pytest.mark.asyncio
    async def test_ingest_repo_works_in_normal_mode(self):
        mock_ingester = MagicMock()
        mock_ingester.ingest.return_value = {"files": 1, "functions": 2, "classes": 0, "edges": 3}

        with patch("navegador.ingestion.RepoIngester", return_value=mock_ingester):
            result = await self.fx.call_tool_fn("ingest_repo", {"path": "/some/repo"})

        data = json.loads(result[0]["text"])
        assert data["files"] == 1

    @pytest.mark.asyncio
    async def test_write_cypher_query_not_validated_in_normal_mode(self):
        """In normal mode, write queries are NOT blocked by validate_cypher
        (only complexity checks apply)."""
        self.fx.store.query.return_value = MagicMock(result_set=[])
        result = await self.fx.call_tool_fn(
            "query_graph",
            {"cypher": "CREATE (n:Node {name: 'x'}) RETURN n LIMIT 1"},
        )
        # CREATE with RETURN+LIMIT passes complexity; store.query is invoked
        self.fx.store.query.assert_called_once()

    @pytest.mark.asyncio
    async def test_complexity_check_still_applies_in_normal_mode(self):
        """Complexity checks fire in all modes, even without read_only."""
        result = await self.fx.call_tool_fn(
            "query_graph", {"cypher": "MATCH (a)-[*1..100]->(b) RETURN a LIMIT 10"}
        )
        assert "Error" in result[0]["text"]
        self.fx.store.query.assert_not_called()
