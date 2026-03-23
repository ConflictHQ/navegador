"""
DjangoEnricher — post-ingestion enrichment for Django codebases.

Promotes generic Function/Class nodes to Django-specific semantic types:
  - View         functions in views.py files
  - Model        classes that inherit from Model
  - URLPattern   functions in urls.py files
  - Serializer   classes in serializers.py files
  - Task         functions decorated with @task or in tasks.py
  - Admin        classes in admin.py files
"""

from navegador.enrichment.base import EnrichmentResult, FrameworkEnricher


class DjangoEnricher(FrameworkEnricher):
    """Enriches a navegador graph with Django framework semantics."""

    @property
    def framework_name(self) -> str:
        return "django"

    @property
    def detection_patterns(self) -> list[str]:
        return ["manage.py", "django.conf", "settings.py", "urls.py"]

    def enrich(self) -> EnrichmentResult:
        result = EnrichmentResult()

        # ── Views ────────────────────────────────────────────────────────────
        rows = self._query_rows(
            "MATCH (n:Function) WHERE n.file_path CONTAINS 'views.py' "
            "RETURN n.name AS name, n.file_path AS file_path"
        )
        for name, file_path in rows:
            self._promote_node(name, file_path, "View")
        result.patterns_found["views"] = len(rows)
        result.promoted += len(rows)

        # ── Models ───────────────────────────────────────────────────────────
        rows = self._query_rows(
            "MATCH (n:Class)-[:INHERITS]->(b) WHERE b.name CONTAINS 'Model' "
            "RETURN n.name AS name, n.file_path AS file_path"
        )
        for name, file_path in rows:
            self._promote_node(name, file_path, "Model")
        result.patterns_found["models"] = len(rows)
        result.promoted += len(rows)

        # ── URL patterns ─────────────────────────────────────────────────────
        rows = self._query_rows(
            "MATCH (n:Function) WHERE n.file_path CONTAINS 'urls.py' "
            "RETURN n.name AS name, n.file_path AS file_path"
        )
        for name, file_path in rows:
            self._promote_node(name, file_path, "URLPattern")
        result.patterns_found["url_patterns"] = len(rows)
        result.promoted += len(rows)

        # ── Serializers ──────────────────────────────────────────────────────
        rows = self._query_rows(
            "MATCH (n:Class) WHERE n.file_path CONTAINS 'serializers.py' "
            "RETURN n.name AS name, n.file_path AS file_path"
        )
        for name, file_path in rows:
            self._promote_node(name, file_path, "Serializer")
        result.patterns_found["serializers"] = len(rows)
        result.promoted += len(rows)

        # ── Tasks (decorator or tasks.py) ─────────────────────────────────
        rows = self._query_rows(
            "MATCH (n:Function) WHERE n.file_path CONTAINS 'tasks.py' "
            "OR (n.decorators IS NOT NULL AND n.decorators CONTAINS 'task') "
            "RETURN n.name AS name, n.file_path AS file_path"
        )
        for name, file_path in rows:
            self._promote_node(name, file_path, "Task")
        result.patterns_found["tasks"] = len(rows)
        result.promoted += len(rows)

        # ── Admin ─────────────────────────────────────────────────────────
        rows = self._query_rows(
            "MATCH (n:Class) WHERE n.file_path CONTAINS 'admin.py' "
            "RETURN n.name AS name, n.file_path AS file_path"
        )
        for name, file_path in rows:
            self._promote_node(name, file_path, "Admin")
        result.patterns_found["admin"] = len(rows)
        result.promoted += len(rows)

        return result

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _query_rows(self, cypher: str) -> list[tuple[str, str]]:
        """Run a Cypher query and return (name, file_path) pairs."""
        query_result = self.store.query(cypher)
        rows = query_result.result_set or []
        return [(row[0], row[1]) for row in rows]
