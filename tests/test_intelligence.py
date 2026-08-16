"""
Tests for the navegador intelligence layer.

Covers:
  - SemanticSearch._cosine_similarity
  - SemanticSearch.index / search (mock graph + mock LLM)
  - CommunityDetector.detect / store_communities (mock graph)
  - NLPEngine.natural_query / name_communities / generate_docs (mock LLM)
  - DocGenerator template mode and LLM mode (mock LLM)
  - CLI commands: semantic-search, communities, ask, generate-docs, docs

All LLM providers are mocked — no real API calls are made.
"""

from __future__ import annotations

import hashlib
import json
import math
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from navegador.cli.commands import main
from navegador.graph import GraphStore

# ── Helpers ───────────────────────────────────────────────────────────────────


def _mock_store(result_rows=None):
    """Return a MagicMock GraphStore whose .query() returns the given rows."""
    store = MagicMock()
    result = MagicMock()
    result.result_set = result_rows if result_rows is not None else []
    store.query.return_value = result
    return store


def _mock_provider(complete_return="mocked answer", embed_return=None):
    """Return a MagicMock LLMProvider."""
    if embed_return is None:
        embed_return = [0.1, 0.2, 0.3, 0.4]
    provider = MagicMock()
    provider.complete.return_value = complete_return
    provider.embed.return_value = embed_return
    provider.name = "mock"
    provider.model = "mock-model"
    return provider


# ── SemanticSearch ────────────────────────────────────────────────────────────
#
# These run against a real embedded store and a real vector index. The previous
# versions mocked the store and asserted the old implementation's internals —
# JSON-encoded vectors and a Python cosine loop — so they could not survive
# moving the k-NN search into the database (#182), and would not have noticed
# if it returned nothing.


class DeterministicProvider:
    """
    A real embedding function with no network: hash tokens into buckets.

    Similar text lands in overlapping buckets, so ranking is meaningful and
    reproducible without an API key or a recorded fixture.
    """

    def __init__(self, dimension: int = 16) -> None:
        self.dimension = dimension
        self.calls = 0

    def embed(self, text: str) -> list[float]:
        self.calls += 1
        vector = [0.0] * self.dimension
        for token in text.lower().replace("_", " ").replace("—", " ").split():
            vector[int(hashlib.md5(token.encode()).hexdigest(), 16) % self.dimension] += 1.0
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]


@pytest.fixture
def store(tmp_path):
    return GraphStore.sqlite(str(tmp_path / "g.db"))


@pytest.fixture
def populated(store):
    store.query(
        "CREATE (:Function {name:'validate_jwt_token', file_path:'auth.py', "
        "docstring:'verify a signed token'})"
    )
    store.query(
        "CREATE (:Function {name:'render_template', file_path:'views.py', "
        "docstring:'produce html output'})"
    )
    store.query(
        "CREATE (:Class {name:'TokenValidator', file_path:'auth.py', docstring:'validates tokens'})"
    )
    return store


class TestSemanticSearchIndex:
    def test_index_embeds_every_labelled_node(self, populated):
        from navegador.intelligence.search import SemanticSearch

        provider = DeterministicProvider()
        assert SemanticSearch(populated, provider).index() == 3
        assert provider.calls == 3

    def test_reindexing_unchanged_content_embeds_nothing(self, populated):
        """
        The old index() re-embedded everything on every call, re-paying the
        whole bill to learn nothing had changed.
        """
        from navegador.intelligence.search import SemanticSearch

        ss = SemanticSearch(populated, DeterministicProvider())
        ss.index()
        assert ss.index() == 0

    def test_changed_content_is_re_embedded(self, populated):
        from navegador.intelligence.search import SemanticSearch

        ss = SemanticSearch(populated, DeterministicProvider())
        ss.index()
        populated.query(
            "MATCH (n:Function {name:'render_template'}) SET n.docstring = 'now does something else'"
        )
        assert ss.index() == 1

    def test_nodes_without_a_docstring_are_still_indexed(self, store):
        """
        Only docstring-bearing nodes used to be embeddable, which made most
        real functions invisible. A name carries meaning on its own.
        """
        from navegador.intelligence.search import SemanticSearch

        store.query("CREATE (:Function {name:'parse_import_statement', file_path:'p.py'})")
        assert SemanticSearch(store, DeterministicProvider()).index() == 1

    def test_index_returns_zero_for_empty_graph(self, store):
        from navegador.intelligence.search import SemanticSearch

        provider = DeterministicProvider()
        assert SemanticSearch(store, provider).index() == 0
        assert provider.calls == 0

    def test_limit_is_honoured(self, populated):
        from navegador.intelligence.search import SemanticSearch

        assert SemanticSearch(populated, DeterministicProvider()).index(limit=2) == 2


