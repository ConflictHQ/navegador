"""Tests for navegador.ingestion.memory — MemoryIngester and helpers."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from navegador.graph.schema import EdgeType, NodeLabel
from navegador.ingestion.memory import (
    MemoryIngester,
    _first_line,
    _parse_frontmatter,
    _parse_gitmodules,
    _parse_memory_index,
)


def _make_store():
    store = MagicMock()
    store.query.return_value = MagicMock(result_set=[])
    return store


# -- _parse_frontmatter -------------------------------------------------------


class TestParseFrontmatter:
    def test_full_frontmatter_parses_all_fields(self):
        text = (
            "---\n"
            "name: no mocking databases\n"
            "description: integration tests must hit real DB\n"
            "type: feedback\n"
            "---\n"
            "\nBody content here.\n"
        )
        meta, body = _parse_frontmatter(text)
        assert meta["name"] == "no mocking databases"
        assert meta["description"] == "integration tests must hit real DB"
        assert meta["type"] == "feedback"
        assert "Body content here." in body

    def test_frontmatter_without_type_field(self):
        text = (
            "---\n"
            "name: some memory\n"
            "description: a description\n"
            "---\n"
            "\nThe body.\n"
        )
        meta, body = _parse_frontmatter(text)
        assert "type" not in meta
        assert meta["name"] == "some memory"
        assert "The body." in body

    def test_bare_markdown_no_frontmatter(self):
        text = "# Just a heading\n\nSome content.\n"
        meta, body = _parse_frontmatter(text)
        assert meta == {}
        assert body == text

    def test_malformed_yaml_no_crash(self):
        """Frontmatter with no valid key:value lines still returns empty meta."""
        text = "---\njust some text without colons\n---\n\nBody.\n"
        meta, body = _parse_frontmatter(text)
        assert meta == {}
        assert "Body." in body

    def test_frontmatter_with_empty_body(self):
        text = "---\nname: empty-body\ntype: project\n---\n"
        meta, body = _parse_frontmatter(text)
        assert meta["name"] == "empty-body"
        assert meta["type"] == "project"
        assert body.strip() == ""

    def test_frontmatter_preserves_colons_in_value(self):
        """Values like URLs contain colons — only the first colon is the delimiter."""
        text = "---\nname: url ref\ndescription: see http://example.com for details\n---\n\nBody.\n"
        meta, body = _parse_frontmatter(text)
        assert meta["description"] == "see http://example.com for details"

    def test_frontmatter_strips_whitespace_from_keys_and_values(self):
        text = "---\n  name  :  spaced out  \ntype: user\n---\n\nContent.\n"
        meta, body = _parse_frontmatter(text)
        assert meta["name"] == "spaced out"
        assert meta["type"] == "user"


# -- _first_line ---------------------------------------------------------------


class TestFirstLine:
    def test_returns_first_nonempty_line(self):
        assert _first_line("\n\nHello world\nSecond line") == "Hello world"

    def test_strips_leading_hash_markers(self):
        assert _first_line("## My heading") == "My heading"

    def test_empty_string_returns_empty(self):
        assert _first_line("") == ""

    def test_all_blank_lines_returns_empty(self):
        assert _first_line("\n\n   \n\n") == ""

    def test_strips_multiple_hashes(self):
        assert _first_line("### Deeply nested heading") == "Deeply nested heading"


# -- _parse_memory_index -------------------------------------------------------


class TestParseMemoryIndex:
    def test_parses_standard_index_entry(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            index_file = Path(tmpdir) / "MEMORY.md"
            index_file.write_text(
                "- [No DB mocking](feedback_testing.md) -- integration tests only\n"
            )
            result = _parse_memory_index(index_file)
            assert "feedback_testing.md" in result
            name, desc = result["feedback_testing.md"]
            assert name == "No DB mocking"
            assert desc == "integration tests only"

    def test_parses_em_dash_separator(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            index_file = Path(tmpdir) / "MEMORY.md"
            index_file.write_text(
                "- [Auth rewrite](project_auth.md) \u2014 driven by legal compliance\n"
            )
            result = _parse_memory_index(index_file)
            assert "project_auth.md" in result
            _, desc = result["project_auth.md"]
            assert "legal compliance" in desc

    def test_ignores_non_matching_lines(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            index_file = Path(tmpdir) / "MEMORY.md"
            index_file.write_text(
                "# Memory Index\n\n"
                "Some intro text.\n\n"
                "- [Valid](feedback_x.md) -- a real entry\n"
                "- This is not a link line\n"
            )
            result = _parse_memory_index(index_file)
            assert len(result) == 1
            assert "feedback_x.md" in result

    def test_missing_memory_md_returns_empty(self):
        result = _parse_memory_index(Path("/nonexistent/MEMORY.md"))
        assert result == {}

    def test_filename_as_link_text_yields_empty_name(self):
        """When link text IS the filename, name should be empty (not the filename)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            index_file = Path(tmpdir) / "MEMORY.md"
            index_file.write_text(
                "- [feedback_testing.md](feedback_testing.md) -- some desc\n"
            )
            result = _parse_memory_index(index_file)
            name, _ = result["feedback_testing.md"]
            assert name == ""


