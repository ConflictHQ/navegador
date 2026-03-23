"""Tests for ContextBundle serialization and ContextLoader with mock store."""

import json
from unittest.mock import MagicMock

from navegador.context.loader import ContextBundle, ContextLoader, ContextNode

# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_bundle():
    target = ContextNode(
        type="Function",
        name="get_user",
        file_path="src/auth.py",
        line_start=42,
        docstring="Return a user by ID.",
        signature="def get_user(user_id: int) -> User:",
    )
    nodes = [
        ContextNode(type="Function", name="validate_token", file_path="src/auth.py", line_start=10),
        ContextNode(type="Class", name="User", file_path="src/models.py", line_start=5),
    ]
    edges = [{"from": "get_user", "type": "CALLS", "to": "validate_token"}]
    return ContextBundle(target=target, nodes=nodes, edges=edges,
                         metadata={"query": "function_context"})


def _make_knowledge_bundle():
    target = ContextNode(
        type="Concept", name="JWT", description="Stateless token auth", domain="auth"
    )
    nodes = [
        ContextNode(type="Rule", name="Tokens must expire"),
        ContextNode(type="WikiPage", name="Auth Overview"),
    ]
    edges = [
        {"from": "Tokens must expire", "type": "GOVERNS", "to": "JWT"},
        {"from": "Auth Overview", "type": "DOCUMENTS", "to": "JWT"},
    ]
    return ContextBundle(target=target, nodes=nodes, edges=edges)


def _mock_store(rows=None):
    store = MagicMock()
    result = MagicMock()
    result.result_set = rows or []
    store.query.return_value = result
    return store


# ── ContextNode ───────────────────────────────────────────────────────────────

class TestContextNode:
    def test_defaults(self):
        n = ContextNode(type="Function", name="foo")
        assert n.file_path == ""
        assert n.line_start is None
        assert n.docstring is None
        assert n.domain is None

    def test_knowledge_fields(self):
        n = ContextNode(type="Concept", name="Payment", description="A payment", domain="billing")
        assert n.description == "A payment"
        assert n.domain == "billing"


# ── ContextBundle.to_dict ─────────────────────────────────────────────────────

class TestContextBundleDict:
    def test_structure(self):
        b = _make_bundle()
        d = b.to_dict()
        assert d["target"]["name"] == "get_user"
        assert len(d["nodes"]) == 2
        assert len(d["edges"]) == 1
        assert d["metadata"]["query"] == "function_context"

    def test_roundtrip(self):
        b = _make_bundle()
        d = b.to_dict()
        assert d["target"]["type"] == "Function"
        assert d["nodes"][0]["name"] == "validate_token"


# ── ContextBundle.to_json ─────────────────────────────────────────────────────

class TestContextBundleJson:
    def test_valid_json(self):
        b = _make_bundle()
        data = json.loads(b.to_json())
        assert data["target"]["name"] == "get_user"

    def test_indent(self):
        b = _make_bundle()
        raw = b.to_json(indent=4)
        assert "    " in raw  # 4-space indent


# ── ContextBundle.to_markdown ─────────────────────────────────────────────────

class TestContextBundleMarkdown:
    def test_contains_name(self):
        b = _make_bundle()
        md = b.to_markdown()
        assert "get_user" in md

    def test_contains_edge(self):
        md = _make_bundle().to_markdown()
        assert "CALLS" in md
        assert "validate_token" in md

    def test_contains_docstring(self):
        md = _make_bundle().to_markdown()
        assert "Return a user by ID." in md

    def test_contains_signature(self):
        md = _make_bundle().to_markdown()
        assert "def get_user" in md

    def test_knowledge_bundle(self):
        md = _make_knowledge_bundle().to_markdown()
        assert "JWT" in md
        assert "auth" in md
        assert "GOVERNS" in md

    def test_empty_nodes(self):
        target = ContextNode(type="File", name="empty.py", file_path="empty.py")
        b = ContextBundle(target=target)
        md = b.to_markdown()
        assert "empty.py" in md


# ── ContextLoader ─────────────────────────────────────────────────────────────

class TestContextLoaderFile:
    def test_load_file_empty(self):
        store = _mock_store([])
        loader = ContextLoader(store)
        bundle = loader.load_file("src/auth.py")
        assert bundle.target.name == "auth.py"
        assert bundle.target.type == "File"
        assert bundle.nodes == []

    def test_load_file_with_rows(self):
        rows = [["Function", "get_user", 10, "Get a user", "def get_user()"]]
        store = _mock_store(rows)
        loader = ContextLoader(store)
        bundle = loader.load_file("src/auth.py")
        assert len(bundle.nodes) == 1
        assert bundle.nodes[0].name == "get_user"
        assert bundle.nodes[0].type == "Function"


