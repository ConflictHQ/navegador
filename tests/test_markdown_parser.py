"""Tests for navegador.ingestion.markdown — MarkdownParser and helpers."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

from navegador.graph.schema import EdgeType, NodeLabel
from navegador.ingestion.markdown import (
    MarkdownParser,
    _extract_md_links,
    _extract_title,
)


def _make_store():
    store = MagicMock()
    store.query.return_value = MagicMock(result_set=[])
    return store


# -- _extract_title ------------------------------------------------------------


class TestExtractTitle:
    def test_extracts_h1_heading(self):
        content = "# My Great Document\n\nSome content.\n"
        assert _extract_title(content, "file.md") == "My Great Document"

    def test_falls_back_to_filename_stem(self):
        content = "No heading here.\nJust paragraphs.\n"
        assert _extract_title(content, "my-doc.md") == "my doc"

    def test_filename_underscores_become_spaces(self):
        content = "No heading.\n"
        assert _extract_title(content, "getting_started.md") == "getting started"

    def test_uses_first_h1_not_h2(self):
        content = "## Section heading\n\n# Actual Title\n"
        assert _extract_title(content, "fallback.md") == "Actual Title"

    def test_strips_whitespace_from_heading(self):
        content = "#   Padded Title   \n"
        assert _extract_title(content, "x.md") == "Padded Title"


# -- _extract_md_links ---------------------------------------------------------


class TestExtractMdLinks:
    def test_extracts_relative_md_link(self):
        content = "See [other doc](other.md) for details."
        links = _extract_md_links(content)
        assert links == ["other.md"]

    def test_ignores_http_links(self):
        content = "Visit [site](https://example.com/page.md) for more."
        links = _extract_md_links(content)
        assert links == []

    def test_ignores_anchor_links(self):
        content = "Jump to [section](#heading)."
        links = _extract_md_links(content)
        assert links == []

    def test_strips_anchor_fragment_from_md_link(self):
        content = "See [section](other.md#heading) for details."
        links = _extract_md_links(content)
        assert links == ["other.md"]

    def test_extracts_multiple_links(self):
        content = "[A](a.md) and [B](b.md) and [C](c.md)"
        links = _extract_md_links(content)
        assert links == ["a.md", "b.md", "c.md"]

    def test_no_links_returns_empty(self):
        content = "Plain text with no links."
        assert _extract_md_links(content) == []

    def test_ignores_http_md_links(self):
        content = "[docs](http://docs.example.com/setup.md)"
        assert _extract_md_links(content) == []


# -- MarkdownParser.parse_file -------------------------------------------------


class TestMarkdownParserParseFile:
    def test_creates_document_node_with_correct_props(self):
        store = _make_store()
        parser = MarkdownParser()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            md = root / "README.md"
            md.write_text("# Project Overview\n\nThis is the overview.\n")
            result = parser.parse_file(md, root, store)

        assert result == {"documents": 1, "links": 0}
        store.create_node.assert_called_once()
        label, props = store.create_node.call_args[0]
        assert label == NodeLabel.Document
        assert props["name"] == "Project Overview"
        assert props["title"] == "Project Overview"
        assert props["path"] == "README.md"
        assert "This is the overview." in props["content"]

    def test_file_without_heading_uses_filename(self):
        store = _make_store()
        parser = MarkdownParser()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            md = root / "quick-start.md"
            md.write_text("Just some content, no heading.\n")
            parser.parse_file(md, root, store)

        _, props = store.create_node.call_args[0]
        assert props["title"] == "quick start"

    def test_content_capped_at_4000_chars(self):
        store = _make_store()
        parser = MarkdownParser()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            md = root / "huge.md"
            md.write_text("# Big Doc\n" + "x" * 5000)
            parser.parse_file(md, root, store)

        _, props = store.create_node.call_args[0]
        assert len(props["content"]) == 4000

    def test_skips_files_in_memory_directory(self):
        store = _make_store()
        parser = MarkdownParser()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            mem = root / "memory"
            mem.mkdir()
            md = mem / "feedback_testing.md"
            md.write_text("# Should be skipped\n")
            result = parser.parse_file(md, root, store)

        assert result == {}
        store.create_node.assert_not_called()

    def test_creates_references_edge_for_internal_link(self):
        store = _make_store()
        parser = MarkdownParser()
        with tempfile.TemporaryDirectory() as tmpdir:
            # resolve() needed on macOS where /var is a symlink to /private/var;
            # the parser calls .resolve() on the linked path so repo_root must match.
            root = Path(tmpdir).resolve()
            target = root / "other.md"
            target.write_text("# Other Doc\n\nTarget content.\n")
            source = root / "index.md"
            source.write_text("# Index\n\nSee [other](other.md) for more.\n")

            result = parser.parse_file(source, root, store)

        assert result["links"] == 1
        # Two create_node calls: source doc + linked doc stub
        assert store.create_node.call_count == 2
        store.create_edge.assert_called_once()
        args = store.create_edge.call_args[0]
        assert args[0] == NodeLabel.Document
        assert args[1] == {"path": "index.md"}
        assert args[2] == EdgeType.REFERENCES
        assert args[3] == NodeLabel.Document
        assert args[4] == {"path": "other.md"}

    def test_linked_doc_stub_has_correct_title(self):
        store = _make_store()
        parser = MarkdownParser()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            target = root / "setup-guide.md"
            target.write_text("# Setup Guide\n\nInstructions here.\n")
            source = root / "readme.md"
            source.write_text("# Readme\n\nSee [guide](setup-guide.md).\n")

            parser.parse_file(source, root, store)

        # Second create_node call is the linked doc stub
        stub_label, stub_props = store.create_node.call_args_list[1][0]
        assert stub_label == NodeLabel.Document
        assert stub_props["title"] == "Setup Guide"
        assert stub_props["path"] == "setup-guide.md"

    def test_no_edge_when_linked_file_does_not_exist(self):
        store = _make_store()
        parser = MarkdownParser()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            md = root / "index.md"
            md.write_text("# Index\n\nSee [missing](nonexistent.md).\n")
            result = parser.parse_file(md, root, store)

        assert result["links"] == 0
        store.create_edge.assert_not_called()
        # Only one create_node call (the source doc)
        assert store.create_node.call_count == 1

    def test_relative_path_in_subdirectory(self):
        store = _make_store()
        parser = MarkdownParser()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            subdir = root / "docs"
            subdir.mkdir()
            md = subdir / "guide.md"
            md.write_text("# Guide\n\nSome guide content.\n")
            parser.parse_file(md, root, store)

        _, props = store.create_node.call_args[0]
        assert props["path"] == "docs/guide.md"

    def test_multiple_links_in_one_file(self):
        store = _make_store()
        parser = MarkdownParser()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            (root / "a.md").write_text("# A\n")
            (root / "b.md").write_text("# B\n")
            source = root / "index.md"
            source.write_text("# Index\n\n[A](a.md) and [B](b.md)\n")

            result = parser.parse_file(source, root, store)

        assert result["links"] == 2
        # 1 source + 2 stubs
        assert store.create_node.call_count == 3
        assert store.create_edge.call_count == 2

    def test_link_escaping_repo_root_is_skipped(self):
        """A link resolving outside repo_root should be silently ignored."""
        store = _make_store()
        parser = MarkdownParser()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "repo"
            root.mkdir()
            # Create a file outside root that the link points to
            outside = Path(tmpdir) / "outside.md"
            outside.write_text("# Outside\n")
            source = root / "doc.md"
            source.write_text("# Doc\n\nSee [outside](../outside.md).\n")

            result = parser.parse_file(source, root, store)

        assert result["links"] == 0
        store.create_edge.assert_not_called()

    def test_create_edge_failure_is_non_fatal(self):
        """If create_edge raises, parsing still succeeds."""
        store = _make_store()
        store.create_edge.side_effect = Exception("edge creation failed")
        parser = MarkdownParser()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            (root / "target.md").write_text("# Target\n")
            source = root / "source.md"
            source.write_text("# Source\n\n[T](target.md)\n")

            result = parser.parse_file(source, root, store)

        # Link count stays 0 because the edge creation failed
        assert result["links"] == 0
        assert result["documents"] == 1

    def test_deeply_nested_memory_dir_skipped(self):
        """Files in any directory named 'memory' in the path should be skipped."""
        store = _make_store()
        parser = MarkdownParser()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            deep_mem = root / "docs" / "memory" / "archive"
            deep_mem.mkdir(parents=True)
            md = deep_mem / "old.md"
            md.write_text("# Old memory file\n")
            result = parser.parse_file(md, root, store)

        assert result == {}
        store.create_node.assert_not_called()
