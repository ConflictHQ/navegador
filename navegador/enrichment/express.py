"""
Express.js framework enricher.

Promotes generic Function/Class nodes to semantic Express types:
  - Route       — app.get / app.post / app.put / app.delete / app.patch calls
  - Middleware  — app.use calls
  - Controller  — functions/classes defined in a controllers/ directory
  - Router      — Router() instantiations / express.Router()
"""

from navegador.enrichment.base import EnrichmentResult, FrameworkEnricher

# HTTP method prefixes that indicate a route definition
_ROUTE_PREFIXES = (
    "app.get",
    "app.post",
    "app.put",
    "app.delete",
    "app.patch",
    "router.get",
    "router.post",
    "router.put",
    "router.delete",
    "router.patch",
)


class ExpressEnricher(FrameworkEnricher):
    """Enricher for Express.js codebases."""

    @property
    def framework_name(self) -> str:
        return "express"

    @property
    def detection_patterns(self) -> list[str]:
        return ["express"]

    def enrich(self) -> EnrichmentResult:
        result = EnrichmentResult()

        # ── Routes: app.<method> or router.<method> patterns ─────────────────
        route_rows = (
            self.store.query(
                "MATCH (n) WHERE (n.name STARTS WITH 'app.get' "
                "OR n.name STARTS WITH 'app.post' "
                "OR n.name STARTS WITH 'app.put' "
                "OR n.name STARTS WITH 'app.delete' "
                "OR n.name STARTS WITH 'app.patch' "
                "OR n.name STARTS WITH 'router.get' "
                "OR n.name STARTS WITH 'router.post' "
                "OR n.name STARTS WITH 'router.put' "
                "OR n.name STARTS WITH 'router.delete' "
                "OR n.name STARTS WITH 'router.patch') "
                "AND n.file_path IS NOT NULL "
                "RETURN n.name, n.file_path",
            ).result_set
            or []
        )
        for name, file_path in route_rows:
            self._promote_node(name, file_path, "ExpressRoute")
            result.promoted += 1
        result.patterns_found["routes"] = len(route_rows)

        # ── Middleware: app.use calls ─────────────────────────────────────────
        middleware_rows = (
            self.store.query(
                "MATCH (n) WHERE (n.name STARTS WITH 'app.use' "
                "OR n.name STARTS WITH 'router.use') "
                "AND n.file_path IS NOT NULL "
                "RETURN n.name, n.file_path",
            ).result_set
            or []
        )
        for name, file_path in middleware_rows:
            self._promote_node(name, file_path, "ExpressMiddleware")
            result.promoted += 1
        result.patterns_found["middleware"] = len(middleware_rows)

        # ── Controllers: nodes whose file_path contains /controllers/ ─────────
        controller_rows = (
            self.store.query(
                "MATCH (n) WHERE n.file_path CONTAINS '/controllers/' "
                "AND n.name IS NOT NULL "
                "RETURN n.name, n.file_path",
            ).result_set
            or []
        )
        for name, file_path in controller_rows:
            self._promote_node(name, file_path, "ExpressController")
            result.promoted += 1
        result.patterns_found["controllers"] = len(controller_rows)

        # ── Routers: Router() / express.Router() instantiations ──────────────
        router_rows = (
            self.store.query(
                "MATCH (n) WHERE (n.name = 'Router' OR n.name CONTAINS 'Router()' "
                "OR n.name CONTAINS 'express.Router') "
                "AND n.file_path IS NOT NULL "
                "RETURN n.name, n.file_path",
            ).result_set
            or []
        )
        for name, file_path in router_rows:
            self._promote_node(name, file_path, "ExpressRouter")
            result.promoted += 1
        result.patterns_found["routers"] = len(router_rows)

        return result