class TestContextLoaderFunction:
    def test_load_function_no_results(self):
        store = _mock_store([])
        loader = ContextLoader(store)
        bundle = loader.load_function("get_user", file_path="src/auth.py")
        assert bundle.target.name == "get_user"
        assert bundle.nodes == []
        assert bundle.edges == []

    def test_load_function_with_callee(self):
        def side_effect(query, params):
            result = MagicMock()
            if "CALLEES" in query or "callee" in query.lower():
                result.result_set = [["Function", "validate_token", "src/auth.py", 5]]
            elif "CALLERS" in query or "caller" in query.lower():
                result.result_set = []
            else:
                result.result_set = []
            return result

        store = MagicMock()
        store.query.side_effect = side_effect
        loader = ContextLoader(store)
        loader.load_function("get_user")
        # Should have called query multiple times
        assert store.query.called

    def test_load_function_default_file_path(self):
        store = _mock_store([])
        loader = ContextLoader(store)
        bundle = loader.load_function("foo")
        assert bundle.target.file_path == ""


class TestContextLoaderClass:
    def test_load_class_empty(self):
        store = _mock_store([])
        loader = ContextLoader(store)
        bundle = loader.load_class("AuthService")
        assert bundle.target.name == "AuthService"
        assert bundle.target.type == "Class"

    def test_load_class_with_parent(self):
        call_count = [0]

        def side_effect(query, params=None):
            result = MagicMock()
            call_count[0] += 1
            if call_count[0] == 1:
                result.result_set = [["BaseService", "src/base.py"]]
            else:
                result.result_set = []
            return result

        store = MagicMock()
        store.query.side_effect = side_effect
        loader = ContextLoader(store)
        bundle = loader.load_class("AuthService")
        assert len(bundle.nodes) >= 1


class TestContextLoaderExplain:
    def test_explain_empty(self):
        store = _mock_store([])
        loader = ContextLoader(store)
        bundle = loader.explain("get_user")
        assert bundle.target.name == "get_user"
        assert bundle.metadata["query"] == "explain"


class TestContextLoaderSearch:
    def test_search_empty(self):
        store = _mock_store([])
        loader = ContextLoader(store)
        results = loader.search("auth")
        assert results == []

    def test_search_returns_nodes(self):
        rows = [["Function", "authenticate", "src/auth.py", 10, "Authenticate a user"]]
        store = _mock_store(rows)
        loader = ContextLoader(store)
        results = loader.search("auth")
        assert len(results) == 1
        assert results[0].name == "authenticate"
        assert results[0].type == "Function"

    def test_search_all(self):
        rows = [["Concept", "Authentication", "", None, "The auth concept"]]
        store = _mock_store(rows)
        loader = ContextLoader(store)
        results = loader.search_all("auth")
        assert len(results) == 1

    def test_search_by_docstring(self):
        rows = [["Function", "login", "src/auth.py", 5, "Log in a user"]]
        store = _mock_store(rows)
        loader = ContextLoader(store)
        results = loader.search_by_docstring("log in")
        assert len(results) == 1

    def test_decorated_by(self):
        rows = [["Function", "protected_view", "src/views.py", 20]]
        store = _mock_store(rows)
        loader = ContextLoader(store)
        results = loader.decorated_by("login_required")
        assert len(results) == 1
        assert results[0].name == "protected_view"


class TestContextLoaderConcept:
    def test_load_concept_not_found(self):
        store = _mock_store([])
        loader = ContextLoader(store)
        bundle = loader.load_concept("Unknown")
        assert bundle.metadata.get("found") is False

    def test_load_concept_found(self):
        rows = [["JWT", "Stateless token auth", "active", "auth", [], [], [], []]]
        store = _mock_store(rows)
        loader = ContextLoader(store)
        bundle = loader.load_concept("JWT")
        assert bundle.target.name == "JWT"
        assert bundle.target.description == "Stateless token auth"

    def test_load_domain(self):
        rows = [["Function", "login", "src/auth.py", "Log in"]]
        store = _mock_store(rows)
        loader = ContextLoader(store)
        bundle = loader.load_domain("auth")
        assert bundle.target.name == "auth"
        assert bundle.target.type == "Domain"


# ── to_markdown with status and node docstrings ───────────────────────────────