# -- Type mapping (_TYPE_MAP) --------------------------------------------------


class TestTypeMapping:
    def test_feedback_maps_to_rule(self):
        from navegador.ingestion.memory import _TYPE_MAP
        assert _TYPE_MAP["feedback"] == NodeLabel.Rule

    def test_project_maps_to_decision(self):
        from navegador.ingestion.memory import _TYPE_MAP
        assert _TYPE_MAP["project"] == NodeLabel.Decision

    def test_reference_maps_to_wikipage(self):
        from navegador.ingestion.memory import _TYPE_MAP
        assert _TYPE_MAP["reference"] == NodeLabel.WikiPage

    def test_user_maps_to_person(self):
        from navegador.ingestion.memory import _TYPE_MAP
        assert _TYPE_MAP["user"] == NodeLabel.Person


# -- MemoryIngester.ingest() ---------------------------------------------------


class TestIngest:
    def test_raises_on_missing_directory(self):
        store = _make_store()
        ingester = MemoryIngester(store)
        with pytest.raises(FileNotFoundError, match="Memory directory not found"):
            ingester.ingest("/nonexistent/memory")

    def test_ingests_frontmatter_file_creates_correct_node(self):
        store = _make_store()
        ingester = MemoryIngester(store)
        with tempfile.TemporaryDirectory() as tmpdir:
            md = Path(tmpdir) / "feedback_testing.md"
            md.write_text(
                "---\n"
                "name: no DB mocks\n"
                "description: tests must use real database\n"
                "type: feedback\n"
                "---\n"
                "\nAlways run against a real DB.\n"
            )
            stats = ingester.ingest(tmpdir, repo_name="my-app")

        assert stats["ingested"] == 1
        assert stats["skipped"] == 0
        store.create_node.assert_called_once()
        label, props = store.create_node.call_args[0]
        assert label == NodeLabel.Rule
        assert props["name"] == "no DB mocks"
        assert props["memory_type"] == "feedback"
        assert props["repo"] == "my-app"
        assert "Always run against a real DB." in props["rationale"]

    def test_ingests_bare_file_with_prefix_type(self):
        store = _make_store()
        ingester = MemoryIngester(store)
        with tempfile.TemporaryDirectory() as tmpdir:
            md = Path(tmpdir) / "project_auth_rewrite.md"
            md.write_text("Auth rewrite is driven by legal requirements.\n")
            # No frontmatter, but has project_ prefix
            stats = ingester.ingest(tmpdir, repo_name="my-app")

        assert stats["ingested"] == 1
        label, props = store.create_node.call_args[0]
        assert label == NodeLabel.Decision
        # Name derived from filename slug: "auth rewrite"
        assert props["name"] == "auth rewrite"

    def test_skips_bare_file_without_type_prefix(self):
        store = _make_store()
        ingester = MemoryIngester(store)
        with tempfile.TemporaryDirectory() as tmpdir:
            md = Path(tmpdir) / "random_notes.md"
            md.write_text("Some random notes without a type prefix.\n")
            stats = ingester.ingest(tmpdir, repo_name="my-app")

        assert stats["ingested"] == 0
        assert stats["skipped"] == 1
        store.create_node.assert_not_called()

    def test_skips_memory_md_index_file(self):
        store = _make_store()
        ingester = MemoryIngester(store)
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "MEMORY.md").write_text("- [X](feedback_x.md) -- desc\n")
            (Path(tmpdir) / "feedback_x.md").write_text(
                "---\nname: X\ntype: feedback\n---\n\nContent.\n"
            )
            stats = ingester.ingest(tmpdir, repo_name="my-app")

        # Only feedback_x.md ingested, MEMORY.md skipped
        assert stats["ingested"] == 1
        assert store.create_node.call_count == 1

    def test_frontmatter_without_type_derives_from_filename(self):
        """Frontmatter that omits type: field falls back to filename prefix."""
        store = _make_store()
        ingester = MemoryIngester(store)
        with tempfile.TemporaryDirectory() as tmpdir:
            md = Path(tmpdir) / "reference_grafana.md"
            md.write_text(
                "---\nname: Grafana dashboard\ndescription: oncall board\n---\n\nCheck grafana.\n"
            )
            stats = ingester.ingest(tmpdir, repo_name="my-app")

        assert stats["ingested"] == 1
        label, _ = store.create_node.call_args[0]
        assert label == NodeLabel.WikiPage

    def test_missing_name_skips_file(self):
        """Files with empty name after parsing are skipped."""
        store = _make_store()
        ingester = MemoryIngester(store)
        with tempfile.TemporaryDirectory() as tmpdir:
            md = Path(tmpdir) / "feedback_.md"
            md.write_text(
                "---\ntype: feedback\n---\n\nBody only, no name.\n"
            )
            stats = ingester.ingest(tmpdir, repo_name="my-app")

        assert stats["skipped"] == 1
        store.create_node.assert_not_called()

    def test_unknown_type_skips_file(self):
        store = _make_store()
        ingester = MemoryIngester(store)
        with tempfile.TemporaryDirectory() as tmpdir:
            md = Path(tmpdir) / "feedback_x.md"
            md.write_text(
                "---\nname: Test\ntype: invalid_type\n---\n\nBody.\n"
            )
            stats = ingester.ingest(tmpdir, repo_name="my-app")

        assert stats["skipped"] == 1
        store.create_node.assert_not_called()

    def test_clear_flag_calls_store_query(self):
        store = _make_store()
        ingester = MemoryIngester(store)
        with tempfile.TemporaryDirectory() as tmpdir:
            ingester.ingest(tmpdir, repo_name="my-app", clear=True)
        store.query.assert_called()
        cypher_arg = store.query.call_args_list[0][0][0]
        assert "DETACH DELETE" in cypher_arg

    def test_returns_by_type_counts(self):
        store = _make_store()
        ingester = MemoryIngester(store)
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "feedback_a.md").write_text(
                "---\nname: A\ntype: feedback\n---\n\nBody.\n"
            )
            (Path(tmpdir) / "feedback_b.md").write_text(
                "---\nname: B\ntype: feedback\n---\n\nBody.\n"
            )
            (Path(tmpdir) / "user_leo.md").write_text(
                "---\nname: Leo\ntype: user\n---\n\nEngineer.\n"
            )
            stats = ingester.ingest(tmpdir, repo_name="my-app")

        assert stats["by_type"]["feedback"] == 2
        assert stats["by_type"]["user"] == 1
        assert stats["ingested"] == 3

    def test_links_memory_node_to_repository(self):
        store = _make_store()
        ingester = MemoryIngester(store)
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "feedback_x.md").write_text(
                "---\nname: Test Rule\ntype: feedback\n---\n\nContent.\n"
            )
            ingester.ingest(tmpdir, repo_name="my-app")

        store.create_edge.assert_called_once_with(
            NodeLabel.Repository,
            {"name": "my-app"},
            EdgeType.CONTAINS,
            NodeLabel.Rule,
            {"name": "Test Rule"},
        )

    def test_link_to_repo_tolerates_missing_repository_node(self):
        """create_edge raising should not crash the ingest."""
        store = _make_store()
        store.create_edge.side_effect = Exception("Repository node not found")
        ingester = MemoryIngester(store)
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "feedback_x.md").write_text(
                "---\nname: X\ntype: feedback\n---\n\nBody.\n"
            )
            stats = ingester.ingest(tmpdir, repo_name="my-app")
        # Ingest succeeds despite the edge error
        assert stats["ingested"] == 1

    def test_bare_file_uses_memory_index_for_name(self):
        store = _make_store()
        ingester = MemoryIngester(store)
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "MEMORY.md").write_text(
                "- [Auth Rewrite Context](project_auth.md) -- compliance-driven rewrite\n"
            )
            (Path(tmpdir) / "project_auth.md").write_text(
                "The auth rewrite is driven by legal compliance.\n"
            )
            stats = ingester.ingest(tmpdir, repo_name="my-app")

        assert stats["ingested"] == 1
        _, props = store.create_node.call_args[0]
        assert props["name"] == "Auth Rewrite Context"
        assert "compliance-driven rewrite" in props["description"]