class TestSemanticSearchQuery:
    def test_nearest_match_ranks_first(self, populated):
        from navegador.intelligence.search import SemanticSearch

        ss = SemanticSearch(populated, DeterministicProvider())
        ss.index()
        results = ss.search("validate token", limit=3)

        assert results, "vector index returned nothing"
        assert results[0]["name"] == "validate_jwt_token"

    def test_results_are_sorted_by_descending_score(self, populated):
        """
        The index returns cosine *distance*, where lower is nearer. Passing
        that through unchanged would invert every ranking.
        """
        from navegador.intelligence.search import SemanticSearch

        ss = SemanticSearch(populated, DeterministicProvider())
        ss.index()
        scores = [r["score"] for r in ss.search("validate token", limit=5)]
        assert scores == sorted(scores, reverse=True)
        assert all(0.0 <= s <= 1.0 for s in scores)

    def test_limit_is_respected(self, populated):
        from navegador.intelligence.search import SemanticSearch

        ss = SemanticSearch(populated, DeterministicProvider())
        ss.index()
        assert len(ss.search("token", limit=2)) <= 2

    def test_results_span_labels(self, populated):
        """
        A FalkorDB node pattern takes one label and an index is per label, so
        the query fans out; a bug there silently drops whole node types.
        """
        from navegador.intelligence.search import SemanticSearch

        ss = SemanticSearch(populated, DeterministicProvider())
        ss.index()
        types = {r["type"] for r in ss.search("token validation", limit=10)}
        assert "Class" in types and "Function" in types

    def test_search_before_indexing_returns_empty(self, populated):
        """No index yet is not an error — it is simply no answer."""
        from navegador.intelligence.search import SemanticSearch

        assert SemanticSearch(populated, DeterministicProvider()).search("anything") == []

    def test_search_empty_graph_returns_empty_list(self, store):
        from navegador.intelligence.search import SemanticSearch

        assert SemanticSearch(store, DeterministicProvider()).search("anything") == []


# ── CommunityDetector ─────────────────────────────────────────────────────────


