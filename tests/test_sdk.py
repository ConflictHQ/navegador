"""
Tests for the Navegador Python SDK (navegador/sdk.py).

All tests use a mock GraphStore so no real database is required.
"""

from unittest.mock import MagicMock, patch

import pytest

from navegador.sdk import Navegador


# ── Helpers ───────────────────────────────────────────────────────────────────


def _mock_store(rows=None):
    """Return a mock GraphStore whose .query() yields the given rows."""
    store = MagicMock()
    result = MagicMock()
    result.result_set = rows or []
    store.query.return_value = result
    return store


def _nav(rows=None):
    """Return a Navegador instance wired to a mock store."""
    return Navegador(_mock_store(rows))


# ── Constructor tests ─────────────────────────────────────────────────────────


class TestConstructors:
    def test_direct_init_stores_store(self):
        store = _mock_store()
        nav = Navegador(store)
        assert nav._store is store

    def test_sqlite_classmethod(self):
        fake_store = _mock_store()
        with patch("navegador.graph.store.GraphStore.sqlite", return_value=fake_store) as mock_sqlite:
            nav = Navegador.sqlite("/tmp/test.db")
            mock_sqlite.assert_called_once_with("/tmp/test.db")
            assert nav._store is fake_store

    def test_sqlite_default_path(self):
        fake_store = _mock_store()
        with patch("navegador.graph.store.GraphStore.sqlite", return_value=fake_store) as mock_sqlite:
            Navegador.sqlite()
            mock_sqlite.assert_called_once_with(".navegador/graph.db")

    def test_redis_classmethod(self):
        fake_store = _mock_store()
        with patch("navegador.graph.store.GraphStore.redis", return_value=fake_store) as mock_redis:
            nav = Navegador.redis("redis://myhost:6379")
            mock_redis.assert_called_once_with("redis://myhost:6379")
            assert nav._store is fake_store

    def test_redis_default_url(self):
        fake_store = _mock_store()
        with patch("navegador.graph.store.GraphStore.redis", return_value=fake_store) as mock_redis:
            Navegador.redis()
            mock_redis.assert_called_once_with("redis://localhost:6379")


# ── Ingestion ─────────────────────────────────────────────────────────────────


class TestIngest:
    def test_ingest_delegates_to_repo_ingester(self):
        store = _mock_store()
        nav = Navegador(store)
        expected = {"files": 3, "functions": 10, "classes": 2, "edges": 5, "skipped": 0}

        with patch("navegador.ingestion.RepoIngester") as MockIngester:
            mock_instance = MockIngester.return_value
            mock_instance.ingest.return_value = expected

            result = nav.ingest("/some/repo")

            MockIngester.assert_called_once_with(store)
            mock_instance.ingest.assert_called_once_with(
                "/some/repo", clear=False, incremental=False
            )
            assert result == expected

    def test_ingest_passes_clear_and_incremental(self):
        store = _mock_store()
        nav = Navegador(store)

        with patch("navegador.ingestion.RepoIngester") as MockIngester:
            mock_instance = MockIngester.return_value
            mock_instance.ingest.return_value = {}

            nav.ingest("/repo", clear=True, incremental=True)
            mock_instance.ingest.assert_called_once_with(
                "/repo", clear=True, incremental=True
            )


# ── Context loading ───────────────────────────────────────────────────────────


class TestFileContext:
    def test_returns_context_bundle(self):
        from navegador.context.loader import ContextBundle, ContextNode

        nav = _nav([])
        bundle = nav.file_context("src/auth.py")
        assert isinstance(bundle, ContextBundle)
        assert bundle.target.type == "File"
        assert bundle.target.name == "auth.py"

    def test_passes_file_path(self):
        store = _mock_store([])
        nav = Navegador(store)
        nav.file_context("src/auth.py")
        # store.query must have been called with the file path param
        call_args = store.query.call_args
        assert call_args[0][1]["path"] == "src/auth.py"


