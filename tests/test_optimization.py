"""Tests for navegador.ingestion.optimization (#42 – #45)."""

from __future__ import annotations

import tempfile
import threading
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from navegador.ingestion.optimization import (
    DiffResult,
    GraphDiffer,
    IncrementalParser,
    NodeDescriptor,
    ParallelIngester,
    TreeCache,
)


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_store(rows=None):
    """Return a MagicMock GraphStore whose query() returns *rows*."""
    store = MagicMock()
    store.query.return_value = MagicMock(result_set=rows or [])
    return store


def _mock_tree(name: str = "tree") -> MagicMock:
    t = MagicMock()
    t.__repr__ = lambda self: f"<MockTree {name}>"
    return t


# ── #42 — TreeCache ───────────────────────────────────────────────────────────


class TestTreeCache:
    # ── get / put ──────────────────────────────────────────────────────────────

    def test_get_returns_none_on_cold_cache(self):
        cache = TreeCache()
        assert cache.get("foo.py", "abc") is None

    def test_put_and_get_roundtrip(self):
        cache = TreeCache()
        tree = _mock_tree()
        cache.put("foo.py", "abc123", tree)
        assert cache.get("foo.py", "abc123") is tree

    def test_get_miss_does_not_return_wrong_hash(self):
        cache = TreeCache()
        tree = _mock_tree()
        cache.put("foo.py", "hash-A", tree)
        assert cache.get("foo.py", "hash-B") is None

    def test_get_miss_does_not_return_wrong_path(self):
        cache = TreeCache()
        tree = _mock_tree()
        cache.put("foo.py", "hash-A", tree)
        assert cache.get("bar.py", "hash-A") is None

    def test_put_overwrites_existing_entry(self):
        cache = TreeCache()
        t1 = _mock_tree("t1")
        t2 = _mock_tree("t2")
        cache.put("foo.py", "abc", t1)
        cache.put("foo.py", "abc", t2)
        assert cache.get("foo.py", "abc") is t2

    # ── LRU eviction ──────────────────────────────────────────────────────────

    def test_evicts_lru_entry_when_full(self):
        cache = TreeCache(max_size=2)
        t1 = _mock_tree("t1")
        t2 = _mock_tree("t2")
        t3 = _mock_tree("t3")

        cache.put("a.py", "1", t1)
        cache.put("b.py", "2", t2)
        # Cache is now full; inserting t3 should evict t1 (LRU).
        cache.put("c.py", "3", t3)

        assert cache.get("a.py", "1") is None
        assert cache.get("b.py", "2") is t2
        assert cache.get("c.py", "3") is t3

    def test_get_promotes_entry_so_it_is_not_evicted(self):
        cache = TreeCache(max_size=2)
        t1 = _mock_tree("t1")
        t2 = _mock_tree("t2")
        t3 = _mock_tree("t3")

        cache.put("a.py", "1", t1)
        cache.put("b.py", "2", t2)
        # Touch t1 so it becomes the most-recently used.
        cache.get("a.py", "1")
        # t2 is now the LRU; adding t3 should evict t2.
        cache.put("c.py", "3", t3)

        assert cache.get("a.py", "1") is t1
        assert cache.get("b.py", "2") is None
        assert cache.get("c.py", "3") is t3

    def test_size_respects_max_size(self):
        cache = TreeCache(max_size=3)
        for i in range(10):
            cache.put(f"file{i}.py", str(i), _mock_tree())
        assert len(cache) <= 3

    def test_constructor_rejects_zero_max_size(self):
        with pytest.raises(ValueError):
            TreeCache(max_size=0)

    # ── stats ──────────────────────────────────────────────────────────────────

    def test_stats_initial_state(self):
        cache = TreeCache()
        s = cache.stats()
        assert s["hits"] == 0
        assert s["misses"] == 0
        assert s["size"] == 0

    def test_stats_records_hits(self):
        cache = TreeCache()
        cache.put("x.py", "h", _mock_tree())
        cache.get("x.py", "h")
        cache.get("x.py", "h")
        assert cache.stats()["hits"] == 2

    def test_stats_records_misses(self):
        cache = TreeCache()
        cache.get("x.py", "h")
        cache.get("y.py", "h")
        assert cache.stats()["misses"] == 2

    def test_stats_size_tracks_entries(self):
        cache = TreeCache(max_size=10)
        cache.put("a.py", "1", _mock_tree())
        cache.put("b.py", "2", _mock_tree())
        assert cache.stats()["size"] == 2

    def test_stats_max_size_reported(self):
        cache = TreeCache(max_size=42)
        assert cache.stats()["max_size"] == 42

    # ── clear ──────────────────────────────────────────────────────────────────

    def test_clear_removes_all_entries(self):
        cache = TreeCache()
        cache.put("a.py", "1", _mock_tree())
        cache.put("b.py", "2", _mock_tree())
        cache.clear()
        assert len(cache) == 0
        assert cache.get("a.py", "1") is None

    def test_clear_resets_stats(self):
        cache = TreeCache()
        cache.put("a.py", "1", _mock_tree())
        cache.get("a.py", "1")
        cache.get("a.py", "bad")
        cache.clear()
        s = cache.stats()
        assert s["hits"] == 0
        assert s["misses"] == 0
        assert s["size"] == 0

    # ── thread safety ──────────────────────────────────────────────────────────

    def test_concurrent_puts_do_not_corrupt_state(self):
        cache = TreeCache(max_size=50)
        errors = []

        def writer(n: int) -> None:
            try:
                for i in range(20):
                    cache.put(f"file{n}_{i}.py", str(i), _mock_tree())
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=writer, args=(t,)) for t in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert len(cache) <= 50