class TestCommunityDetector:
    """Tests use a fully in-memory mock — no real FalkorDB required."""

    def _make_store(self, node_rows, edge_rows):
        """
        Return a MagicMock store that returns different rows for the first vs
        subsequent query calls (nodes query, edges query).
        """
        store = MagicMock()

        node_result = MagicMock()
        node_result.result_set = node_rows
        edge_result = MagicMock()
        edge_result.result_set = edge_rows

        # First call → node query, second call → edge query, rest → set_community
        store.query.side_effect = [node_result, edge_result] + [
            MagicMock(result_set=[]) for _ in range(100)
        ]
        return store

    def test_two_cliques_form_separate_communities(self):
        from navegador.intelligence.community import CommunityDetector

        # Nodes: 0-1-2 form a triangle (clique), 3-4 form a pair
        # They have no edges between groups → two communities
        node_rows = [
            [0, "func_a", "a.py", "Function"],
            [1, "func_b", "a.py", "Function"],
            [2, "func_c", "a.py", "Function"],
            [3, "func_d", "b.py", "Function"],
            [4, "func_e", "b.py", "Function"],
        ]
        edge_rows = [
            [0, 1],
            [1, 2],
            [0, 2],  # triangle
            [3, 4],  # pair
        ]
        store = self._make_store(node_rows, edge_rows)
        detector = CommunityDetector(store)
        communities = detector.detect(min_size=2)

        assert len(communities) == 2
        sizes = sorted(c.size for c in communities)
        assert sizes == [2, 3]

    def test_min_size_filters_small_communities(self):
        from navegador.intelligence.community import CommunityDetector

        node_rows = [
            [0, "a", "x.py", "Function"],
            [1, "b", "x.py", "Function"],
            [2, "c", "x.py", "Function"],  # isolated
        ]
        edge_rows = [[0, 1]]
        store = self._make_store(node_rows, edge_rows)
        detector = CommunityDetector(store)

        communities = detector.detect(min_size=2)
        # Only the pair {a, b} passes; isolated node c gets size=1 (filtered)
        assert all(c.size >= 2 for c in communities)

    def test_empty_graph_returns_empty_list(self):
        from navegador.intelligence.community import CommunityDetector

        store = self._make_store([], [])
        detector = CommunityDetector(store)
        communities = detector.detect()
        assert communities == []

    def test_community_density_is_one_for_complete_graph(self):
        from navegador.intelligence.community import CommunityDetector

        # 3-node complete graph
        node_rows = [
            [0, "x", "", "Function"],
            [1, "y", "", "Function"],
            [2, "z", "", "Function"],
        ]
        edge_rows = [[0, 1], [1, 2], [0, 2]]
        store = self._make_store(node_rows, edge_rows)
        detector = CommunityDetector(store)
        communities = detector.detect(min_size=3)

        assert len(communities) == 1
        assert communities[0].density == pytest.approx(1.0)

    def test_community_members_are_strings(self):
        from navegador.intelligence.community import CommunityDetector

        node_rows = [
            [0, "func_alpha", "f.py", "Function"],
            [1, "func_beta", "f.py", "Function"],
        ]
        edge_rows = [[0, 1]]
        store = self._make_store(node_rows, edge_rows)
        detector = CommunityDetector(store)
        communities = detector.detect(min_size=2)

        members = communities[0].members
        assert all(isinstance(m, str) for m in members)
        assert set(members) == {"func_alpha", "func_beta"}

    def test_store_communities_calls_query_for_each_node(self):
        from navegador.intelligence.community import CommunityDetector

        node_rows = [
            [10, "n1", "", "Function"],
            [11, "n2", "", "Function"],
        ]
        edge_rows = [[10, 11]]
        store = self._make_store(node_rows, edge_rows)
        detector = CommunityDetector(store)
        detector.detect(min_size=2)

        # Reset side_effect so store_communities calls work cleanly
        store.query.side_effect = None
        store.query.return_value = MagicMock(result_set=[])

        updated = detector.store_communities()
        assert updated == 2  # two nodes
        assert store.query.call_count >= 2

    def test_community_sorted_largest_first(self):
        from navegador.intelligence.community import CommunityDetector

        # 4-node clique + 2-node pair with a bridge → label propagation may merge
        # Use two fully disconnected groups of sizes 4 and 2
        node_rows = [
            [0, "a", "", "F"],
            [1, "b", "", "F"],
            [2, "c", "", "F"],
            [3, "d", "", "F"],
            [4, "e", "", "F"],
            [5, "f", "", "F"],
        ]
        edge_rows = [
            [0, 1],
            [1, 2],
            [2, 3],
            [0, 3],  # 4-cycle (all same community)
            [4, 5],  # pair
        ]
        store = self._make_store(node_rows, edge_rows)
        detector = CommunityDetector(store)
        communities = detector.detect(min_size=2)
        sizes = [c.size for c in communities]
        assert sizes == sorted(sizes, reverse=True)


# ── NLPEngine ─────────────────────────────────────────────────────────────────