class TestFunctionContext:
    def test_returns_context_bundle(self):
        from navegador.context.loader import ContextBundle

        nav = _nav([])
        bundle = nav.function_context("validate_token")
        assert isinstance(bundle, ContextBundle)
        assert bundle.target.name == "validate_token"
        assert bundle.target.type == "Function"

    def test_passes_file_path_and_depth(self):
        store = _mock_store([])
        nav = Navegador(store)

        with patch("navegador.context.loader.ContextLoader.load_function") as mock_load:
            from navegador.context.loader import ContextBundle, ContextNode
            mock_load.return_value = ContextBundle(
                target=ContextNode(type="Function", name="fn")
            )
            nav.function_context("fn", file_path="src/x.py", depth=3)
            mock_load.assert_called_once_with("fn", file_path="src/x.py", depth=3)

    def test_default_depth(self):
        store = _mock_store([])
        nav = Navegador(store)

        with patch("navegador.context.loader.ContextLoader.load_function") as mock_load:
            from navegador.context.loader import ContextBundle, ContextNode
            mock_load.return_value = ContextBundle(
                target=ContextNode(type="Function", name="fn")
            )
            nav.function_context("fn")
            mock_load.assert_called_once_with("fn", file_path="", depth=2)


class TestClassContext:
    def test_returns_context_bundle(self):
        from navegador.context.loader import ContextBundle

        nav = _nav([])
        bundle = nav.class_context("AuthService")
        assert isinstance(bundle, ContextBundle)
        assert bundle.target.name == "AuthService"
        assert bundle.target.type == "Class"

    def test_passes_file_path(self):
        store = _mock_store([])
        nav = Navegador(store)

        with patch("navegador.context.loader.ContextLoader.load_class") as mock_load:
            from navegador.context.loader import ContextBundle, ContextNode
            mock_load.return_value = ContextBundle(
                target=ContextNode(type="Class", name="AuthService")
            )
            nav.class_context("AuthService", file_path="src/auth.py")
            mock_load.assert_called_once_with("AuthService", file_path="src/auth.py")


class TestExplain:
    def test_returns_context_bundle(self):
        from navegador.context.loader import ContextBundle

        nav = _nav([])
        bundle = nav.explain("validate_token")
        assert isinstance(bundle, ContextBundle)
        assert bundle.metadata["query"] == "explain"

    def test_passes_file_path(self):
        store = _mock_store([])
        nav = Navegador(store)

        with patch("navegador.context.loader.ContextLoader.explain") as mock_explain:
            from navegador.context.loader import ContextBundle, ContextNode
            mock_explain.return_value = ContextBundle(
                target=ContextNode(type="Node", name="fn")
            )
            nav.explain("fn", file_path="src/x.py")
            mock_explain.assert_called_once_with("fn", file_path="src/x.py")


# ── Knowledge ─────────────────────────────────────────────────────────────────


class TestAddConcept:
    def test_delegates_to_knowledge_ingester(self):
        store = _mock_store()
        nav = Navegador(store)

        with patch("navegador.ingestion.KnowledgeIngester") as MockK:
            mock_k = MockK.return_value
            nav.add_concept("JWT", description="Stateless token", domain="auth")
            MockK.assert_called_once_with(store)
            mock_k.add_concept.assert_called_once_with(
                "JWT", description="Stateless token", domain="auth"
            )


class TestAddRule:
    def test_delegates_to_knowledge_ingester(self):
        store = _mock_store()
        nav = Navegador(store)

        with patch("navegador.ingestion.KnowledgeIngester") as MockK:
            mock_k = MockK.return_value
            nav.add_rule("tokens must expire", severity="critical")
            mock_k.add_rule.assert_called_once_with(
                "tokens must expire", severity="critical"
            )


class TestAddDecision:
    def test_delegates_to_knowledge_ingester(self):
        store = _mock_store()
        nav = Navegador(store)

        with patch("navegador.ingestion.KnowledgeIngester") as MockK:
            mock_k = MockK.return_value
            nav.add_decision("Use FalkorDB", rationale="Cypher + SQLite", status="accepted")
            mock_k.add_decision.assert_called_once_with(
                "Use FalkorDB", rationale="Cypher + SQLite", status="accepted"
            )


class TestAddPerson:
    def test_delegates_to_knowledge_ingester(self):
        store = _mock_store()
        nav = Navegador(store)

        with patch("navegador.ingestion.KnowledgeIngester") as MockK:
            mock_k = MockK.return_value
            nav.add_person("Alice", email="alice@example.com", role="lead")
            mock_k.add_person.assert_called_once_with(
                "Alice", email="alice@example.com", role="lead"
            )


class TestAddDomain:
    def test_delegates_to_knowledge_ingester(self):
        store = _mock_store()
        nav = Navegador(store)

        with patch("navegador.ingestion.KnowledgeIngester") as MockK:
            mock_k = MockK.return_value
            nav.add_domain("auth", description="Authentication layer")
            mock_k.add_domain.assert_called_once_with(
                "auth", description="Authentication layer"
            )


