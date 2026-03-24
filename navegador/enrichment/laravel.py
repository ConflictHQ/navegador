"""
Laravel framework enricher.

Promotes generic graph nodes to Laravel semantic types:
  - Controller  (files under Controllers/)
  - Model       (classes that extend Model)
  - Route       (files under routes/)
  - Job         (files under Jobs/)
  - Policy      (files under Policies/)
"""

from navegador.enrichment.base import EnrichmentResult, FrameworkEnricher


class LaravelEnricher(FrameworkEnricher):
    """Enriches a navegador graph with Laravel-specific semantics."""

    @property
    def framework_name(self) -> str:
        return "laravel"

    @property
    def detection_patterns(self) -> list[str]:
        return ["Illuminate"]

    @property
    def detection_files(self) -> list[str]:
        return ["artisan"]

    def enrich(self) -> EnrichmentResult:
        result = EnrichmentResult()

        # Path-based promotions: (file_path_fragment, semantic_type, pattern_key)
        path_promotions = [
            ("Controllers/", "LaravelController", "controllers"),
            ("routes/", "LaravelRoute", "routes"),
            ("Jobs/", "LaravelJob", "jobs"),
            ("Policies/", "LaravelPolicy", "policies"),
        ]

        for path_fragment, semantic_type, pattern_key in path_promotions:
            query_result = self.store.query(
                "MATCH (n) WHERE n.file_path IS NOT NULL "
                "AND n.file_path CONTAINS $fragment "
                "RETURN n.name, n.file_path",
                {"fragment": path_fragment},
            )
            rows = query_result.result_set or []
            count = 0
            for row in rows:
                name, file_path = row[0], row[1]
                self._promote_node(name, file_path, semantic_type)
                count += 1
                result.promoted += 1
            result.patterns_found[pattern_key] = count

        # Model detection: classes that extend Model (name or file_path contains "Model")
        model_result = self.store.query(
            "MATCH (n) WHERE "
            "(n.name IS NOT NULL AND n.name CONTAINS $fragment) OR "
            "(n.file_path IS NOT NULL AND n.file_path CONTAINS $fragment) "
            "RETURN n.name, n.file_path",
            {"fragment": "Model"},
        )
        rows = model_result.result_set or []
        model_count = 0
        for row in rows:
            name, file_path = row[0], row[1]
            self._promote_node(name, file_path, "LaravelModel")
            model_count += 1
            result.promoted += 1
        result.patterns_found["models"] = model_count

        return result
