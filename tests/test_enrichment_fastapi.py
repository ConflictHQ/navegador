"""
Tests for navegador.enrichment.fastapi — FastAPIEnricher.

All tests use a mock GraphStore so no real database is required.
"""

from unittest.mock import MagicMock, call, patch

import pytest

from navegador.enrichment.base import EnrichmentResult, FrameworkEnricher
from navegador.enrichment.fastapi import FastAPIEnricher
from navegador.graph.store import GraphStore


# ── Helpers ────────────────────────────────────────────────────────────────────


def _mock_store(result_set=None):
    """Return a GraphStore backed by a mock graph that always returns result_set."""
    client = MagicMock()
    graph = MagicMock()
    graph.query.return_value = MagicMock(result_set=result_set or [])
    client.select_graph.return_value = graph
    return GraphStore(client)


def _mock_store_with_responses(responses):
    """
    Return a GraphStore whose graph.query() returns successive mock results.

    ``responses`` is a list of result_set values in call order.  Once
    exhausted, subsequent calls return an empty result_set.
    """
    client = MagicMock()
    graph = MagicMock()
    iter_resp = iter(responses)

    def _side_effect(cypher, params=None):
        rs = next(iter_resp, [])
        return MagicMock(result_set=rs)

    graph.query.side_effect = _side_effect
    client.select_graph.return_value = graph
    return GraphStore(client)


# ── Identity ───────────────────────────────────────────────────────────────────


class TestFastAPIEnricherIdentity:
    def test_framework_name(self):
        store = _mock_store()
        enricher = FastAPIEnricher(store)
        assert enricher.framework_name == "fastapi"

    def test_detection_patterns_includes_fastapi_lowercase(self):
        store = _mock_store()
        assert "fastapi" in FastAPIEnricher(store).detection_patterns

    def test_detection_patterns_includes_fastapi_class(self):
        store = _mock_store()
        assert "FastAPI" in FastAPIEnricher(store).detection_patterns

    def test_detection_patterns_includes_apirouter(self):
        store = _mock_store()
        assert "APIRouter" in FastAPIEnricher(store).detection_patterns

    def test_is_subclass_of_framework_enricher(self):
        store = _mock_store()
        assert isinstance(FastAPIEnricher(store), FrameworkEnricher)


# ── detect() ──────────────────────────────────────────────────────────────────


class TestFastAPIEnricherDetect:
    def test_detect_returns_true_when_fastapi_import_found(self):
        store = _mock_store(result_set=[[1]])
        assert FastAPIEnricher(store).detect() is True

    def test_detect_returns_false_when_no_match(self):
        store = _mock_store(result_set=[[0]])
        assert FastAPIEnricher(store).detect() is False

    def test_detect_returns_false_on_empty_result_set(self):
        store = _mock_store(result_set=[])
        assert FastAPIEnricher(store).detect() is False

    def test_detect_queries_all_three_patterns_when_no_match(self):
        store = _mock_store(result_set=[[0]])
        FastAPIEnricher(store).detect()
        # Three detection patterns → three queries
        assert store._graph.query.call_count == 3

    def test_detect_short_circuits_on_first_match(self):
        store = _mock_store(result_set=[[7]])
        FastAPIEnricher(store).detect()
        assert store._graph.query.call_count == 1


# ── enrich() returns EnrichmentResult ─────────────────────────────────────────


class TestFastAPIEnricherEnrichReturnType:
    def test_returns_enrichment_result(self):
        store = _mock_store()
        result = FastAPIEnricher(store).enrich()
        assert isinstance(result, EnrichmentResult)

    def test_result_has_expected_pattern_keys(self):
        store = _mock_store()
        result = FastAPIEnricher(store).enrich()
        assert "routes" in result.patterns_found
        assert "dependencies" in result.patterns_found
        assert "pydantic_models" in result.patterns_found
        assert "background_tasks" in result.patterns_found

    def test_result_promoted_is_sum_of_patterns(self):
        store = _mock_store()
        result = FastAPIEnricher(store).enrich()
        assert result.promoted == sum(result.patterns_found.values())

    def test_no_matches_gives_zero_promoted(self):
        store = _mock_store(result_set=[])
        result = FastAPIEnricher(store).enrich()
        assert result.promoted == 0