class TestNLPEngine:
    def test_natural_query_calls_complete_twice(self):
        """Should call complete once for Cypher generation, once for formatting."""
        from navegador.intelligence.nlp import NLPEngine

        cypher_response = "MATCH (n:Function) RETURN n.name LIMIT 5"
        format_response = "There are 5 functions: ..."
        provider = MagicMock()
        provider.complete.side_effect = [cypher_response, format_response]

        store = _mock_store([["func_a"], ["func_b"]])
        engine = NLPEngine(store, provider)

        result = engine.natural_query("List all functions")
        assert result == format_response
        assert provider.complete.call_count == 2

    def test_natural_query_handles_query_error(self):
        """When the generated Cypher fails, return an error message."""
        from navegador.intelligence.nlp import NLPEngine

        provider = _mock_provider(complete_return="INVALID CYPHER !!!")
        store = MagicMock()
        store.query.side_effect = Exception("syntax error")

        engine = NLPEngine(store, provider)
        result = engine.natural_query("broken question")

        assert "Failed" in result or "Error" in result or "syntax error" in result

    def test_natural_query_strips_markdown_fences(self):
        """LLM output with ```cypher fences should still execute."""
        from navegador.intelligence.nlp import NLPEngine

        fenced_cypher = "```cypher\nMATCH (n) RETURN n.name LIMIT 1\n```"
        provider = MagicMock()
        provider.complete.side_effect = [fenced_cypher, "One node found."]

        store = _mock_store([["some_node"]])
        engine = NLPEngine(store, provider)
        result = engine.natural_query("find a node")

        assert result == "One node found."
        # Verify the actual query executed was the clean Cypher (no fences)
        executed_cypher = store.query.call_args[0][0]
        assert "```" not in executed_cypher

    def test_name_communities_returns_one_entry_per_community(self):
        from navegador.intelligence.community import Community
        from navegador.intelligence.nlp import NLPEngine

        store = _mock_store()
        provider = _mock_provider(complete_return="Authentication Services")

        comms = [
            Community(name="community_1", members=["login", "logout", "verify_token"], size=3),
            Community(name="community_2", members=["fetch_data", "store_record"], size=2),
        ]
        engine = NLPEngine(store, provider)
        named = engine.name_communities(comms)

        assert len(named) == 2
        assert all("suggested_name" in n for n in named)
        assert all("original_name" in n for n in named)
        assert provider.complete.call_count == 2

    def test_name_communities_fallback_on_llm_error(self):
        """If LLM raises, the original name is used."""
        from navegador.intelligence.community import Community
        from navegador.intelligence.nlp import NLPEngine

        store = _mock_store()
        provider = MagicMock()
        provider.complete.side_effect = RuntimeError("API down")

        comm = Community(name="community_0", members=["a", "b"], size=2)
        engine = NLPEngine(store, provider)
        named = engine.name_communities([comm])

        assert named[0]["suggested_name"] == "community_0"

    def test_generate_docs_returns_llm_string(self):
        from navegador.intelligence.nlp import NLPEngine

        expected_docs = "## my_func\nDoes great things."
        store = _mock_store(
            [["Function", "my_func", "app.py", "Does great things.", "def my_func():"]]
        )
        # Make subsequent query calls (callers, callees) also return empty
        store.query.side_effect = [
            MagicMock(
                result_set=[
                    ["Function", "my_func", "app.py", "Does great things.", "def my_func():"]
                ]
            ),
            MagicMock(result_set=[]),
            MagicMock(result_set=[]),
        ]
        provider = _mock_provider(complete_return=expected_docs)

        engine = NLPEngine(store, provider)
        result = engine.generate_docs("my_func", file_path="app.py")

        assert result == expected_docs
        provider.complete.assert_called_once()

    def test_generate_docs_works_when_node_not_found(self):
        """When node doesn't exist, still calls LLM with empty context."""
        from navegador.intelligence.nlp import NLPEngine

        store = MagicMock()
        store.query.return_value = MagicMock(result_set=[])
        provider = _mock_provider(complete_return="No docs available.")

        engine = NLPEngine(store, provider)
        result = engine.generate_docs("nonexistent_func")

        assert "No docs available." in result


# ── DocGenerator (template mode) ─────────────────────────────────────────────


class TestDocGeneratorTemplateMode:
    def test_generate_file_docs_returns_markdown_with_symbols(self):
        from navegador.intelligence.docgen import DocGenerator

        rows = [
            ["Function", "greet", "Does greeting", "def greet():", 10],
            ["Class", "Greeter", "A greeter class", "class Greeter:", 20],
        ]
        store = _mock_store(rows)
        gen = DocGenerator(store, provider=None)

        docs = gen.generate_file_docs("app.py")

        assert "app.py" in docs
        assert "greet" in docs
        assert "Greeter" in docs
        assert "Does greeting" in docs

    def test_generate_file_docs_handles_empty_file(self):
        from navegador.intelligence.docgen import DocGenerator

        store = _mock_store([])
        gen = DocGenerator(store, provider=None)

        docs = gen.generate_file_docs("empty.py")
        assert "No symbols" in docs

    def test_generate_module_docs_groups_by_file(self):
        from navegador.intelligence.docgen import DocGenerator

        rows = [
            ["Function", "func_a", "nav/graph/store.py", "Store a node", "def func_a():"],
            ["Class", "GraphStore", "nav/graph/store.py", "Wraps the graph.", "class GraphStore:"],
            ["Function", "func_b", "nav/graph/queries.py", "Query helper", "def func_b():"],
        ]
        store = _mock_store(rows)
        gen = DocGenerator(store, provider=None)

        docs = gen.generate_module_docs("nav.graph")
        assert "nav/graph/store.py" in docs
        assert "nav/graph/queries.py" in docs
        assert "func_a" in docs
        assert "GraphStore" in docs

    def test_generate_module_docs_handles_no_results(self):
        from navegador.intelligence.docgen import DocGenerator

        store = _mock_store([])
        gen = DocGenerator(store, provider=None)

        docs = gen.generate_module_docs("empty.module")
        assert "No symbols" in docs

    def test_generate_project_docs_includes_stats_and_files(self):
        from navegador.intelligence.docgen import DocGenerator

        store = MagicMock()

        stats_result = MagicMock()
        stats_result.result_set = [
            ["Function", 42],
            ["Class", 10],
        ]
        files_result = MagicMock()
        files_result.result_set = [
            ["navegador/graph/store.py"],
            ["navegador/cli/commands.py"],
        ]
        sym_result = MagicMock()
        sym_result.result_set = [
            ["Function", "my_func", "navegador/graph/store.py", "Does things"],
        ]
        store.query.side_effect = [stats_result, files_result, sym_result]

        gen = DocGenerator(store, provider=None)
        docs = gen.generate_project_docs()

        assert "Project Documentation" in docs
        assert "Function" in docs
        assert "42" in docs
        assert "navegador/graph/store.py" in docs

    def test_signature_included_when_present(self):
        from navegador.intelligence.docgen import DocGenerator

        rows = [["Function", "my_func", "My doc", "def my_func(x: int) -> str:", 5]]
        store = _mock_store(rows)
        gen = DocGenerator(store, provider=None)

        docs = gen.generate_file_docs("f.py")
        assert "def my_func(x: int) -> str:" in docs


