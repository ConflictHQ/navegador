"""Tests for navegador.enrichment.react_native — ReactNativeEnricher."""

from unittest.mock import MagicMock

from navegador.enrichment import EnrichmentResult
from navegador.enrichment.react_native import ReactNativeEnricher
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
    def test_framework_name_is_react_native(self):
        enricher = ReactNativeEnricher(_mock_store())
        assert enricher.framework_name == "react-native"


# ── detection_patterns ────────────────────────────────────────────────────────


class TestDetectionPatterns:
    def test_contains_react_native_hyphenated(self):
        enricher = ReactNativeEnricher(_mock_store())
        assert "react-native" in enricher.detection_patterns

    def test_contains_expo(self):
        enricher = ReactNativeEnricher(_mock_store())
        assert "expo" in enricher.detection_patterns

    def test_returns_list(self):
        enricher = ReactNativeEnricher(_mock_store())
        assert isinstance(enricher.detection_patterns, list)

    def test_has_at_least_two_patterns(self):
        enricher = ReactNativeEnricher(_mock_store())
        assert len(enricher.detection_patterns) >= 2


# ── detect() ─────────────────────────────────────────────────────────────────


class TestDetect:
    def test_returns_true_when_pattern_found(self):
        store = _mock_store(result_set=[[1]])
        enricher = ReactNativeEnricher(store)
        assert enricher.detect() is True

    def test_returns_false_when_no_match(self):
        store = _mock_store(result_set=[[0]])
        enricher = ReactNativeEnricher(store)
        assert enricher.detect() is False

    def test_returns_false_on_empty_result_set(self):
        store = _mock_store(result_set=[])
        enricher = ReactNativeEnricher(store)
        assert enricher.detect() is False

    def test_short_circuits_on_first_match(self):
        store = _mock_store(result_set=[[1]])
        enricher = ReactNativeEnricher(store)
        assert enricher.detect() is True
        assert store._graph.query.call_count == 1


# ── enrich() ─────────────────────────────────────────────────────────────────