# -- _upsert_memory_node property mapping --------------------------------------


class TestUpsertMemoryNodeProps:
    """Verify that each type maps content to the correct property."""

    def test_rule_node_has_rationale(self):
        store = _make_store()
        ingester = MemoryIngester(store)
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "feedback_x.md").write_text(
                "---\nname: Rule X\ntype: feedback\ndescription: desc\n---\n\nRationale here.\n"
            )
            ingester.ingest(tmpdir, repo_name="r")
        _, props = store.create_node.call_args[0]
        assert props["rationale"] == "Rationale here."

    def test_decision_node_has_status_accepted(self):
        store = _make_store()
        ingester = MemoryIngester(store)
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "project_x.md").write_text(
                "---\nname: Decision X\ntype: project\ndescription: desc\n---\n\nDetails.\n"
            )
            ingester.ingest(tmpdir, repo_name="r")
        _, props = store.create_node.call_args[0]
        assert props["status"] == "accepted"
        assert "Details." in props["rationale"]

    def test_wikipage_node_has_content_and_source(self):
        store = _make_store()
        ingester = MemoryIngester(store)
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "reference_grafana.md").write_text(
                "---\nname: Grafana\ntype: reference\ndescription: dashboards\n---\n\nURL here.\n"
            )
            ingester.ingest(tmpdir, repo_name="r")
        _, props = store.create_node.call_args[0]
        assert props["content"] == "URL here."
        assert props["source"] == "memory"

    def test_person_node_merges_description_and_body(self):
        store = _make_store()
        ingester = MemoryIngester(store)
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "user_leo.md").write_text(
                "---\nname: Leo\ntype: user\ndescription: senior eng\n---\n\n"
                "Go expert, new to React.\n"
            )
            ingester.ingest(tmpdir, repo_name="r")
        _, props = store.create_node.call_args[0]
        assert "senior eng" in props["description"]
        assert "Go expert" in props["description"]


