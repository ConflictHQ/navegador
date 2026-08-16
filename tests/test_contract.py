# Copyright CONFLICT LLC 2026 (weareconflict.com)
"""
Conformance tests for #158 — supergraph interop contract v1.0.

The contract makes every graph in the system addressable from every other one.
Navegador owns the **code realm**, which is deliberately never compiled into a
brain: a brain-side `implemented_in` edge carries a code address, and traversal
continues here. That hop is the thing under test.

Real embedded stores throughout — an address is only useful if it resolves
against a graph a real ingest produced, and mocking the store would test the
grammar while leaving the resolution untested.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from navegador.contract import (
    CONTRACT_VERSION,
    JOIN_EDGE,
    REALM,
    AddressError,
    address_for_node,
    format_address,
    parse_address,
    propose_join_edges,
    resolve,
)
from navegador.graph.store import GraphStore


@pytest.fixture()
def store(tmp_path_factory):
    s = GraphStore.sqlite(str(tmp_path_factory.mktemp("contract") / "graph.db"))
    yield s
    s.close()


@pytest.fixture()
def code_graph(store):
    """A small code graph: a file, its symbols, and one call edge."""
    store.query("CREATE (:File {path: 'src/auth.py', name: 'auth.py'})")
    store.query("CREATE (:Function {name: 'validate_token', file_path: 'src/auth.py'})")
    store.query("CREATE (:Class {name: 'Auth', file_path: 'src/auth.py'})")
    store.query("CREATE (:Method {name: 'validate', file_path: 'src/auth.py'})")
    store.query("CREATE (:Function {name: 'login', file_path: 'src/views.py'})")
    store.query("MATCH (a {name: 'login'}), (b {name: 'validate_token'}) MERGE (a)-[:CALLS]->(b)")
    return store


# ── Address grammar ────────────────────────────────────────────────────────


class TestAddressGrammar:
    @pytest.mark.parametrize(
        "address",
        [
            "calliope-astrolift/code:src/auth.py",
            "calliope-astrolift/code:src/auth.py#validate_token",
            "code:src/auth.py#Auth.validate",
            "code:src/auth.py",
        ],
    )
    def test_round_trip(self, address):
        assert str(parse_address(address)) == address

    def test_repo_is_optional(self):
        """Instance-local addresses omit the namespace, per the grammar."""
        assert format_address("src/auth.py") == "code:src/auth.py"

    def test_repo_qualifies_the_address(self):
        assert format_address("src/auth.py", repo="myrepo") == "myrepo/code:src/auth.py"

    def test_symbol_is_fragment_separated(self):
        assert format_address("src/auth.py", "validate_token") == "code:src/auth.py#validate_token"

    def test_windows_separators_are_normalised(self):
        """Paths are POSIX in the contract regardless of the ingesting host."""
        assert format_address("src\\auth.py") == "code:src/auth.py"

    def test_parsed_fields(self):
        parsed = parse_address("myrepo/code:src/auth.py#Auth.validate")
        assert (parsed.repo, parsed.path, parsed.symbol) == (
            "myrepo",
            "src/auth.py",
            "Auth.validate",
        )

    @pytest.mark.parametrize(
        "address", ["", "   ", "doc:README.md", "not-an-address", "/code:", "code:"]
    )
    def test_malformed_addresses_are_rejected(self, address):
        with pytest.raises(AddressError):
            parse_address(address)

    def test_a_foreign_realm_is_rejected_clearly(self):
        """A brain kind is not ours to resolve; say so rather than miss silently."""
        with pytest.raises(AddressError, match="not a code-realm address"):
            parse_address("myrepo/story:AUTH-1")

    def test_empty_path_is_rejected(self):
        with pytest.raises(AddressError):
            format_address("")


class TestAddressForNode:
    def test_file_node(self):
        assert address_for_node("File", "auth.py", "src/auth.py") == "code:src/auth.py"

    def test_symbol_node(self):
        address = address_for_node("Function", "validate_token", "src/auth.py")
        assert address == "code:src/auth.py#validate_token"

    def test_repo_qualification(self):
        address = address_for_node("Function", "f", "src/a.py", repo="myrepo")
        assert address.startswith("myrepo/code:")

    @pytest.mark.parametrize("label", ["Concept", "Decision", "Rule", "Repository", ""])
    def test_brain_realm_nodes_get_no_code_address(self, label):
        """The brain is authoritative for its own kinds — we must not claim them."""
        assert address_for_node(label, "Thing", "src/a.py") is None

    def test_a_node_without_a_path_is_not_addressable(self):
        assert address_for_node("Function", "orphan", "") is None


# ── The supergraph hop ─────────────────────────────────────────────────────


class TestResolveJoinEdgeTargets:
    def test_file_address_resolves(self, code_graph):
        resolved = resolve(code_graph, "code:src/auth.py")
        assert resolved.found
        assert resolved.label == "File"

    def test_function_address_resolves(self, code_graph):
        resolved = resolve(code_graph, "code:src/auth.py#validate_token")
        assert (resolved.found, resolved.label, resolved.name) == (
            True,
            "Function",
            "validate_token",
        )

    def test_qualified_method_resolves_to_the_method(self, code_graph):
        """`Auth.validate` — the qualifier is the class, the target is the method."""
        resolved = resolve(code_graph, "code:src/auth.py#Auth.validate")
        assert resolved.found
        assert resolved.name == "validate"

    def test_traversal_continues_from_the_resolved_node(self, code_graph):
        """
        The acceptance criterion: having crossed the join edge with nothing but
        the address, ordinary code-graph traversal carries on from here.
        """
        resolved = resolve(code_graph, "code:src/auth.py#validate_token")
        callers = code_graph.query(
            "MATCH (a)-[:CALLS]->(b {name: $n, file_path: $p}) RETURN a.name",
            {"n": resolved.name, "p": resolved.path},
        ).result_set
        assert [r[0] for r in callers] == ["login"]

    def test_repo_prefixed_paths_also_resolve(self, store):
        """Workspace ingests record repo-prefixed paths; both spellings must work."""
        store.query("CREATE (:Function {name: 'handler', file_path: 'svc/src/app.py'})")
        store.query("CREATE (:File {path: 'svc/src/app.py', name: 'app.py'})")
        assert resolve(store, "svc/code:src/app.py#handler").found
        assert resolve(store, "svc/code:src/app.py").found

    def test_a_miss_is_reported_not_raised(self, code_graph):
        resolved = resolve(code_graph, "code:src/nope.py#absent")
        assert resolved.found is False
        assert resolved.address == "code:src/nope.py#absent"

    def test_a_miss_carries_no_invented_node(self, code_graph):
        resolved = resolve(code_graph, "code:src/nope.py#absent")
        assert (resolved.label, resolved.name) == ("", "")

    def test_a_foreign_realm_address_raises(self, code_graph):
        with pytest.raises(AddressError):
            resolve(code_graph, "myrepo/story:AUTH-1")


class TestVersionDeclaration:
    def test_resolution_states_the_contract_version(self, code_graph):
        assert resolve(code_graph, "code:src/auth.py").to_dict()["contract"] == "1.0"

    def test_resolution_states_the_realm(self, code_graph):
        assert resolve(code_graph, "code:src/auth.py").to_dict()["realm"] == REALM

    def test_parsed_address_states_the_contract_version(self):
        assert parse_address("code:a.py").to_dict()["contract"] == CONTRACT_VERSION

    def test_proposals_state_the_contract_version(self, code_graph):
        assert propose_join_edges(code_graph)["contract"] == "1.0"


# ── Proposing the reverse edges ────────────────────────────────────────────


class TestProposeJoinEdges:
    @pytest.fixture()
    def documented(self, code_graph):
        code_graph.query(
            "CREATE (:Document {name: 'auth-design.md', path: 'docs/auth-design.md', "
            "content: '# Auth\\n\\nEvery request is checked by `validate_token` first.'})"
        )
        return code_graph

    def test_a_proposal_is_produced(self, documented):
        payload = propose_join_edges(documented)
        assert payload["count"] >= 1

    def test_the_proposal_targets_a_code_address(self, documented):
        targets = [p["target"] for p in propose_join_edges(documented)["proposals"]]
        assert "code:src/auth.py#validate_token" in targets

    def test_the_proposal_uses_the_join_edge(self, documented):
        assert all(p["edge"] == JOIN_EDGE for p in propose_join_edges(documented)["proposals"])

    def test_targets_are_repo_qualified_on_request(self, documented):
        payload = propose_join_edges(documented, repo="myrepo")
        assert all(p["target"].startswith("myrepo/code:") for p in payload["proposals"])

    def test_proposals_carry_confidence_and_evidence(self, documented):
        proposal = propose_join_edges(documented)["proposals"][0]
        assert 0 < proposal["confidence"] <= 1.0
        assert proposal["evidence"]["strategy"]
        assert proposal["evidence"]["producer"] == "navegador"

    def test_the_source_is_named_in_brain_kinds(self, documented):
        """The brain validates kinds against its own schema; speak its vocabulary."""
        proposal = propose_join_edges(documented)["proposals"][0]
        assert proposal["source"]["kind"] == "doc"

    def test_nothing_is_written_to_the_graph(self, documented):
        """Navegador proposes; the brain commits. Proposing must not mutate."""
        propose_join_edges(documented)
        rows = documented.query("MATCH ()-[r:implemented_in]->() RETURN count(r)").result_set
        assert rows[0][0] == 0

    def test_brain_only_targets_are_not_proposed(self, code_graph):
        """
        A doc→Concept affinity lives entirely inside the brain realm. Proposing
        it would mean minting a code address for a node we do not own.
        """
        code_graph.query("CREATE (:Concept {name: 'idempotency'})")
        code_graph.query(
            "CREATE (:Document {name: 'notes.md', path: 'docs/notes.md', "
            "content: '# Notes\\n\\nEverything here relies on `idempotency`.'})"
        )
        payload = propose_join_edges(code_graph)
        assert all("code:" in p["target"] for p in payload["proposals"])

    def test_confidence_floor_is_honoured(self, documented):
        assert propose_join_edges(documented, min_confidence=0.99)["count"] == 0

    def test_an_empty_graph_proposes_nothing(self, store):
        payload = propose_join_edges(store)
        assert payload["proposals"] == []
        assert payload["contract"] == CONTRACT_VERSION


# ── The MCP surface ────────────────────────────────────────────────────────


def mcp_handlers(store):
    """
    Capture the tool handlers a real store produces.

    Only `mcp.server`/`mcp.types` are stubbed, to reach the decorated functions;
    `Tool` and `TextContent` become dicts so their fields can be read. The store
    and the loader behind them are real.
    """
    holders: dict[str, object] = {}

    def capture(key):
        """Stand in for `@server.list_tools()` — keep the function it decorates."""

        def decorator_factory():
            def decorator(fn):
                holders[key] = fn
                return fn

            return decorator

        return decorator_factory

    mock_server = MagicMock()
    mock_server.list_tools = capture("list")
    mock_server.call_tool = capture("call")

    mock_mcp_server = MagicMock()
    mock_mcp_server.Server.return_value = mock_server
    mock_mcp_types = MagicMock()
    mock_mcp_types.Tool = dict
    mock_mcp_types.TextContent = dict

    with patch.dict(
        "sys.modules",
        {"mcp": MagicMock(), "mcp.server": mock_mcp_server, "mcp.types": mock_mcp_types},
    ):
        from importlib import reload

        import navegador.mcp.server as srv

        reload(srv)
        srv.create_mcp_server(lambda: store)

    return holders["list"], holders["call"]


@pytest.fixture()
def mcp(code_graph):
    return mcp_handlers(code_graph)


class TestMcpSurface:
    @pytest.mark.asyncio
    async def test_both_tools_are_advertised(self, mcp):
        list_tools, _ = mcp
        names = {t["name"] for t in await list_tools()}
        assert {"resolve_address", "propose_join_edges"} <= names

    @pytest.mark.asyncio
    async def test_resolve_address_returns_the_node(self, mcp):
        _, call = mcp
        result = await call("resolve_address", {"address": "code:src/auth.py#validate_token"})
        payload = json.loads(result[0]["text"])
        assert (payload["found"], payload["name"], payload["contract"]) == (
            True,
            "validate_token",
            "1.0",
        )

    @pytest.mark.asyncio
    async def test_the_neighbourhood_carries_addresses(self, mcp):
        """A brain that just crossed a join edge keeps traversing from here."""
        _, call = mcp
        result = await call("resolve_address", {"address": "code:src/auth.py#validate_token"})
        callers = json.loads(result[0]["text"])["neighbourhood"]["callers"]
        assert [c["address"] for c in callers] == ["code:src/views.py#login"]

    @pytest.mark.asyncio
    async def test_a_miss_explains_itself(self, mcp):
        _, call = mcp
        result = await call("resolve_address", {"address": "code:src/nope.py"})
        payload = json.loads(result[0]["text"])
        assert payload["found"] is False
        assert "hint" in payload

    @pytest.mark.asyncio
    async def test_a_foreign_realm_is_an_error_not_a_miss(self, mcp):
        _, call = mcp
        result = await call("resolve_address", {"address": "myrepo/story:AUTH-1"})
        assert "not a code-realm address" in result[0]["text"]

    @pytest.mark.asyncio
    async def test_propose_join_edges_is_contract_shaped(self, mcp):
        _, call = mcp
        payload = json.loads((await call("propose_join_edges", {}))[0]["text"])
        assert payload["contract"] == "1.0"
        assert payload["join_edge"] == JOIN_EDGE

    @pytest.mark.asyncio
    async def test_search_results_carry_addresses(self, mcp):
        """The address is how a brain records an edge pointing back at a hit."""
        _, call = mcp
        result = await call("search_symbols", {"query": "validate_token"})
        assert "code:src/auth.py#validate_token" in result[0]["text"]
