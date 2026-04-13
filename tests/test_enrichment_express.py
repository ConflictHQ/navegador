"""Tests for navegador.enrichment.express — ExpressEnricher."""

from unittest.mock import MagicMock

from navegador.enrichment import EnrichmentResult
from navegador.enrichment.express import ExpressEnricher
from navegador.graph.store import GraphStore

# ── Helpers ───────────────────────────────────────────────────────────────────


def _mock_store(result_set=None):
    client = MagicMock()
    graph = MagicMock()
    graph.query.return_value = MagicMock(result_set=result_set)
    client.select_graph.return_value = graph
    return GraphStore(client)


def _mock_store_with_responses(responses):
    """Return a GraphStore whose graph.query returns the given result sets in order.

    Any calls beyond the provided list (e.g. _promote_node SET queries) receive
    a MagicMock with result_set=None, which is harmless.
    """
    client = MagicMock()
    graph = MagicMock()
    response_iter = iter(responses)

    def _side_effect(cypher, params):
        try:
            rs = next(response_iter)
        except StopIteration:
            rs = None
        return MagicMock(result_set=rs)

    graph.query.side_effect = _side_effect
    client.select_graph.return_value = graph
    return GraphStore(client)


# ── framework_name ────────────────────────────────────────────────────────────


class TestFrameworkName:
    def test_framework_name_is_express(self):
        enricher = ExpressEnricher(_mock_store())
        assert enricher.framework_name == "express"


# ── detection_patterns ────────────────────────────────────────────────────────


class TestDetectionPatterns:
    def test_contains_express_lowercase(self):
        enricher = ExpressEnricher(_mock_store())
        assert "express" in enricher.detection_patterns

    def test_returns_list(self):
        enricher = ExpressEnricher(_mock_store())
        assert isinstance(enricher.detection_patterns, list)

    def test_is_nonempty(self):
        enricher = ExpressEnricher(_mock_store())
        assert len(enricher.detection_patterns) >= 1


# ── detect() ─────────────────────────────────────────────────────────────────


class TestDetect:
    def test_returns_true_when_pattern_found(self):
        store = _mock_store(result_set=[[1]])
        enricher = ExpressEnricher(store)
        assert enricher.detect() is True

    def test_returns_false_when_no_match(self):
        store = _mock_store(result_set=[[0]])
        enricher = ExpressEnricher(store)
        assert enricher.detect() is False

    def test_returns_false_on_empty_result_set(self):
        store = _mock_store(result_set=[])
        enricher = ExpressEnricher(store)
        assert enricher.detect() is False

    def test_short_circuits_on_first_match(self):
        store = _mock_store(result_set=[[2]])
        enricher = ExpressEnricher(store)
        assert enricher.detect() is True
        assert store._graph.query.call_count == 1


# ── enrich() ─────────────────────────────────────────────────────────────────