class TestEnrich:
    def _empty_enricher(self):
        # 4 pattern queries: components, screens, hooks, navigation
        store = _mock_store_with_responses([[], [], [], []])
        return ReactNativeEnricher(store)

    def test_returns_enrichment_result(self):
        assert isinstance(self._empty_enricher().enrich(), EnrichmentResult)

    def test_zero_promoted_when_no_matches(self):
        result = self._empty_enricher().enrich()
        assert result.promoted == 0

    def test_patterns_found_keys_present(self):
        result = self._empty_enricher().enrich()
        assert "components" in result.patterns_found
        assert "screens" in result.patterns_found
        assert "hooks" in result.patterns_found
        assert "navigation" in result.patterns_found

    def test_promotes_component_nodes(self):
        """JSX/TSX nodes should become RNComponent."""
        store = _mock_store_with_responses(
            [
                [["Button", "src/components/Button.tsx"]],  # components
                [],  # screens
                [],  # hooks
                [],  # navigation
            ]
        )
        enricher = ReactNativeEnricher(store)
        result = enricher.enrich()
        assert result.promoted == 1
        assert result.patterns_found["components"] == 1

        calls = store._graph.query.call_args_list
        promote_calls = [c for c in calls if "SET n.semantic_type" in c[0][0]]
        assert len(promote_calls) == 1
        assert promote_calls[0][0][1]["semantic_type"] == "RNComponent"

    def test_promotes_screen_nodes_by_path(self):
        """Nodes under /screens/ should become RNScreen."""
        store = _mock_store_with_responses(
            [
                [],  # components
                [["HomeScreen", "src/screens/HomeScreen.tsx"]],  # screens
                [],  # hooks
                [],  # navigation
            ]
        )
        enricher = ReactNativeEnricher(store)
        result = enricher.enrich()
        assert result.promoted == 1
        assert result.patterns_found["screens"] == 1

        calls = store._graph.query.call_args_list
        promote_calls = [c for c in calls if "SET n.semantic_type" in c[0][0]]
        assert promote_calls[0][0][1]["semantic_type"] == "RNScreen"

    def test_promotes_screen_nodes_by_name_suffix(self):
        """Nodes whose name ends with 'Screen' should become RNScreen."""
        store = _mock_store_with_responses(
            [
                [],  # components
                [["ProfileScreen", "src/ProfileScreen.tsx"]],  # screens
                [],  # hooks
                [],  # navigation
            ]
        )
        enricher = ReactNativeEnricher(store)
        result = enricher.enrich()
        assert result.promoted == 1

        calls = store._graph.query.call_args_list
        promote_calls = [c for c in calls if "SET n.semantic_type" in c[0][0]]
        assert promote_calls[0][0][1]["semantic_type"] == "RNScreen"

    def test_promotes_hook_nodes(self):
        """use* functions should become RNHook."""
        store = _mock_store_with_responses(
            [
                [],  # components
                [],  # screens
                [["useTheme", "src/hooks/useTheme.ts"]],  # hooks
                [],  # navigation
            ]
        )
        enricher = ReactNativeEnricher(store)
        result = enricher.enrich()
        assert result.promoted == 1
        assert result.patterns_found["hooks"] == 1

        calls = store._graph.query.call_args_list
        promote_calls = [c for c in calls if "SET n.semantic_type" in c[0][0]]
        assert promote_calls[0][0][1]["semantic_type"] == "RNHook"

    def test_promotes_navigation_nodes(self):
        """createStackNavigator etc. should become RNNavigation."""
        store = _mock_store_with_responses(
            [
                [],  # components
                [],  # screens
                [],  # hooks
                [["createStackNavigator", "src/navigation/AppNavigator.ts"]],  # navigation
            ]
        )
        enricher = ReactNativeEnricher(store)
        result = enricher.enrich()
        assert result.promoted == 1
        assert result.patterns_found["navigation"] == 1

        calls = store._graph.query.call_args_list
        promote_calls = [c for c in calls if "SET n.semantic_type" in c[0][0]]
        assert promote_calls[0][0][1]["semantic_type"] == "RNNavigation"

    def test_promoted_accumulates_across_patterns(self):
        # Each matched node triggers a _promote_node SET query after its pattern query.
        store = _mock_store_with_responses(
            [
                [["View", "a.tsx"], ["Text", "b.tsx"]],   # components query (2 rows)
                None,   # _promote_node for View
                None,   # _promote_node for Text
                [["HomeScreen", "screens/Home.tsx"]],      # screens query (1 row)
                None,   # _promote_node for HomeScreen
                [["useAuth", "hooks/useAuth.ts"]],          # hooks query (1 row)
                None,   # _promote_node for useAuth
                [["NavigationContainer", "nav/App.ts"],
                 ["createStackNavigator", "nav/App.ts"]],  # navigation query (2 rows)
                None,   # _promote_node for NavigationContainer
                None,   # _promote_node for createStackNavigator
            ]
        )
        enricher = ReactNativeEnricher(store)
        result = enricher.enrich()
        assert result.promoted == 6
        assert result.patterns_found["components"] == 2
        assert result.patterns_found["screens"] == 1
        assert result.patterns_found["hooks"] == 1
        assert result.patterns_found["navigation"] == 2

    def test_promote_node_called_with_correct_semantic_type_for_navigation(self):
        store = _mock_store_with_responses(
            [
                [],
                [],
                [],
                [["createBottomTabNavigator", "src/nav/Tabs.ts"]],
            ]
        )
        enricher = ReactNativeEnricher(store)
        enricher.enrich()

        calls = store._graph.query.call_args_list
        promote_calls = [c for c in calls if "SET n.semantic_type" in c[0][0]]
        params = promote_calls[0][0][1]
        assert params["name"] == "createBottomTabNavigator"
        assert params["file_path"] == "src/nav/Tabs.ts"
        assert params["semantic_type"] == "RNNavigation"