# -- ingest_workspace() --------------------------------------------------------


class TestIngestWorkspace:
    def test_ingests_root_memory_dir(self):
        store = _make_store()
        ingester = MemoryIngester(store)
        with tempfile.TemporaryDirectory() as tmpdir:
            mem = Path(tmpdir) / "memory"
            mem.mkdir()
            (mem / "feedback_x.md").write_text(
                "---\nname: X\ntype: feedback\n---\n\nBody.\n"
            )
            totals = ingester.ingest_workspace(tmpdir)

        assert totals["ingested"] == 1
        assert len(totals["repos"]) == 1

    def test_ingests_submodule_memory_dirs(self):
        store = _make_store()
        ingester = MemoryIngester(store)
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            # .gitmodules pointing to a submodule
            (root / ".gitmodules").write_text(
                "[submodule \"sub-a\"]\n\tpath = sub-a\n\turl = git@github.com:org/sub-a.git\n"
            )
            sub_mem = root / "sub-a" / "memory"
            sub_mem.mkdir(parents=True)
            (sub_mem / "feedback_y.md").write_text(
                "---\nname: Y\ntype: feedback\n---\n\nBody.\n"
            )
            totals = ingester.ingest_workspace(tmpdir)

        assert totals["ingested"] == 1
        assert "sub-a" in totals["repos"]

    def test_workspace_no_memory_dirs_returns_zeros(self):
        store = _make_store()
        ingester = MemoryIngester(store)
        with tempfile.TemporaryDirectory() as tmpdir:
            totals = ingester.ingest_workspace(tmpdir)
        assert totals["ingested"] == 0
        assert totals["repos"] == []


# -- ingest_recursive() --------------------------------------------------------