class TestEnrich:
    def _empty_enricher(self):
        # 4 pattern queries: routes, middleware, controllers, routers
        store = _mock_store_with_responses([[], [], [], []])
        return ExpressEnricher(store)

    def test_returns_enrichment_result(self):
        assert isinstance(self._empty_enricher().enrich(), EnrichmentResult)

    def test_zero_promoted_when_no_matches(self):
        result = self._empty_enricher().enrich()
        assert result.promoted == 0

    def test_patterns_found_keys_present(self):
        result = self._empty_enricher().enrich()
        assert "routes" in result.patterns_found
        assert "middleware" in result.patterns_found
        assert "controllers" in result.patterns_found
        assert "routers" in result.patterns_found

    def test_promotes_route_nodes(self):
        """app.get / app.post etc. should be promoted to ExpressRoute."""
        store = _mock_store_with_responses(
            [
                [["app.get/users", "src/routes/users.js"]],  # routes
                [],  # middleware
                [],  # controllers
                [],  # routers
            ]
        )
        enricher = ExpressEnricher(store)
        result = enricher.enrich()
        assert result.promoted == 1
        assert result.patterns_found["routes"] == 1

        calls = store._graph.query.call_args_list
        promote_calls = [c for c in calls if "SET n.semantic_type" in c[0][0]]
        assert len(promote_calls) == 1
        assert promote_calls[0][0][1]["semantic_type"] == "ExpressRoute"

    def test_promotes_middleware_nodes(self):
        """app.use calls should be promoted to ExpressMiddleware."""
        store = _mock_store_with_responses(
            [
                [],  # routes
                [["app.use/auth", "src/middleware/auth.js"]],  # middleware
                [],  # controllers
                [],  # routers
            ]
        )
        enricher = ExpressEnricher(store)
        result = enricher.enrich()
        assert result.promoted == 1
        assert result.patterns_found["middleware"] == 1

        calls = store._graph.query.call_args_list
        promote_calls = [c for c in calls if "SET n.semantic_type" in c[0][0]]
        assert promote_calls[0][0][1]["semantic_type"] == "ExpressMiddleware"

    def test_promotes_controller_nodes(self):
        """Nodes inside /controllers/ should become ExpressController."""
        store = _mock_store_with_responses(
            [
                [],  # routes
                [],  # middleware
                [["UserController", "src/controllers/UserController.js"]],  # controllers
                [],  # routers
            ]
        )
        enricher = ExpressEnricher(store)
        result = enricher.enrich()
        assert result.promoted == 1
        assert result.patterns_found["controllers"] == 1

        calls = store._graph.query.call_args_list
        promote_calls = [c for c in calls if "SET n.semantic_type" in c[0][0]]
        assert promote_calls[0][0][1]["semantic_type"] == "ExpressController"

    def test_promotes_router_nodes(self):
        """Router() patterns should become ExpressRouter."""
        store = _mock_store_with_responses(
            [
                [],  # routes
                [],  # middleware
                [],  # controllers
                [["Router", "src/routes/index.js"]],  # routers
            ]
        )
        enricher = ExpressEnricher(store)
        result = enricher.enrich()
        assert result.promoted == 1
        assert result.patterns_found["routers"] == 1

        calls = store._graph.query.call_args_list
        promote_calls = [c for c in calls if "SET n.semantic_type" in c[0][0]]
        assert promote_calls[0][0][1]["semantic_type"] == "ExpressRouter"

    def test_promoted_accumulates_across_patterns(self):
        # Each matched node triggers a _promote_node SET query after its pattern query.
        store = _mock_store_with_responses(
            [
                [["app.get/a", "r.js"], ["app.post/b", "r.js"]],  # routes query (2 rows)
                None,   # _promote_node for app.get/a
                None,   # _promote_node for app.post/b
                [["app.use/logger", "m.js"]],                       # middleware query (1 row)
                None,   # _promote_node for app.use/logger
                [["AuthCtrl", "controllers/auth.js"]],              # controllers query (1 row)
                None,   # _promote_node for AuthCtrl
                [],     # routers query
            ]
        )
        enricher = ExpressEnricher(store)
        result = enricher.enrich()
        assert result.promoted == 4
        assert result.patterns_found["routes"] == 2
        assert result.patterns_found["middleware"] == 1
        assert result.patterns_found["controllers"] == 1
        assert result.patterns_found["routers"] == 0

    def test_promote_node_called_with_correct_name_and_file(self):
        store = _mock_store_with_responses(
            [
                [["app.get/items", "src/routes/items.js"]],
                [], [], [],
            ]
        )
        enricher = ExpressEnricher(store)
        enricher.enrich()

        calls = store._graph.query.call_args_list
        promote_calls = [c for c in calls if "SET n.semantic_type" in c[0][0]]
        params = promote_calls[0][0][1]
        assert params["name"] == "app.get/items"
        assert params["file_path"] == "src/routes/items.js"
