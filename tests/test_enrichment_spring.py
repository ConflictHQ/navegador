"""Tests for navegador.enrichment.spring — SpringEnricher."""

from unittest.mock import MagicMock

import pytest

from navegador.enrichment.base import EnrichmentResult, FrameworkEnricher
from navegador.enrichment.spring import SpringEnricher
from navegador.graph.store import GraphStore


# ── Helpers ───────────────────────────────────────────────────────────────────


def _mock_store(result_set=None):
    """Return a GraphStore backed by a mock FalkorDB graph."""
    client = MagicMock()
    graph = MagicMock()
    graph.query.return_value = MagicMock(result_set=result_set)
    client.select_graph.return_value = graph
    return GraphStore(client)


def _store_with_side_effect(side_effect):
    """Return a GraphStore whose graph.query uses a side_effect callable."""
    client = MagicMock()
    graph = MagicMock()
    graph.query.side_effect = side_effect
    client.select_graph.return_value = graph
    return GraphStore(client)


# ── Identity / contract ───────────────────────────────────────────────────────


class TestSpringEnricherIdentity:
    def test_framework_name(self):
        store = _mock_store()
        assert SpringEnricher(store).framework_name == "spring"

    def test_is_framework_enricher_subclass(self):
        assert issubclass(SpringEnricher, FrameworkEnricher)

    def test_detection_patterns_contains_spring_boot_application(self):
        store = _mock_store()
        assert "@SpringBootApplication" in SpringEnricher(store).detection_patterns

    def test_detection_patterns_contains_spring_boot(self):
        store = _mock_store()
        assert "spring-boot" in SpringEnricher(store).detection_patterns

    def test_detection_patterns_contains_application_properties(self):
        store = _mock_store()
        assert "application.properties" in SpringEnricher(store).detection_patterns

    def test_detection_patterns_has_three_entries(self):
        store = _mock_store()
        assert len(SpringEnricher(store).detection_patterns) == 3


# ── enrich() return type ──────────────────────────────────────────────────────


class TestSpringEnricherEnrichReturnType:
    def test_returns_enrichment_result(self):
        store = _mock_store(result_set=[])
        result = SpringEnricher(store).enrich()
        assert isinstance(result, EnrichmentResult)

    def test_result_has_promoted_attribute(self):
        store = _mock_store(result_set=[])
        result = SpringEnricher(store).enrich()
        assert hasattr(result, "promoted")

    def test_result_has_edges_added_attribute(self):
        store = _mock_store(result_set=[])
        result = SpringEnricher(store).enrich()
        assert hasattr(result, "edges_added")

    def test_result_has_patterns_found_attribute(self):
        store = _mock_store(result_set=[])
        result = SpringEnricher(store).enrich()
        assert hasattr(result, "patterns_found")


# ── enrich() with no matching nodes ──────────────────────────────────────────


class TestSpringEnricherNoMatches:
    def test_promoted_is_zero_when_no_nodes(self):
        store = _mock_store(result_set=[])
        result = SpringEnricher(store).enrich()
        assert result.promoted == 0

    def test_all_pattern_counts_zero_when_no_nodes(self):
        store = _mock_store(result_set=[])
        result = SpringEnricher(store).enrich()
        for key in ("controllers", "rest_controllers", "services", "repositories", "entities"):
            assert result.patterns_found[key] == 0

    def test_patterns_found_has_five_keys(self):
        store = _mock_store(result_set=[])
        result = SpringEnricher(store).enrich()
        assert set(result.patterns_found.keys()) == {
            "controllers", "rest_controllers", "services", "repositories", "entities"
        }


# ── enrich() with matching nodes ─────────────────────────────────────────────