# ── DocGenerator (LLM mode) ───────────────────────────────────────────────────


class TestDocGeneratorLLMMode:
    def test_generate_file_docs_uses_nlp_engine(self):
        from navegador.intelligence.docgen import DocGenerator

        rows = [["Function", "my_func", "Generated docs for my_func", "def my_func():", 1]]
        store = MagicMock()
        # 1st call: _FILE_SYMBOLS  2nd+: NLPEngine internal calls
        store.query.return_value = MagicMock(result_set=rows)

        provider = _mock_provider(complete_return="## my_func\nLLM-generated content.")
        gen = DocGenerator(store, provider=provider)
        docs = gen.generate_file_docs("app.py")

        assert "app.py" in docs
        provider.complete.assert_called()

    def test_generate_project_docs_uses_llm(self):
        from navegador.intelligence.docgen import DocGenerator

        store = MagicMock()
        # Return empty for template sub-calls
        store.query.return_value = MagicMock(result_set=[])

        provider = _mock_provider(complete_return="# Project README\nLLM wrote this.")
        gen = DocGenerator(store, provider=provider)
        docs = gen.generate_project_docs()

        assert "Project README" in docs or "LLM wrote this" in docs
        provider.complete.assert_called_once()


# ── CLI: semantic-search ──────────────────────────────────────────────────────


class TestSemanticSearchCLI:
    def test_search_outputs_table(self):
        runner = CliRunner()
        with (
            patch("navegador.cli.commands._get_store") as mock_store_fn,
            patch("navegador.llm.auto_provider") as mock_auto,
        ):
            store = _mock_store([])
            mock_store_fn.return_value = store
            mock_provider = _mock_provider(embed_return=[1.0, 0.0])
            mock_auto.return_value = mock_provider

            # search returns no results
            from navegador.intelligence.search import SemanticSearch

            with patch.object(SemanticSearch, "search", return_value=[]):
                result = runner.invoke(main, ["semantic-search", "test query"])
                assert result.exit_code == 0

    def test_search_with_index_flag(self):
        runner = CliRunner()
        with (
            patch("navegador.cli.commands._get_store") as mock_store_fn,
            patch("navegador.llm.auto_provider") as mock_auto,
        ):
            store = _mock_store([])
            mock_store_fn.return_value = store
            mock_provider = _mock_provider()
            mock_auto.return_value = mock_provider

            from navegador.intelligence.search import SemanticSearch

            with (
                patch.object(SemanticSearch, "index", return_value=5) as mock_index,
                patch.object(SemanticSearch, "search", return_value=[]),
            ):
                result = runner.invoke(main, ["semantic-search", "test", "--index"])
                assert result.exit_code == 0
                mock_index.assert_called_once()

    def test_search_json_output(self):
        runner = CliRunner()
        fake_results = [
            {"type": "Function", "name": "foo", "file_path": "a.py", "text": "doc", "score": 0.95}
        ]
        with (
            patch("navegador.cli.commands._get_store") as mock_store_fn,
            patch("navegador.llm.auto_provider") as mock_auto,
        ):
            store = _mock_store([])
            mock_store_fn.return_value = store
            mock_auto.return_value = _mock_provider()

            from navegador.intelligence.search import SemanticSearch

            with patch.object(SemanticSearch, "search", return_value=fake_results):
                result = runner.invoke(main, ["semantic-search", "foo", "--json"])
                assert result.exit_code == 0
                data = json.loads(result.output)
                assert isinstance(data, list)
                assert data[0]["name"] == "foo"