class TestContextBundleMarkdownBranches:
    def test_markdown_includes_status(self):
        target = ContextNode(type="Concept", name="JWT", status="active")
        b = ContextBundle(target=target)
        md = b.to_markdown()
        assert "active" in md

    def test_markdown_node_with_docstring(self):
        target = ContextNode(type="File", name="app.py", file_path="app.py")
        node = ContextNode(type="Function", name="foo", file_path="app.py",
                           docstring="Does something useful.")
        b = ContextBundle(target=target, nodes=[node])
        md = b.to_markdown()
        assert "Does something useful." in md

    def test_markdown_node_with_description_fallback(self):
        target = ContextNode(type="Concept", name="JWT")
        node = ContextNode(type="Rule", name="must_expire",
                           description="Tokens must expire.")
        b = ContextBundle(target=target, nodes=[node])
        md = b.to_markdown()
        assert "Tokens must expire." in md


# ── load_function with callers and decorators ─────────────────────────────────

class TestContextLoaderFunctionBranches:
    def test_load_function_with_callers(self):
        call_count = [0]

        def side_effect(query, params):
            result = MagicMock()
            call_count[0] += 1
            if call_count[0] == 1:
                result.result_set = []  # callees empty
            elif call_count[0] == 2:
                result.result_set = [["Function", "caller_fn", "src/x.py", 5]]
            else:
                result.result_set = []  # decorators empty
            return result

        store = MagicMock()
        store.query.side_effect = side_effect
        loader = ContextLoader(store)
        bundle = loader.load_function("get_user", file_path="src/auth.py")
        assert any(n.name == "caller_fn" for n in bundle.nodes)
        assert any(e["type"] == "CALLS" for e in bundle.edges)

    def test_load_function_with_decorators(self):
        call_count = [0]

        def side_effect(query, params):
            result = MagicMock()
            call_count[0] += 1
            if call_count[0] <= 2:
                result.result_set = []  # callees and callers empty
            else:
                result.result_set = [["login_required", "src/decorators.py"]]
            return result

        store = MagicMock()
        store.query.side_effect = side_effect
        loader = ContextLoader(store)
        bundle = loader.load_function("my_view", file_path="src/views.py")
        assert any(n.name == "login_required" for n in bundle.nodes)


# ── load_class with subs and refs ─────────────────────────────────────────────

class TestContextLoaderClassBranches:
    def test_load_class_with_subclasses(self):
        call_count = [0]

        def side_effect(query, params):
            result = MagicMock()
            call_count[0] += 1
            if call_count[0] == 1:
                result.result_set = []  # parents empty
            elif call_count[0] == 2:
                result.result_set = [["ChildService", "src/child.py"]]
            else:
                result.result_set = []  # refs empty
            return result

        store = MagicMock()
        store.query.side_effect = side_effect
        loader = ContextLoader(store)
        bundle = loader.load_class("BaseService")
        assert any(n.name == "ChildService" for n in bundle.nodes)
        assert any(e["type"] == "INHERITS" for e in bundle.edges)

    def test_load_class_with_references(self):
        call_count = [0]

        def side_effect(query, params):
            result = MagicMock()
            call_count[0] += 1
            if call_count[0] <= 2:
                result.result_set = []  # parents and subs empty
            else:
                result.result_set = [["Function", "use_service", "src/x.py", 10]]
            return result

        store = MagicMock()
        store.query.side_effect = side_effect
        loader = ContextLoader(store)
        bundle = loader.load_class("AuthService")
        assert any(n.name == "use_service" for n in bundle.nodes)


# ── explain with data ─────────────────────────────────────────────────────────

class TestContextLoaderExplainBranches:
    def test_explain_with_outbound_data(self):
        call_count = [0]

        def side_effect(query, params):
            result = MagicMock()
            call_count[0] += 1
            if call_count[0] == 1:
                result.result_set = [["CALLS", "Function", "helper", "src/utils.py"]]
            else:
                result.result_set = []
            return result

        store = MagicMock()
        store.query.side_effect = side_effect
        loader = ContextLoader(store)
        bundle = loader.explain("main_fn")
        assert any(n.name == "helper" for n in bundle.nodes)
        assert any(e["type"] == "CALLS" for e in bundle.edges)

    def test_explain_with_inbound_data(self):
        call_count = [0]

        def side_effect(query, params):
            result = MagicMock()
            call_count[0] += 1
            if call_count[0] == 1:
                result.result_set = []
            else:
                result.result_set = [["CALLS", "Function", "caller", "src/main.py"]]
            return result

        store = MagicMock()
        store.query.side_effect = side_effect
        loader = ContextLoader(store)
        bundle = loader.explain("helper_fn")
        assert any(n.name == "caller" for n in bundle.nodes)