# ── #43 — IncrementalParser ───────────────────────────────────────────────────


class TestIncrementalParser:
    def _make_language_and_parser(self):
        """
        Return a fake tree-sitter Language object whose parser.parse()
        returns a fresh MagicMock tree.
        """
        fake_tree = _mock_tree("parsed")
        fake_parser = MagicMock()
        fake_parser.parse.return_value = fake_tree
        fake_language = MagicMock()

        # Patch tree_sitter.Parser so IncrementalParser can instantiate it.
        mock_ts_parser = MagicMock()
        mock_ts_parser.parse.return_value = fake_tree
        mock_ts_class = MagicMock(return_value=mock_ts_parser)

        return fake_tree, mock_ts_parser, mock_ts_class, fake_language

    def test_parse_returns_tree(self):
        cache = TreeCache()
        inc = IncrementalParser(cache)

        fake_tree = _mock_tree()
        mock_ts_parser = MagicMock()
        mock_ts_parser.parse.return_value = fake_tree

        with patch("tree_sitter.Parser", return_value=mock_ts_parser):
            result = inc.parse(b"source", MagicMock(), "foo.py", "hash1")

        assert result is fake_tree

    def test_parse_stores_tree_in_cache(self):
        cache = TreeCache()
        inc = IncrementalParser(cache)

        fake_tree = _mock_tree()
        mock_ts_parser = MagicMock()
        mock_ts_parser.parse.return_value = fake_tree

        with patch("tree_sitter.Parser", return_value=mock_ts_parser):
            inc.parse(b"source", MagicMock(), "foo.py", "hash1")

        assert cache.get("foo.py", "hash1") is fake_tree

    def test_parse_returns_cached_tree_without_calling_parser(self):
        cached_tree = _mock_tree("cached")
        cache = TreeCache()
        cache.put("foo.py", "hash1", cached_tree)

        inc = IncrementalParser(cache)
        mock_ts_parser = MagicMock()

        with patch("tree_sitter.Parser", return_value=mock_ts_parser):
            result = inc.parse(b"source", MagicMock(), "foo.py", "hash1")

        assert result is cached_tree
        mock_ts_parser.parse.assert_not_called()

    def test_cache_hit_increments_hit_count(self):
        cache = TreeCache()
        tree = _mock_tree()
        cache.put("foo.py", "hashX", tree)

        inc = IncrementalParser(cache)
        with patch("tree_sitter.Parser", return_value=MagicMock()):
            inc.parse(b"src", MagicMock(), "foo.py", "hashX")

        assert cache.stats()["hits"] == 1

    def test_parse_passes_old_tree_on_rehash(self):
        """When a stale tree exists for the same path, it is passed as old_tree."""
        cache = TreeCache()
        stale_tree = _mock_tree("stale")
        cache.put("bar.py", "old-hash", stale_tree)

        new_tree = _mock_tree("new")
        mock_ts_parser = MagicMock()
        mock_ts_parser.parse.return_value = new_tree

        inc = IncrementalParser(cache)
        with patch("tree_sitter.Parser", return_value=mock_ts_parser):
            result = inc.parse(b"new source", MagicMock(), "bar.py", "new-hash")

        assert result is new_tree
        # old_tree must have been passed as the second positional argument.
        mock_ts_parser.parse.assert_called_once_with(b"new source", stale_tree)

    def test_parse_without_old_tree_calls_parse_with_source_only(self):
        cache = TreeCache()
        new_tree = _mock_tree()
        mock_ts_parser = MagicMock()
        mock_ts_parser.parse.return_value = new_tree

        inc = IncrementalParser(cache)
        with patch("tree_sitter.Parser", return_value=mock_ts_parser):
            inc.parse(b"source", MagicMock(), "baz.py", "hash1")

        mock_ts_parser.parse.assert_called_once_with(b"source")

    def test_default_cache_is_created_if_none_given(self):
        inc = IncrementalParser()
        assert isinstance(inc.cache, TreeCache)

    def test_custom_cache_is_used(self):
        cache = TreeCache(max_size=5)
        inc = IncrementalParser(cache)
        assert inc.cache is cache

    def test_fallback_when_tree_sitter_not_importable(self):
        """When tree_sitter is unavailable, language is used directly as parser."""
        cache = TreeCache()
        fake_tree = _mock_tree()
        fake_language = MagicMock()
        fake_language.parse.return_value = fake_tree

        inc = IncrementalParser(cache)

        import builtins

        real_import = builtins.__import__

        def _block_tree_sitter(name, *args, **kwargs):
            if name == "tree_sitter":
                raise ImportError("mocked absence")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=_block_tree_sitter):
            result = inc.parse(b"source", fake_language, "x.py", "h1")

        assert result is fake_tree