# ── CLI: communities ──────────────────────────────────────────────────────────


class TestCommunitiesCLI:
    def _make_communities(self):
        from navegador.intelligence.community import Community

        return [
            Community(name="community_0", members=["a", "b", "c"], size=3, density=1.0),
            Community(name="community_1", members=["x", "y"], size=2, density=1.0),
        ]

    def test_communities_outputs_table(self):
        runner = CliRunner()
        with patch("navegador.cli.commands._get_store") as mock_store_fn:
            mock_store_fn.return_value = _mock_store()
            from navegador.intelligence.community import CommunityDetector

            with patch.object(CommunityDetector, "detect", return_value=self._make_communities()):
                result = runner.invoke(main, ["communities"])
                assert result.exit_code == 0

    def test_communities_json_output(self):
        runner = CliRunner()
        with patch("navegador.cli.commands._get_store") as mock_store_fn:
            mock_store_fn.return_value = _mock_store()
            from navegador.intelligence.community import CommunityDetector

            with patch.object(CommunityDetector, "detect", return_value=self._make_communities()):
                result = runner.invoke(main, ["communities", "--json"])
                assert result.exit_code == 0
                data = json.loads(result.output)
                assert len(data) == 2
                assert data[0]["name"] == "community_0"

    def test_communities_min_size_passed(self):
        runner = CliRunner()
        with patch("navegador.cli.commands._get_store") as mock_store_fn:
            mock_store_fn.return_value = _mock_store()
            from navegador.intelligence.community import CommunityDetector

            with patch.object(CommunityDetector, "detect", return_value=[]) as mock_detect:
                runner.invoke(main, ["communities", "--min-size", "5"])
                mock_detect.assert_called_once_with(min_size=5)

    def test_communities_empty_graph_message(self):
        runner = CliRunner()
        with patch("navegador.cli.commands._get_store") as mock_store_fn:
            mock_store_fn.return_value = _mock_store()
            from navegador.intelligence.community import CommunityDetector

            with patch.object(CommunityDetector, "detect", return_value=[]):
                result = runner.invoke(main, ["communities"])
                assert result.exit_code == 0
                assert "No communities" in result.output or result.exit_code == 0

    def test_communities_store_labels_flag(self):
        runner = CliRunner()
        with patch("navegador.cli.commands._get_store") as mock_store_fn:
            mock_store_fn.return_value = _mock_store()
            from navegador.intelligence.community import CommunityDetector

            with (
                patch.object(CommunityDetector, "detect", return_value=self._make_communities()),
                patch.object(CommunityDetector, "store_communities", return_value=5) as mock_store,
            ):
                result = runner.invoke(main, ["communities", "--store-labels"])
                assert result.exit_code == 0
                mock_store.assert_called_once()


# ── CLI: ask ──────────────────────────────────────────────────────────────────


class TestAskCLI:
    def test_ask_prints_answer(self):
        runner = CliRunner()
        with (
            patch("navegador.cli.commands._get_store") as mock_store_fn,
            patch("navegador.llm.auto_provider") as mock_auto,
        ):
            mock_store_fn.return_value = _mock_store()
            mock_auto.return_value = _mock_provider()

            from navegador.intelligence.nlp import NLPEngine

            with patch.object(NLPEngine, "natural_query", return_value="The answer is 42."):
                result = runner.invoke(main, ["ask", "What is the answer?"])
                assert result.exit_code == 0
                assert "42" in result.output

    def test_ask_with_explicit_provider(self):
        runner = CliRunner()
        with (
            patch("navegador.cli.commands._get_store") as mock_store_fn,
            patch("navegador.llm.get_provider") as mock_get,
        ):
            mock_store_fn.return_value = _mock_store()
            mock_get.return_value = _mock_provider()

            from navegador.intelligence.nlp import NLPEngine

            with patch.object(NLPEngine, "natural_query", return_value="Answer."):
                result = runner.invoke(main, ["ask", "question", "--provider", "openai"])
                assert result.exit_code == 0
                mock_get.assert_called_once_with("openai", model="")


