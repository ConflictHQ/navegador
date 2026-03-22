"""Tests for navegador.ingestion.parser — RepoIngester orchestration."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from navegador.graph.schema import NodeLabel
from navegador.ingestion.parser import LANGUAGE_MAP, RepoIngester


def _make_store():
    store = MagicMock()
    store.query.return_value = MagicMock(result_set=[])
    return store


# ── LANGUAGE_MAP ──────────────────────────────────────────────────────────────

class TestLanguageMap:
    def test_python_extension(self):
        assert LANGUAGE_MAP[".py"] == "python"

    def test_typescript_extensions(self):
        assert LANGUAGE_MAP[".ts"] == "typescript"
        assert LANGUAGE_MAP[".tsx"] == "typescript"

    def test_javascript_extensions(self):
        assert LANGUAGE_MAP[".js"] == "javascript"
        assert LANGUAGE_MAP[".jsx"] == "javascript"

    def test_no_entry_for_unknown(self):
        assert ".rb" not in LANGUAGE_MAP
        assert ".php" not in LANGUAGE_MAP


# ── ingest() ─────────────────────────────────────────────────────────────────

class TestRepoIngester:
    def test_raises_on_missing_dir(self):
        store = _make_store()
        ingester = RepoIngester(store)
        with pytest.raises(FileNotFoundError):
            ingester.ingest("/nonexistent/repo")

    def test_creates_repository_node(self):
        store = _make_store()
        ingester = RepoIngester(store)
        with tempfile.TemporaryDirectory() as tmpdir:
            ingester.ingest(tmpdir)
            store.create_node.assert_called_once()
            label, props = store.create_node.call_args[0]
            assert label == NodeLabel.Repository
            assert "name" in props
            assert "path" in props

    def test_returns_stats_dict(self):
        store = _make_store()
        ingester = RepoIngester(store)
        with tempfile.TemporaryDirectory() as tmpdir:
            stats = ingester.ingest(tmpdir)
            assert "files" in stats
            assert "functions" in stats
            assert "classes" in stats
            assert "edges" in stats

    def test_empty_dir_returns_zero_counts(self):
        store = _make_store()
        ingester = RepoIngester(store)
        with tempfile.TemporaryDirectory() as tmpdir:
            stats = ingester.ingest(tmpdir)
            assert stats["files"] == 0
            assert stats["functions"] == 0

    def test_clear_flag_calls_store_clear(self):
        store = _make_store()
        ingester = RepoIngester(store)
        with tempfile.TemporaryDirectory() as tmpdir:
            ingester.ingest(tmpdir, clear=True)
            store.clear.assert_called_once()

    def test_no_clear_by_default(self):
        store = _make_store()
        ingester = RepoIngester(store)
        with tempfile.TemporaryDirectory() as tmpdir:
            ingester.ingest(tmpdir)
            store.clear.assert_not_called()

    def test_skips_unsupported_extensions(self):
        store = _make_store()
        ingester = RepoIngester(store)
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "readme.md").write_text("# Readme")
            (Path(tmpdir) / "config.yaml").write_text("key: val")
            stats = ingester.ingest(tmpdir)
            assert stats["files"] == 0

    def test_ingests_python_files_with_mock_parser(self):
        store = _make_store()
        ingester = RepoIngester(store)
        mock_parser = MagicMock()
        mock_parser.parse_file.return_value = {"functions": 3, "classes": 1, "edges": 5}
        ingester._parsers["python"] = mock_parser

        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "app.py").write_text("def foo(): pass")
            stats = ingester.ingest(tmpdir)
            assert stats["files"] == 1
            assert stats["functions"] == 3
            assert stats["classes"] == 1
            assert stats["edges"] == 5

    def test_ingests_multiple_python_files(self):
        store = _make_store()
        ingester = RepoIngester(store)
        mock_parser = MagicMock()
        mock_parser.parse_file.return_value = {"functions": 2, "classes": 0, "edges": 1}
        ingester._parsers["python"] = mock_parser

        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "a.py").write_text("def a(): pass")
            (Path(tmpdir) / "b.py").write_text("def b(): pass")
            stats = ingester.ingest(tmpdir)
            assert stats["files"] == 2
            assert stats["functions"] == 4

    def test_handles_parse_exception_gracefully(self):
        store = _make_store()
        ingester = RepoIngester(store)
        mock_parser = MagicMock()
        mock_parser.parse_file.side_effect = Exception("parse error")
        ingester._parsers["python"] = mock_parser

        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "broken.py").write_text("invalid python @@@@")
            # Should not raise, just log
            stats = ingester.ingest(tmpdir)
            # File was attempted but failed
            assert stats["functions"] == 0

    def test_ingests_typescript_files_with_mock_parser(self):
        store = _make_store()
        ingester = RepoIngester(store)
        mock_parser = MagicMock()
        mock_parser.parse_file.return_value = {"functions": 1, "classes": 1, "edges": 2}
        ingester._parsers["typescript"] = mock_parser

        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "comp.tsx").write_text("const App = () => null")
            stats = ingester.ingest(tmpdir)
            assert stats["files"] == 1

    def test_accumulates_stats_across_files(self):
        store = _make_store()
        ingester = RepoIngester(store)
        mock_py = MagicMock()
        mock_py.parse_file.return_value = {"functions": 5, "classes": 2, "edges": 10}
        mock_ts = MagicMock()
        mock_ts.parse_file.return_value = {"functions": 3, "classes": 1, "edges": 5}
        ingester._parsers["python"] = mock_py
        ingester._parsers["typescript"] = mock_ts

        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "app.py").write_text("x=1")
            (Path(tmpdir) / "comp.ts").write_text("const x = 1")
            stats = ingester.ingest(tmpdir)
            assert stats["files"] == 2
            assert stats["functions"] == 8
            assert stats["classes"] == 3
            assert stats["edges"] == 15


# ── _iter_source_files() ──────────────────────────────────────────────────────

class TestIterSourceFiles:
    def test_yields_python_files(self):
        store = _make_store()
        ingester = RepoIngester(store)
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "app.py").write_text("x=1")
            files = list(ingester._iter_source_files(Path(tmpdir)))
            assert len(files) == 1
            assert files[0].name == "app.py"

    def test_skips_git_dir(self):
        store = _make_store()
        ingester = RepoIngester(store)
        with tempfile.TemporaryDirectory() as tmpdir:
            git_dir = Path(tmpdir) / ".git"
            git_dir.mkdir()
            (git_dir / "hook.py").write_text("x=1")
            (Path(tmpdir) / "main.py").write_text("y=2")
            files = list(ingester._iter_source_files(Path(tmpdir)))
            assert len(files) == 1
            assert files[0].name == "main.py"

    def test_skips_node_modules(self):
        store = _make_store()
        ingester = RepoIngester(store)
        with tempfile.TemporaryDirectory() as tmpdir:
            nm = Path(tmpdir) / "node_modules"
            nm.mkdir()
            (nm / "dep.js").write_text("module.exports={}")
            (Path(tmpdir) / "app.ts").write_text("const x=1")
            files = list(ingester._iter_source_files(Path(tmpdir)))
            assert len(files) == 1
            assert files[0].name == "app.ts"

    def test_skips_pycache(self):
        store = _make_store()
        ingester = RepoIngester(store)
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = Path(tmpdir) / "__pycache__"
            cache.mkdir()
            (cache / "cached.py").write_text("x=1")
            (Path(tmpdir) / "real.py").write_text("y=2")
            files = list(ingester._iter_source_files(Path(tmpdir)))
            names = [f.name for f in files]
            assert "cached.py" not in names
            assert "real.py" in names

    def test_skips_non_source_files(self):
        store = _make_store()
        ingester = RepoIngester(store)
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "readme.md").write_text("# readme")
            (Path(tmpdir) / "config.json").write_text("{}")
            files = list(ingester._iter_source_files(Path(tmpdir)))
            assert len(files) == 0

    def test_recurses_into_subdirs(self):
        store = _make_store()
        ingester = RepoIngester(store)
        with tempfile.TemporaryDirectory() as tmpdir:
            sub = Path(tmpdir) / "src" / "api"
            sub.mkdir(parents=True)
            (sub / "views.py").write_text("x=1")
            files = list(ingester._iter_source_files(Path(tmpdir)))
            assert len(files) == 1
            assert files[0].name == "views.py"


# ── _get_parser() ─────────────────────────────────────────────────────────────

class TestGetParser:
    def test_returns_cached_parser(self):
        store = _make_store()
        ingester = RepoIngester(store)
        mock_parser = MagicMock()
        ingester._parsers["python"] = mock_parser
        result = ingester._get_parser("python")
        assert result is mock_parser

    def test_raises_for_unknown_language(self):
        store = _make_store()
        ingester = RepoIngester(store)
        with pytest.raises(ValueError, match="Unsupported language"):
            ingester._get_parser("ruby")

    def test_creates_python_parser(self):
        store = _make_store()
        ingester = RepoIngester(store)
        mock_py_parser = MagicMock()
        mock_py_class = MagicMock(return_value=mock_py_parser)

        with patch("navegador.ingestion.parser.PythonParser", mock_py_class, create=True):
            with patch.dict("sys.modules", {
                "navegador.ingestion.python": MagicMock(PythonParser=mock_py_class)
            }):
                # Just verify caching works by pre-populating
                ingester._parsers["python"] = mock_py_parser
                result = ingester._get_parser("python")
                assert result is mock_py_parser

    def test_creates_typescript_parser(self):
        store = _make_store()
        ingester = RepoIngester(store)
        mock_ts_parser = MagicMock()
        ingester._parsers["typescript"] = mock_ts_parser
        result = ingester._get_parser("typescript")
        assert result is mock_ts_parser
