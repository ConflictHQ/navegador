"""Tests for navegador.enrichment.django — DjangoEnricher."""

from unittest.mock import MagicMock, call

import pytest

from navegador.enrichment.base import EnrichmentResult, FrameworkEnricher
from navegador.enrichment.django import DjangoEnricher
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
    """Return a GraphStore whose graph.query uses the given side_effect callable."""
    client = MagicMock()
    graph = MagicMock()
    graph.query.side_effect = side_effect
    client.select_graph.return_value = graph
    return GraphStore(client)


# ── Metadata ──────────────────────────────────────────────────────────────────


class TestDjangoEnricherMetadata:
    def test_framework_name(self):
        enricher = DjangoEnricher(_mock_store())
        assert enricher.framework_name == "django"

    def test_detection_patterns_contains_manage_py(self):
        enricher = DjangoEnricher(_mock_store())
        assert "manage.py" in enricher.detection_patterns

    def test_detection_patterns_contains_django_conf(self):
        enricher = DjangoEnricher(_mock_store())
        assert "django.conf" in enricher.detection_patterns

    def test_detection_patterns_contains_settings_py(self):
        enricher = DjangoEnricher(_mock_store())
        assert "settings.py" in enricher.detection_patterns

    def test_detection_patterns_contains_urls_py(self):
        enricher = DjangoEnricher(_mock_store())
        assert "urls.py" in enricher.detection_patterns

    def test_detection_patterns_is_list_of_strings(self):
        enricher = DjangoEnricher(_mock_store())
        patterns = enricher.detection_patterns
        assert isinstance(patterns, list)
        assert all(isinstance(p, str) for p in patterns)

    def test_is_subclass_of_framework_enricher(self):
        assert issubclass(DjangoEnricher, FrameworkEnricher)

    def test_enrich_returns_enrichment_result(self):
        store = _mock_store(result_set=[])
        enricher = DjangoEnricher(store)
        result = enricher.enrich()
        assert isinstance(result, EnrichmentResult)


# ── detect() ──────────────────────────────────────────────────────────────────


class TestDjangoEnricherDetect:
    def test_detect_returns_true_when_urls_py_present(self):
        store = _mock_store(result_set=[[1]])
        enricher = DjangoEnricher(store)
        assert enricher.detect() is True

    def test_detect_returns_false_when_no_patterns_match(self):
        store = _mock_store(result_set=[[0]])
        enricher = DjangoEnricher(store)
        assert enricher.detect() is False

    def test_detect_returns_false_when_result_set_empty(self):
        store = _mock_store(result_set=[])
        enricher = DjangoEnricher(store)
        assert enricher.detect() is False

    def test_detect_returns_false_when_result_set_none(self):
        store = _mock_store(result_set=None)
        enricher = DjangoEnricher(store)
        assert enricher.detect() is False

    def test_detect_short_circuits_on_first_match(self):
        store = _mock_store(result_set=[[5]])
        enricher = DjangoEnricher(store)
        assert enricher.detect() is True
        # Should only query once (short-circuit)
        assert store._graph.query.call_count == 1

    def test_detect_tries_all_patterns_before_giving_up(self):
        store = _mock_store(result_set=[[0]])
        enricher = DjangoEnricher(store)
        enricher.detect()
        assert store._graph.query.call_count == len(enricher.detection_patterns)


# ── enrich() — views ──────────────────────────────────────────────────────────


