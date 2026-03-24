"""
React Native framework enricher.

Promotes generic Function/Class nodes to semantic React Native types:
  - Component   — JSX/TSX files (same heuristic as React web)
  - Screen      — files under screens/ directory or names ending in "Screen"
  - Hook        — functions whose name starts with "use"
  - Navigation  — createStackNavigator / createBottomTabNavigator / NavigationContainer etc.
"""

from navegador.enrichment.base import EnrichmentResult, FrameworkEnricher

_NAVIGATION_PATTERNS = (
    "createStackNavigator",
    "createBottomTabNavigator",
    "createDrawerNavigator",
    "createNativeStackNavigator",
    "NavigationContainer",
    "useNavigation",
    "useRoute",
)


class ReactNativeEnricher(FrameworkEnricher):
    """Enricher for React Native and Expo codebases."""

    @property
    def framework_name(self) -> str:
        return "react-native"

    @property
    def detection_patterns(self) -> list[str]:
        return ["react-native", "expo"]

    @property
    def detection_files(self) -> list[str]:
        return ["app.json"]

    def enrich(self) -> EnrichmentResult:
        result = EnrichmentResult()

        # ── Components: functions/classes in .jsx or .tsx files ──────────────
        component_rows = (
            self.store.query(
                "MATCH (n) WHERE (n.file_path CONTAINS '.jsx' OR n.file_path CONTAINS '.tsx') "
                "AND n.name IS NOT NULL "
                "RETURN n.name, n.file_path",
            ).result_set
            or []
        )
        for name, file_path in component_rows:
            self._promote_node(name, file_path, "RNComponent")
            result.promoted += 1
        result.patterns_found["components"] = len(component_rows)

        # ── Screens: nodes under screens/ or whose names end with "Screen" ───
        screen_rows = (
            self.store.query(
                "MATCH (n) WHERE (n.file_path CONTAINS '/screens/' "
                "OR (n.name IS NOT NULL AND n.name ENDS WITH 'Screen')) "
                "AND n.name IS NOT NULL "
                "RETURN n.name, n.file_path",
            ).result_set
            or []
        )
        for name, file_path in screen_rows:
            self._promote_node(name, file_path, "RNScreen")
            result.promoted += 1
        result.patterns_found["screens"] = len(screen_rows)

        # ── Hooks: functions whose name starts with "use" ─────────────────────
        hook_rows = (
            self.store.query(
                "MATCH (n) WHERE n.name STARTS WITH 'use' "
                "AND n.name <> 'use' "
                "AND n.file_path IS NOT NULL "
                "RETURN n.name, n.file_path",
            ).result_set
            or []
        )
        for name, file_path in hook_rows:
            self._promote_node(name, file_path, "RNHook")
            result.promoted += 1
        result.patterns_found["hooks"] = len(hook_rows)

        # ── Navigation: navigator factory / container patterns ────────────────
        nav_conditions = " OR ".join(f"n.name CONTAINS '{pat}'" for pat in _NAVIGATION_PATTERNS)
        nav_rows = (
            self.store.query(
                f"MATCH (n) WHERE ({nav_conditions}) "
                "AND n.file_path IS NOT NULL "
                "RETURN n.name, n.file_path",
            ).result_set
            or []
        )
        for name, file_path in nav_rows:
            self._promote_node(name, file_path, "RNNavigation")
            result.promoted += 1
        result.patterns_found["navigation"] = len(nav_rows)

        return result