# ── load_concept with populated related nodes ─────────────────────────────────

class TestContextLoaderConceptBranches:
    def test_load_concept_with_related_concepts_rules_wiki_implements(self):
        rows = [[
            "JWT",
            "Stateless token auth",
            "active",
            "auth",
            ["OAuth"],           # related concepts
            ["Tokens must expire"],  # rules
            ["Auth Overview"],   # wiki pages
            ["validate_token"],  # implementing code
        ]]
        store = _mock_store(rows)
        loader = ContextLoader(store)
        bundle = loader.load_concept("JWT")
        names = {n.name for n in bundle.nodes}
        assert "OAuth" in names
        assert "Tokens must expire" in names
        assert "Auth Overview" in names
        assert "validate_token" in names
        types = {e["type"] for e in bundle.edges}
        assert "RELATED_TO" in types
        assert "GOVERNS" in types
        assert "DOCUMENTS" in types
        assert "IMPLEMENTS" in types


# ── load_decision ───────────────────────────────────────────────────────────

class TestContextLoaderDecision:
    def test_load_decision_not_found(self):
        store = _mock_store([])
        loader = ContextLoader(store)
        bundle = loader.load_decision("Nonexistent")
        assert bundle.metadata.get("found") is False
        assert bundle.target.type == "Decision"

    def test_load_decision_found(self):
        rows = [[
            "Use FalkorDB",
            "Graph DB for navegador",
            "Cypher queries, SQLite backend",
            "Neo4j, ArangoDB",
            "accepted",
            "2026-03-01",
            "infrastructure",
            [],  # documents
            [],  # decided_by
            [],  # domains
        ]]
        store = _mock_store(rows)
        loader = ContextLoader(store)
        bundle = loader.load_decision("Use FalkorDB")
        assert bundle.target.name == "Use FalkorDB"
        assert bundle.target.rationale == "Cypher queries, SQLite backend"
        assert bundle.target.alternatives == "Neo4j, ArangoDB"
        assert bundle.target.status == "accepted"

    def test_load_decision_with_related_nodes(self):
        rows = [[
            "Use FalkorDB",
            "Graph DB",
            "Cypher",
            "Neo4j",
            "accepted",
            "2026-03-01",
            "infra",
            ["GraphStore"],       # documents
            ["Alice"],            # decided_by
            ["infrastructure"],   # domains
        ]]
        store = _mock_store(rows)
        loader = ContextLoader(store)
        bundle = loader.load_decision("Use FalkorDB")
        names = {n.name for n in bundle.nodes}
        assert "GraphStore" in names
        assert "Alice" in names
        edge_types = {e["type"] for e in bundle.edges}
        assert "DOCUMENTS" in edge_types
        assert "DECIDED_BY" in edge_types


# ── find_owners ──────────────────────────────────────────────────────────────

class TestContextLoaderFindOwners:
    def test_find_owners_empty(self):
        store = _mock_store([])
        loader = ContextLoader(store)
        results = loader.find_owners("AuthService")
        assert results == []

    def test_find_owners_returns_people(self):
        rows = [["Class", "AuthService", "Alice", "alice@example.com", "lead", "auth"]]
        store = _mock_store(rows)
        loader = ContextLoader(store)
        results = loader.find_owners("AuthService")
        assert len(results) == 1
        assert results[0].name == "Alice"
        assert results[0].type == "Person"

    def test_find_owners_passes_file_path(self):
        store = _mock_store([])
        loader = ContextLoader(store)
        loader.find_owners("foo", file_path="src/foo.py")
        store.query.assert_called_once()
        args = store.query.call_args
        assert args[0][1]["file_path"] == "src/foo.py"


# ── search_knowledge ────────────────────────────────────────────────────────

class TestContextLoaderSearchKnowledge:
    def test_search_knowledge_empty(self):
        store = _mock_store([])
        loader = ContextLoader(store)
        results = loader.search_knowledge("xyz")
        assert results == []

    def test_search_knowledge_returns_nodes(self):
        rows = [["Concept", "JWT", "Stateless token auth", "auth", "active"]]
        store = _mock_store(rows)
        loader = ContextLoader(store)
        results = loader.search_knowledge("JWT")
        assert len(results) == 1
        assert results[0].name == "JWT"
        assert results[0].type == "Concept"
        assert results[0].domain == "auth"

    def test_search_knowledge_passes_limit(self):
        store = _mock_store([])
        loader = ContextLoader(store)
        loader.search_knowledge("auth", limit=5)
        args = store.query.call_args
        assert args[0][1]["limit"] == 5
