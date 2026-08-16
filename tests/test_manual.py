# Copyright CONFLICT LLC 2026 (weareconflict.com)
"""
Tests for navegador.manual — documentation packaged with the CLI.

Run against the real bundled docs: the point of the feature is that the files
are actually present in the installed package, which a fixture would not prove.
"""

import json

import pytest
from click.testing import CliRunner

from navegador.cli.commands import main
from navegador.manual import (
    ManualError,
    docs_root,
    find_page,
    list_pages,
    search,
)


class TestPackagedDocs:
    def test_docs_ship_with_the_package(self):
        """The whole feature is that these files are inside navegador/."""
        root = docs_root()
        assert root.is_dir()
        assert root.parent.name == "navegador"

    def test_pages_are_discovered(self):
        pages = list_pages()
        assert len(pages) > 10
        assert all(p.path.is_file() for p in pages)

    def test_every_page_has_a_title(self):
        assert all(p.title.strip() for p in list_pages())

    def test_slugs_are_extensionless_posix_paths(self):
        slugs = {p.slug for p in list_pages()}
        assert "index" in slugs
        assert "guide/mcp-integration" in slugs
        assert not any(s.endswith(".md") or "\\" in s for s in slugs)

    def test_section_is_the_leading_path_segment(self):
        by_slug = {p.slug: p for p in list_pages()}
        assert by_slug["guide/mcp-integration"].section == "guide"
        assert by_slug["index"].section == ""


class TestFindPage:
    def test_exact_slug(self):
        assert find_page("guide/mcp-integration").slug == "guide/mcp-integration"

    def test_trailing_fragment(self):
        assert find_page("quickstart").slug == "getting-started/quickstart"

    def test_md_suffix_is_tolerated(self):
        assert find_page("guide/cluster.md").slug == "guide/cluster"

    def test_leading_slash_is_tolerated(self):
        assert find_page("/index").slug == "index"

    def test_unknown_page_lists_what_exists(self):
        with pytest.raises(ManualError, match="Available:"):
            find_page("no-such-page")

    def test_empty_slug_is_rejected(self):
        with pytest.raises(ManualError):
            find_page("   ")

    def test_page_content_is_readable(self):
        assert find_page("index").read().strip()


class TestSearch:
    def test_finds_a_known_term(self):
        assert search("FalkorDB")

    def test_results_carry_context(self):
        hits = search("FalkorDB")
        assert all("slug" in h and "context" in h for h in hits)
        assert any(h["context"] for h in hits)

    def test_case_insensitive(self):
        assert {h["slug"] for h in search("falkordb")} == {h["slug"] for h in search("FALKORDB")}

    def test_no_matches_returns_empty(self):
        assert search("zzzz-definitely-not-in-the-docs-zzzz") == []

    def test_empty_query_returns_empty(self):
        assert search("  ") == []

    def test_respects_limit(self):
        assert len(search("the", limit=2)) <= 2

    def test_rank_key_is_not_leaked(self):
        assert all("_rank" not in h for h in search("FalkorDB"))


class TestManualCommand:
    def test_lists_pages_by_default(self):
        result = CliRunner().invoke(main, ["manual"])
        assert result.exit_code == 0
        assert "quickstart" in result.output

    def test_reads_a_page(self):
        result = CliRunner().invoke(main, ["manual", "index", "--raw"])
        assert result.exit_code == 0
        assert result.output.strip()

    def test_search_flag(self):
        result = CliRunner().invoke(main, ["manual", "--search", "FalkorDB"])
        assert result.exit_code == 0
        assert "FalkorDB" in result.output or "falkordb" in result.output.lower()

    def test_json_listing_is_machine_readable(self):
        result = CliRunner().invoke(main, ["manual", "--json"])
        assert result.exit_code == 0
        pages = json.loads(result.output)
        assert all({"slug", "title", "section"} <= set(p) for p in pages)

    def test_json_page_includes_content(self):
        result = CliRunner().invoke(main, ["manual", "index", "--json"])
        assert result.exit_code == 0
        assert json.loads(result.output)["content"].strip()

    def test_unknown_page_exits_nonzero(self):
        result = CliRunner().invoke(main, ["manual", "no-such-page"])
        assert result.exit_code != 0
