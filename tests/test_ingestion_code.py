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

    def test_go_rust_java_extensions(self):
        assert LANGUAGE_MAP[".go"] == "go"
        assert LANGUAGE_MAP[".rs"] == "rust"
        assert LANGUAGE_MAP[".java"] == "java"

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

    def test_creates_python_parser_via_lazy_import(self):
        store = _make_store()
        ingester = RepoIngester(store)
        mock_py_parser = MagicMock()
        mock_py_class = MagicMock(return_value=mock_py_parser)
        with patch.dict("sys.modules", {
            "navegador.ingestion.python": MagicMock(PythonParser=mock_py_class)
        }):
            result = ingester._get_parser("python")
        assert result is mock_py_parser
        mock_py_class.assert_called_once_with()

    def test_creates_typescript_parser_via_lazy_import(self):
        store = _make_store()
        ingester = RepoIngester(store)
        mock_ts_parser = MagicMock()
        mock_ts_class = MagicMock(return_value=mock_ts_parser)
        with patch.dict("sys.modules", {
            "navegador.ingestion.typescript": MagicMock(TypeScriptParser=mock_ts_class)
        }):
            result = ingester._get_parser("typescript")
        assert result is mock_ts_parser
        mock_ts_class.assert_called_once_with("typescript")

    def test_creates_go_parser_via_lazy_import(self):
        store = _make_store()
        ingester = RepoIngester(store)
        mock_go_parser = MagicMock()
        mock_go_class = MagicMock(return_value=mock_go_parser)
        with patch.dict("sys.modules", {
            "navegador.ingestion.go": MagicMock(GoParser=mock_go_class)
        }):
            result = ingester._get_parser("go")
        assert result is mock_go_parser
        mock_go_class.assert_called_once_with()

    def test_creates_rust_parser_via_lazy_import(self):
        store = _make_store()
        ingester = RepoIngester(store)
        mock_rust_parser = MagicMock()
        mock_rust_class = MagicMock(return_value=mock_rust_parser)
        with patch.dict("sys.modules", {
            "navegador.ingestion.rust": MagicMock(RustParser=mock_rust_class)
        }):
            result = ingester._get_parser("rust")
        assert result is mock_rust_parser
        mock_rust_class.assert_called_once_with()

    def test_creates_java_parser_via_lazy_import(self):
        store = _make_store()
        ingester = RepoIngester(store)
        mock_java_parser = MagicMock()
        mock_java_class = MagicMock(return_value=mock_java_parser)
        with patch.dict("sys.modules", {
            "navegador.ingestion.java": MagicMock(JavaParser=mock_java_class)
        }):
            result = ingester._get_parser("java")
        assert result is mock_java_parser
        mock_java_class.assert_called_once_with()


# ── defensive continue branch ─────────────────────────────────────────────────

class TestIngesterContinueBranch:
    def test_skips_file_when_language_not_in_map(self):
        """
        _iter_source_files filters to LANGUAGE_MAP extensions, but ingest()
        has a defensive `if not language: continue`.  Test it by patching
        _iter_source_files to yield a .rb path.
        """
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        store = _make_store()
        ingester = RepoIngester(store)
        with tempfile.TemporaryDirectory() as tmpdir:
            rb_file = Path(tmpdir) / "script.rb"
            rb_file.write_text("puts 'hello'")
            with patch.object(ingester, "_iter_source_files", return_value=[rb_file]):
                stats = ingester.ingest(tmpdir)
        assert stats["files"] == 0


# ── LanguageParser base class ─────────────────────────────────────────────────

# ── Incremental ingestion ─────────────────────────────────────────────────────