# ── #44 — GraphDiffer ─────────────────────────────────────────────────────────


def _nd(label: str, name: str, line_start: int, **extra) -> NodeDescriptor:
    return NodeDescriptor(label=label, name=name, line_start=line_start, extra=extra)


class TestNodeDescriptor:
    def test_identity_key(self):
        nd = _nd("Function", "foo", 10)
        assert nd.identity_key() == ("Function", "foo", 10)

    def test_equality_same(self):
        assert _nd("Function", "foo", 10) == _nd("Function", "foo", 10)

    def test_equality_different_line(self):
        assert _nd("Function", "foo", 10) != _nd("Function", "foo", 11)

    def test_equality_different_extra(self):
        a = _nd("Function", "foo", 10, docstring="hello")
        b = _nd("Function", "foo", 10, docstring="world")
        assert a != b


class TestGraphDiffer:
    def test_diff_empty_new_and_empty_existing(self):
        store = _make_store(rows=[])
        differ = GraphDiffer(store)
        result = differ.diff_file("src/app.py", [])
        assert result == DiffResult(added=0, modified=0, unchanged=0, removed=0)

    def test_diff_all_new_nodes(self):
        store = _make_store(rows=[])
        differ = GraphDiffer(store)
        nodes = [
            _nd("Function", "foo", 1),
            _nd("Class", "Bar", 10),
        ]
        result = differ.diff_file("src/app.py", nodes)
        assert result.added == 2
        assert result.modified == 0
        assert result.unchanged == 0
        assert result.removed == 0

    def test_diff_all_unchanged_nodes(self):
        store = _make_store(rows=[
            ["Function", "foo", 1],
            ["Class", "Bar", 10],
        ])
        differ = GraphDiffer(store)
        nodes = [
            _nd("Function", "foo", 1),
            _nd("Class", "Bar", 10),
        ]
        result = differ.diff_file("src/app.py", nodes)
        assert result.unchanged == 2
        assert result.added == 0
        assert result.modified == 0
        assert result.removed == 0

    def test_diff_modified_node(self):
        """Same identity key but different extra props counts as modified."""
        store = _make_store(rows=[["Function", "foo", 1]])
        differ = GraphDiffer(store)
        # Existing node in store has no extra; new node has docstring.
        nodes = [_nd("Function", "foo", 1, docstring="now documented")]
        result = differ.diff_file("src/app.py", nodes)
        # The identity key matches but extra differs → modified.
        assert result.modified == 1
        assert result.unchanged == 0
        assert result.added == 0

    def test_diff_removed_nodes(self):
        store = _make_store(rows=[
            ["Function", "foo", 1],
            ["Function", "bar", 5],
        ])
        differ = GraphDiffer(store)
        # Only foo is present in new parse; bar was removed.
        nodes = [_nd("Function", "foo", 1)]
        result = differ.diff_file("src/app.py", nodes)
        assert result.removed == 1
        assert result.unchanged == 1

    def test_diff_mixed_scenario(self):
        store = _make_store(rows=[
            ["Function", "old_func", 1],
            ["Class", "MyClass", 20],
        ])
        differ = GraphDiffer(store)
        new_nodes = [
            _nd("Class", "MyClass", 20),   # unchanged
            _nd("Function", "new_func", 5),  # added
        ]
        result = differ.diff_file("src/app.py", new_nodes)
        assert result.unchanged == 1
        assert result.added == 1
        assert result.removed == 1
        assert result.modified == 0

    def test_diff_skips_rows_with_none_name(self):
        store = _make_store(rows=[[None, None, None]])
        differ = GraphDiffer(store)
        result = differ.diff_file("src/app.py", [_nd("Function", "foo", 1)])
        # The None row is skipped; foo is treated as a new node.
        assert result.added == 1
        assert result.removed == 0

    def test_total_changes_property(self):
        result = DiffResult(added=3, modified=1, unchanged=5, removed=2)
        assert result.total_changes == 6

    def test_store_is_queried_with_file_path(self):
        store = _make_store(rows=[])
        differ = GraphDiffer(store)
        differ.diff_file("src/models.py", [])
        # Ensure the store was actually queried with the right path param.
        store.query.assert_called_once()
        _, kwargs_or_positional = store.query.call_args[0], store.query.call_args
        # The second positional arg to store.query should contain file_path.
        call_params = store.query.call_args[0][1]
        assert call_params["file_path"] == "src/models.py"


