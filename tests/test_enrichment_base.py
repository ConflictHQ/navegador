"""Tests for navegador.enrichment — EnrichmentResult, FrameworkEnricher, and CLI."""

import json
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from navegador.enrichment import EnrichmentResult, FrameworkEnricher
from navegador.graph.store import GraphStore

# ── Helpers ───────────────────────────────────────────────────────────────────


def _mock_store(result_set=None):
    """Return a GraphStore backed by a mock FalkorDB graph."""
    client = MagicMock()
    graph = MagicMock()
    graph.query.return_value = MagicMock(result_set=result_set)
    client.select_graph.return_value = graph
    store = GraphStore(client)
    return store


class MockEnricher(FrameworkEnricher):
    """Concrete enricher used in tests."""

    @property
    def framework_name(self) -> str:
        return "mock"

    @property
    def detection_patterns(self) -> list[str]:
        return ["mock_module", "mock_settings.py"]

    def enrich(self) -> EnrichmentResult:
        result = EnrichmentResult()
        result.promoted = 3
        result.edges_added = 2
        result.patterns_found = {"mock_view": 3, "mock_model": 0}
        return result


# ── EnrichmentResult defaults ─────────────────────────────────────────────────


class TestEnrichmentResult:
    def test_promoted_defaults_to_zero(self):
        r = EnrichmentResult()
        assert r.promoted == 0

    def test_edges_added_defaults_to_zero(self):
        r = EnrichmentResult()
        assert r.edges_added == 0

    def test_patterns_found_defaults_to_empty_dict(self):
        r = EnrichmentResult()
        assert r.patterns_found == {}

    def test_attributes_are_mutable(self):
        r = EnrichmentResult()
        r.promoted = 5
        r.edges_added = 10
        r.patterns_found["view"] = 7
        assert r.promoted == 5
        assert r.edges_added == 10
        assert r.patterns_found["view"] == 7

    def test_instances_are_independent(self):
        r1 = EnrichmentResult()
        r2 = EnrichmentResult()
        r1.patterns_found["x"] = 1
        assert "x" not in r2.patterns_found


# ── FrameworkEnricher.detect() ────────────────────────────────────────────────


class TestDetect:
    def test_returns_true_when_pattern_matches(self):
        store = _mock_store(result_set=[[1]])
        enricher = MockEnricher(store)
        assert enricher.detect() is True

    def test_returns_false_when_no_match(self):
        store = _mock_store(result_set=[[0]])
        enricher = MockEnricher(store)
        assert enricher.detect() is False

    def test_returns_false_when_result_set_is_empty(self):
        store = _mock_store(result_set=[])
        enricher = MockEnricher(store)
        assert enricher.detect() is False

    def test_returns_false_when_result_set_is_none(self):
        store = _mock_store(result_set=None)
        enricher = MockEnricher(store)
        assert enricher.detect() is False

    def test_returns_true_on_second_pattern_if_first_misses(self):
        """detect() short-circuits on the first positive match, but we verify
        it tries subsequent patterns when earlier ones return zero."""
        call_count = 0

        def _side_effect(cypher, params):
            nonlocal call_count
            call_count += 1
            # First pattern returns 0, second returns 1
            count = 1 if call_count >= 2 else 0
            return MagicMock(result_set=[[count]])

        client = MagicMock()
        graph = MagicMock()
        graph.query.side_effect = _side_effect
        client.select_graph.return_value = graph
        store = GraphStore(client)

        enricher = MockEnricher(store)
        assert enricher.detect() is True
        assert call_count == 2

    def test_detect_queries_each_pattern_with_correct_param(self):
        store = _mock_store(result_set=[[0]])
        enricher = MockEnricher(store)
        enricher.detect()

        calls = store._graph.query.call_args_list
        # Two patterns → two queries
        assert len(calls) == 2
        params0 = calls[0][0][1] if len(calls[0][0]) > 1 else calls[0][1].get("params", {})
        params1 = calls[1][0][1] if len(calls[1][0]) > 1 else calls[1][1].get("params", {})
        assert params0 == {"name": "mock_module"}
        assert params1 == {"name": "mock_settings.py"}

    def test_stops_early_when_first_pattern_matches(self):
        store = _mock_store(result_set=[[5]])
        enricher = MockEnricher(store)
        assert enricher.detect() is True
        # Should only query once (short-circuit on first match)
        assert store._graph.query.call_count == 1