class TestSpringEnricherWithMatches:
    def _make_store_for_annotation(self, target_annotation, rows):
        """Return a store that returns `rows` only when the annotation matches."""

        def side_effect(cypher, params):
            annotation = params.get("annotation", "")
            if annotation == target_annotation:
                return MagicMock(result_set=rows)
            return MagicMock(result_set=[])

        return _store_with_side_effect(side_effect)

    def test_controller_promoted(self):
        store = self._make_store_for_annotation(
            "@Controller",
            [["UserController", "src/main/java/com/example/UserController.java"]],
        )
        result = SpringEnricher(store).enrich()
        assert result.patterns_found["controllers"] == 1
        assert result.promoted >= 1

    def test_rest_controller_promoted(self):
        store = self._make_store_for_annotation(
            "@RestController",
            [["UserRestController", "src/main/java/com/example/UserRestController.java"]],
        )
        result = SpringEnricher(store).enrich()
        assert result.patterns_found["rest_controllers"] == 1
        assert result.promoted >= 1

    def test_service_promoted(self):
        store = self._make_store_for_annotation(
            "@Service",
            [["UserService", "src/main/java/com/example/UserService.java"]],
        )
        result = SpringEnricher(store).enrich()
        assert result.patterns_found["services"] == 1
        assert result.promoted >= 1

    def test_repository_promoted(self):
        store = self._make_store_for_annotation(
            "@Repository",
            [["UserRepository", "src/main/java/com/example/UserRepository.java"]],
        )
        result = SpringEnricher(store).enrich()
        assert result.patterns_found["repositories"] == 1
        assert result.promoted >= 1

    def test_entity_promoted(self):
        store = self._make_store_for_annotation(
            "@Entity",
            [["User", "src/main/java/com/example/User.java"]],
        )
        result = SpringEnricher(store).enrich()
        assert result.patterns_found["entities"] == 1
        assert result.promoted >= 1

    def test_promoted_count_accumulates_across_types(self):
        rows_map = {
            "@Controller": [
                ["UserController", "src/main/java/UserController.java"],
            ],
            "@RestController": [
                ["ApiController", "src/main/java/ApiController.java"],
                ["OrderController", "src/main/java/OrderController.java"],
            ],
            "@Service": [],
            "@Repository": [["UserRepository", "src/main/java/UserRepository.java"]],
            "@Entity": [],
        }

        def side_effect(cypher, params):
            annotation = params.get("annotation", "")
            return MagicMock(result_set=rows_map.get(annotation, []))

        store = _store_with_side_effect(side_effect)
        result = SpringEnricher(store).enrich()
        assert result.promoted == 4
        assert result.patterns_found["controllers"] == 1
        assert result.patterns_found["rest_controllers"] == 2
        assert result.patterns_found["repositories"] == 1

    def test_promote_node_called_with_spring_controller_type(self):
        store = self._make_store_for_annotation(
            "@Controller",
            [["UserController", "src/main/java/UserController.java"]],
        )
        SpringEnricher(store).enrich()
        calls = [str(c) for c in store._graph.query.call_args_list]
        promote_calls = [c for c in calls if "semantic_type" in c and "SpringController" in c]
        assert len(promote_calls) >= 1

    def test_promote_node_called_with_spring_service_type(self):
        store = self._make_store_for_annotation(
            "@Service",
            [["UserService", "src/main/java/UserService.java"]],
        )
        SpringEnricher(store).enrich()
        calls = [str(c) for c in store._graph.query.call_args_list]
        promote_calls = [c for c in calls if "semantic_type" in c and "SpringService" in c]
        assert len(promote_calls) >= 1

    def test_promote_node_called_with_spring_repository_type(self):
        store = self._make_store_for_annotation(
            "@Repository",
            [["UserRepository", "src/main/java/UserRepository.java"]],
        )
        SpringEnricher(store).enrich()
        calls = [str(c) for c in store._graph.query.call_args_list]
        promote_calls = [c for c in calls if "semantic_type" in c and "SpringRepository" in c]
        assert len(promote_calls) >= 1

    def test_promote_node_called_with_spring_entity_type(self):
        store = self._make_store_for_annotation(
            "@Entity",
            [["User", "src/main/java/User.java"]],
        )
        SpringEnricher(store).enrich()
        calls = [str(c) for c in store._graph.query.call_args_list]
        promote_calls = [c for c in calls if "semantic_type" in c and "SpringEntity" in c]
        assert len(promote_calls) >= 1

    def test_promote_node_called_with_spring_rest_controller_type(self):
        store = self._make_store_for_annotation(
            "@RestController",
            [["ApiController", "src/main/java/ApiController.java"]],
        )
        SpringEnricher(store).enrich()
        calls = [str(c) for c in store._graph.query.call_args_list]
        promote_calls = [c for c in calls if "semantic_type" in c and "SpringRestController" in c]
        assert len(promote_calls) >= 1

    def test_query_uses_annotation_param_not_fragment(self):
        """Verify enrich() passes 'annotation' key, not 'fragment', to the store."""
        captured = []

        def side_effect(cypher, params):
            captured.append(params)
            return MagicMock(result_set=[])

        store = _store_with_side_effect(side_effect)
        SpringEnricher(store).enrich()
        # All enrich queries (not _promote_node queries) should use 'annotation'
        enrich_queries = [p for p in captured if "annotation" in p]
        assert len(enrich_queries) == 5


# ── detect() integration ──────────────────────────────────────────────────────


class TestSpringEnricherDetect:
    def test_detect_true_when_spring_boot_annotation_present(self):
        store = _mock_store(result_set=[[1]])
        assert SpringEnricher(store).detect() is True

    def test_detect_false_when_no_patterns_match(self):
        store = _mock_store(result_set=[[0]])
        assert SpringEnricher(store).detect() is False

    def test_detect_false_on_empty_result_set(self):
        store = _mock_store(result_set=[])
        assert SpringEnricher(store).detect() is False
