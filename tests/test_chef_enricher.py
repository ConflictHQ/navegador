"""Tests for navegador.enrichment.chef — ChefEnricher."""

from unittest.mock import MagicMock

from navegador.enrichment.chef import ChefEnricher


def _make_store(query_results=None):
    """Create a mock GraphStore.

    *query_results* maps Cypher query substrings to result_set lists.
    Unmatched queries return an empty result_set.
    """
    store = MagicMock()
    mapping = query_results or {}

    def _side_effect(query, params=None):
        result = MagicMock()
        for substr, rows in mapping.items():
            if substr in query:
                result.result_set = rows
                return result
        result.result_set = []
        return result

    store.query.side_effect = _side_effect
    return store


class TestIdentity:
    """Framework identity properties."""

    def test_framework_name(self):
        store = _make_store()
        enricher = ChefEnricher(store)
        assert enricher.framework_name == "chef"

    def test_detection_files(self):
        store = _make_store()
        enricher = ChefEnricher(store)
        assert "metadata.rb" in enricher.detection_files
        assert "Berksfile" in enricher.detection_files

    def test_detection_patterns(self):
        store = _make_store()
        enricher = ChefEnricher(store)
        assert "chef" in enricher.detection_patterns


class TestDetect:
    """Tests for detect() — framework presence detection."""

    def test_detect_true_when_metadata_rb_exists(self):
        store = _make_store(
            {
                "f.name = $name": [[1]],
            }
        )
        enricher = ChefEnricher(store)
        assert enricher.detect() is True

    def test_detect_false_when_no_markers(self):
        store = _make_store()
        enricher = ChefEnricher(store)
        assert enricher.detect() is False

    def test_detect_true_via_import_pattern(self):
        store = _make_store(
            {
                "n.name = $name OR n.module = $name": [[1]],
            }
        )
        enricher = ChefEnricher(store)
        assert enricher.detect() is True


class TestEnrichRecipes:
    """Tests for enrich() promoting recipe files."""

    def test_promotes_recipe_files(self):
        store = _make_store(
            {
                "n.file_path CONTAINS $pattern": [
                    ["default.rb", "cookbooks/web/recipes/default.rb"],
                    ["install.rb", "cookbooks/web/recipes/install.rb"],
                ],
            }
        )
        enricher = ChefEnricher(store)
        result = enricher.enrich()

        assert result.patterns_found["recipes"] == 2
        assert result.promoted >= 2

        # Verify _promote_node was called via store.query SET
        set_calls = [c for c in store.query.call_args_list if "SET n.semantic_type" in str(c)]
        assert len(set_calls) >= 2


class TestEnrichResources:
    """Tests for enrich() promoting Chef resource calls."""

    def test_promotes_resource_functions(self):
        # _enrich_resources queries twice (recipes/ and libraries/),
        # so we use a custom side_effect to return data only once.
        call_count = {"resource": 0}
        original_results = [
            ["package", "cookbooks/web/recipes/default.rb"],
            ["template", "cookbooks/web/recipes/default.rb"],
            ["not_a_resource", "cookbooks/web/recipes/default.rb"],
        ]

        def _side_effect(query, params=None):
            result = MagicMock()
            if "(n:Function OR n:Method)" in query:
                call_count["resource"] += 1
                if call_count["resource"] == 1:
                    result.result_set = original_results
                else:
                    result.result_set = []
            else:
                result.result_set = []
            return result

        store = MagicMock()
        store.query.side_effect = _side_effect
        enricher = ChefEnricher(store)
        result = enricher.enrich()

        # "package" and "template" match, "not_a_resource" does not
        assert result.patterns_found["resources"] == 2

    def test_skips_non_resource_functions(self):
        store = _make_store(
            {
                "(n:Function OR n:Method)": [
                    ["my_helper", "cookbooks/web/libraries/helpers.rb"],
                ],
            }
        )
        enricher = ChefEnricher(store)
        result = enricher.enrich()

        assert result.patterns_found["resources"] == 0


class TestEnrichIncludeRecipe:
    """Tests for enrich() handling include_recipe edges."""

    def test_creates_depends_on_edge(self):
        # Strategy 1: follow CALLS edges from include_recipe nodes
        def _query_side_effect(query, params=None):
            result = MagicMock()
            if "[:CALLS]" in query and "n.name = $name" in query:
                result.result_set = [
                    [
                        "cookbooks/web/recipes/default.rb",
                        "database::install",
                    ],
                ]
            elif "f.file_path CONTAINS $recipes" in query:
                result.result_set = [["install.rb"]]
            elif "f.file_path = $path" in query:
                result.result_set = [["default.rb"]]
            elif "MERGE" in query:
                result.result_set = []
            else:
                result.result_set = []
            return result

        store = MagicMock()
        store.query.side_effect = _query_side_effect
        enricher = ChefEnricher(store)
        result = enricher.enrich()

        assert result.edges_added >= 1
        assert result.patterns_found["include_recipe"] >= 1

        # Verify MERGE query was issued for the DEPENDS_ON edge
        merge_calls = [
            c for c in store.query.call_args_list if "MERGE" in str(c) and "DEPENDS_ON" in str(c)
        ]
        assert len(merge_calls) >= 1

    def test_no_edges_when_no_include_recipe(self):
        store = _make_store()
        enricher = ChefEnricher(store)
        result = enricher.enrich()

        assert result.edges_added == 0
        assert result.patterns_found["include_recipe"] == 0


class TestEnrichCookbooks:
    """Tests for enrich() promoting cookbook metadata files."""

    def test_promotes_metadata_rb(self):
        store = _make_store(
            {
                "n.name = $name": [
                    ["metadata.rb", "cookbooks/web/metadata.rb"],
                ],
            }
        )
        enricher = ChefEnricher(store)
        result = enricher.enrich()

        assert result.patterns_found["cookbooks"] == 1
        set_calls = [c for c in store.query.call_args_list if "chef_cookbook" in str(c)]
        assert len(set_calls) >= 1