class TestIngestRecursive:
    def test_finds_and_ingests_nested_memory_dirs(self):
        store = _make_store()
        ingester = MemoryIngester(store)
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            mem_a = root / "service-a" / "memory"
            mem_a.mkdir(parents=True)
            (mem_a / "feedback_a.md").write_text(
                "---\nname: A\ntype: feedback\n---\n\nBody.\n"
            )
            mem_b = root / "service-b" / "memory"
            mem_b.mkdir(parents=True)
            (mem_b / "project_b.md").write_text(
                "---\nname: B\ntype: project\n---\n\nBody.\n"
            )
            totals = ingester.ingest_recursive(tmpdir)

        assert totals["ingested"] == 2
        assert set(totals["repos"]) == {"service-a", "service-b"}

    def test_skips_memory_dir_without_md_files(self):
        store = _make_store()
        ingester = MemoryIngester(store)
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            empty_mem = root / "service-x" / "memory"
            empty_mem.mkdir(parents=True)
            (empty_mem / "readme.txt").write_text("not a memory file")
            totals = ingester.ingest_recursive(tmpdir)

        assert totals["ingested"] == 0
        assert totals["repos"] == []

    def test_skips_regular_files_named_memory(self):
        """A file named 'memory' (not a directory) should be ignored."""
        store = _make_store()
        ingester = MemoryIngester(store)
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "memory").write_text("I am a file, not a dir")
            totals = ingester.ingest_recursive(tmpdir)

        assert totals["ingested"] == 0


# -- _parse_gitmodules ---------------------------------------------------------


class TestParseGitmodules:
    def test_parses_single_submodule(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            gm = Path(tmpdir) / ".gitmodules"
            gm.write_text(
                "[submodule \"api\"]\n\tpath = services/api\n\turl = git@github.com:org/api.git\n"
            )
            paths = _parse_gitmodules(gm)
        assert paths == ["services/api"]

    def test_parses_multiple_submodules(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            gm = Path(tmpdir) / ".gitmodules"
            gm.write_text(
                "[submodule \"a\"]\n\tpath = libs/a\n\turl = x\n"
                "[submodule \"b\"]\n\tpath = libs/b\n\turl = y\n"
            )
            paths = _parse_gitmodules(gm)
        assert paths == ["libs/a", "libs/b"]

    def test_empty_gitmodules_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            gm = Path(tmpdir) / ".gitmodules"
            gm.write_text("")
            paths = _parse_gitmodules(gm)
        assert paths == []


# -- _resolve_repo_name --------------------------------------------------------


class TestResolveRepoName:
    def test_falls_back_to_parent_dir_name(self):
        store = _make_store()
        store.query.return_value = MagicMock(result_set=[])
        ingester = MemoryIngester(store)
        with tempfile.TemporaryDirectory() as tmpdir:
            mem = Path(tmpdir) / "my-project" / "memory"
            mem.mkdir(parents=True)
            name = ingester._resolve_repo_name(mem)
        assert name == "my-project"

    def test_uses_graph_repo_node_when_present(self):
        store = _make_store()
        store.query.return_value = MagicMock(result_set=[["graph-repo-name"]])
        ingester = MemoryIngester(store)
        with tempfile.TemporaryDirectory() as tmpdir:
            mem = Path(tmpdir) / "my-project" / "memory"
            mem.mkdir(parents=True)
            name = ingester._resolve_repo_name(mem)
        assert name == "graph-repo-name"

    def test_tolerates_query_exception(self):
        store = _make_store()
        store.query.side_effect = Exception("DB error")
        ingester = MemoryIngester(store)
        with tempfile.TemporaryDirectory() as tmpdir:
            mem = Path(tmpdir) / "fallback-name" / "memory"
            mem.mkdir(parents=True)
            name = ingester._resolve_repo_name(mem)
        assert name == "fallback-name"


# -- _clear_memory_nodes -------------------------------------------------------


class TestClearMemoryNodes:
    def test_issues_detach_delete_query(self):
        store = _make_store()
        ingester = MemoryIngester(store)
        ingester._clear_memory_nodes("my-repo")
        store.query.assert_called_once()
        cypher, params = store.query.call_args[0]
        assert "DETACH DELETE" in cypher
        assert params["repo"] == "my-repo"