class TestAnnotate:
    def test_delegates_to_knowledge_ingester(self):
        store = _mock_store()
        nav = Navegador(store)

        with patch("navegador.ingestion.KnowledgeIngester") as MockK:
            mock_k = MockK.return_value
            nav.annotate("validate_token", "Function", concept="JWT")
            mock_k.annotate_code.assert_called_once_with(
                "validate_token", "Function", concept="JWT", rule=None
            )

    def test_passes_rule(self):
        store = _mock_store()
        nav = Navegador(store)

        with patch("navegador.ingestion.KnowledgeIngester") as MockK:
            mock_k = MockK.return_value
            nav.annotate("validate_token", "Function", rule="tokens must expire")
            mock_k.annotate_code.assert_called_once_with(
                "validate_token", "Function", concept=None, rule="tokens must expire"
            )


class TestConceptLoad:
    def test_delegates_to_context_loader(self):
        rows = [["JWT", "Stateless token auth", "active", "auth", [], [], [], []]]
        nav = _nav(rows)
        bundle = nav.concept("JWT")
        assert bundle.target.name == "JWT"
        assert bundle.target.type == "Concept"

    def test_not_found_returns_bundle_with_found_false(self):
        nav = _nav([])
        bundle = nav.concept("NonExistent")
        assert bundle.metadata.get("found") is False


class TestDomainLoad:
    def test_delegates_to_context_loader(self):
        rows = [["Function", "login", "src/auth.py", "Log in"]]
        nav = _nav(rows)
        bundle = nav.domain("auth")
        assert bundle.target.name == "auth"
        assert bundle.target.type == "Domain"


class TestDecisionLoad:
    def test_delegates_to_context_loader(self):
        rows = [[
            "Use FalkorDB",
            "Graph DB",
            "Cypher queries",
            "Neo4j",
            "accepted",
            "2026-01-01",
            "infrastructure",
            [],
            [],
        ]]
        nav = _nav(rows)
        bundle = nav.decision("Use FalkorDB")
        assert bundle.target.name == "Use FalkorDB"
        assert bundle.target.type == "Decision"
        assert bundle.target.rationale == "Cypher queries"

    def test_not_found(self):
        nav = _nav([])
        bundle = nav.decision("Unknown")
        assert bundle.metadata.get("found") is False


# ── Search ────────────────────────────────────────────────────────────────────


class TestSearch:
    def test_search_returns_nodes(self):
        from navegador.context.loader import ContextNode

        rows = [["Function", "validate_token", "src/auth.py", 10, "Validate a token"]]
        nav = _nav(rows)
        results = nav.search("validate")
        assert len(results) == 1
        assert isinstance(results[0], ContextNode)
        assert results[0].name == "validate_token"

    def test_search_empty(self):
        nav = _nav([])
        assert nav.search("xyz") == []

    def test_search_passes_limit(self):
        store = _mock_store([])
        nav = Navegador(store)

        with patch("navegador.context.loader.ContextLoader.search") as mock_search:
            mock_search.return_value = []
            nav.search("auth", limit=5)
            mock_search.assert_called_once_with("auth", limit=5)


class TestSearchAll:
    def test_search_all_returns_nodes(self):
        from navegador.context.loader import ContextNode

        rows = [["Concept", "JWT", "", None, "Stateless token auth"]]
        nav = _nav(rows)
        results = nav.search_all("JWT")
        assert len(results) == 1
        assert results[0].type == "Concept"

    def test_search_all_passes_limit(self):
        store = _mock_store([])
        nav = Navegador(store)

        with patch("navegador.context.loader.ContextLoader.search_all") as mock_sa:
            mock_sa.return_value = []
            nav.search_all("auth", limit=10)
            mock_sa.assert_called_once_with("auth", limit=10)


class TestSearchKnowledge:
    def test_search_knowledge_returns_nodes(self):
        from navegador.context.loader import ContextNode

        rows = [["Concept", "JWT", "Stateless token auth", "auth", "active"]]
        nav = _nav(rows)
        results = nav.search_knowledge("JWT")
        assert len(results) == 1
        assert results[0].domain == "auth"

    def test_search_knowledge_empty(self):
        nav = _nav([])
        assert nav.search_knowledge("missing") == []

    def test_search_knowledge_passes_limit(self):
        store = _mock_store([])
        nav = Navegador(store)

        with patch("navegador.context.loader.ContextLoader.search_knowledge") as mock_sk:
            mock_sk.return_value = []
            nav.search_knowledge("auth", limit=3)
            mock_sk.assert_called_once_with("auth", limit=3)


