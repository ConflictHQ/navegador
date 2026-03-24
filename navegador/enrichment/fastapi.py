"""
FastAPI framework enricher.

Promotes generic Function/Class nodes to FastAPI semantic types:
  - Route          — functions decorated with @app.<method> or @router.<method>
  - Dependency     — functions with Depends( in their signature
  - PydanticModel  — classes that inherit from BaseModel
  - BackgroundTask — functions decorated with @app.on_event or using BackgroundTasks
"""

from navegador.enrichment.base import EnrichmentResult, FrameworkEnricher
from navegador.graph.store import GraphStore

# HTTP verbs recognised as route decorators
_HTTP_METHODS = ("get", "post", "put", "delete", "patch", "head", "options")


class FastAPIEnricher(FrameworkEnricher):
    """Enriches a navegador graph with FastAPI-specific semantic types."""

    def __init__(self, store: GraphStore) -> None:
        super().__init__(store)

    # ── Identity ──────────────────────────────────────────────────────────────

    @property
    def framework_name(self) -> str:
        return "fastapi"

    @property
    def detection_patterns(self) -> list[str]:
        return ["fastapi"]

    # ── Enrichment ────────────────────────────────────────────────────────────

    def enrich(self) -> EnrichmentResult:
        result = EnrichmentResult()

        routes = self._enrich_routes()
        result.promoted += routes
        result.patterns_found["routes"] = routes

        dependencies = self._enrich_dependencies()
        result.promoted += dependencies
        result.patterns_found["dependencies"] = dependencies

        pydantic_models = self._enrich_pydantic_models()
        result.promoted += pydantic_models
        result.patterns_found["pydantic_models"] = pydantic_models

        background_tasks = self._enrich_background_tasks()
        result.promoted += background_tasks
        result.patterns_found["background_tasks"] = background_tasks

        return result

    # ── Pattern helpers ───────────────────────────────────────────────────────

    def _enrich_routes(self) -> int:
        """
        Find Function/Method nodes linked to Decorator nodes whose names match
        @app.<method> or @router.<method> patterns (e.g. app.get, router.post).

        Falls back to querying the ``signature`` and ``docstring`` properties for
        route decorator patterns when no Decorator nodes are present in the graph.
        """
        promoted = 0

        # Strategy 1: Decorator nodes connected via DECORATES edges
        for http_method in _HTTP_METHODS:
            result = self.store.query(
                "MATCH (d:Decorator)-[:DECORATES]->(n) "
                "WHERE d.name CONTAINS $pattern "
                "RETURN n.name, n.file_path",
                {"pattern": f".{http_method}"},
            )
            rows = result.result_set or []
            for row in rows:
                name, file_path = row[0], row[1]
                if name and file_path:
                    self._promote_node(name, file_path, "Route", {"http_method": http_method})
                    promoted += 1

        # Strategy 2: signature / docstring heuristics (no Decorator nodes)
        for http_method in _HTTP_METHODS:
            for prop in ("signature", "docstring"):
                result = self.store.query(
                    f"MATCH (n) WHERE (n:Function OR n:Method) "
                    f"AND n.{prop} IS NOT NULL "
                    f"AND n.{prop} CONTAINS $pattern "
                    "RETURN n.name, n.file_path",
                    {"pattern": f".{http_method}("},
                )
                rows = result.result_set or []
                for row in rows:
                    name, file_path = row[0], row[1]
                    if name and file_path:
                        self._promote_node(name, file_path, "Route", {"http_method": http_method})
                        promoted += 1

        return promoted

    def _enrich_dependencies(self) -> int:
        """
        Find Function/Method nodes whose signature contains ``Depends(``.
        These are FastAPI dependency-injection callables.
        """
        promoted = 0

        for prop in ("signature", "docstring"):
            result = self.store.query(
                f"MATCH (n) WHERE (n:Function OR n:Method) "
                f"AND n.{prop} IS NOT NULL "
                f"AND n.{prop} CONTAINS $pattern "
                "RETURN n.name, n.file_path",
                {"pattern": "Depends("},
            )
            rows = result.result_set or []
            for row in rows:
                name, file_path = row[0], row[1]
                if name and file_path:
                    self._promote_node(name, file_path, "Dependency")
                    promoted += 1

        # Also match via Decorator nodes named "Depends"
        result = self.store.query(
            "MATCH (d:Decorator)-[:DECORATES]->(n) "
            "WHERE d.name CONTAINS $pattern "
            "RETURN n.name, n.file_path",
            {"pattern": "Depends"},
        )
        rows = result.result_set or []
        for row in rows:
            name, file_path = row[0], row[1]
            if name and file_path:
                self._promote_node(name, file_path, "Dependency")
                promoted += 1

        return promoted

    def _enrich_pydantic_models(self) -> int:
        """
        Find Class nodes that inherit from ``BaseModel`` via INHERITS edges,
        or whose name appears as a base in the raw INHERITS graph.
        """
        promoted = 0

        # Primary: INHERITS edges pointing to a node named "BaseModel"
        result = self.store.query(
            "MATCH (n:Class)-[:INHERITS]->(base) "
            "WHERE base.name = $base_name "
            "RETURN n.name, n.file_path",
            {"base_name": "BaseModel"},
        )
        rows = result.result_set or []
        for row in rows:
            name, file_path = row[0], row[1]
            if name and file_path:
                self._promote_node(name, file_path, "PydanticModel")
                promoted += 1

        # Fallback: docstring or source mentions BaseModel
        result = self.store.query(
            "MATCH (n:Class) "
            "WHERE n.docstring IS NOT NULL AND n.docstring CONTAINS $pattern "
            "RETURN n.name, n.file_path",
            {"pattern": "BaseModel"},
        )
        rows = result.result_set or []
        for row in rows:
            name, file_path = row[0], row[1]
            if name and file_path:
                self._promote_node(name, file_path, "PydanticModel")
                promoted += 1

        return promoted

    def _enrich_background_tasks(self) -> int:
        """
        Find Function/Method nodes that:
          - are decorated with @app.on_event (via Decorator nodes or signature), or
          - reference ``BackgroundTasks`` in their signature / docstring.
        """
        promoted = 0

        # Strategy 1: Decorator nodes matching on_event
        result = self.store.query(
            "MATCH (d:Decorator)-[:DECORATES]->(n) "
            "WHERE d.name CONTAINS $pattern "
            "RETURN n.name, n.file_path",
            {"pattern": "on_event"},
        )
        rows = result.result_set or []
        for row in rows:
            name, file_path = row[0], row[1]
            if name and file_path:
                self._promote_node(name, file_path, "BackgroundTask")
                promoted += 1

        # Strategy 2: signature / docstring heuristics for on_event and BackgroundTasks
        for pattern in ("on_event(", "BackgroundTasks"):
            for prop in ("signature", "docstring"):
                result = self.store.query(
                    f"MATCH (n) WHERE (n:Function OR n:Method) "
                    f"AND n.{prop} IS NOT NULL "
                    f"AND n.{prop} CONTAINS $pattern "
                    "RETURN n.name, n.file_path",
                    {"pattern": pattern},
                )
                rows = result.result_set or []
                for row in rows:
                    name, file_path = row[0], row[1]
                    if name and file_path:
                        self._promote_node(name, file_path, "BackgroundTask")
                        promoted += 1

        return promoted
