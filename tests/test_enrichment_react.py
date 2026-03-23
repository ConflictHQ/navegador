"""Tests for navegador.enrichment.react — ReactEnricher."""

from unittest.mock import MagicMock, call

import pytest

from navegador.enrichment import EnrichmentResult
from navegador.enrichment.react import ReactEnricher
from navegador.graph.store import GraphStore


# ── Helpers ───────────────────────────────────────────────────────────────────


def _mock_store(result_set=None):
    """Return a GraphStore backed by a mock FalkorDB graph."""
    client = MagicMock()
    graph = MagicMock()
    graph.query.return_value = MagicMock(result_set=result_set)
    client.select_graph.return_value = graph
    return GraphStore(client)


def _mock_store_with_responses(responses):
    """Return a GraphStore whose graph.query returns the given result sets in order.

    Any calls beyond the provided list (e.g. _promote_node SET queries) receive
    a MagicMock with result_set=None, which is harmless since the enrichers do
    not inspect the return value of promote calls.
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
    def test_framework_name_is_react(self):
        enricher = ReactEnricher(_mock_store())
        assert enricher.framework_name == "react"


# ── detection_patterns ────────────────────────────────────────────────────────


class TestDetectionPatterns:
    def test_contains_react(self):
        enricher = ReactEnricher(_mock_store())
        assert "react" in enricher.detection_patterns

    def test_contains_React(self):
        enricher = ReactEnricher(_mock_store())
        assert "React" in enricher.detection_patterns

    def test_contains_next_config(self):
        enricher = ReactEnricher(_mock_store())
        assert "next.config" in enricher.detection_patterns

    def test_contains_next_router(self):
        enricher = ReactEnricher(_mock_store())
        assert "next/router" in enricher.detection_patterns

    def test_returns_list(self):
        enricher = ReactEnricher(_mock_store())
        assert isinstance(enricher.detection_patterns, list)


# ── detect() ─────────────────────────────────────────────────────────────────


class TestDetect:
    def test_returns_true_when_react_pattern_found(self):
        store = _mock_store(result_set=[[1]])
        enricher = ReactEnricher(store)
        assert enricher.detect() is True

    def test_returns_false_when_no_pattern_found(self):
        store = _mock_store(result_set=[[0]])
        enricher = ReactEnricher(store)
        assert enricher.detect() is False

    def test_returns_false_on_empty_result_set(self):
        store = _mock_store(result_set=[])
        enricher = ReactEnricher(store)
        assert enricher.detect() is False

    def test_short_circuits_on_first_match(self):
        store = _mock_store(result_set=[[3]])
        enricher = ReactEnricher(store)
        assert enricher.detect() is True
        assert store._graph.query.call_count == 1


# ── enrich() ─────────────────────────────────────────────────────────────────


class TestEnrich:
    def _make_enricher_empty(self):
        """Enricher that returns empty result sets for every pattern query."""
        # 5 pattern queries: components, pages, api_routes, hooks, stores
        store = _mock_store_with_responses([[], [], [], [], []])
        return ReactEnricher(store)

    def test_returns_enrichment_result(self):
        enricher = self._make_enricher_empty()
        assert isinstance(enricher.enrich(), EnrichmentResult)

    def test_zero_promoted_when_no_matches(self):
        enricher = self._make_enricher_empty()
        result = enricher.enrich()
        assert result.promoted == 0

    def test_patterns_found_keys_present(self):
        enricher = self._make_enricher_empty()
        result = enricher.enrich()
        assert "components" in result.patterns_found
        assert "pages" in result.patterns_found
        assert "api_routes" in result.patterns_found
        assert "hooks" in result.patterns_found
        assert "stores" in result.patterns_found

    def test_promotes_component_nodes(self):
        """A JSX file node should be promoted to ReactComponent."""
        store = _mock_store_with_responses(
            [
                [["Button", "src/components/Button.jsx"]],  # components
                [],  # pages
                [],  # api_routes
                [],  # hooks
                [],  # stores
                # _promote_node calls handled by fallback
            ]
        )
        enricher = ReactEnricher(store)
        result = enricher.enrich()
        assert result.promoted == 1
        assert result.patterns_found["components"] == 1

        # Verify _promote_node called store.query with correct semantic_type
        calls = store._graph.query.call_args_list
        promote_calls = [c for c in calls if "SET n.semantic_type" in c[0][0]]
        assert len(promote_calls) == 1
        _, params = promote_calls[0][0]
        assert params["semantic_type"] == "ReactComponent"
        assert params["name"] == "Button"

    def test_promotes_page_nodes(self):
        """Nodes inside /pages/ directory should become NextPage."""
        store = _mock_store_with_responses(
            [
                [],  # components
                [["IndexPage", "src/pages/index.tsx"]],  # pages
                [],  # api_routes
                [],  # hooks
                [],  # stores
            ]
        )
        enricher = ReactEnricher(store)
        result = enricher.enrich()
        assert result.promoted == 1
        assert result.patterns_found["pages"] == 1

        calls = store._graph.query.call_args_list
        promote_calls = [c for c in calls if "SET n.semantic_type" in c[0][0]]
        assert promote_calls[0][0][1]["semantic_type"] == "NextPage"

    def test_promotes_api_route_nodes(self):
        """Nodes inside /pages/api/ should become NextApiRoute."""
        store = _mock_store_with_responses(
            [
                [],  # components
                [],  # pages
                [["handler", "src/pages/api/user.ts"]],  # api_routes
                [],  # hooks
                [],  # stores
            ]
        )
        enricher = ReactEnricher(store)
        result = enricher.enrich()
        assert result.promoted == 1
        assert result.patterns_found["api_routes"] == 1

        calls = store._graph.query.call_args_list
        promote_calls = [c for c in calls if "SET n.semantic_type" in c[0][0]]
        assert promote_calls[0][0][1]["semantic_type"] == "NextApiRoute"

    def test_promotes_hook_nodes(self):
        """Functions starting with 'use' should become ReactHook."""
        store = _mock_store_with_responses(
            [
                [],  # components
                [],  # pages
                [],  # api_routes
                [["useAuth", "src/hooks/useAuth.ts"]],  # hooks
                [],  # stores
            ]
        )
        enricher = ReactEnricher(store)
        result = enricher.enrich()
        assert result.promoted == 1
        assert result.patterns_found["hooks"] == 1

        calls = store._graph.query.call_args_list
        promote_calls = [c for c in calls if "SET n.semantic_type" in c[0][0]]
        assert promote_calls[0][0][1]["semantic_type"] == "ReactHook"

    def test_promotes_store_nodes(self):
        """createStore / useStore patterns should become ReactStore."""
        store = _mock_store_with_responses(
            [
                [],  # components
                [],  # pages
                [],  # api_routes
                [],  # hooks
                [["createStore", "src/store/index.ts"]],  # stores
            ]
        )
        enricher = ReactEnricher(store)
        result = enricher.enrich()
        assert result.promoted == 1
        assert result.patterns_found["stores"] == 1

        calls = store._graph.query.call_args_list
        promote_calls = [c for c in calls if "SET n.semantic_type" in c[0][0]]
        assert promote_calls[0][0][1]["semantic_type"] == "ReactStore"

    def test_promoted_count_accumulates_across_patterns(self):
        """promoted should be the sum of all matched nodes."""
        # Each matched node triggers a _promote_node SET query after its pattern query.
        # Responses must be interleaved: pattern_query, promote, promote, pattern_query, ...
        store = _mock_store_with_responses(
            [
                [["Btn", "a.jsx"], ["Input", "b.tsx"]],  # components query (2 rows)
                None,   # _promote_node for Btn
                None,   # _promote_node for Input
                [["Home", "pages/index.tsx"]],            # pages query (1 row)
                None,   # _promote_node for Home
                [],     # api_routes query
                [["useUser", "hooks/useUser.ts"]],         # hooks query (1 row)
                None,   # _promote_node for useUser
                [],     # stores query
            ]
        )
        enricher = ReactEnricher(store)
        result = enricher.enrich()
        assert result.promoted == 4
        assert result.patterns_found["components"] == 2
        assert result.patterns_found["pages"] == 1
        assert result.patterns_found["hooks"] == 1
