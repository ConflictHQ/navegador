"""
Terraform enricher for cross-file module resolution and resource linking.

Promotes and links Terraform graph nodes:
  - Cross-file variable references  (REFERENCES edges)
  - Module source resolution         (DEPENDS_ON edges to local source dirs)
  - Provider grouping                (BELONGS_TO edges to provider nodes)
"""

from navegador.enrichment.base import EnrichmentResult, FrameworkEnricher
from navegador.graph.store import GraphStore

# Common Terraform provider prefixes and their canonical provider names
_PROVIDER_PREFIXES = {
    "aws_": "aws",
    "google_": "google",
    "azurerm_": "azurerm",
    "azuread_": "azuread",
    "kubernetes_": "kubernetes",
    "helm_": "helm",
    "vault_": "vault",
    "datadog_": "datadog",
    "cloudflare_": "cloudflare",
    "digitalocean_": "digitalocean",
    "github_": "github",
    "null_": "null",
    "random_": "random",
    "local_": "local",
    "tls_": "tls",
    "archive_": "archive",
    "external_": "external",
    "template_": "template",
    "time_": "time",
}


class TerraformEnricher(FrameworkEnricher):
    """Enriches a navegador graph with Terraform-specific semantics."""

    def __init__(self, store: GraphStore) -> None:
        super().__init__(store)

    # ── Identity ──────────────────────────────────────────────────────────────

    @property
    def framework_name(self) -> str:
        return "terraform"

    @property
    def detection_patterns(self) -> list[str]:
        return []  # No import nodes for Terraform

    @property
    def detection_files(self) -> list[str]:
        return ["main.tf", "variables.tf", "outputs.tf", "providers.tf"]

    # ── Enrichment ────────────────────────────────────────────────────────────

    def enrich(self) -> EnrichmentResult:
        result = EnrichmentResult()

        var_refs = self._enrich_variable_references()
        result.edges_added += var_refs
        result.patterns_found["variable_references"] = var_refs

        module_deps = self._enrich_module_sources()
        result.edges_added += module_deps
        result.patterns_found["module_sources"] = module_deps

        provider_links = self._enrich_provider_grouping()
        result.edges_added += provider_links
        result.patterns_found["provider_grouping"] = provider_links

        return result

    # ── Pattern helpers ───────────────────────────────────────────────────────

    def _enrich_variable_references(self) -> int:
        """Find terraform_variable and terraform_output nodes that reference
        variables defined in other files, and create REFERENCES edges."""
        edges_added = 0

        # Find all terraform_variable nodes
        var_result = self.store.query(
            "MATCH (v) WHERE v.semantic_type = $var_type RETURN v.name, v.file_path",
            {"var_type": "terraform_variable"},
        )
        var_rows = var_result.result_set or []
        var_by_name: dict[str, list[str]] = {}
        for row in var_rows:
            name, file_path = row[0], row[1]
            if name and file_path:
                var_by_name.setdefault(name, []).append(file_path)

        # Find terraform_output nodes and check if they reference variables
        # from other files (outputs often reference var.xxx)
        output_result = self.store.query(
            "MATCH (o) WHERE o.semantic_type = $out_type RETURN o.name, o.file_path",
            {"out_type": "terraform_output"},
        )
        output_rows = output_result.result_set or []
        for row in output_rows:
            out_name, out_file = row[0], row[1]
            if not (out_name and out_file):
                continue

            # Check CALLS or REFERENCES edges from this output to variables
            ref_result = self.store.query(
                "MATCH (o)-[:CALLS]->(target) "
                "WHERE o.name = $name AND o.file_path = $path "
                "RETURN target.name, target.file_path",
                {"name": out_name, "path": out_file},
            )
            ref_rows = ref_result.result_set or []
            for ref_row in ref_rows:
                target_name, target_path = ref_row[0], ref_row[1]
                if target_name and target_path and target_path != out_file:
                    self._add_semantic_edge(out_name, "REFERENCES", target_name)
                    edges_added += 1

        # Also link variables in different files that share the same name
        # (e.g. variables.tf defines var, main.tf uses it)
        for var_name, paths in var_by_name.items():
            if len(paths) <= 1:
                continue
            # Find nodes in other files that reference this variable
            for path in paths:
                ref_result = self.store.query(
                    "MATCH (n) WHERE n.file_path <> $path "
                    "AND n.name = $name "
                    "AND n.semantic_type = $var_type "
                    "RETURN n.name, n.file_path",
                    {"path": path, "name": var_name, "var_type": "terraform_variable"},
                )
                ref_rows = ref_result.result_set or []
                for ref_row in ref_rows:
                    ref_name = ref_row[0]
                    if ref_name:
                        self._add_semantic_edge(var_name, "REFERENCES", ref_name)
                        edges_added += 1

        return edges_added

    def _enrich_module_sources(self) -> int:
        """Find terraform_module nodes with local source paths and create
        DEPENDS_ON edges to File nodes in the referenced directory."""
        edges_added = 0

        # Find Module nodes with terraform_module semantic type
        module_result = self.store.query(
            "MATCH (m) WHERE m.semantic_type = $mod_type RETURN m.name, m.file_path",
            {"mod_type": "terraform_module"},
        )
        module_rows = module_result.result_set or []

        for row in module_rows:
            mod_name, mod_file = row[0], row[1]
            if not (mod_name and mod_file):
                continue

            # Check for CALLS edges that may point to the source path,
            # or look for a source property on the node
            source_result = self.store.query(
                "MATCH (m)-[:CALLS]->(target) "
                "WHERE m.name = $name AND m.file_path = $path "
                "RETURN target.name, target.file_path",
                {"name": mod_name, "path": mod_file},
            )
            source_rows = source_result.result_set or []

            for source_row in source_rows:
                target_name, target_path = source_row[0], source_row[1]
                if target_name and target_path:
                    self._add_semantic_edge(mod_name, "DEPENDS_ON", target_name)
                    edges_added += 1
                    continue

            # Fallback: look for File nodes whose path contains the module name
            # (local modules are often in ./modules/<name>/)
            file_result = self.store.query(
                "MATCH (f:File) WHERE f.file_path CONTAINS $fragment RETURN f.name",
                {"fragment": f"/modules/{mod_name}/"},
            )
            file_rows = file_result.result_set or []
            for file_row in file_rows:
                target_name = file_row[0]
                if target_name:
                    self._add_semantic_edge(mod_name, "DEPENDS_ON", target_name)
                    edges_added += 1

        return edges_added

    def _enrich_provider_grouping(self) -> int:
        """Group Terraform resources by their provider prefix and create
        BELONGS_TO edges from resources to provider nodes."""
        edges_added = 0

        # Find all terraform_resource nodes
        resource_result = self.store.query(
            "MATCH (r) WHERE r.semantic_type = $res_type RETURN r.name, r.file_path",
            {"res_type": "terraform_resource"},
        )
        resource_rows = resource_result.result_set or []

        for row in resource_rows:
            res_name, res_file = row[0], row[1]
            if not (res_name and res_file):
                continue

            # Match resource name against provider prefixes
            for prefix, provider in _PROVIDER_PREFIXES.items():
                if res_name.startswith(prefix):
                    # Find or reference the provider node
                    provider_result = self.store.query(
                        "MATCH (p) WHERE p.name = $provider "
                        "AND p.semantic_type = $prov_type "
                        "RETURN p.name",
                        {"provider": provider, "prov_type": "terraform_provider"},
                    )
                    provider_rows = provider_result.result_set or []
                    if provider_rows and provider_rows[0][0]:
                        self._add_semantic_edge(
                            res_name,
                            "BELONGS_TO",
                            provider,
                        )
                        edges_added += 1
                    break  # Only match the first (most specific) prefix

        return edges_added