# ── CLI: generate-docs ────────────────────────────────────────────────────────


class TestGenerateDocsCLI:
    def test_generate_docs_prints_output(self):
        runner = CliRunner()
        with (
            patch("navegador.cli.commands._get_store") as mock_store_fn,
            patch("navegador.llm.auto_provider") as mock_auto,
        ):
            mock_store_fn.return_value = _mock_store()
            mock_auto.return_value = _mock_provider()

            from navegador.intelligence.nlp import NLPEngine

            with patch.object(NLPEngine, "generate_docs", return_value="## my_func\nDocs here."):
                result = runner.invoke(main, ["generate-docs", "my_func"])
                assert result.exit_code == 0
                assert "my_func" in result.output or "Docs" in result.output

    def test_generate_docs_with_file_option(self):
        runner = CliRunner()
        with (
            patch("navegador.cli.commands._get_store") as mock_store_fn,
            patch("navegador.llm.auto_provider") as mock_auto,
        ):
            mock_store_fn.return_value = _mock_store()
            mock_auto.return_value = _mock_provider()

            from navegador.intelligence.nlp import NLPEngine

            with patch.object(NLPEngine, "generate_docs", return_value="Docs.") as mock_gd:
                runner.invoke(main, ["generate-docs", "my_func", "--file", "app.py"])
                mock_gd.assert_called_once_with("my_func", file_path="app.py")


# ── CLI: docs ─────────────────────────────────────────────────────────────────


class TestDocsCLI:
    def test_docs_file_path(self):
        runner = CliRunner()
        with patch("navegador.cli.commands._get_store") as mock_store_fn:
            mock_store_fn.return_value = _mock_store()
            from navegador.intelligence.docgen import DocGenerator

            with patch.object(
                DocGenerator, "generate_file_docs", return_value="# File docs"
            ) as mock_fd:
                result = runner.invoke(main, ["docs", "app/store.py"])
                assert result.exit_code == 0
                mock_fd.assert_called_once_with("app/store.py")

    def test_docs_module_name(self):
        runner = CliRunner()
        with patch("navegador.cli.commands._get_store") as mock_store_fn:
            mock_store_fn.return_value = _mock_store()
            from navegador.intelligence.docgen import DocGenerator

            with patch.object(
                DocGenerator, "generate_module_docs", return_value="# Module docs"
            ) as mock_md:
                result = runner.invoke(main, ["docs", "navegador.graph"])
                assert result.exit_code == 0
                mock_md.assert_called_once_with("navegador.graph")

    def test_docs_project_flag(self):
        runner = CliRunner()
        with patch("navegador.cli.commands._get_store") as mock_store_fn:
            mock_store_fn.return_value = _mock_store()
            from navegador.intelligence.docgen import DocGenerator

            with patch.object(
                DocGenerator, "generate_project_docs", return_value="# Project"
            ) as mock_pd:
                result = runner.invoke(main, ["docs", ".", "--project"])
                assert result.exit_code == 0
                mock_pd.assert_called_once()

    def test_docs_json_output(self):
        runner = CliRunner()
        with patch("navegador.cli.commands._get_store") as mock_store_fn:
            mock_store_fn.return_value = _mock_store()
            from navegador.intelligence.docgen import DocGenerator

            with patch.object(DocGenerator, "generate_project_docs", return_value="# Project"):
                result = runner.invoke(main, ["docs", ".", "--project", "--json"])
                assert result.exit_code == 0
                data = json.loads(result.output)
                assert "docs" in data

    def test_docs_with_llm_provider(self):
        runner = CliRunner()
        with (
            patch("navegador.cli.commands._get_store") as mock_store_fn,
            patch(
                "navegador.intelligence.docgen.DocGenerator.generate_file_docs",
                return_value="# Docs",
            ),
        ):
            mock_store_fn.return_value = _mock_store()
            with patch("navegador.llm.get_provider") as mock_get:
                mock_get.return_value = _mock_provider()
                result = runner.invoke(main, ["docs", "app/store.py", "--provider", "openai"])
                assert result.exit_code == 0
                mock_get.assert_called_once_with("openai", model="")
