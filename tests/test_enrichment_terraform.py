"""Tests for navegador.enrichment.terraform — Terraform enricher."""

from unittest.mock import MagicMock

from navegador.enrichment.terraform import _PROVIDER_PREFIXES, TerraformEnricher


def _make_query_store(query_results: dict[str, list] | None = None):
    """Build a mock store that dispatches results based on query content keywords.

    query_results maps a keyword to the result_set for queries containing that keyword.
    All unmatched queries return empty result_set.
    The store also tracks queries via .query_log for inspection.
    """
    store = MagicMock()
    store.query_log = []
    mapping = query_results or {}

    def _query(cypher, params=None):
        store.query_log.append((cypher, params))
        r = MagicMock()
        r.result_set = []
        for keyword, rs in mapping.items():
            if keyword in cypher:
                r.result_set = rs
                break
        return r

    store.query.side_effect = _query
    return store


def _empty_result():
    r = MagicMock()
    r.result_set = []
    return r


# ── Identity ────────────────────────────────────────────────────────────────


class TestTerraformEnricherIdentity:
    def test_framework_name(self):
        store = MagicMock()
        enricher = TerraformEnricher(store)
        assert enricher.framework_name == "terraform"

    def test_detection_patterns_empty(self):
        """Terraform has no import-based detection patterns."""
        store = MagicMock()
        enricher = TerraformEnricher(store)
        assert enricher.detection_patterns == []

    def test_detection_files(self):
        store = MagicMock()
        enricher = TerraformEnricher(store)
        files = enricher.detection_files
        assert "main.tf" in files
        assert "variables.tf" in files
        assert "outputs.tf" in files
        assert "providers.tf" in files


# ── enrich() orchestration ──────────────────────────────────────────────────


class TestEnrich:
    def test_enrich_returns_result_with_all_pattern_keys(self):
        """enrich() calls all three sub-enrichment methods and returns their counts."""
        store = MagicMock()
        store.query.return_value = _empty_result()
        enricher = TerraformEnricher(store)
        result = enricher.enrich()

        assert "variable_references" in result.patterns_found
        assert "module_sources" in result.patterns_found
        assert "provider_grouping" in result.patterns_found

    def test_enrich_sums_edges(self):
        """edges_added is the sum of all three sub-method edge counts."""
        store = MagicMock()
        store.query.return_value = _empty_result()
        enricher = TerraformEnricher(store)
        result = enricher.enrich()
        assert result.edges_added == 0


# ── _enrich_variable_references ─────────────────────────────────────────────


