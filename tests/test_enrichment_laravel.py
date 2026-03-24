"""Tests for navegador.enrichment.laravel — LaravelEnricher."""

from unittest.mock import MagicMock

import pytest

from navegador.enrichment.base import EnrichmentResult, FrameworkEnricher
from navegador.enrichment.laravel import LaravelEnricher
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


class TestLaravelEnricherIdentity:
    def test_framework_name(self):
        store = _mock_store()
        assert LaravelEnricher(store).framework_name == "laravel"

    def test_is_framework_enricher_subclass(self):
        assert issubclass(LaravelEnricher, FrameworkEnricher)

    def test_detection_patterns_contains_illuminate(self):
        store = _mock_store()
        assert "Illuminate" in LaravelEnricher(store).detection_patterns

    def test_detection_files_contains_artisan(self):
        store = _mock_store()
        assert "artisan" in LaravelEnricher(store).detection_files

    def test_detection_patterns_has_one_entry(self):
        store = _mock_store()
        assert len(LaravelEnricher(store).detection_patterns) == 1

    def test_detection_files_is_nonempty(self):
        store = _mock_store()
        assert len(LaravelEnricher(store).detection_files) >= 1


# ── enrich() return type ──────────────────────────────────────────────────────


class TestLaravelEnricherEnrichReturnType:
    def test_returns_enrichment_result(self):
        store = _mock_store(result_set=[])
        result = LaravelEnricher(store).enrich()
        assert isinstance(result, EnrichmentResult)

    def test_result_has_promoted_attribute(self):
        store = _mock_store(result_set=[])
        result = LaravelEnricher(store).enrich()
        assert hasattr(result, "promoted")

    def test_result_has_edges_added_attribute(self):
        store = _mock_store(result_set=[])
        result = LaravelEnricher(store).enrich()
        assert hasattr(result, "edges_added")

    def test_result_has_patterns_found_attribute(self):
        store = _mock_store(result_set=[])
        result = LaravelEnricher(store).enrich()
        assert hasattr(result, "patterns_found")


# ── enrich() with no matching nodes ──────────────────────────────────────────


class TestLaravelEnricherNoMatches:
    def test_promoted_is_zero_when_no_nodes(self):
        store = _mock_store(result_set=[])
        result = LaravelEnricher(store).enrich()
        assert result.promoted == 0

    def test_all_pattern_counts_zero_when_no_nodes(self):
        store = _mock_store(result_set=[])
        result = LaravelEnricher(store).enrich()
        for key in ("controllers", "routes", "jobs", "policies", "models"):
            assert result.patterns_found[key] == 0

    def test_patterns_found_has_five_keys(self):
        store = _mock_store(result_set=[])
        result = LaravelEnricher(store).enrich()
        assert set(result.patterns_found.keys()) == {
            "controllers", "routes", "jobs", "policies", "models"
        }


# ── enrich() with matching nodes ─────────────────────────────────────────────


