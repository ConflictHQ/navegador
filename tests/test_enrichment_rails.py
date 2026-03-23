"""Tests for navegador.enrichment.rails — RailsEnricher."""

from unittest.mock import MagicMock, call

import pytest

from navegador.enrichment.base import EnrichmentResult, FrameworkEnricher
from navegador.enrichment.rails import RailsEnricher
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


class TestRailsEnricherIdentity:
    def test_framework_name(self):
        store = _mock_store()
        assert RailsEnricher(store).framework_name == "rails"

    def test_is_framework_enricher_subclass(self):
        assert issubclass(RailsEnricher, FrameworkEnricher)

    def test_detection_patterns_contains_gemfile(self):
        store = _mock_store()
        assert "Gemfile" in RailsEnricher(store).detection_patterns

    def test_detection_patterns_contains_routes(self):
        store = _mock_store()
        assert "config/routes.rb" in RailsEnricher(store).detection_patterns

    def test_detection_patterns_contains_application_controller(self):
        store = _mock_store()
        assert "ApplicationController" in RailsEnricher(store).detection_patterns

    def test_detection_patterns_contains_active_record(self):
        store = _mock_store()
        assert "ActiveRecord" in RailsEnricher(store).detection_patterns

    def test_detection_patterns_has_four_entries(self):
        store = _mock_store()
        assert len(RailsEnricher(store).detection_patterns) == 4


# ── enrich() return type ──────────────────────────────────────────────────────


class TestRailsEnricherEnrichReturnType:
    def test_returns_enrichment_result(self):
        store = _mock_store(result_set=[])
        result = RailsEnricher(store).enrich()
        assert isinstance(result, EnrichmentResult)

    def test_result_has_promoted_attribute(self):
        store = _mock_store(result_set=[])
        result = RailsEnricher(store).enrich()
        assert hasattr(result, "promoted")

    def test_result_has_edges_added_attribute(self):
        store = _mock_store(result_set=[])
        result = RailsEnricher(store).enrich()
        assert hasattr(result, "edges_added")

    def test_result_has_patterns_found_attribute(self):
        store = _mock_store(result_set=[])
        result = RailsEnricher(store).enrich()
        assert hasattr(result, "patterns_found")


# ── enrich() with no matching nodes ──────────────────────────────────────────


class TestRailsEnricherNoMatches:
    def test_promoted_is_zero_when_no_nodes(self):
        store = _mock_store(result_set=[])
        result = RailsEnricher(store).enrich()
        assert result.promoted == 0

    def test_all_pattern_counts_zero_when_no_nodes(self):
        store = _mock_store(result_set=[])
        result = RailsEnricher(store).enrich()
        for key in ("controllers", "models", "routes", "jobs", "concerns"):
            assert result.patterns_found[key] == 0

    def test_patterns_found_has_five_keys(self):
        store = _mock_store(result_set=[])
        result = RailsEnricher(store).enrich()
        assert set(result.patterns_found.keys()) == {
            "controllers", "models", "routes", "jobs", "concerns"
        }


# ── enrich() with matching nodes ─────────────────────────────────────────────