# ── Graph ─────────────────────────────────────────────────────────────────────


class TestQuery:
    def test_delegates_to_store(self):
        store = _mock_store([[42]])
        nav = Navegador(store)
        result = nav.query("MATCH (n) RETURN count(n)")
        store.query.assert_called_once_with("MATCH (n) RETURN count(n)", None)
        assert result.result_set == [[42]]

    def test_passes_params(self):
        store = _mock_store([])
        nav = Navegador(store)
        nav.query("MATCH (n:Function {name: $name}) RETURN n", {"name": "foo"})
        store.query.assert_called_once_with(
            "MATCH (n:Function {name: $name}) RETURN n", {"name": "foo"}
        )


class TestStats:
    def test_returns_dict_with_expected_keys(self):
        node_result = MagicMock()
        node_result.result_set = [["Function", 5], ["Class", 2]]
        edge_result = MagicMock()
        edge_result.result_set = [["CALLS", 8], ["INHERITS", 1]]

        store = MagicMock()
        store.query.side_effect = [node_result, edge_result]

        nav = Navegador(store)
        s = nav.stats()

        assert s["total_nodes"] == 7
        assert s["total_edges"] == 9
        assert s["nodes"]["Function"] == 5
        assert s["nodes"]["Class"] == 2
        assert s["edges"]["CALLS"] == 8
        assert s["edges"]["INHERITS"] == 1

    def test_empty_graph(self):
        store = _mock_store([])
        nav = Navegador(store)
        s = nav.stats()
        assert s["total_nodes"] == 0
        assert s["total_edges"] == 0
        assert s["nodes"] == {}
        assert s["edges"] == {}


class TestExport:
    def test_delegates_to_export_graph(self):
        store = _mock_store()
        nav = Navegador(store)
        expected = {"nodes": 10, "edges": 5}

        with patch("navegador.graph.export.export_graph", return_value=expected) as mock_export:
            result = nav.export("/tmp/out.jsonl")
            mock_export.assert_called_once_with(store, "/tmp/out.jsonl")
            assert result == expected


class TestImportGraph:
    def test_delegates_to_import_graph(self):
        store = _mock_store()
        nav = Navegador(store)
        expected = {"nodes": 10, "edges": 5}

        with patch("navegador.graph.export.import_graph", return_value=expected) as mock_import:
            result = nav.import_graph("/tmp/in.jsonl")
            mock_import.assert_called_once_with(store, "/tmp/in.jsonl", clear=True)
            assert result == expected

    def test_passes_clear_false(self):
        store = _mock_store()
        nav = Navegador(store)

        with patch("navegador.graph.export.import_graph", return_value={}) as mock_import:
            nav.import_graph("/tmp/in.jsonl", clear=False)
            mock_import.assert_called_once_with(store, "/tmp/in.jsonl", clear=False)


class TestClear:
    def test_delegates_to_store(self):
        store = _mock_store()
        nav = Navegador(store)
        nav.clear()
        store.clear.assert_called_once()


# ── Owners ────────────────────────────────────────────────────────────────────


class TestFindOwners:
    def test_returns_person_nodes(self):
        from navegador.context.loader import ContextNode

        rows = [["Class", "AuthService", "Alice", "alice@example.com", "lead", "auth"]]
        nav = _nav(rows)
        results = nav.find_owners("AuthService")
        assert len(results) == 1
        assert isinstance(results[0], ContextNode)
        assert results[0].type == "Person"
        assert results[0].name == "Alice"

    def test_empty(self):
        nav = _nav([])
        assert nav.find_owners("nobody") == []

    def test_passes_file_path(self):
        store = _mock_store([])
        nav = Navegador(store)

        with patch("navegador.context.loader.ContextLoader.find_owners") as mock_fo:
            mock_fo.return_value = []
            nav.find_owners("AuthService", file_path="src/auth.py")
            mock_fo.assert_called_once_with("AuthService", file_path="src/auth.py")


# ── Top-level import ──────────────────────────────────────────────────────────


class TestTopLevelImport:
    def test_navegador_exported_from_package(self):
        import navegador

        assert hasattr(navegador, "Navegador")
        assert navegador.Navegador is Navegador

    def test_navegador_in_all(self):
        import navegador

        assert "Navegador" in navegador.__all__
