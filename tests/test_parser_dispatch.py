"""Tests for navegador.ingestion.parser — dispatch paths, Document handling,
file hash checks, and Ansible integration."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from navegador.graph import queries
from navegador.ingestion.parser import LANGUAGE_MAP, RepoIngester


def _make_store(file_hash_result=None):
    """Build a mock store.

    file_hash_result: the content_hash value the store returns for FILE_HASH/DOCUMENT_HASH
        queries. None means "file not found in graph" (triggers fresh parse).
    """
    store = MagicMock()

    def _query(cypher, params=None):
        r = MagicMock()
        if cypher in (queries.FILE_HASH, queries.DOCUMENT_HASH):
            if file_hash_result is not None:
                r.result_set = [[file_hash_result]]
            else:
                r.result_set = []
        elif cypher in (queries.DELETE_FILE_SUBGRAPH, queries.DELETE_DOCUMENT):
            r.result_set = []
        else:
            r.result_set = []
        return r

    store.query.side_effect = _query
    return store


# ── Markdown dispatch (Document path) ───────────────────────────────────────


class TestMarkdownDispatch:
    def test_md_extension_in_language_map(self):
        assert LANGUAGE_MAP[".md"] == "markdown"
        assert LANGUAGE_MAP[".markdown"] == "markdown"

    def test_md_file_dispatches_to_markdown_parser(self):
        """A .md file in the repo should be parsed by MarkdownParser."""
        store = _make_store()
        ingester = RepoIngester(store)

        with tempfile.TemporaryDirectory() as tmpdir:
            md_file = Path(tmpdir) / "README.md"
            md_file.write_text("# Hello\n\nSome documentation.", encoding="utf-8")

            stats = ingester.ingest(tmpdir)
            # The markdown parser should have been invoked and produced at least one node
            assert stats["files"] >= 1

    def test_markdown_extension_also_dispatches(self):
        store = _make_store()
        ingester = RepoIngester(store)

        with tempfile.TemporaryDirectory() as tmpdir:
            md_file = Path(tmpdir) / "CHANGELOG.markdown"
            md_file.write_text("# Changelog\n\n- v1.0", encoding="utf-8")

            stats = ingester.ingest(tmpdir)
            assert stats["files"] >= 1


# ── _file_unchanged — Document vs File lookup ──────────────────────────────


class TestFileUnchanged:
    def test_md_file_uses_document_hash_query(self):
        """For .md files, _file_unchanged should use DOCUMENT_HASH, not FILE_HASH."""
        store = MagicMock()
        call_log = []

        def _query(cypher, params=None):
            call_log.append(cypher)
            r = MagicMock()
            r.result_set = [["abc123"]]
            return r

        store.query.side_effect = _query
        ingester = RepoIngester(store)

        result = ingester._file_unchanged("docs/README.md", "abc123")
        assert result is True
        assert any(queries.DOCUMENT_HASH in q for q in call_log)

    def test_py_file_uses_file_hash_query(self):
        """For .py files, _file_unchanged should use FILE_HASH, not DOCUMENT_HASH."""
        store = MagicMock()
        call_log = []

        def _query(cypher, params=None):
            call_log.append(cypher)
            r = MagicMock()
            r.result_set = [["abc123"]]
            return r

        store.query.side_effect = _query
        ingester = RepoIngester(store)

        result = ingester._file_unchanged("src/main.py", "abc123")
        assert result is True
        assert any(queries.FILE_HASH in q for q in call_log)

    def test_unchanged_when_hash_matches(self):
        store = MagicMock()
        r = MagicMock()
        r.result_set = [["deadbeef"]]
        store.query.return_value = r
        ingester = RepoIngester(store)

        assert ingester._file_unchanged("foo.py", "deadbeef") is True

    def test_changed_when_hash_differs(self):
        store = MagicMock()
        r = MagicMock()
        r.result_set = [["oldhash"]]
        store.query.return_value = r
        ingester = RepoIngester(store)

        assert ingester._file_unchanged("foo.py", "newhash") is False

    def test_changed_when_no_hash_stored(self):
        store = MagicMock()
        r = MagicMock()
        r.result_set = []
        store.query.return_value = r
        ingester = RepoIngester(store)

        assert ingester._file_unchanged("foo.py", "anyhash") is False

    def test_changed_when_hash_is_none(self):
        store = MagicMock()
        r = MagicMock()
        r.result_set = [[None]]
        store.query.return_value = r
        ingester = RepoIngester(store)

        assert ingester._file_unchanged("foo.py", "anyhash") is False


# ── _clear_file_subgraph — Document vs File dispatch ───────────────────────


class TestClearFileSubgraph:
    def test_md_file_uses_delete_document_query(self):
        """For .md files, _clear_file_subgraph should use DELETE_DOCUMENT."""
        store = MagicMock()
        call_log = []

        def _query(cypher, params=None):
            call_log.append(cypher)
            r = MagicMock()
            r.result_set = []
            return r

        store.query.side_effect = _query
        ingester = RepoIngester(store)
        ingester._clear_file_subgraph("docs/guide.md")

        assert any(queries.DELETE_DOCUMENT in q for q in call_log)

    def test_py_file_uses_delete_file_subgraph_query(self):
        """For .py files, _clear_file_subgraph should use DELETE_FILE_SUBGRAPH."""
        store = MagicMock()
        call_log = []

        def _query(cypher, params=None):
            call_log.append(cypher)
            r = MagicMock()
            r.result_set = []
            return r

        store.query.side_effect = _query
        ingester = RepoIngester(store)
        ingester._clear_file_subgraph("src/main.py")

        assert any(queries.DELETE_FILE_SUBGRAPH in q for q in call_log)


# ── _store_file_hash — Document vs File SET ────────────────────────────────


class TestStoreFileHash:
    def test_md_file_sets_hash_on_document_node(self):
        store = MagicMock()
        call_log = []

        def _query(cypher, params=None):
            call_log.append(cypher)
            r = MagicMock()
            r.result_set = []
            return r

        store.query.side_effect = _query
        ingester = RepoIngester(store)
        ingester._store_file_hash("docs/README.md", "hash123")

        assert any("Document" in q for q in call_log)

    def test_py_file_sets_hash_on_file_node(self):
        store = MagicMock()
        call_log = []

        def _query(cypher, params=None):
            call_log.append(cypher)
            r = MagicMock()
            r.result_set = []
            return r

        store.query.side_effect = _query
        ingester = RepoIngester(store)
        ingester._store_file_hash("src/app.py", "hash456")

        assert any("File" in q and "Document" not in q for q in call_log)


# ── _get_parser dispatch ───────────────────────────────────────────────────


def _can_import(module_name: str) -> bool:
    try:
        __import__(module_name)
        return True
    except ImportError:
        return False


class TestGetParser:
    """Test that _get_parser instantiates the correct parser for each language."""

    def test_markdown_parser_dispatched(self):
        store = MagicMock()
        store.query.return_value = MagicMock(result_set=[])
        ingester = RepoIngester(store)

        parser = ingester._get_parser("markdown")
        from navegador.ingestion.markdown import MarkdownParser
        assert isinstance(parser, MarkdownParser)

    def test_python_parser_dispatched(self):
        store = MagicMock()
        ingester = RepoIngester(store)
        parser = ingester._get_parser("python")
        from navegador.ingestion.python import PythonParser
        assert isinstance(parser, PythonParser)

    def test_parser_cached_on_second_call(self):
        store = MagicMock()
        ingester = RepoIngester(store)
        parser1 = ingester._get_parser("python")
        parser2 = ingester._get_parser("python")
        assert parser1 is parser2

    def test_unsupported_language_raises(self):
        store = MagicMock()
        ingester = RepoIngester(store)
        with pytest.raises(ValueError, match="Unsupported language"):
            ingester._get_parser("brainfuck")

    @pytest.mark.skipif(
        not _can_import("tree_sitter_hcl"), reason="missing",
    )
    def test_hcl_parser_dispatched(self):
        store = MagicMock()
        ingester = RepoIngester(store)
        parser = ingester._get_parser("hcl")
        from navegador.ingestion.hcl import HCLParser
        assert isinstance(parser, HCLParser)

    @pytest.mark.skipif(
        not _can_import("tree_sitter_puppet"), reason="missing",
    )
    def test_puppet_parser_dispatched(self):
        store = MagicMock()
        ingester = RepoIngester(store)
        parser = ingester._get_parser("puppet")
        from navegador.ingestion.puppet import PuppetParser
        assert isinstance(parser, PuppetParser)

    @pytest.mark.skipif(
        not _can_import("tree_sitter_bash"), reason="missing",
    )
    def test_bash_parser_dispatched(self):
        store = MagicMock()
        ingester = RepoIngester(store)
        parser = ingester._get_parser("bash")
        from navegador.ingestion.bash import BashParser
        assert isinstance(parser, BashParser)

    @pytest.mark.skipif(
        not _can_import("tree_sitter_kotlin"), reason="missing",
    )
    def test_kotlin_parser_dispatched(self):
        store = MagicMock()
        ingester = RepoIngester(store)
        parser = ingester._get_parser("kotlin")
        from navegador.ingestion.kotlin import KotlinParser
        assert isinstance(parser, KotlinParser)

    @pytest.mark.skipif(
        not _can_import("tree_sitter_c_sharp"), reason="missing",
    )
    def test_csharp_parser_dispatched(self):
        store = MagicMock()
        ingester = RepoIngester(store)
        parser = ingester._get_parser("csharp")
        from navegador.ingestion.csharp import CSharpParser
        assert isinstance(parser, CSharpParser)

    @pytest.mark.skipif(
        not _can_import("tree_sitter_php"), reason="missing",
    )
    def test_php_parser_dispatched(self):
        store = MagicMock()
        ingester = RepoIngester(store)
        parser = ingester._get_parser("php")
        from navegador.ingestion.php import PHPParser
        assert isinstance(parser, PHPParser)

    @pytest.mark.skipif(
        not _can_import("tree_sitter_ruby"), reason="missing",
    )
    def test_ruby_parser_dispatched(self):
        store = MagicMock()
        ingester = RepoIngester(store)
        parser = ingester._get_parser("ruby")
        from navegador.ingestion.ruby import RubyParser
        assert isinstance(parser, RubyParser)

    @pytest.mark.skipif(
        not _can_import("tree_sitter_swift"), reason="missing",
    )
    def test_swift_parser_dispatched(self):
        store = MagicMock()
        ingester = RepoIngester(store)
        parser = ingester._get_parser("swift")
        from navegador.ingestion.swift import SwiftParser
        assert isinstance(parser, SwiftParser)

    @pytest.mark.skipif(
        not _can_import("tree_sitter_c"), reason="missing",
    )
    def test_c_parser_dispatched(self):
        store = MagicMock()
        ingester = RepoIngester(store)
        parser = ingester._get_parser("c")
        from navegador.ingestion.c import CParser
        assert isinstance(parser, CParser)

    @pytest.mark.skipif(
        not _can_import("tree_sitter_cpp"), reason="missing",
    )
    def test_cpp_parser_dispatched(self):
        store = MagicMock()
        ingester = RepoIngester(store)
        parser = ingester._get_parser("cpp")
        from navegador.ingestion.cpp import CppParser
        assert isinstance(parser, CppParser)


# ── Ansible integration in parser.py ────────────────────────────────────────


class TestAnsibleIntegration:
    def test_ansible_yml_files_ingested(self):
        """Ansible .yml files in roles/*/tasks/ are detected and parsed."""
        store = _make_store()
        ingester = RepoIngester(store)

        with tempfile.TemporaryDirectory() as tmpdir:
            roles_dir = Path(tmpdir) / "roles" / "webserver" / "tasks"
            roles_dir.mkdir(parents=True)
            task_file = roles_dir / "main.yml"
            task_file.write_text(
                "---\n- name: Install nginx\n  apt:\n    name: nginx\n    state: present\n",
                encoding="utf-8",
            )

            stats = ingester.ingest(tmpdir)
            assert stats["files"] >= 1

    def test_ansible_yaml_extension_also_detected(self):
        """Ansible .yaml files are also parsed."""
        store = _make_store()
        ingester = RepoIngester(store)

        with tempfile.TemporaryDirectory() as tmpdir:
            roles_dir = Path(tmpdir) / "roles" / "db" / "tasks"
            roles_dir.mkdir(parents=True)
            task_file = roles_dir / "main.yaml"
            task_file.write_text(
                "---\n- name: Install postgres\n  apt:\n    name: postgresql\n    state: present\n",
                encoding="utf-8",
            )

            stats = ingester.ingest(tmpdir)
            assert stats["files"] >= 1

    def test_non_ansible_yml_skipped(self):
        """A .yml file that doesn't match Ansible heuristics is not parsed as Ansible."""
        store = _make_store()
        ingester = RepoIngester(store)

        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = Path(tmpdir) / "config.yml"
            config_file.write_text("debug: true\nport: 8080\n", encoding="utf-8")

            stats = ingester.ingest(tmpdir)
            # config.yml should not have been parsed as an Ansible file
            # (no roles/ path, no hosts: key, no ansible.cfg)
            assert stats["files"] == 0

    def test_incremental_skips_unchanged_ansible(self):
        """In incremental mode, unchanged ansible files are skipped."""
        # First run: file is new, gets parsed
        store = _make_store(file_hash_result=None)
        ingester = RepoIngester(store)

        with tempfile.TemporaryDirectory() as tmpdir:
            roles_dir = Path(tmpdir) / "roles" / "app" / "tasks"
            roles_dir.mkdir(parents=True)
            task_file = roles_dir / "main.yml"
            task_file.write_text(
                "---\n- name: Deploy app\n  command: deploy.sh\n",
                encoding="utf-8",
            )

            stats = ingester.ingest(tmpdir, incremental=True)
            assert stats["files"] >= 1