# ── FrameworkEnricher._promote_node() ────────────────────────────────────────


class TestPromoteNode:
    def test_calls_store_query_with_correct_cypher_and_params(self):
        store = _mock_store()
        enricher = MockEnricher(store)
        enricher._promote_node("MyView", "app/views.py", "DjangoView")

        store._graph.query.assert_called_once()
        cypher, params = store._graph.query.call_args[0]
        assert "SET n.semantic_type = $semantic_type" in cypher
        assert "n.name = $name" in cypher
        assert "n.file_path = $file_path" in cypher
        assert params["name"] == "MyView"
        assert params["file_path"] == "app/views.py"
        assert params["semantic_type"] == "DjangoView"

    def test_extra_props_appended_to_set_clause(self):
        store = _mock_store()
        enricher = MockEnricher(store)
        enricher._promote_node(
            "MyModel", "app/models.py", "DjangoModel", props={"table": "my_table"}
        )

        cypher, params = store._graph.query.call_args[0]
        assert "n.table = $table" in cypher
        assert params["table"] == "my_table"

    def test_no_extra_props_produces_clean_cypher(self):
        store = _mock_store()
        enricher = MockEnricher(store)
        enricher._promote_node("Fn", "a.py", "endpoint")

        cypher, _ = store._graph.query.call_args[0]
        # Should not have a trailing comma or extra SET clause pieces
        assert cypher.count("SET") == 1
        assert "None" not in cypher

    def test_multiple_extra_props(self):
        store = _mock_store()
        enricher = MockEnricher(store)
        enricher._promote_node(
            "Router", "routes.py", "FastAPIRouter", props={"prefix": "/api", "version": "v1"}
        )

        cypher, params = store._graph.query.call_args[0]
        assert "n.prefix = $prefix" in cypher
        assert "n.version = $version" in cypher
        assert params["prefix"] == "/api"
        assert params["version"] == "v1"


# ── FrameworkEnricher._add_semantic_edge() ────────────────────────────────────


class TestAddSemanticEdge:
    def test_calls_store_query_with_correct_cypher_and_params(self):
        store = _mock_store()
        enricher = MockEnricher(store)
        enricher._add_semantic_edge("UserView", "HANDLES", "UserModel")

        store._graph.query.assert_called_once()
        cypher, params = store._graph.query.call_args[0]
        assert "MERGE (a)-[r:HANDLES]->(b)" in cypher
        assert "a.name = $from_name" in cypher
        assert "b.name = $to_name" in cypher
        assert params["from_name"] == "UserView"
        assert params["to_name"] == "UserModel"

    def test_extra_props_produce_set_clause(self):
        store = _mock_store()
        enricher = MockEnricher(store)
        enricher._add_semantic_edge(
            "ViewA", "CALLS", "ViewB", props={"weight": 1, "layer": "http"}
        )

        cypher, params = store._graph.query.call_args[0]
        assert "SET" in cypher
        assert "r.weight = $weight" in cypher
        assert "r.layer = $layer" in cypher
        assert params["weight"] == 1
        assert params["layer"] == "http"

    def test_no_extra_props_no_set_clause(self):
        store = _mock_store()
        enricher = MockEnricher(store)
        enricher._add_semantic_edge("A", "LINKS", "B")

        cypher, _ = store._graph.query.call_args[0]
        assert "SET" not in cypher

    def test_edge_type_is_interpolated(self):
        store = _mock_store()
        enricher = MockEnricher(store)
        enricher._add_semantic_edge("X", "CUSTOM_EDGE_TYPE", "Y")

        cypher, _ = store._graph.query.call_args[0]
        assert "CUSTOM_EDGE_TYPE" in cypher


# ── MockEnricher.enrich() contract ────────────────────────────────────────────


class TestMockEnricherEnrich:
    def test_returns_enrichment_result(self):
        store = _mock_store()
        enricher = MockEnricher(store)
        result = enricher.enrich()
        assert isinstance(result, EnrichmentResult)

    def test_result_values(self):
        store = _mock_store()
        enricher = MockEnricher(store)
        result = enricher.enrich()
        assert result.promoted == 3
        assert result.edges_added == 2
        assert result.patterns_found == {"mock_view": 3, "mock_model": 0}

    def test_framework_name(self):
        store = _mock_store()
        assert MockEnricher(store).framework_name == "mock"

    def test_detection_patterns(self):
        store = _mock_store()
        patterns = MockEnricher(store).detection_patterns
        assert "mock_module" in patterns
        assert "mock_settings.py" in patterns


