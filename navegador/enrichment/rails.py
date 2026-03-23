"""
Rails framework enricher.

Promotes generic graph nodes to Rails semantic types:
  - Controller  (files under controllers/)
  - Model       (files under models/)
  - Route       (routes.rb)
  - Job         (files under jobs/)
  - Concern     (files under concerns/)
"""

from navegador.enrichment.base import EnrichmentResult, FrameworkEnricher


class RailsEnricher(FrameworkEnricher):
    """Enriches a navegador graph with Rails-specific semantics."""

    @property
    def framework_name(self) -> str:
        return "rails"

    @property
    def detection_patterns(self) -> list[str]:
        return ["Gemfile", "config/routes.rb", "ApplicationController", "ActiveRecord"]

    def enrich(self) -> EnrichmentResult:
        result = EnrichmentResult()

        # Each tuple: (file_path_fragment, semantic_type, pattern_key)
        promotions = [
            ("controllers/", "RailsController", "controllers"),
            ("models/", "RailsModel", "models"),
            ("routes.rb", "RailsRoute", "routes"),
            ("jobs/", "RailsJob", "jobs"),
            ("concerns/", "RailsConcern", "concerns"),
        ]

        for path_fragment, semantic_type, pattern_key in promotions:
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

        return result