# ── Routes ────────────────────────────────────────────────────────────────────


class TestFastAPIEnricherRoutes:
    def _enricher_with_route_hit(self, name="get_items", file_path="app/main.py"):
        """
        Build a FastAPIEnricher whose store returns one matching row for the
        *first* Decorator-based route query (app.get), then empty for the rest.
        """
        client = MagicMock()
        graph = MagicMock()
        call_count = [0]

        def _side_effect(cypher, params=None):
            call_count[0] += 1
            # Return a hit on the very first call only
            if call_count[0] == 1:
                return MagicMock(result_set=[[name, file_path]])
            return MagicMock(result_set=[])

        graph.query.side_effect = _side_effect
        client.select_graph.return_value = graph
        store = GraphStore(client)
        return FastAPIEnricher(store)

    def test_route_promoted_increments_count(self):
        enricher = self._enricher_with_route_hit()
        result = enricher.enrich()
        assert result.patterns_found["routes"] >= 1

    def test_route_calls_promote_node_with_route_semantic_type(self):
        enricher = self._enricher_with_route_hit("list_users", "app/users.py")
        with patch.object(enricher, "_promote_node") as mock_promote:
            # Re-wire store so only the first query returns a hit
            call_count = [0]

            def _side_effect(cypher, params=None):
                call_count[0] += 1
                if call_count[0] == 1:
                    return MagicMock(result_set=[["list_users", "app/users.py"]])
                return MagicMock(result_set=[])

            enricher.store._graph.query.side_effect = _side_effect
            enricher.enrich()

        calls = [c for c in mock_promote.call_args_list if c[0][2] == "Route"]
        assert len(calls) >= 1
        assert calls[0][0][0] == "list_users"
        assert calls[0][0][1] == "app/users.py"

    def test_route_http_method_stored_as_prop(self):
        """The http_method kwarg should be passed to _promote_node for route nodes."""
        store = _mock_store()
        enricher = FastAPIEnricher(store)

        call_count = [0]

        def _side_effect(cypher, params=None):
            call_count[0] += 1
            if call_count[0] == 1:
                return MagicMock(result_set=[["create_item", "app/main.py"]])
            return MagicMock(result_set=[])

        store._graph.query.side_effect = _side_effect

        with patch.object(enricher, "_promote_node") as mock_promote:
            enricher.enrich()

        route_calls = [c for c in mock_promote.call_args_list if c[0][2] == "Route"]
        assert route_calls, "Expected at least one _promote_node call with 'Route'"
        _, kwargs = route_calls[0]
        props = kwargs.get("props") or (route_calls[0][0][3] if len(route_calls[0][0]) > 3 else None)
        assert props is not None
        assert "http_method" in props


# ── Dependencies ──────────────────────────────────────────────────────────────


class TestFastAPIEnricherDependencies:
    def test_dependency_promoted_for_depends_in_signature(self):
        store = _mock_store()
        enricher = FastAPIEnricher(store)

        def _side_effect(cypher, params=None):
            params = params or {}
            if params.get("pattern") == "Depends(":
                return MagicMock(result_set=[["get_db", "app/deps.py"]])
            return MagicMock(result_set=[])

        store._graph.query.side_effect = _side_effect

        with patch.object(enricher, "_promote_node") as mock_promote:
            enricher.enrich()

        dep_calls = [c for c in mock_promote.call_args_list if c[0][2] == "Dependency"]
        assert len(dep_calls) >= 1

    def test_dependency_semantic_type_is_dependency(self):
        store = _mock_store()
        enricher = FastAPIEnricher(store)

        def _side_effect(cypher, params=None):
            params = params or {}
            if "Depends" in params.get("pattern", ""):
                return MagicMock(result_set=[["auth_user", "app/auth.py"]])
            return MagicMock(result_set=[])

        store._graph.query.side_effect = _side_effect

        with patch.object(enricher, "_promote_node") as mock_promote:
            enricher.enrich()

        dep_calls = [c for c in mock_promote.call_args_list if c[0][2] == "Dependency"]
        assert dep_calls


