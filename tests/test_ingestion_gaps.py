"""Tests for coverage gaps in dependencies.py, knowledge.py, docgen.py, and testmap.py."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from navegador.graph.schema import EdgeType, NodeLabel

# ═══════════════════════════════════════════════════════════════════════════
# dependencies.py — go.mod parsing and Cargo.toml parsing
# ═══════════════════════════════════════════════════════════════════════════


class TestDependencyIngesterGoMod:
    """Test ingest_gomod() — lines 176-225."""

    def test_parses_module_declaration(self):
        from navegador.dependencies import DependencyIngester

        store = MagicMock()
        ing = DependencyIngester(store)

        with tempfile.TemporaryDirectory() as tmpdir:
            gomod = Path(tmpdir) / "go.mod"
            gomod.write_text(
                "module github.com/example/myapp\n\ngo 1.21\n",
                encoding="utf-8",
            )
            result = ing.ingest_gomod(str(gomod))

        assert result["packages"] == 0
        # Module declaration creates a Concept node
        calls = store.create_node.call_args_list
        module_calls = [c for c in calls if c[0][0] == NodeLabel.Concept and "go:" in str(c)]
        assert len(module_calls) >= 1

    def test_parses_single_line_require(self):
        from navegador.dependencies import DependencyIngester

        store = MagicMock()
        ing = DependencyIngester(store)

        with tempfile.TemporaryDirectory() as tmpdir:
            gomod = Path(tmpdir) / "go.mod"
            gomod.write_text(
                "module example.com/app\n\nrequire github.com/gin-gonic/gin v1.9.1\n",
                encoding="utf-8",
            )
            result = ing.ingest_gomod(str(gomod))

        assert result["packages"] == 1

    def test_parses_block_require(self):
        from navegador.dependencies import DependencyIngester

        store = MagicMock()
        ing = DependencyIngester(store)

        with tempfile.TemporaryDirectory() as tmpdir:
            gomod = Path(tmpdir) / "go.mod"
            gomod.write_text(
                "module example.com/app\n\n"
                "require (\n"
                "\tgithub.com/gin-gonic/gin v1.9.1\n"
                "\tgithub.com/stretchr/testify v1.8.4\n"
                "\t// indirect comment\n"
                ")\n",
                encoding="utf-8",
            )
            result = ing.ingest_gomod(str(gomod))

        assert result["packages"] == 2

    def test_skips_comments_in_require_block(self):
        from navegador.dependencies import DependencyIngester

        store = MagicMock()
        ing = DependencyIngester(store)

        with tempfile.TemporaryDirectory() as tmpdir:
            gomod = Path(tmpdir) / "go.mod"
            gomod.write_text(
                "module example.com/app\n\n"
                "require (\n"
                "\t// This is a comment\n"
                "\tgithub.com/pkg/errors v0.9.1\n"
                ")\n",
                encoding="utf-8",
            )
            result = ing.ingest_gomod(str(gomod))

        assert result["packages"] == 1

    def test_empty_gomod(self):
        from navegador.dependencies import DependencyIngester

        store = MagicMock()
        ing = DependencyIngester(store)

        with tempfile.TemporaryDirectory() as tmpdir:
            gomod = Path(tmpdir) / "go.mod"
            gomod.write_text("module example.com/app\n", encoding="utf-8")
            result = ing.ingest_gomod(str(gomod))

        assert result["packages"] == 0


class TestDependencyIngesterCargo:
    """Test _parse_cargo_toml — lines 288-291, 317, 321 (inline table parsing)."""

    def test_parses_simple_dependency(self):
        from navegador.dependencies import DependencyIngester

        store = MagicMock()
        ing = DependencyIngester(store)

        with tempfile.TemporaryDirectory() as tmpdir:
            cargo = Path(tmpdir) / "Cargo.toml"
            cargo.write_text(
                "[package]\nname = \"myapp\"\nversion = \"0.1.0\"\n\n"
                "[dependencies]\nserde = \"1.0\"\n"
                "tokio = \"1.28\"\n",
                encoding="utf-8",
            )
            result = ing.ingest_cargo(str(cargo))

        assert result["packages"] == 2

    def test_parses_inline_table_dependency(self):
        from navegador.dependencies import DependencyIngester

        store = MagicMock()
        ing = DependencyIngester(store)

        with tempfile.TemporaryDirectory() as tmpdir:
            cargo = Path(tmpdir) / "Cargo.toml"
            cargo.write_text(
                "[dependencies]\n"
                'serde = { version = "1.0", features = ["derive"] }\n',
                encoding="utf-8",
            )
            result = ing.ingest_cargo(str(cargo))

        assert result["packages"] == 1

    def test_parses_dev_dependencies(self):
        from navegador.dependencies import DependencyIngester

        store = MagicMock()
        ing = DependencyIngester(store)

        with tempfile.TemporaryDirectory() as tmpdir:
            cargo = Path(tmpdir) / "Cargo.toml"
            cargo.write_text(
                "[dev-dependencies]\n"
                'criterion = "0.5"\n',
                encoding="utf-8",
            )
            result = ing.ingest_cargo(str(cargo))

        assert result["packages"] == 1

    def test_parses_build_dependencies(self):
        from navegador.dependencies import DependencyIngester

        store = MagicMock()
        ing = DependencyIngester(store)

        with tempfile.TemporaryDirectory() as tmpdir:
            cargo = Path(tmpdir) / "Cargo.toml"
            cargo.write_text(
                "[build-dependencies]\n"
                'cc = "1.0"\n',
                encoding="utf-8",
            )
            result = ing.ingest_cargo(str(cargo))

        assert result["packages"] == 1

    def test_target_specific_dependencies(self):
        """Target-specific deps like [target.'cfg(...)'.dependencies] should be parsed."""
        from navegador.dependencies import DependencyIngester

        store = MagicMock()
        ing = DependencyIngester(store)

        with tempfile.TemporaryDirectory() as tmpdir:
            cargo = Path(tmpdir) / "Cargo.toml"
            cargo.write_text(
                "[target.'cfg(windows)'.dependencies]\n"
                'winapi = "0.3"\n',
                encoding="utf-8",
            )
            result = ing.ingest_cargo(str(cargo))

        assert result["packages"] == 1


# ═══════════════════════════════════════════════════════════════════════════
# knowledge.py — _memory_governs (lines 265-283)
# ═══════════════════════════════════════════════════════════════════════════


class TestKnowledgeMemoryGoverns:
    """Test annotate_code with memory parameter which triggers _memory_governs."""

    def test_memory_governs_creates_edge_when_node_found(self):
        from navegador.ingestion.knowledge import KnowledgeIngester

        store = MagicMock()
        # Query returns a Rule node (label, repo)
        r = MagicMock()
        r.result_set = [["Rule", "my-repo"]]
        store.query.return_value = r

        ki = KnowledgeIngester(store)
        ki.annotate_code("validate_token", "Function", memory="token_expiry_rule")

        # Should have created a GOVERNS edge from Rule -> Function
        store.create_edge.assert_called()
        args = store.create_edge.call_args[0]
        assert args[0] == NodeLabel.Rule
        assert args[1] == {"name": "token_expiry_rule", "repo": "my-repo"}
        assert args[2] == EdgeType.GOVERNS
        assert args[3] == NodeLabel.Function
        assert args[4] == {"name": "validate_token"}

    def test_memory_governs_logs_warning_when_node_not_found(self):
        from navegador.ingestion.knowledge import KnowledgeIngester

        store = MagicMock()
        r = MagicMock()
        r.result_set = []  # no node found
        store.query.return_value = r

        ki = KnowledgeIngester(store)
        # Should not crash
        ki.annotate_code("some_fn", "Function", memory="nonexistent_memory")

        # No edge should have been created
        store.create_edge.assert_not_called()

    def test_memory_governs_with_decision_node(self):
        from navegador.ingestion.knowledge import KnowledgeIngester

        store = MagicMock()
        r = MagicMock()
        r.result_set = [["Decision", "my-repo"]]
        store.query.return_value = r

        ki = KnowledgeIngester(store)
        ki.annotate_code("UserModel", "Class", memory="use_uuid_decision")

        args = store.create_edge.call_args[0]
        assert args[0] == NodeLabel.Decision

    def test_annotate_code_with_concept_and_memory(self):
        """Both concept and memory can be set simultaneously."""
        from navegador.ingestion.knowledge import KnowledgeIngester

        store = MagicMock()
        r = MagicMock()
        r.result_set = [["WikiPage", "my-repo"]]
        store.query.return_value = r

        ki = KnowledgeIngester(store)
        ki.annotate_code("process_payment", "Function", concept="payments", memory="pci_wiki")

        # Should have created both ANNOTATES (concept) and GOVERNS (memory) edges
        assert store.create_edge.call_count == 2

    def test_memory_governs_fails_closed_on_ambiguous_name(self):
        """If the same memory name exists in multiple repos and no repo is specified,
        _memory_governs must not create any edge (fail closed, not fan out)."""
        from navegador.ingestion.knowledge import KnowledgeIngester

        store = MagicMock()
        r = MagicMock()
        # Two repos both have a memory node named "deploy_rule"
        r.result_set = [["Rule", "repo1"], ["Rule", "repo2"]]
        store.query.return_value = r

        ki = KnowledgeIngester(store)
        ki.annotate_code("deploy", "Function", memory="deploy_rule")  # no repo=

        # Must not create any edge — ambiguity detected, fail closed
        store.create_edge.assert_not_called()

    def test_memory_governs_succeeds_when_repo_disambiguates(self):
        """When repo is provided and a single match is returned, the edge is created
        with the (name, repo) composite key."""
        from navegador.ingestion.knowledge import KnowledgeIngester

        store = MagicMock()
        r = MagicMock()
        r.result_set = [["Rule", "repo1"]]  # scoped — only one match
        store.query.return_value = r

        ki = KnowledgeIngester(store)
        ki.annotate_code("deploy", "Function", memory="deploy_rule", repo="repo1")

        store.create_edge.assert_called_once()
        args = store.create_edge.call_args[0]
        assert args[1] == {"name": "deploy_rule", "repo": "repo1"}


# ═══════════════════════════════════════════════════════════════════════════
# docgen.py — LLM-mode methods (lines 268-296)
# ═══════════════════════════════════════════════════════════════════════════


class TestDocGeneratorLLMMode:
    """Test generate_file_docs and generate_module_docs in LLM mode."""

    def _mock_store_with_symbols(self, symbols):
        """Return a store whose query returns symbol rows for _FILE_SYMBOLS / _MODULE_SYMBOLS."""
        store = MagicMock()
        r = MagicMock()
        r.result_set = symbols
        store.query.return_value = r
        return store

    def test_llm_file_docs_calls_nlp_engine(self):
        from navegador.intelligence.docgen import DocGenerator

        store = self._mock_store_with_symbols([
            ["Function", "my_func", "", "def my_func()", 10],
        ])
        provider = MagicMock()
        provider.complete.return_value = "Generated docs for my_func"

        gen = DocGenerator(store, provider=provider)

        with patch("navegador.intelligence.nlp.NLPEngine") as MockNLP:
            mock_engine = MagicMock()
            mock_engine.generate_docs.return_value = "## my_func\nDoes things."
            MockNLP.return_value = mock_engine

            result = gen.generate_file_docs("src/main.py")

        assert "my_func" in result

    def test_llm_file_docs_empty_symbols(self):
        from navegador.intelligence.docgen import DocGenerator

        store = self._mock_store_with_symbols([])
        provider = MagicMock()
        gen = DocGenerator(store, provider=provider)

        with patch("navegador.intelligence.nlp.NLPEngine"):
            result = gen.generate_file_docs("empty.py")

        assert "No symbols found" in result

    def test_llm_module_docs_calls_nlp_engine(self):
        from navegador.intelligence.docgen import DocGenerator

        store = self._mock_store_with_symbols([
            ["Function", "handler", "navegador/api/views.py", "", ""],
        ])
        provider = MagicMock()
        gen = DocGenerator(store, provider=provider)

        with patch("navegador.intelligence.nlp.NLPEngine") as MockNLP:
            mock_engine = MagicMock()
            mock_engine.generate_docs.return_value = "## handler\nHandles requests."
            MockNLP.return_value = mock_engine

            result = gen.generate_module_docs("navegador.api")

        assert "handler" in result

    def test_llm_module_docs_empty_symbols(self):
        from navegador.intelligence.docgen import DocGenerator

        store = self._mock_store_with_symbols([])
        provider = MagicMock()
        gen = DocGenerator(store, provider=provider)

        with patch("navegador.intelligence.nlp.NLPEngine"):
            result = gen.generate_module_docs("navegador.missing")

        assert "No symbols found" in result

    def test_template_project_docs_truncates_files(self):
        """Project docs should show '... and N more' when > 50 files."""
        from navegador.intelligence.docgen import DocGenerator

        store = MagicMock()
        call_count = [0]

        def _query(cypher, params=None):
            r = MagicMock()
            call_count[0] += 1
            if call_count[0] == 1:
                # _STATS
                r.result_set = [["Function", 100], ["Class", 20]]
            elif call_count[0] == 2:
                # _PROJECT_FILES — 55 files
                r.result_set = [[f"file_{i}.py"] for i in range(55)]
            else:
                # _TOP_SYMBOLS
                r.result_set = []
            return r

        store.query.side_effect = _query
        gen = DocGenerator(store)
        result = gen.generate_project_docs()

        assert "and 5 more" in result


# ═══════════════════════════════════════════════════════════════════════════
# testmap.py — edge cases (lines 167-226)
# ═══════════════════════════════════════════════════════════════════════════


class TestTestMapper:
    """Test edge cases in TestMapper."""

    def _make_store(self, test_fns, callee_results=None, prod_results=None):
        """Build a mock store for TestMapper tests.

        Query dispatch based on specific content unique to each query
        constant in testmap.py.
        """
        store = MagicMock()

        def _query(cypher=None, params=None):
            r = MagicMock()
            q = cypher or ""
            if "fn:Function OR fn:Method" in q:
                r.result_set = test_fns
            elif "[:CALLS]->" in q:
                r.result_set = callee_results or []
            elif "MERGE" in q:
                r.result_set = []
            elif "n.name = $name" in q:
                r.result_set = prod_results or []
            else:
                r.result_set = []
            return r

        store.query.side_effect = _query
        return store

    def test_no_test_functions_returns_empty_result(self):
        from navegador.analysis.testmap import TestMapper

        store = self._make_store([])
        mapper = TestMapper(store)
        result = mapper.map_tests()

        assert len(result.links) == 0
        assert len(result.unmatched_tests) == 0
        assert result.edges_created == 0

    def test_test_with_calls_edge_resolved(self):
        from navegador.analysis.testmap import TestMapper

        store = self._make_store(
            test_fns=[["test_validate_token", "tests/test_auth.py", 10]],
            callee_results=[["Function", "validate_token", "auth.py"]],
        )
        mapper = TestMapper(store)
        result = mapper.map_tests()

        assert len(result.links) == 1
        assert result.links[0].prod_name == "validate_token"
        assert result.links[0].source == "calls"

    def test_unmatched_test_recorded(self):
        from navegador.analysis.testmap import TestMapper

        store = self._make_store(
            test_fns=[["test_something_obscure", "tests/test_misc.py", 5]],
            callee_results=[],
            prod_results=[],
        )
        mapper = TestMapper(store)
        result = mapper.map_tests()

        assert len(result.unmatched_tests) == 1
        assert result.unmatched_tests[0]["name"] == "test_something_obscure"

    def test_heuristic_resolution_strips_test_prefix(self):
        from navegador.analysis.testmap import TestMapper

        store = self._make_store(
            test_fns=[["test_handle_request", "tests/test_views.py", 20]],
            callee_results=[],  # no CALLS edge
            prod_results=[["Function", "handle_request", "views.py"]],
        )
        mapper = TestMapper(store)
        result = mapper.map_tests()

        assert len(result.links) == 1
        assert result.links[0].prod_name == "handle_request"
        assert result.links[0].source == "heuristic"

    def test_heuristic_tries_shorter_prefixes(self):
        """test_foo_bar should try 'foo_bar' first, then 'foo'."""
        from navegador.analysis.testmap import TestMapper

        call_log = []
        store = MagicMock()

        def _query(cypher=None, params=None):
            r = MagicMock()
            q = cypher or ""
            if "fn:Function OR fn:Method" in q:
                r.result_set = [["test_foo_bar", "test.py", 1]]
            elif "[:CALLS]->" in q:
                r.result_set = []
            elif "n.name = $name" in q:
                call_log.append(params["name"])
                if params["name"] == "foo":
                    r.result_set = [["Function", "foo", "lib.py"]]
                else:
                    r.result_set = []
            elif "MERGE" in q:
                r.result_set = []
            else:
                r.result_set = []
            return r

        store.query.side_effect = _query
        mapper = TestMapper(store)
        result = mapper.map_tests()

        assert "foo_bar" in call_log
        assert "foo" in call_log
        assert len(result.links) == 1
        assert result.links[0].prod_name == "foo"

    def test_query_exception_in_get_test_functions_handled(self):
        from navegador.analysis.testmap import TestMapper

        store = MagicMock()
        store.query.side_effect = RuntimeError("DB error")
        mapper = TestMapper(store)
        result = mapper.map_tests()

        assert len(result.links) == 0
        assert len(result.unmatched_tests) == 0

    def test_query_exception_in_resolve_calls_handled(self):
        from navegador.analysis.testmap import TestMapper

        store = MagicMock()

        def _query(cypher=None, params=None):
            r = MagicMock()
            q = cypher or ""
            if "fn:Function OR fn:Method" in q:
                r.result_set = [["test_fn", "test.py", 1]]
            elif "[:CALLS]->" in q:
                raise RuntimeError("query failed")
            elif "n.name = $name" in q:
                r.result_set = []
            elif "MERGE" in q:
                r.result_set = []
            else:
                r.result_set = []
            return r

        store.query.side_effect = _query
        mapper = TestMapper(store)
        result = mapper.map_tests()

        # Should have continued despite exception in resolve_via_calls
        assert len(result.unmatched_tests) == 1

    def test_query_exception_in_heuristic_handled(self):
        from navegador.analysis.testmap import TestMapper

        store = MagicMock()

        def _query(cypher=None, params=None):
            r = MagicMock()
            q = cypher or ""
            if "fn:Function OR fn:Method" in q:
                r.result_set = [["test_alpha", "test.py", 1]]
            elif "[:CALLS]->" in q:
                r.result_set = []
            elif "n.name = $name" in q:
                raise RuntimeError("heuristic query failed")
            elif "MERGE" in q:
                r.result_set = []
            else:
                r.result_set = []
            return r

        store.query.side_effect = _query
        mapper = TestMapper(store)
        result = mapper.map_tests()

        # Should handle the exception and mark as unmatched
        assert len(result.unmatched_tests) == 1

    def test_create_edge_exception_handled(self):
        """If MERGE query for TESTS edge fails, edges_created stays at 0."""
        from navegador.analysis.testmap import TestMapper

        store = MagicMock()

        def _query(cypher=None, params=None):
            r = MagicMock()
            q = cypher or ""
            if "fn:Function OR fn:Method" in q:
                r.result_set = [["test_fn", "test.py", 1]]
            elif "[:CALLS]->" in q:
                r.result_set = [["Function", "fn", "lib.py"]]
            elif "MERGE" in q:
                raise RuntimeError("MERGE failed")
            else:
                r.result_set = []
            return r

        store.query.side_effect = _query
        mapper = TestMapper(store)
        result = mapper.map_tests()

        assert len(result.links) == 1
        assert result.edges_created == 0

    def test_to_dict_format(self):
        from navegador.analysis.testmap import TestLink, TestMapResult

        result = TestMapResult(
            links=[
                TestLink(
                    test_name="test_foo", test_file="test.py",
                    prod_name="foo", prod_file="lib.py",
                    prod_type="Function", source="calls",
                ),
            ],
            unmatched_tests=[{"name": "test_orphan", "file_path": "test.py", "line_start": 1}],
            edges_created=1,
        )
        d = result.to_dict()

        assert d["summary"]["matched"] == 1
        assert d["summary"]["unmatched"] == 1
        assert d["summary"]["edges_created"] == 1
        assert d["links"][0]["test_name"] == "test_foo"
        assert d["links"][0]["prod_name"] == "foo"
        assert d["unmatched_tests"][0]["name"] == "test_orphan"

    def test_heuristic_non_test_name_returns_none(self):
        """_resolve_via_heuristic should return None for names not starting with test_."""
        from navegador.analysis.testmap import TestMapper

        store = MagicMock()
        mapper = TestMapper(store)
        assert mapper._resolve_via_heuristic("not_a_test") is None