class TestDjangoEnricherViews:
    def _make_store_for_views(self, view_rows):
        """
        Return a store that yields view_rows for the views query and [] for everything else.
        """
        call_count = [0]

        def side_effect(cypher, params=None):
            call_count[0] += 1
            # First enrich query targets views.py functions
            if "views.py" in cypher and "Function" in cypher and call_count[0] == 1:
                return MagicMock(result_set=view_rows)
            return MagicMock(result_set=[])

        return _store_with_side_effect(side_effect)

    def test_promotes_functions_in_views_py(self):
        view_rows = [["my_view", "app/views.py"], ["another_view", "app/views.py"]]
        store = self._make_store_for_views(view_rows)
        enricher = DjangoEnricher(store)
        result = enricher.enrich()
        assert result.patterns_found["views"] == 2
        assert result.promoted >= 2

    def test_no_views_produces_zero_count(self):
        store = _mock_store(result_set=[])
        enricher = DjangoEnricher(store)
        result = enricher.enrich()
        assert result.patterns_found["views"] == 0

    def test_view_promote_node_called_with_semantic_type_view(self):
        view_rows = [["index_view", "myapp/views.py"]]

        call_count = [0]

        def side_effect(cypher, params=None):
            call_count[0] += 1
            if "views.py" in cypher and "Function" in cypher and call_count[0] == 1:
                return MagicMock(result_set=view_rows)
            # _promote_node calls also go through store.query — return empty
            return MagicMock(result_set=[])

        store = _store_with_side_effect(side_effect)
        enricher = DjangoEnricher(store)
        enricher.enrich()

        # Find the _promote_node call for "index_view"
        promote_calls = [
            c for c in store._graph.query.call_args_list
            if "SET n.semantic_type" in c[0][0]
            and c[0][1].get("name") == "index_view"
        ]
        assert len(promote_calls) == 1
        assert promote_calls[0][0][1]["semantic_type"] == "View"


# ── enrich() — models ─────────────────────────────────────────────────────────


class TestDjangoEnricherModels:
    def _make_store_for_models(self, model_rows):
        call_count = [0]

        def side_effect(cypher, params=None):
            call_count[0] += 1
            # Second enrich query targets Model-inheriting classes
            if "INHERITS" in cypher and call_count[0] == 2:
                return MagicMock(result_set=model_rows)
            return MagicMock(result_set=[])

        return _store_with_side_effect(side_effect)

    def test_promotes_classes_inheriting_from_model(self):
        model_rows = [["UserProfile", "app/models.py"], ["Post", "blog/models.py"]]
        store = self._make_store_for_models(model_rows)
        enricher = DjangoEnricher(store)
        result = enricher.enrich()
        assert result.patterns_found["models"] == 2
        assert result.promoted >= 2

    def test_no_models_produces_zero_count(self):
        store = _mock_store(result_set=[])
        enricher = DjangoEnricher(store)
        result = enricher.enrich()
        assert result.patterns_found["models"] == 0

    def test_model_promote_node_called_with_semantic_type_model(self):
        model_rows = [["Article", "news/models.py"]]

        call_count = [0]

        def side_effect(cypher, params=None):
            call_count[0] += 1
            if "INHERITS" in cypher and call_count[0] == 2:
                return MagicMock(result_set=model_rows)
            return MagicMock(result_set=[])

        store = _store_with_side_effect(side_effect)
        enricher = DjangoEnricher(store)
        enricher.enrich()

        promote_calls = [
            c for c in store._graph.query.call_args_list
            if "SET n.semantic_type" in c[0][0]
            and c[0][1].get("name") == "Article"
        ]
        assert len(promote_calls) == 1
        assert promote_calls[0][0][1]["semantic_type"] == "Model"


# ── enrich() — serializers ────────────────────────────────────────────────────


