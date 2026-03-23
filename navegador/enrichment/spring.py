"""
Spring Boot framework enricher.

Promotes generic graph nodes to Spring Boot semantic types:
  - Controller   (classes annotated with @Controller or @RestController)
  - Service      (classes annotated with @Service)
  - Repository   (classes annotated with @Repository)
  - Entity       (classes annotated with @Entity)
"""

from navegador.enrichment.base import EnrichmentResult, FrameworkEnricher


class SpringEnricher(FrameworkEnricher):
    """Enriches a navegador graph with Spring Boot-specific semantics."""

    @property
    def framework_name(self) -> str:
        return "spring"

    @property
    def detection_patterns(self) -> list[str]:
        return ["@SpringBootApplication", "spring-boot", "application.properties"]

    def enrich(self) -> EnrichmentResult:
        result = EnrichmentResult()

        # Each tuple: (annotation_fragment, semantic_type, pattern_key)
        # Annotations are stored as node names or in node properties; we search
        # both n.name and n.file_path for the annotation string.
        promotions = [
            ("@Controller", "SpringController", "controllers"),
            ("@RestController", "SpringRestController", "rest_controllers"),
            ("@Service", "SpringService", "services"),
            ("@Repository", "SpringRepository", "repositories"),
            ("@Entity", "SpringEntity", "entities"),
        ]

        for annotation, semantic_type, pattern_key in promotions:
            query_result = self.store.query(
                "MATCH (n) WHERE "
                "(n.name IS NOT NULL AND n.name CONTAINS $annotation) OR "
                "(n.file_path IS NOT NULL AND n.file_path CONTAINS $annotation) "
                "RETURN n.name, n.file_path",
                {"annotation": annotation},
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