# ── Pydantic Models ───────────────────────────────────────────────────────────


class TestFastAPIEnricherPydanticModels:
    def test_pydantic_model_promoted_via_inherits_edge(self):
        store = _mock_store()
        enricher = FastAPIEnricher(store)

        def _side_effect(cypher, params=None):
            params = params or {}
            if params.get("base_name") == "BaseModel":
                return MagicMock(result_set=[["UserSchema", "app/schemas.py"]])
            return MagicMock(result_set=[])

        store._graph.query.side_effect = _side_effect

        with patch.object(enricher, "_promote_node") as mock_promote:
            enricher.enrich()

        pm_calls = [c for c in mock_promote.call_args_list if c[0][2] == "PydanticModel"]
        assert len(pm_calls) >= 1
        assert pm_calls[0][0][0] == "UserSchema"
        assert pm_calls[0][0][1] == "app/schemas.py"

    def test_pydantic_model_semantic_type_string(self):
        store = _mock_store()
        enricher = FastAPIEnricher(store)

        def _side_effect(cypher, params=None):
            params = params or {}
            if params.get("base_name") == "BaseModel":
                return MagicMock(result_set=[["ItemModel", "app/models.py"]])
            return MagicMock(result_set=[])

        store._graph.query.side_effect = _side_effect

        with patch.object(enricher, "_promote_node") as mock_promote:
            enricher.enrich()

        types = {c[0][2] for c in mock_promote.call_args_list}
        assert "PydanticModel" in types

    def test_pydantic_model_fallback_via_docstring(self):
        """If no INHERITS edge, docstring containing 'BaseModel' is the fallback."""
        store = _mock_store()
        enricher = FastAPIEnricher(store)

        call_count = [0]

        def _side_effect(cypher, params=None):
            call_count[0] += 1
            params = params or {}
            # Fail INHERITS query, succeed on docstring fallback
            if params.get("base_name") == "BaseModel":
                return MagicMock(result_set=[])
            if "BaseModel" in params.get("pattern", "") and "docstring" in cypher:
                return MagicMock(result_set=[["CreateUser", "app/schemas.py"]])
            return MagicMock(result_set=[])

        store._graph.query.side_effect = _side_effect

        with patch.object(enricher, "_promote_node") as mock_promote:
            enricher.enrich()

        pm_calls = [c for c in mock_promote.call_args_list if c[0][2] == "PydanticModel"]
        assert len(pm_calls) >= 1


# ── Background Tasks ──────────────────────────────────────────────────────────