class TestIncrementalIngestion:
    def test_incremental_returns_skipped_count(self):
        store = _make_store()
        ingester = RepoIngester(store)
        with tempfile.TemporaryDirectory() as tmpdir:
            stats = ingester.ingest(tmpdir, incremental=True)
            assert "skipped" in stats

    def test_incremental_skips_unchanged_file(self):
        store = _make_store()
        ingester = RepoIngester(store)
        mock_parser = MagicMock()
        mock_parser.parse_file.return_value = {"functions": 1, "classes": 0, "edges": 0}
        ingester._parsers["python"] = mock_parser

        with tempfile.TemporaryDirectory() as tmpdir:
            py_file = Path(tmpdir) / "app.py"
            py_file.write_text("def foo(): pass")

            # First ingest: file is new, should be parsed
            stats1 = ingester.ingest(tmpdir, incremental=True)
            assert stats1["files"] == 1
            assert stats1["skipped"] == 0

            # Simulate stored hash matching
            from navegador.ingestion.parser import _file_hash
            current_hash = _file_hash(py_file)
            rel_path = "app.py"

            # Mock _file_unchanged to return True
            ingester._file_unchanged = MagicMock(return_value=True)
            stats2 = ingester.ingest(tmpdir, incremental=True)
            assert stats2["files"] == 0
            assert stats2["skipped"] == 1

    def test_incremental_reparses_changed_file(self):
        store = _make_store()
        ingester = RepoIngester(store)
        mock_parser = MagicMock()
        mock_parser.parse_file.return_value = {"functions": 1, "classes": 0, "edges": 0}
        ingester._parsers["python"] = mock_parser

        with tempfile.TemporaryDirectory() as tmpdir:
            py_file = Path(tmpdir) / "app.py"
            py_file.write_text("def foo(): pass")

            ingester._file_unchanged = MagicMock(return_value=False)
            ingester._clear_file_subgraph = MagicMock()
            stats = ingester.ingest(tmpdir, incremental=True)
            assert stats["files"] == 1
            ingester._clear_file_subgraph.assert_called_once()

    def test_non_incremental_does_not_check_hash(self):
        store = _make_store()
        ingester = RepoIngester(store)
        mock_parser = MagicMock()
        mock_parser.parse_file.return_value = {"functions": 1, "classes": 0, "edges": 0}
        ingester._parsers["python"] = mock_parser

        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "app.py").write_text("def foo(): pass")
            ingester._file_unchanged = MagicMock()
            ingester.ingest(tmpdir, incremental=False)
            ingester._file_unchanged.assert_not_called()

    def test_file_hash_is_deterministic(self):
        from navegador.ingestion.parser import _file_hash
        with tempfile.TemporaryDirectory() as tmpdir:
            f = Path(tmpdir) / "test.py"
            f.write_text("x = 1")
            h1 = _file_hash(f)
            h2 = _file_hash(f)
            assert h1 == h2
            assert len(h1) == 64  # SHA-256 hex

    def test_file_hash_changes_on_content_change(self):
        from navegador.ingestion.parser import _file_hash
        with tempfile.TemporaryDirectory() as tmpdir:
            f = Path(tmpdir) / "test.py"
            f.write_text("x = 1")
            h1 = _file_hash(f)
            f.write_text("x = 2")
            h2 = _file_hash(f)
            assert h1 != h2


class TestFileUnchanged:
    def test_returns_false_for_new_file(self):
        store = _make_store()
        store.query.return_value = MagicMock(result_set=[])
        ingester = RepoIngester(store)
        assert ingester._file_unchanged("app.py", "abc123") is False

    def test_returns_false_for_null_hash(self):
        store = _make_store()
        store.query.return_value = MagicMock(result_set=[[None]])
        ingester = RepoIngester(store)
        assert ingester._file_unchanged("app.py", "abc123") is False

    def test_returns_true_when_hash_matches(self):
        store = _make_store()
        store.query.return_value = MagicMock(result_set=[["abc123"]])
        ingester = RepoIngester(store)
        assert ingester._file_unchanged("app.py", "abc123") is True

    def test_returns_false_when_hash_differs(self):
        store = _make_store()
        store.query.return_value = MagicMock(result_set=[["old_hash"]])
        ingester = RepoIngester(store)
        assert ingester._file_unchanged("app.py", "new_hash") is False


class TestWatch:
    def test_watch_raises_on_missing_dir(self):
        store = _make_store()
        ingester = RepoIngester(store)
        with pytest.raises(FileNotFoundError):
            ingester.watch("/nonexistent/repo")

    def test_watch_calls_callback_and_stops_on_false(self):
        store = _make_store()
        ingester = RepoIngester(store)
        call_count = [0]

        def callback(stats):
            call_count[0] += 1
            return False  # stop immediately

        with tempfile.TemporaryDirectory() as tmpdir:
            ingester.watch(tmpdir, interval=0.01, callback=callback)
        assert call_count[0] == 1

    def test_watch_runs_multiple_cycles(self):
        store = _make_store()
        ingester = RepoIngester(store)
        call_count = [0]

        def callback(stats):
            call_count[0] += 1
            return call_count[0] < 3  # run 3 times then stop

        with tempfile.TemporaryDirectory() as tmpdir:
            ingester.watch(tmpdir, interval=0.01, callback=callback)
        assert call_count[0] == 3


class TestLanguageParserBase:
    def test_parse_file_raises_not_implemented(self):
        from pathlib import Path

        import pytest

        from navegador.ingestion.parser import LanguageParser
        lp = LanguageParser()
        with pytest.raises(NotImplementedError):
            lp.parse_file(Path("/tmp/x.py"), Path("/tmp"), MagicMock())
