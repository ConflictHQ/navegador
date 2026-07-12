"""Regression tests for #127 — a missing optional tree-sitter grammar must
skip that language's files with a warning, never abort the ingest."""

import logging
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from navegador.ingestion.optimization import ParallelIngester
from navegador.ingestion.parser import RepoIngester

_MISSING = ImportError("Install tree-sitter-bash: pip install tree-sitter-bash")


def _repo(tmpdir: str) -> Path:
    root = Path(tmpdir)
    (root / "script.sh").write_text("echo hi\n", encoding="utf-8")
    (root / "other.sh").write_text("echo bye\n", encoding="utf-8")
    (root / "app.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    return root


def _store():
    store = MagicMock()
    store.query.return_value = MagicMock(result_set=[])
    return store


class TestMissingGrammarSkips:
    def test_ingest_completes_and_counts_skipped_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = _repo(tmpdir)
            ingester = RepoIngester(_store())
            with patch("navegador.ingestion.bash.BashParser", side_effect=_MISSING):
                stats = ingester.ingest(root)

        assert stats["grammar_skipped"] == 2
        assert stats["files"] == 1  # app.py still parsed
        assert "bash" in ingester.unavailable_grammars
        assert "tree-sitter-bash" in ingester.unavailable_grammars["bash"]

    def test_warning_logged_once_per_language(self, caplog):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = _repo(tmpdir)
            ingester = RepoIngester(_store())
            with (
                patch("navegador.ingestion.bash.BashParser", side_effect=_MISSING),
                caplog.at_level(logging.WARNING, logger="navegador.ingestion.parser"),
            ):
                ingester.ingest(root)

        skip_warnings = [r for r in caplog.records if "Skipping bash files" in r.message]
        assert len(skip_warnings) == 1

    def test_get_parser_does_not_retry_missing_grammar(self):
        ingester = RepoIngester(_store())
        with patch("navegador.ingestion.bash.BashParser", side_effect=_MISSING) as ctor:
            assert ingester._get_parser("bash") is None
            assert ingester._get_parser("bash") is None
            assert ctor.call_count == 1

    def test_parallel_ingest_completes_and_counts_skipped_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = _repo(tmpdir)
            parallel = ParallelIngester(_store())
            with patch("navegador.ingestion.bash.BashParser", side_effect=_MISSING):
                stats = parallel.ingest_parallel(root, max_workers=2)

        assert stats["grammar_skipped"] == 2
        assert stats["errors"] == 0
        assert stats["files"] == 1

    def test_unsupported_language_still_raises(self):
        ingester = RepoIngester(_store())
        try:
            ingester._get_parser("cobol")
        except ValueError as e:
            assert "Unsupported language" in str(e)
        else:  # pragma: no cover
            raise AssertionError("expected ValueError for unsupported language")