class TestDjangoEnricherSerializers:
    def _make_store_for_serializers(self, serializer_rows):
        call_count = [0]

        def side_effect(cypher, params=None):
            call_count[0] += 1
            # Fourth enrich query targets serializers.py classes
            if "serializers.py" in cypher and call_count[0] == 4:
                return MagicMock(result_set=serializer_rows)
            return MagicMock(result_set=[])

        return _store_with_side_effect(side_effect)

    def test_promotes_classes_in_serializers_py(self):
        serializer_rows = [["UserSerializer", "api/serializers.py"]]
        store = self._make_store_for_serializers(serializer_rows)
        enricher = DjangoEnricher(store)
        result = enricher.enrich()
        assert result.patterns_found["serializers"] == 1
        assert result.promoted >= 1

    def test_no_serializers_produces_zero_count(self):
        store = _mock_store(result_set=[])
        enricher = DjangoEnricher(store)
        result = enricher.enrich()
        assert result.patterns_found["serializers"] == 0

    def test_serializer_promote_node_called_with_semantic_type_serializer(self):
        serializer_rows = [["PostSerializer", "blog/serializers.py"]]

        call_count = [0]

        def side_effect(cypher, params=None):
            call_count[0] += 1
            if "serializers.py" in cypher and call_count[0] == 4:
                return MagicMock(result_set=serializer_rows)
            return MagicMock(result_set=[])

        store = _store_with_side_effect(side_effect)
        enricher = DjangoEnricher(store)
        enricher.enrich()

        promote_calls = [
            c for c in store._graph.query.call_args_list
            if "SET n.semantic_type" in c[0][0]
            and c[0][1].get("name") == "PostSerializer"
        ]
        assert len(promote_calls) == 1
        assert promote_calls[0][0][1]["semantic_type"] == "Serializer"


# ── enrich() — tasks ──────────────────────────────────────────────────────────


class TestDjangoEnricherTasks:
    def _make_store_for_tasks(self, task_rows):
        call_count = [0]

        def side_effect(cypher, params=None):
            call_count[0] += 1
            # Fifth enrich query targets tasks.py functions or @task decorator
            if "tasks.py" in cypher and call_count[0] == 5:
                return MagicMock(result_set=task_rows)
            return MagicMock(result_set=[])

        return _store_with_side_effect(side_effect)

    def test_promotes_functions_in_tasks_py(self):
        task_rows = [["send_email", "myapp/tasks.py"], ["process_order", "shop/tasks.py"]]
        store = self._make_store_for_tasks(task_rows)
        enricher = DjangoEnricher(store)
        result = enricher.enrich()
        assert result.patterns_found["tasks"] == 2
        assert result.promoted >= 2

    def test_no_tasks_produces_zero_count(self):
        store = _mock_store(result_set=[])
        enricher = DjangoEnricher(store)
        result = enricher.enrich()
        assert result.patterns_found["tasks"] == 0

    def test_task_cypher_includes_decorator_check(self):
        store = _mock_store(result_set=[])
        enricher = DjangoEnricher(store)
        enricher.enrich()

        # Find the tasks query
        tasks_queries = [
            c[0][0] for c in store._graph.query.call_args_list
            if "tasks.py" in c[0][0]
        ]
        assert len(tasks_queries) == 1
        assert "decorators" in tasks_queries[0]
        assert "task" in tasks_queries[0]

    def test_task_promote_node_called_with_semantic_type_task(self):
        task_rows = [["send_welcome_email", "users/tasks.py"]]

        call_count = [0]

        def side_effect(cypher, params=None):
            call_count[0] += 1
            if "tasks.py" in cypher and call_count[0] == 5:
                return MagicMock(result_set=task_rows)
            return MagicMock(result_set=[])

        store = _store_with_side_effect(side_effect)
        enricher = DjangoEnricher(store)
        enricher.enrich()

        promote_calls = [
            c for c in store._graph.query.call_args_list
            if "SET n.semantic_type" in c[0][0]
            and c[0][1].get("name") == "send_welcome_email"
        ]
        assert len(promote_calls) == 1
        assert promote_calls[0][0][1]["semantic_type"] == "Task"


# ── enrich() — URL patterns ───────────────────────────────────────────────────