class TestRailsEnricherWithMatches:
    def _make_store_for_fragment(self, target_fragment, rows):
        """Return a store that returns `rows` only when the query fragment matches."""

        def side_effect(cypher, params):
            fragment = params.get("fragment", "")
            if fragment == target_fragment:
                return MagicMock(result_set=rows)
            return MagicMock(result_set=[])

        return _store_with_side_effect(side_effect)

    def test_controller_promoted(self):
        store = self._make_store_for_fragment(
            "controllers/", [["UsersController", "app/controllers/users_controller.rb"]]
        )
        result = RailsEnricher(store).enrich()
        assert result.patterns_found["controllers"] == 1
        assert result.promoted >= 1

    def test_model_promoted(self):
        store = self._make_store_for_fragment(
            "models/", [["User", "app/models/user.rb"]]
        )
        result = RailsEnricher(store).enrich()
        assert result.patterns_found["models"] == 1
        assert result.promoted >= 1

    def test_route_promoted(self):
        store = self._make_store_for_fragment(
            "routes.rb", [["routes", "config/routes.rb"]]
        )
        result = RailsEnricher(store).enrich()
        assert result.patterns_found["routes"] == 1
        assert result.promoted >= 1

    def test_job_promoted(self):
        store = self._make_store_for_fragment(
            "jobs/", [["SendEmailJob", "app/jobs/send_email_job.rb"]]
        )
        result = RailsEnricher(store).enrich()
        assert result.patterns_found["jobs"] == 1
        assert result.promoted >= 1

    def test_concern_promoted(self):
        store = self._make_store_for_fragment(
            "concerns/", [["Auditable", "app/models/concerns/auditable.rb"]]
        )
        result = RailsEnricher(store).enrich()
        assert result.patterns_found["concerns"] == 1
        assert result.promoted >= 1

    def test_promoted_count_accumulates_across_types(self):
        rows_map = {
            "controllers/": [
                ["UsersController", "app/controllers/users_controller.rb"],
                ["PostsController", "app/controllers/posts_controller.rb"],
            ],
            "models/": [["User", "app/models/user.rb"]],
            "routes.rb": [],
            "jobs/": [],
            "concerns/": [],
        }

        def side_effect(cypher, params):
            fragment = params.get("fragment", "")
            return MagicMock(result_set=rows_map.get(fragment, []))

        store = _store_with_side_effect(side_effect)
        result = RailsEnricher(store).enrich()
        assert result.promoted == 3
        assert result.patterns_found["controllers"] == 2
        assert result.patterns_found["models"] == 1

    def test_promote_node_called_with_correct_semantic_type_for_controller(self):
        store = self._make_store_for_fragment(
            "controllers/", [["UsersController", "app/controllers/users_controller.rb"]]
        )
        RailsEnricher(store).enrich()

        # The _promote_node path ultimately calls store.query with SET n.semantic_type
        calls = [str(c) for c in store._graph.query.call_args_list]
        promote_calls = [c for c in calls if "semantic_type" in c and "RailsController" in c]
        assert len(promote_calls) >= 1

    def test_promote_node_called_with_correct_semantic_type_for_model(self):
        store = self._make_store_for_fragment(
            "models/", [["User", "app/models/user.rb"]]
        )
        RailsEnricher(store).enrich()

        calls = [str(c) for c in store._graph.query.call_args_list]
        promote_calls = [c for c in calls if "semantic_type" in c and "RailsModel" in c]
        assert len(promote_calls) >= 1

    def test_promote_node_called_with_correct_semantic_type_for_job(self):
        store = self._make_store_for_fragment(
            "jobs/", [["SendEmailJob", "app/jobs/send_email_job.rb"]]
        )
        RailsEnricher(store).enrich()

        calls = [str(c) for c in store._graph.query.call_args_list]
        promote_calls = [c for c in calls if "semantic_type" in c and "RailsJob" in c]
        assert len(promote_calls) >= 1

    def test_promote_node_called_with_correct_semantic_type_for_concern(self):
        store = self._make_store_for_fragment(
            "concerns/", [["Auditable", "app/models/concerns/auditable.rb"]]
        )
        RailsEnricher(store).enrich()

        calls = [str(c) for c in store._graph.query.call_args_list]
        promote_calls = [c for c in calls if "semantic_type" in c and "RailsConcern" in c]
        assert len(promote_calls) >= 1

    def test_promote_node_called_with_correct_semantic_type_for_route(self):
        store = self._make_store_for_fragment(
            "routes.rb", [["routes", "config/routes.rb"]]
        )
        RailsEnricher(store).enrich()

        calls = [str(c) for c in store._graph.query.call_args_list]
        promote_calls = [c for c in calls if "semantic_type" in c and "RailsRoute" in c]
        assert len(promote_calls) >= 1


# ── detect() integration ──────────────────────────────────────────────────────


class TestRailsEnricherDetect:
    def test_detect_true_when_gemfile_present(self):
        store = _mock_store(result_set=[[1]])
        assert RailsEnricher(store).detect() is True

    def test_detect_false_when_no_patterns_match(self):
        store = _mock_store(result_set=[[0]])
        assert RailsEnricher(store).detect() is False

    def test_detect_false_on_empty_result_set(self):
        store = _mock_store(result_set=[])
        assert RailsEnricher(store).detect() is False