class TestLaravelEnricherWithMatches:
    def _make_store_for_fragment(self, target_fragment, rows):
        """Return a store that returns `rows` only when the fragment matches."""

        def side_effect(cypher, params):
            fragment = params.get("fragment", "")
            if fragment == target_fragment:
                return MagicMock(result_set=rows)
            return MagicMock(result_set=[])

        return _store_with_side_effect(side_effect)

    def test_controller_promoted(self):
        store = self._make_store_for_fragment(
            "Controllers/",
            [["UserController", "app/Http/Controllers/UserController.php"]],
        )
        result = LaravelEnricher(store).enrich()
        assert result.patterns_found["controllers"] == 1
        assert result.promoted >= 1

    def test_route_promoted(self):
        store = self._make_store_for_fragment(
            "routes/",
            [["web", "routes/web.php"]],
        )
        result = LaravelEnricher(store).enrich()
        assert result.patterns_found["routes"] == 1
        assert result.promoted >= 1

    def test_job_promoted(self):
        store = self._make_store_for_fragment(
            "Jobs/",
            [["SendEmailJob", "app/Jobs/SendEmailJob.php"]],
        )
        result = LaravelEnricher(store).enrich()
        assert result.patterns_found["jobs"] == 1
        assert result.promoted >= 1

    def test_policy_promoted(self):
        store = self._make_store_for_fragment(
            "Policies/",
            [["UserPolicy", "app/Policies/UserPolicy.php"]],
        )
        result = LaravelEnricher(store).enrich()
        assert result.patterns_found["policies"] == 1
        assert result.promoted >= 1

    def test_model_promoted_via_model_keyword(self):
        store = self._make_store_for_fragment(
            "Model",
            [["User", "app/Models/User.php"]],
        )
        result = LaravelEnricher(store).enrich()
        assert result.patterns_found["models"] == 1
        assert result.promoted >= 1

    def test_promoted_count_accumulates_across_types(self):
        rows_map = {
            "Controllers/": [
                ["UserController", "app/Http/Controllers/UserController.php"],
                ["PostController", "app/Http/Controllers/PostController.php"],
            ],
            "routes/": [["web", "routes/web.php"]],
            "Jobs/": [],
            "Policies/": [["UserPolicy", "app/Policies/UserPolicy.php"]],
            "Model": [],
        }

        def side_effect(cypher, params):
            fragment = params.get("fragment", "")
            return MagicMock(result_set=rows_map.get(fragment, []))

        store = _store_with_side_effect(side_effect)
        result = LaravelEnricher(store).enrich()
        assert result.promoted == 4
        assert result.patterns_found["controllers"] == 2
        assert result.patterns_found["routes"] == 1
        assert result.patterns_found["policies"] == 1
        assert result.patterns_found["jobs"] == 0
        assert result.patterns_found["models"] == 0

    def test_promote_node_called_with_laravel_controller_type(self):
        store = self._make_store_for_fragment(
            "Controllers/",
            [["UserController", "app/Http/Controllers/UserController.php"]],
        )
        LaravelEnricher(store).enrich()
        calls = [str(c) for c in store._graph.query.call_args_list]
        promote_calls = [c for c in calls if "semantic_type" in c and "LaravelController" in c]
        assert len(promote_calls) >= 1

    def test_promote_node_called_with_laravel_model_type(self):
        store = self._make_store_for_fragment(
            "Model",
            [["User", "app/Models/User.php"]],
        )
        LaravelEnricher(store).enrich()
        calls = [str(c) for c in store._graph.query.call_args_list]
        promote_calls = [c for c in calls if "semantic_type" in c and "LaravelModel" in c]
        assert len(promote_calls) >= 1

    def test_promote_node_called_with_laravel_route_type(self):
        store = self._make_store_for_fragment(
            "routes/",
            [["web", "routes/web.php"]],
        )
        LaravelEnricher(store).enrich()
        calls = [str(c) for c in store._graph.query.call_args_list]
        promote_calls = [c for c in calls if "semantic_type" in c and "LaravelRoute" in c]
        assert len(promote_calls) >= 1

    def test_promote_node_called_with_laravel_job_type(self):
        store = self._make_store_for_fragment(
            "Jobs/",
            [["SendEmailJob", "app/Jobs/SendEmailJob.php"]],
        )
        LaravelEnricher(store).enrich()
        calls = [str(c) for c in store._graph.query.call_args_list]
        promote_calls = [c for c in calls if "semantic_type" in c and "LaravelJob" in c]
        assert len(promote_calls) >= 1

    def test_promote_node_called_with_laravel_policy_type(self):
        store = self._make_store_for_fragment(
            "Policies/",
            [["UserPolicy", "app/Policies/UserPolicy.php"]],
        )
        LaravelEnricher(store).enrich()
        calls = [str(c) for c in store._graph.query.call_args_list]
        promote_calls = [c for c in calls if "semantic_type" in c and "LaravelPolicy" in c]
        assert len(promote_calls) >= 1

    def test_model_query_uses_fragment_param_named_fragment(self):
        """Model detection must pass fragment='Model', not an 'annotation' key."""
        captured = []

        def side_effect(cypher, params):
            captured.append(dict(params))
            return MagicMock(result_set=[])

        store = _store_with_side_effect(side_effect)
        LaravelEnricher(store).enrich()
        model_queries = [p for p in captured if p.get("fragment") == "Model"]
        assert len(model_queries) >= 1


# ── detect() integration ──────────────────────────────────────────────────────


class TestLaravelEnricherDetect:
    def test_detect_true_when_artisan_present(self):
        store = _mock_store(result_set=[[1]])
        assert LaravelEnricher(store).detect() is True

    def test_detect_false_when_no_patterns_match(self):
        store = _mock_store(result_set=[[0]])
        assert LaravelEnricher(store).detect() is False

    def test_detect_false_on_empty_result_set(self):
        store = _mock_store(result_set=[])
        assert LaravelEnricher(store).detect() is False
