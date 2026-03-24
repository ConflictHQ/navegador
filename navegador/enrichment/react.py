"""
React / Next.js framework enricher.

Promotes generic Function/Class nodes to semantic React types:
  - Component   — JSX files or PascalCase functions in .jsx/.tsx
  - Page        — files under pages/ directory
  - ApiRoute    — files under pages/api/ or app/api/ directory
  - Hook        — functions whose name starts with "use"
  - Store       — functions/classes matching createStore or useStore patterns
"""

from navegador.enrichment.base import EnrichmentResult, FrameworkEnricher


class ReactEnricher(FrameworkEnricher):
    """Enricher for React and Next.js codebases."""

    @property
    def framework_name(self) -> str:
        return "react"

    @property
    def detection_patterns(self) -> list[str]:
        return ["react", "react-dom", "next"]

    @property
    def detection_files(self) -> list[str]:
        return ["next.config.js", "next.config.ts", "next.config.mjs"]

    def enrich(self) -> EnrichmentResult:
        result = EnrichmentResult()

        # ── Components: functions/classes defined in .jsx or .tsx files ──────
        component_rows = (
            self.store.query(
                "MATCH (n) WHERE (n.file_path CONTAINS '.jsx' OR n.file_path CONTAINS '.tsx') "
                "AND n.name IS NOT NULL "
                "RETURN n.name, n.file_path",
            ).result_set
            or []
        )
        for name, file_path in component_rows:
            self._promote_node(name, file_path, "ReactComponent")
            result.promoted += 1
        result.patterns_found["components"] = len(component_rows)

        # ── Pages: nodes whose file_path contains /pages/ ────────────────────
        page_rows = (
            self.store.query(
                "MATCH (n) WHERE n.file_path CONTAINS '/pages/' "
                "AND NOT n.file_path CONTAINS '/pages/api/' "
                "AND n.name IS NOT NULL "
                "RETURN n.name, n.file_path",
            ).result_set
            or []
        )
        for name, file_path in page_rows:
            self._promote_node(name, file_path, "NextPage")
            result.promoted += 1
        result.patterns_found["pages"] = len(page_rows)

        # ── API Routes: nodes under pages/api/ or app/api/ ───────────────────
        api_rows = (
            self.store.query(
                "MATCH (n) WHERE (n.file_path CONTAINS '/pages/api/' "
                "OR n.file_path CONTAINS '/app/api/') "
                "AND n.name IS NOT NULL "
                "RETURN n.name, n.file_path",
            ).result_set
            or []
        )
        for name, file_path in api_rows:
            self._promote_node(name, file_path, "NextApiRoute")
            result.promoted += 1
        result.patterns_found["api_routes"] = len(api_rows)

        # ── Hooks: functions whose name starts with "use" ────────────────────
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
            self._promote_node(name, file_path, "ReactHook")
            result.promoted += 1
        result.patterns_found["hooks"] = len(hook_rows)

        # ── Stores: createStore / useStore patterns ───────────────────────────
        store_rows = (
            self.store.query(
                "MATCH (n) WHERE (n.name CONTAINS 'createStore' OR n.name CONTAINS 'useStore') "
                "AND n.file_path IS NOT NULL "
                "RETURN n.name, n.file_path",
            ).result_set
            or []
        )
        for name, file_path in store_rows:
            self._promote_node(name, file_path, "ReactStore")
            result.promoted += 1
        result.patterns_found["stores"] = len(store_rows)

        return result