class TestVariableReferences:
    def test_cross_file_output_to_variable_reference(self):
        """An output node that CALLS a variable in another file gets a REFERENCES edge."""
        call_idx = [0]
        store = MagicMock()

        def _query(cypher, params=None):
            r = MagicMock()
            call_idx[0] += 1
            idx = call_idx[0]
            if idx == 1:
                # terraform_variable query
                r.result_set = [["vpc_id", "variables.tf"]]
            elif idx == 2:
                # terraform_output query
                r.result_set = [["vpc_output", "outputs.tf"]]
            elif idx == 3:
                # CALLS from output -> target in different file
                r.result_set = [["vpc_id", "variables.tf"]]
            else:
                # _add_semantic_edge MERGE
                r.result_set = []
            return r

        store.query.side_effect = _query
        enricher = TerraformEnricher(store)
        edges = enricher._enrich_variable_references()
        assert edges == 1

    def test_output_with_no_cross_file_refs_skipped(self):
        """An output referencing a variable in the SAME file is not linked."""
        call_idx = [0]
        store = MagicMock()

        def _query(cypher, params=None):
            r = MagicMock()
            call_idx[0] += 1
            idx = call_idx[0]
            if idx == 1:
                r.result_set = [["x", "main.tf"]]
            elif idx == 2:
                r.result_set = [["out", "main.tf"]]
            elif idx == 3:
                r.result_set = [["x", "main.tf"]]  # same file
            else:
                r.result_set = []
            return r

        store.query.side_effect = _query
        enricher = TerraformEnricher(store)
        edges = enricher._enrich_variable_references()
        assert edges == 0

    def test_same_name_variables_in_multiple_files_linked(self):
        """Variables with the same name in different files get REFERENCES edges."""
        call_idx = [0]
        store = MagicMock()

        def _query(cypher, params=None):
            r = MagicMock()
            call_idx[0] += 1
            idx = call_idx[0]
            if idx == 1:
                # Variables in two files
                r.result_set = [["region", "variables.tf"], ["region", "modules/vars.tf"]]
            elif idx == 2:
                # No outputs
                r.result_set = []
            elif idx == 3:
                # Cross-file query from variables.tf
                r.result_set = [["region", "modules/vars.tf"]]
            elif idx == 5:
                # Cross-file query from modules/vars.tf
                r.result_set = [["region", "variables.tf"]]
            else:
                # MERGE queries for _add_semantic_edge
                r.result_set = []
            return r

        store.query.side_effect = _query
        enricher = TerraformEnricher(store)
        edges = enricher._enrich_variable_references()
        assert edges == 2

    def test_single_file_variable_not_linked(self):
        """A variable that only exists in one file gets no REFERENCES edges."""
        call_idx = [0]
        store = MagicMock()

        def _query(cypher, params=None):
            r = MagicMock()
            call_idx[0] += 1
            if call_idx[0] == 1:
                r.result_set = [["region", "variables.tf"]]
            elif call_idx[0] == 2:
                r.result_set = []
            else:
                r.result_set = []
            return r

        store.query.side_effect = _query
        enricher = TerraformEnricher(store)
        edges = enricher._enrich_variable_references()
        assert edges == 0

    def test_output_with_empty_name_skipped(self):
        call_idx = [0]
        store = MagicMock()

        def _query(cypher, params=None):
            r = MagicMock()
            call_idx[0] += 1
            if call_idx[0] == 1:
                r.result_set = []
            elif call_idx[0] == 2:
                r.result_set = [[None, "outputs.tf"]]
            else:
                r.result_set = []
            return r

        store.query.side_effect = _query
        enricher = TerraformEnricher(store)
        edges = enricher._enrich_variable_references()
        assert edges == 0


# ── _enrich_module_sources ──────────────────────────────────────────────────


class TestModuleSources:
    def test_module_with_calls_edge_to_source(self):
        call_idx = [0]
        store = MagicMock()

        def _query(cypher, params=None):
            r = MagicMock()
            call_idx[0] += 1
            if call_idx[0] == 1:
                r.result_set = [["vpc", "main.tf"]]
            elif call_idx[0] == 2:
                r.result_set = [["vpc_module", "modules/vpc/main.tf"]]
            else:
                r.result_set = []
            return r

        store.query.side_effect = _query
        enricher = TerraformEnricher(store)
        edges = enricher._enrich_module_sources()
        assert edges == 1

    def test_module_fallback_to_file_path_matching(self):
        call_idx = [0]
        store = MagicMock()

        def _query(cypher, params=None):
            r = MagicMock()
            call_idx[0] += 1
            if call_idx[0] == 1:
                r.result_set = [["networking", "main.tf"]]
            elif call_idx[0] == 2:
                r.result_set = []  # no CALLS targets
            elif call_idx[0] == 3:
                r.result_set = [["main.tf"]]  # File node match
            else:
                r.result_set = []
            return r

        store.query.side_effect = _query
        enricher = TerraformEnricher(store)
        edges = enricher._enrich_module_sources()
        assert edges == 1

    def test_module_with_no_source_found(self):
        call_idx = [0]
        store = MagicMock()

        def _query(cypher, params=None):
            r = MagicMock()
            call_idx[0] += 1
            if call_idx[0] == 1:
                r.result_set = [["orphan", "main.tf"]]
            else:
                r.result_set = []
            return r

        store.query.side_effect = _query
        enricher = TerraformEnricher(store)
        edges = enricher._enrich_module_sources()
        assert edges == 0

    def test_module_with_empty_name_skipped(self):
        call_idx = [0]
        store = MagicMock()

        def _query(cypher, params=None):
            r = MagicMock()
            call_idx[0] += 1
            if call_idx[0] == 1:
                r.result_set = [[None, "main.tf"]]
            else:
                r.result_set = []
            return r

        store.query.side_effect = _query
        enricher = TerraformEnricher(store)
        edges = enricher._enrich_module_sources()
        assert edges == 0


