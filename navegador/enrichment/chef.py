"""
Chef framework enricher.

Promotes generic graph nodes created by the Ruby parser to Chef-specific
semantic types:
  - chef_recipe    — files under recipes/
  - chef_cookbook   — metadata.rb files under cookbooks/
  - chef_resource  — functions/methods in recipes/ or libraries/ matching
                     Chef resource names (package, template, service, etc.)
  - include_recipe — DEPENDS_ON edges for cross-recipe includes
"""

from navegador.enrichment.base import EnrichmentResult, FrameworkEnricher
from navegador.graph.store import GraphStore

# Built-in Chef resource types that appear as method calls in recipes
_CHEF_RESOURCES = frozenset(
    {
        "package",
        "template",
        "service",
        "execute",
        "file",
        "directory",
        "cookbook_file",
        "remote_file",
        "cron",
        "user",
        "group",
        "mount",
        "link",
        "bash",
        "ruby_block",
        "apt_package",
        "yum_package",
        "powershell_script",
        "windows_service",
        "chef_gem",
        "log",
        "http_request",
        "remote_directory",
    }
)


class ChefEnricher(FrameworkEnricher):
    """Enriches a navegador graph with Chef-specific semantic types."""

    def __init__(self, store: GraphStore) -> None:
        super().__init__(store)

    # ── Identity ──────────────────────────────────────────────────────────────

    @property
    def framework_name(self) -> str:
        return "chef"

    @property
    def detection_patterns(self) -> list[str]:
        return ["chef"]

    @property
    def detection_files(self) -> list[str]:
        return ["metadata.rb", "Berksfile"]

    # ── Enrichment ────────────────────────────────────────────────────────────

    def enrich(self) -> EnrichmentResult:
        result = EnrichmentResult()

        recipes = self._enrich_recipes()
        result.promoted += recipes
        result.patterns_found["recipes"] = recipes

        cookbooks = self._enrich_cookbooks()
        result.promoted += cookbooks
        result.patterns_found["cookbooks"] = cookbooks

        resources = self._enrich_resources()
        result.promoted += resources
        result.patterns_found["resources"] = resources

        includes = self._enrich_include_recipe()
        result.edges_added += includes
        result.patterns_found["include_recipe"] = includes

        return result

    # ── Pattern helpers ───────────────────────────────────────────────────────

    def _enrich_recipes(self) -> int:
        """Promote File nodes under /recipes/ to chef_recipe."""
        promoted = 0
        query_result = self.store.query(
            "MATCH (n:File) WHERE n.file_path CONTAINS $pattern RETURN n.name, n.file_path",
            {"pattern": "/recipes/"},
        )
        rows = query_result.result_set or []
        for row in rows:
            name, file_path = row[0], row[1]
            if name and file_path:
                self._promote_node(name, file_path, "chef_recipe")
                promoted += 1
        return promoted

    def _enrich_cookbooks(self) -> int:
        """Promote metadata.rb File nodes under /cookbooks/ to chef_cookbook."""
        promoted = 0
        query_result = self.store.query(
            "MATCH (n:File) WHERE n.file_path CONTAINS $cookbooks "
            "AND n.name = $name "
            "RETURN n.name, n.file_path",
            {"cookbooks": "/cookbooks/", "name": "metadata.rb"},
        )
        rows = query_result.result_set or []
        for row in rows:
            name, file_path = row[0], row[1]
            if name and file_path:
                self._promote_node(name, file_path, "chef_cookbook")
                promoted += 1
        return promoted

    def _enrich_resources(self) -> int:
        """Promote Function/Method nodes in recipes/ or libraries/ whose names
        match Chef built-in resource types."""
        promoted = 0
        for path_fragment in ("/recipes/", "/libraries/"):
            query_result = self.store.query(
                "MATCH (n) WHERE (n:Function OR n:Method) "
                "AND n.file_path CONTAINS $pattern "
                "RETURN n.name, n.file_path",
                {"pattern": path_fragment},
            )
            rows = query_result.result_set or []
            for row in rows:
                name, file_path = row[0], row[1]
                if name and file_path and name in _CHEF_RESOURCES:
                    self._promote_node(name, file_path, "chef_resource")
                    promoted += 1
        return promoted

    def _enrich_include_recipe(self) -> int:
        """Link include_recipe calls to the referenced recipe File nodes.

        Looks for Function nodes named ``include_recipe`` and follows CALLS
        edges or checks node properties to find the recipe name argument,
        then creates a DEPENDS_ON edge to the matching recipe File node.
        """
        edges_added = 0

        # Strategy 1: follow CALLS edges from include_recipe nodes
        query_result = self.store.query(
            "MATCH (n:Function)-[:CALLS]->(target) "
            "WHERE n.name = $name "
            "RETURN n.file_path, target.name",
            {"name": "include_recipe"},
        )
        rows = query_result.result_set or []
        for row in rows:
            caller_path, recipe_ref = row[0], row[1]
            if caller_path and recipe_ref:
                # recipe_ref may be "cookbook::recipe" — extract recipe name
                recipe_name = recipe_ref.split("::")[-1] if "::" in recipe_ref else recipe_ref
                # Find the recipe File node
                match_result = self.store.query(
                    "MATCH (f:File) WHERE f.file_path CONTAINS $recipes "
                    "AND f.name CONTAINS $recipe "
                    "RETURN f.name",
                    {"recipes": "/recipes/", "recipe": recipe_name},
                )
                match_rows = match_result.result_set or []
                if match_rows and match_rows[0][0]:
                    # Create DEPENDS_ON from the caller's file to the recipe file
                    caller_file_result = self.store.query(
                        "MATCH (f:File) WHERE f.file_path = $path RETURN f.name",
                        {"path": caller_path},
                    )
                    caller_rows = caller_file_result.result_set or []
                    if caller_rows and caller_rows[0][0]:
                        self._add_semantic_edge(
                            caller_rows[0][0],
                            "DEPENDS_ON",
                            match_rows[0][0],
                        )
                        edges_added += 1

        # Strategy 2: check signature/docstring for include_recipe calls
        for prop in ("signature", "docstring"):
            query_result = self.store.query(
                f"MATCH (n) WHERE (n:Function OR n:Method) "
                f"AND n.{prop} IS NOT NULL "
                f"AND n.{prop} CONTAINS $pattern "
                "RETURN n.name, n.file_path",
                {"pattern": "include_recipe"},
            )
            rows = query_result.result_set or []
            for row in rows:
                name, file_path = row[0], row[1]
                if name and file_path and name == "include_recipe":
                    # Already handled in strategy 1 via CALLS edges
                    continue

        return edges_added