# ── #45 — ParallelIngester ────────────────────────────────────────────────────


class TestParallelIngester:
    def _setup_ingester_with_mock_parser(self, store, parse_result=None):
        """
        Return a ParallelIngester whose internal RepoIngester has a mock
        Python parser installed.
        """
        if parse_result is None:
            parse_result = {"functions": 2, "classes": 1, "edges": 3}

        ingester = ParallelIngester(store)
        mock_parser = MagicMock()
        mock_parser.parse_file.return_value = parse_result
        ingester._ingester._parsers["python"] = mock_parser
        return ingester, mock_parser

    def test_raises_on_missing_dir(self):
        store = _make_store()
        ingester = ParallelIngester(store)
        with pytest.raises(FileNotFoundError):
            ingester.ingest_parallel("/nonexistent/path")

    def test_returns_stats_dict_with_all_keys(self):
        store = _make_store()
        ingester, _ = self._setup_ingester_with_mock_parser(store)
        with tempfile.TemporaryDirectory() as tmpdir:
            stats = ingester.ingest_parallel(tmpdir)
        assert {"files", "functions", "classes", "edges", "skipped", "errors"} <= set(stats)

    def test_processes_single_file(self):
        store = _make_store()
        ingester, mock_parser = self._setup_ingester_with_mock_parser(
            store, {"functions": 3, "classes": 1, "edges": 4}
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "app.py").write_text("def foo(): pass")
            stats = ingester.ingest_parallel(tmpdir)

        assert stats["files"] == 1
        assert stats["functions"] == 3
        assert stats["classes"] == 1
        assert stats["edges"] == 4
        assert stats["errors"] == 0

    def test_processes_multiple_files_concurrently(self):
        store = _make_store()
        ingester, mock_parser = self._setup_ingester_with_mock_parser(
            store, {"functions": 1, "classes": 0, "edges": 0}
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            for i in range(5):
                (Path(tmpdir) / f"mod{i}.py").write_text(f"def f{i}(): pass")
            stats = ingester.ingest_parallel(tmpdir, max_workers=3)

        assert stats["files"] == 5
        assert stats["functions"] == 5
        assert stats["errors"] == 0

    def test_aggregates_stats_across_files(self):
        store = _make_store()
        ingester, _ = self._setup_ingester_with_mock_parser(
            store, {"functions": 2, "classes": 1, "edges": 5}
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "a.py").write_text("x=1")
            (Path(tmpdir) / "b.py").write_text("y=2")
            stats = ingester.ingest_parallel(tmpdir)

        assert stats["files"] == 2
        assert stats["functions"] == 4
        assert stats["classes"] == 2
        assert stats["edges"] == 10

    def test_clear_flag_calls_store_clear(self):
        store = _make_store()
        ingester, _ = self._setup_ingester_with_mock_parser(store)
        with tempfile.TemporaryDirectory() as tmpdir:
            ingester.ingest_parallel(tmpdir, clear=True)
        store.clear.assert_called_once()

    def test_no_clear_by_default(self):
        store = _make_store()
        ingester, _ = self._setup_ingester_with_mock_parser(store)
        with tempfile.TemporaryDirectory() as tmpdir:
            ingester.ingest_parallel(tmpdir)
        store.clear.assert_not_called()

    def test_empty_repo_returns_zero_counts(self):
        store = _make_store()
        ingester, _ = self._setup_ingester_with_mock_parser(store)
        with tempfile.TemporaryDirectory() as tmpdir:
            stats = ingester.ingest_parallel(tmpdir)
        assert stats["files"] == 0
        assert stats["functions"] == 0

    def test_parser_exception_increments_errors_not_files(self):
        store = _make_store()
        ingester = ParallelIngester(store)
        broken_parser = MagicMock()
        broken_parser.parse_file.side_effect = RuntimeError("boom")
        ingester._ingester._parsers["python"] = broken_parser

        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "broken.py").write_text("def x(): pass")
            stats = ingester.ingest_parallel(tmpdir)

        assert stats["files"] == 0
        assert stats["errors"] == 1

    def test_incremental_skips_unchanged_files(self):
        store = _make_store()
        ingester, mock_parser = self._setup_ingester_with_mock_parser(store)
        ingester._ingester._file_unchanged = MagicMock(return_value=True)

        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "unchanged.py").write_text("x=1")
            stats = ingester.ingest_parallel(tmpdir, incremental=True)

        assert stats["skipped"] == 1
        assert stats["files"] == 0
        mock_parser.parse_file.assert_not_called()

    def test_creates_repository_node(self):
        store = _make_store()
        ingester, _ = self._setup_ingester_with_mock_parser(store)
        with tempfile.TemporaryDirectory() as tmpdir:
            ingester.ingest_parallel(tmpdir)

        from navegador.graph.schema import NodeLabel

        store.create_node.assert_called_once()
        label, props = store.create_node.call_args[0]
        assert label == NodeLabel.Repository
        assert "name" in props and "path" in props

    def test_max_workers_none_uses_default(self):
        """Passing max_workers=None should not raise."""
        store = _make_store()
        ingester, _ = self._setup_ingester_with_mock_parser(store)
        with tempfile.TemporaryDirectory() as tmpdir:
            stats = ingester.ingest_parallel(tmpdir, max_workers=None)
        assert isinstance(stats, dict)

    def test_skips_non_python_files(self):
        store = _make_store()
        ingester, mock_parser = self._setup_ingester_with_mock_parser(store)
        with tempfile.TemporaryDirectory() as tmpdir:
            # .yaml is not a supported language; .md is now (markdown)
            (Path(tmpdir) / "config.yaml").write_text("key: value")
            stats = ingester.ingest_parallel(tmpdir)
        assert stats["files"] == 0
        mock_parser.parse_file.assert_not_called()