# ── _enrich_provider_grouping ───────────────────────────────────────────────


class TestProviderGrouping:
    def test_aws_resource_linked_to_aws_provider(self):
        call_idx = [0]
        store = MagicMock()

        def _query(cypher, params=None):
            r = MagicMock()
            call_idx[0] += 1
            if call_idx[0] == 1:
                r.result_set = [["aws_instance", "main.tf"]]
            elif call_idx[0] == 2:
                r.result_set = [["aws"]]
            else:
                r.result_set = []
            return r

        store.query.side_effect = _query
        enricher = TerraformEnricher(store)
        edges = enricher._enrich_provider_grouping()
        assert edges == 1

    def test_resource_without_matching_provider_no_edge(self):
        call_idx = [0]
        store = MagicMock()

        def _query(cypher, params=None):
            r = MagicMock()
            call_idx[0] += 1
            if call_idx[0] == 1:
                r.result_set = [["aws_s3_bucket", "main.tf"]]
            else:
                r.result_set = []
            return r

        store.query.side_effect = _query
        enricher = TerraformEnricher(store)
        edges = enricher._enrich_provider_grouping()
        assert edges == 0

    def test_resource_not_matching_any_prefix(self):
        call_idx = [0]
        store = MagicMock()

        def _query(cypher, params=None):
            r = MagicMock()
            call_idx[0] += 1
            if call_idx[0] == 1:
                r.result_set = [["custom_thing", "main.tf"]]
            else:
                r.result_set = []
            return r

        store.query.side_effect = _query
        enricher = TerraformEnricher(store)
        edges = enricher._enrich_provider_grouping()
        assert edges == 0

    def test_multiple_resources_different_providers(self):
        call_idx = [0]
        store = MagicMock()

        def _query(cypher, params=None):
            r = MagicMock()
            call_idx[0] += 1
            if call_idx[0] == 1:
                r.result_set = [
                    ["aws_instance", "main.tf"],
                    ["google_storage_bucket", "main.tf"],
                ]
            elif call_idx[0] == 2:
                r.result_set = [["aws"]]
            elif call_idx[0] == 4:
                # After MERGE for aws, provider lookup for google
                r.result_set = [["google"]]
            else:
                r.result_set = []
            return r

        store.query.side_effect = _query
        enricher = TerraformEnricher(store)
        edges = enricher._enrich_provider_grouping()
        assert edges == 2

    def test_resource_with_empty_name_skipped(self):
        call_idx = [0]
        store = MagicMock()

        def _query(cypher, params=None):
            r = MagicMock()
            call_idx[0] += 1
            if call_idx[0] == 1:
                r.result_set = [[None, "main.tf"]]
            else:
                r.result_set = []
            return r

        store.query.side_effect = _query
        enricher = TerraformEnricher(store)
        edges = enricher._enrich_provider_grouping()
        assert edges == 0


# ── Provider prefix coverage ────────────────────────────────────────────────


class TestProviderPrefixes:
    def test_known_prefixes_include_major_clouds(self):
        assert "aws_" in _PROVIDER_PREFIXES
        assert "google_" in _PROVIDER_PREFIXES
        assert "azurerm_" in _PROVIDER_PREFIXES

    def test_utility_providers_included(self):
        assert "null_" in _PROVIDER_PREFIXES
        assert "random_" in _PROVIDER_PREFIXES
        assert "local_" in _PROVIDER_PREFIXES
        assert "tls_" in _PROVIDER_PREFIXES

    def test_all_prefixes_end_with_underscore(self):
        for prefix in _PROVIDER_PREFIXES:
            assert prefix.endswith("_"), f"Prefix {prefix!r} should end with underscore"

    def test_all_provider_names_are_non_empty_strings(self):
        for prefix, name in _PROVIDER_PREFIXES.items():
            assert isinstance(name, str)
            assert len(name) > 0