class TestDjangoEnricherURLPatterns:
    def test_url_patterns_count_tracked(self):
        call_count = [0]

        def side_effect(cypher, params=None):
            call_count[0] += 1
            if "urls.py" in cypher and call_count[0] == 3:
                return MagicMock(result_set=[["urlconf", "myapp/urls.py"]])
            return MagicMock(result_set=[])

        store = _store_with_side_effect(side_effect)
        enricher = DjangoEnricher(store)
        result = enricher.enrich()
        assert result.patterns_found["url_patterns"] == 1

    def test_url_pattern_promoted_with_correct_semantic_type(self):
        call_count = [0]

        def side_effect(cypher, params=None):
            call_count[0] += 1
            if "urls.py" in cypher and call_count[0] == 3:
                return MagicMock(result_set=[["urlconf", "myapp/urls.py"]])
            return MagicMock(result_set=[])

        store = _store_with_side_effect(side_effect)
        enricher = DjangoEnricher(store)
        enricher.enrich()

        promote_calls = [
            c for c in store._graph.query.call_args_list
            if "SET n.semantic_type" in c[0][0]
            and c[0][1].get("name") == "urlconf"
        ]
        assert len(promote_calls) == 1
        assert promote_calls[0][0][1]["semantic_type"] == "URLPattern"


# ── enrich() — admin ──────────────────────────────────────────────────────────


class TestDjangoEnricherAdmin:
    def test_admin_count_tracked(self):
        call_count = [0]

        def side_effect(cypher, params=None):
            call_count[0] += 1
            if "admin.py" in cypher and call_count[0] == 6:
                return MagicMock(result_set=[["UserAdmin", "myapp/admin.py"]])
            return MagicMock(result_set=[])

        store = _store_with_side_effect(side_effect)
        enricher = DjangoEnricher(store)
        result = enricher.enrich()
        assert result.patterns_found["admin"] == 1

    def test_admin_promoted_with_correct_semantic_type(self):
        call_count = [0]

        def side_effect(cypher, params=None):
            call_count[0] += 1
            if "admin.py" in cypher and call_count[0] == 6:
                return MagicMock(result_set=[["PostAdmin", "blog/admin.py"]])
            return MagicMock(result_set=[])

        store = _store_with_side_effect(side_effect)
        enricher = DjangoEnricher(store)
        enricher.enrich()

        promote_calls = [
            c for c in store._graph.query.call_args_list
            if "SET n.semantic_type" in c[0][0]
            and c[0][1].get("name") == "PostAdmin"
        ]
        assert len(promote_calls) == 1
        assert promote_calls[0][0][1]["semantic_type"] == "Admin"


# ── enrich() — aggregate result ───────────────────────────────────────────────


class TestDjangoEnricherAggregateResult:
    def test_patterns_found_has_all_expected_keys(self):
        store = _mock_store(result_set=[])
        enricher = DjangoEnricher(store)
        result = enricher.enrich()
        expected_keys = {"views", "models", "url_patterns", "serializers", "tasks", "admin"}
        assert expected_keys == set(result.patterns_found.keys())

    def test_promoted_count_is_sum_of_all_pattern_counts(self):
        """With no matches, promoted should be 0."""
        store = _mock_store(result_set=[])
        enricher = DjangoEnricher(store)
        result = enricher.enrich()
        assert result.promoted == 0
        assert sum(result.patterns_found.values()) == 0

    def test_promoted_accumulates_across_all_patterns(self):
        """Each query returns one row — promoted should equal 6 (one per pattern)."""
        call_count = [0]

        def side_effect(cypher, params=None):
            call_count[0] += 1
            # The 6 SELECT queries are calls 1, 2, 3, 4, 5, 6 (interleaved with SET calls)
            # We track by call_count on the non-SET queries
            if "SET n.semantic_type" not in cypher:
                return MagicMock(result_set=[["some_node", "some_file.py"]])
            return MagicMock(result_set=[])

        store = _store_with_side_effect(side_effect)
        enricher = DjangoEnricher(store)
        result = enricher.enrich()
        assert result.promoted == 6
        assert sum(result.patterns_found.values()) == 6