class TestFastAPIEnricherBackgroundTasks:
    def test_background_task_promoted_via_on_event_decorator(self):
        store = _mock_store()
        enricher = FastAPIEnricher(store)

        def _side_effect(cypher, params=None):
            params = params or {}
            if "on_event" in params.get("pattern", ""):
                return MagicMock(result_set=[["startup_handler", "app/events.py"]])
            return MagicMock(result_set=[])

        store._graph.query.side_effect = _side_effect

        with patch.object(enricher, "_promote_node") as mock_promote:
            enricher.enrich()

        bt_calls = [c for c in mock_promote.call_args_list if c[0][2] == "BackgroundTask"]
        assert len(bt_calls) >= 1

    def test_background_task_promoted_via_background_tasks_in_signature(self):
        store = _mock_store()
        enricher = FastAPIEnricher(store)

        def _side_effect(cypher, params=None):
            params = params or {}
            if "BackgroundTasks" in params.get("pattern", ""):
                return MagicMock(result_set=[["send_email", "app/tasks.py"]])
            return MagicMock(result_set=[])

        store._graph.query.side_effect = _side_effect

        with patch.object(enricher, "_promote_node") as mock_promote:
            enricher.enrich()

        bt_calls = [c for c in mock_promote.call_args_list if c[0][2] == "BackgroundTask"]
        assert len(bt_calls) >= 1

    def test_background_task_semantic_type_string(self):
        store = _mock_store()
        enricher = FastAPIEnricher(store)

        def _side_effect(cypher, params=None):
            params = params or {}
            if "on_event" in params.get("pattern", ""):
                return MagicMock(result_set=[["shutdown_handler", "app/main.py"]])
            return MagicMock(result_set=[])

        store._graph.query.side_effect = _side_effect

        with patch.object(enricher, "_promote_node") as mock_promote:
            enricher.enrich()

        types = {c[0][2] for c in mock_promote.call_args_list}
        assert "BackgroundTask" in types


# ── _promote_node integration ─────────────────────────────────────────────────


class TestFastAPIEnricherPromoteNodeIntegration:
    def test_promote_node_called_for_each_matched_row(self):
        """Two rows returned → two _promote_node calls for that pattern."""
        store = _mock_store()
        enricher = FastAPIEnricher(store)

        call_count = [0]

        def _side_effect(cypher, params=None):
            call_count[0] += 1
            if call_count[0] == 1:
                return MagicMock(result_set=[
                    ["route_a", "app/a.py"],
                    ["route_b", "app/b.py"],
                ])
            return MagicMock(result_set=[])

        store._graph.query.side_effect = _side_effect

        with patch.object(enricher, "_promote_node") as mock_promote:
            enricher.enrich()

        route_calls = [c for c in mock_promote.call_args_list if c[0][2] == "Route"]
        assert len(route_calls) == 2

    def test_rows_with_none_name_are_skipped(self):
        """Rows where name is None must not call _promote_node."""
        store = _mock_store()
        enricher = FastAPIEnricher(store)

        call_count = [0]

        def _side_effect(cypher, params=None):
            call_count[0] += 1
            if call_count[0] == 1:
                return MagicMock(result_set=[[None, "app/main.py"]])
            return MagicMock(result_set=[])

        store._graph.query.side_effect = _side_effect

        with patch.object(enricher, "_promote_node") as mock_promote:
            enricher.enrich()

        route_calls = [c for c in mock_promote.call_args_list if c[0][2] == "Route"]
        assert len(route_calls) == 0

    def test_rows_with_none_file_path_are_skipped(self):
        """Rows where file_path is None must not call _promote_node."""
        store = _mock_store()
        enricher = FastAPIEnricher(store)

        call_count = [0]

        def _side_effect(cypher, params=None):
            call_count[0] += 1
            if call_count[0] == 1:
                return MagicMock(result_set=[["get_items", None]])
            return MagicMock(result_set=[])

        store._graph.query.side_effect = _side_effect

        with patch.object(enricher, "_promote_node") as mock_promote:
            enricher.enrich()

        route_calls = [c for c in mock_promote.call_args_list if c[0][2] == "Route"]
        assert len(route_calls) == 0


# ── HTTP method coverage ──────────────────────────────────────────────────────


class TestFastAPIEnricherHTTPMethods:
    def test_all_http_methods_are_queried(self):
        """The enricher must issue queries for all standard HTTP verbs."""
        store = _mock_store(result_set=[])
        FastAPIEnricher(store).enrich()

        all_params = [
            call_args[0][1] if len(call_args[0]) > 1 else {}
            for call_args in store._graph.query.call_args_list
        ]
        patterns_used = {p.get("pattern", "") for p in all_params}

        for method in ("get", "post", "put", "delete", "patch"):
            assert any(method in pat for pat in patterns_used), (
                f"HTTP method '{method}' not found in any query pattern"
            )