# ── Abstract enforcement ───────────────────────────────────────────────────────


class TestAbstractEnforcement:
    def test_cannot_instantiate_base_class_directly(self):
        store = _mock_store()
        with pytest.raises(TypeError):
            FrameworkEnricher(store)  # type: ignore[abstract]

    def test_subclass_missing_framework_name_raises(self):
        with pytest.raises(TypeError):

            class Incomplete(FrameworkEnricher):
                @property
                def detection_patterns(self):
                    return []

                def enrich(self):
                    return EnrichmentResult()

            Incomplete(_mock_store())

    def test_subclass_missing_detection_patterns_raises(self):
        with pytest.raises(TypeError):

            class Incomplete(FrameworkEnricher):
                @property
                def framework_name(self):
                    return "x"

                def enrich(self):
                    return EnrichmentResult()

            Incomplete(_mock_store())

    def test_subclass_missing_enrich_raises(self):
        with pytest.raises(TypeError):

            class Incomplete(FrameworkEnricher):
                @property
                def framework_name(self):
                    return "x"

                @property
                def detection_patterns(self):
                    return []

            Incomplete(_mock_store())


# ── CLI: navegador enrich ──────────────────────────────────────────────────────


class TestEnrichCLI:
    def _runner(self):
        return CliRunner()

    def test_no_frameworks_detected_message(self):
        from navegador.cli.commands import main

        runner = self._runner()
        with patch("navegador.cli.commands._get_store", return_value=_mock_store()):
            # No enricher modules in package yet — message should appear
            result = runner.invoke(main, ["enrich"])
        assert result.exit_code == 0
        assert "No frameworks detected" in result.output

    def test_unknown_framework_exits_nonzero(self):
        from navegador.cli.commands import main

        runner = self._runner()
        with patch("navegador.cli.commands._get_store", return_value=_mock_store()):
            result = runner.invoke(main, ["enrich", "--framework", "nonexistent_xyz"])
        assert result.exit_code != 0

    def test_json_flag_produces_empty_object_when_no_frameworks(self):
        from navegador.cli.commands import main

        runner = self._runner()
        with patch("navegador.cli.commands._get_store", return_value=_mock_store()):
            result = runner.invoke(main, ["enrich", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, dict)

    def test_enrich_command_exists_in_main_group(self):
        from navegador.cli.commands import main

        assert "enrich" in main.commands

    def test_enrich_runs_enricher_when_framework_registered(self):
        """Patch the enrichment package discovery to inject MockEnricher."""

        from navegador.cli.commands import main

        runner = self._runner()
        store = _mock_store(result_set=[[1]])

        fake_module = MagicMock()
        fake_module.MockEnricher = MockEnricher

        def fake_iter_modules(path):
            yield MagicMock(), "mock_framework", False

        with patch("navegador.cli.commands._get_store", return_value=store), \
             patch("pkgutil.iter_modules", side_effect=fake_iter_modules), \
             patch("importlib.import_module", return_value=fake_module):
            result = runner.invoke(main, ["enrich", "--framework", "mock"])

        assert result.exit_code == 0
        assert "mock" in result.output.lower()

    def test_enrich_json_output_structure(self):
        """Verify JSON output shape when an enricher runs."""

        from navegador.cli.commands import main

        runner = self._runner()
        store = _mock_store(result_set=[[1]])

        fake_module = MagicMock()
        fake_module.MockEnricher = MockEnricher

        def fake_iter_modules(path):
            yield MagicMock(), "mock_framework", False

        with patch("navegador.cli.commands._get_store", return_value=store), \
             patch("pkgutil.iter_modules", side_effect=fake_iter_modules), \
             patch("importlib.import_module", return_value=fake_module):
            result = runner.invoke(main, ["enrich", "--framework", "mock", "--json"])

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "mock" in data
        assert data["mock"]["promoted"] == 3
        assert data["mock"]["edges_added"] == 2
        assert isinstance(data["mock"]["patterns_found"], dict)
