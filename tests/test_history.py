"""Tests for navegador.history — snapshot-based symbol history and lineage."""

import json
from unittest.mock import MagicMock

from navegador.history import (
    HistoryEvent,
    HistoryReport,
    HistoryStore,
    LineageReport,
    LineageStep,
    SnapshotEntry,
    SnapshotInfo,
    _bigrams,
    _name_similarity,
)

# ── Helpers ──────────────────────────────────────────────────────────────────


def _mock_store(result_set=None):
    store = MagicMock()
    result = MagicMock()
    result.result_set = result_set or []
    store.query.return_value = result
    return store


def _multi_mock_store(*result_sets):
    store = MagicMock()
    results = []
    for rs in result_sets:
        r = MagicMock()
        r.result_set = rs
        results.append(r)
    store.query.side_effect = results
    return store


# ── _bigrams ─────────────────────────────────────────────────────────────────


class TestBigrams:
    def test_standard_word(self):
        result = _bigrams("hello")
        assert result == {"he", "el", "ll", "lo"}

    def test_two_char_string(self):
        assert _bigrams("ab") == {"ab"}

    def test_single_char_returns_empty(self):
        assert _bigrams("a") == set()

    def test_empty_string_returns_empty(self):
        assert _bigrams("") == set()

    def test_case_insensitive(self):
        assert _bigrams("Hello") == _bigrams("hello")


# ── _name_similarity ─────────────────────────────────────────────────────────


class TestNameSimilarity:
    def test_identical_returns_one(self):
        assert _name_similarity("foo", "foo") == 1.0

    def test_completely_different(self):
        score = _name_similarity("abc", "xyz")
        assert score == 0.0

    def test_partial_overlap(self):
        score = _name_similarity("validate", "validator")
        # Should have significant overlap but not 1.0
        assert 0.5 < score < 1.0

    def test_single_char_different(self):
        # No bigrams for single chars, returns 0.0
        assert _name_similarity("a", "b") == 0.0

    def test_empty_vs_nonempty(self):
        assert _name_similarity("", "hello") == 0.0

    def test_both_empty(self):
        # a == b path
        assert _name_similarity("", "") == 1.0


# ── HistoryReport ────────────────────────────────────────────────────────────


class TestHistoryReport:
    def test_markdown_with_events(self):
        report = HistoryReport(
            symbol="AuthService",
            file_path="app/auth.py",
            events=[
                HistoryEvent(ref="v1.0", event="first_seen", name="AuthService",
                             file_path="app/auth.py", detail="in `app/auth.py`"),
                HistoryEvent(ref="v2.0", event="moved", name="AuthService",
                             file_path="app/auth/service.py",
                             detail="`app/auth.py` -> `app/auth/service.py`"),
            ],
        )
        md = report.to_markdown()
        assert "# History" in md
        assert "`AuthService`" in md
        assert "`app/auth.py`" in md
        assert "| Ref | Event | Detail |" in md
        assert "**first_seen**" in md
        assert "**moved**" in md
        assert "`v1.0`" in md
        assert "`v2.0`" in md

    def test_markdown_empty_events(self):
        report = HistoryReport(symbol="Ghost", file_path="", events=[])
        md = report.to_markdown()
        assert "No snapshot history found" in md
        assert "| Ref |" not in md

    def test_json_round_trip(self):
        report = HistoryReport(
            symbol="foo",
            file_path="bar.py",
            events=[
                HistoryEvent(ref="v1", event="first_seen", name="foo",
                             file_path="bar.py", detail="in `bar.py`"),
            ],
        )
        raw = report.to_json()
        data = json.loads(raw)
        assert data["symbol"] == "foo"
        assert data["file_path"] == "bar.py"
        assert len(data["events"]) == 1
        assert data["events"][0]["event"] == "first_seen"


# ── LineageReport ────────────────────────────────────────────────────────────


class TestLineageReport:
    def test_markdown_with_chain(self):
        report = LineageReport(
            symbol="AuthService",
            chain=[
                LineageStep(ref="v1", name="AuthService", file_path="auth.py",
                            label="Class", event="created"),
                LineageStep(ref="v2", name="AuthService", file_path="auth.py",
                            label="Class", event="continued"),
                LineageStep(ref="v3", name="AuthService", file_path="services/auth.py",
                            label="Class", event="moved",
                            detail="moved from `auth.py` to `services/auth.py`"),
            ],
        )
        md = report.to_markdown()
        assert "# Lineage" in md
        assert "`AuthService`" in md
        assert "**created**" in md
        # "continued" should not be bold
        assert "continued" in md
        assert "**moved**" in md

    def test_markdown_empty_chain(self):
        report = LineageReport(symbol="Ghost", chain=[])
        md = report.to_markdown()
        assert "No lineage data found" in md

    def test_json_round_trip(self):
        report = LineageReport(
            symbol="foo",
            chain=[
                LineageStep(ref="v1", name="foo", file_path="f.py",
                            label="Function", event="created"),
            ],
        )
        data = json.loads(report.to_json())
        assert data["symbol"] == "foo"
        assert len(data["chain"]) == 1
        assert data["chain"][0]["event"] == "created"


# ── HistoryStore.list_snapshots ──────────────────────────────────────────────


class TestListSnapshots:
    def test_parses_rows_into_snapshot_info(self):
        store = _mock_store([
            ["v1.0", "abc123", "2025-01-01", 50],
            ["v2.0", "def456", "2025-06-01", 75],
        ])
        hs = HistoryStore(store, repo_path="/tmp")
        snapshots = hs.list_snapshots()

        assert len(snapshots) == 2
        assert snapshots[0].ref == "v1.0"
        assert snapshots[0].commit_sha == "abc123"
        assert snapshots[0].committed_at == "2025-01-01"
        assert snapshots[0].symbol_count == 50
        assert snapshots[1].ref == "v2.0"

    def test_empty_result(self):
        store = _mock_store([])
        hs = HistoryStore(store, repo_path="/tmp")
        assert hs.list_snapshots() == []

    def test_handles_none_values(self):
        store = _mock_store([["v1", None, None, None]])
        hs = HistoryStore(store, repo_path="/tmp")
        snapshots = hs.list_snapshots()
        assert snapshots[0].commit_sha == ""
        assert snapshots[0].committed_at == ""
        assert snapshots[0].symbol_count == 0


# ── HistoryStore.symbols_at ──────────────────────────────────────────────────


class TestSymbolsAt:
    def test_parses_rows_into_entries(self):
        store = _mock_store([
            ["Function", "foo", "a.py", 10, 20],
            ["Class", "Bar", "b.py", 1, 50],
        ])
        hs = HistoryStore(store, repo_path="/tmp")
        entries = hs.symbols_at("v1.0")

        assert len(entries) == 2
        assert entries[0].ref == "v1.0"
        assert entries[0].label == "Function"
        assert entries[0].name == "foo"
        assert entries[0].file_path == "a.py"
        assert entries[0].line_start == 10
        assert entries[0].line_end == 20
        assert entries[1].label == "Class"

    def test_empty_snapshot(self):
        store = _mock_store([])
        hs = HistoryStore(store, repo_path="/tmp")
        assert hs.symbols_at("v1.0") == []


# ── HistoryStore.history ─────────────────────────────────────────────────────


class TestHistory:
    def test_first_seen_event(self):
        """First row should always produce a 'first_seen' event."""
        store = _multi_mock_store(
            # _SNAPSHOTS_FOR_SYMBOL query
            [["v1.0", "2025-01-01", "Function", "foo", "a.py"]],
            # list_snapshots (called for removal detection)
            [["v1.0", "abc", "2025-01-01", 10]],
        )
        hs = HistoryStore(store, repo_path="/tmp")
        report = hs.history("foo", file_path="a.py")

        assert isinstance(report, HistoryReport)
        assert report.symbol == "foo"
        assert len(report.events) == 1
        assert report.events[0].event == "first_seen"
        assert "a.py" in report.events[0].detail

    def test_moved_event(self):
        """Symbol appearing in a different file should produce 'moved'."""
        store = _multi_mock_store(
            [
                ["v1.0", "2025-01-01", "Function", "foo", "old/a.py"],
                ["v2.0", "2025-06-01", "Function", "foo", "new/a.py"],
            ],
            [
                ["v2.0", "def456", "2025-06-01", 20],
            ],
        )
        hs = HistoryStore(store, repo_path="/tmp")
        report = hs.history("foo")

        assert len(report.events) == 2
        assert report.events[0].event == "first_seen"
        assert report.events[1].event == "moved"
        assert "old/a.py" in report.events[1].detail
        assert "new/a.py" in report.events[1].detail

    def test_seen_event_same_file(self):
        """Symbol in same file across snapshots should produce 'seen'."""
        store = _multi_mock_store(
            [
                ["v1.0", "2025-01-01", "Function", "foo", "a.py"],
                ["v2.0", "2025-06-01", "Function", "foo", "a.py"],
            ],
            [["v2.0", "def", "2025-06-01", 20]],
        )
        hs = HistoryStore(store, repo_path="/tmp")
        report = hs.history("foo")

        assert report.events[1].event == "seen"

    def test_removed_event_appended(self):
        """If symbol not in latest snapshot, a 'removed' event is appended."""
        store = _multi_mock_store(
            # Symbol only in v1.0
            [["v1.0", "2025-01-01", "Function", "foo", "a.py"]],
            # Latest snapshot is v2.0 — symbol not there
            [
                ["v1.0", "abc", "2025-01-01", 10],
                ["v2.0", "def", "2025-06-01", 20],
            ],
        )
        hs = HistoryStore(store, repo_path="/tmp")
        report = hs.history("foo")

        assert report.events[-1].event == "removed"
        assert report.events[-1].ref == "v2.0"
        assert "not present in latest snapshot" in report.events[-1].detail

    def test_empty_history(self):
        """Symbol not found in any snapshot returns empty events."""
        store = _multi_mock_store(
            [],  # no rows for symbol
            [],  # no snapshots
        )
        hs = HistoryStore(store, repo_path="/tmp")
        report = hs.history("ghost")

        assert report.events == []


# ── HistoryStore.lineage ─────────────────────────────────────────────────────


class TestLineage:
    def _make_history_store_with_patched_methods(self, snapshots, symbols_map):
        """Create HistoryStore with list_snapshots and symbols_at patched."""
        store = _mock_store()
        hs = HistoryStore(store, repo_path="/tmp")
        hs.list_snapshots = MagicMock(return_value=snapshots)
        hs.symbols_at = MagicMock(side_effect=lambda ref: symbols_map.get(ref, []))
        return hs

    def test_continued_same_name_same_file(self):
        snapshots = [
            SnapshotInfo(ref="v1", commit_sha="a", committed_at="2025-01", symbol_count=5),
            SnapshotInfo(ref="v2", commit_sha="b", committed_at="2025-06", symbol_count=5),
        ]
        symbols_map = {
            "v1": [SnapshotEntry(ref="v1", name="foo", label="Function", file_path="a.py")],
            "v2": [SnapshotEntry(ref="v2", name="foo", label="Function", file_path="a.py")],
        }
        hs = self._make_history_store_with_patched_methods(snapshots, symbols_map)
        report = hs.lineage("foo", file_path="a.py")

        assert len(report.chain) == 2
        assert report.chain[0].event == "created"
        assert report.chain[1].event == "continued"

    def test_moved_same_name_different_file(self):
        snapshots = [
            SnapshotInfo(ref="v1", commit_sha="a", committed_at="2025-01", symbol_count=5),
            SnapshotInfo(ref="v2", commit_sha="b", committed_at="2025-06", symbol_count=5),
        ]
        symbols_map = {
            "v1": [SnapshotEntry(ref="v1", name="foo", label="Function", file_path="old.py")],
            "v2": [SnapshotEntry(ref="v2", name="foo", label="Function", file_path="new.py")],
        }
        hs = self._make_history_store_with_patched_methods(snapshots, symbols_map)
        report = hs.lineage("foo", file_path="old.py")

        assert report.chain[0].event == "created"
        assert report.chain[1].event == "moved"
        assert "old.py" in report.chain[1].detail
        assert "new.py" in report.chain[1].detail

    def test_renamed_similar_name_same_file(self):
        snapshots = [
            SnapshotInfo(ref="v1", commit_sha="a", committed_at="2025-01", symbol_count=5),
            SnapshotInfo(ref="v2", commit_sha="b", committed_at="2025-06", symbol_count=5),
        ]
        symbols_map = {
            "v1": [SnapshotEntry(ref="v1", name="validate_user", label="Function",
                                 file_path="auth.py")],
            # Renamed: similar name, same file
            "v2": [SnapshotEntry(ref="v2", name="validate_user_v2", label="Function",
                                 file_path="auth.py")],
        }
        hs = self._make_history_store_with_patched_methods(snapshots, symbols_map)
        report = hs.lineage("validate_user", file_path="auth.py")

        assert report.chain[0].event == "created"
        assert report.chain[1].event == "renamed"
        assert "validate_user" in report.chain[1].detail
        assert "validate_user_v2" in report.chain[1].detail
        assert "similarity" in report.chain[1].detail

    def test_removed_when_not_found(self):
        snapshots = [
            SnapshotInfo(ref="v1", commit_sha="a", committed_at="2025-01", symbol_count=5),
            SnapshotInfo(ref="v2", commit_sha="b", committed_at="2025-06", symbol_count=3),
        ]
        symbols_map = {
            "v1": [SnapshotEntry(ref="v1", name="foo", label="Function", file_path="a.py")],
            "v2": [],  # foo is gone, nothing similar
        }
        hs = self._make_history_store_with_patched_methods(snapshots, symbols_map)
        report = hs.lineage("foo", file_path="a.py")

        assert report.chain[0].event == "created"
        assert report.chain[1].event == "removed"
        assert "not found" in report.chain[1].detail

    def test_empty_snapshots_returns_empty_chain(self):
        hs = self._make_history_store_with_patched_methods([], {})
        report = hs.lineage("foo")
        assert report.chain == []

    def test_lineage_tracks_name_change_across_steps(self):
        """After a rename, the next snapshot should track the new name."""
        # validate_user -> validate_user_v2 has similarity 0.80 (above 0.70 threshold)
        snapshots = [
            SnapshotInfo(ref="v1", commit_sha="a", committed_at="2025-01", symbol_count=5),
            SnapshotInfo(ref="v2", commit_sha="b", committed_at="2025-06", symbol_count=5),
            SnapshotInfo(ref="v3", commit_sha="c", committed_at="2025-12", symbol_count=5),
        ]
        symbols_map = {
            "v1": [SnapshotEntry(ref="v1", name="validate_user", label="Function",
                                 file_path="auth.py")],
            "v2": [SnapshotEntry(ref="v2", name="validate_user_v2", label="Function",
                                 file_path="auth.py")],
            "v3": [SnapshotEntry(ref="v3", name="validate_user_v2", label="Function",
                                 file_path="auth.py")],
        }
        hs = self._make_history_store_with_patched_methods(snapshots, symbols_map)
        report = hs.lineage("validate_user", file_path="auth.py")

        assert report.chain[0].event == "created"
        assert report.chain[1].event == "renamed"
        assert report.chain[2].event == "continued"
        assert report.chain[2].name == "validate_user_v2"


# ── HistoryStore.diff_snapshots ──────────────────────────────────────────────


class TestDiffSnapshots:
    def _make_hs_with_symbols(self, base_symbols, head_symbols):
        store = _mock_store()
        hs = HistoryStore(store, repo_path="/tmp")

        def symbols_at_side_effect(ref):
            if ref == "base":
                return base_symbols
            return head_symbols

        hs.symbols_at = MagicMock(side_effect=symbols_at_side_effect)
        return hs

    def test_added_symbols(self):
        base = [SnapshotEntry(ref="base", name="foo", label="Function", file_path="a.py")]
        head = [
            SnapshotEntry(ref="head", name="foo", label="Function", file_path="a.py"),
            SnapshotEntry(ref="head", name="bar", label="Function", file_path="b.py"),
        ]
        hs = self._make_hs_with_symbols(base, head)
        diff = hs.diff_snapshots("base", "head")

        assert len(diff["added"]) == 1
        assert diff["added"][0]["name"] == "bar"

    def test_removed_symbols(self):
        base = [
            SnapshotEntry(ref="base", name="foo", label="Function", file_path="a.py"),
            SnapshotEntry(ref="base", name="old_fn", label="Function", file_path="c.py"),
        ]
        head = [SnapshotEntry(ref="head", name="foo", label="Function", file_path="a.py")]
        hs = self._make_hs_with_symbols(base, head)
        diff = hs.diff_snapshots("base", "head")

        assert len(diff["removed"]) == 1
        assert diff["removed"][0]["name"] == "old_fn"

    def test_moved_symbols(self):
        base = [SnapshotEntry(ref="base", name="foo", label="Function", file_path="old.py")]
        head = [SnapshotEntry(ref="head", name="foo", label="Function", file_path="new.py")]
        hs = self._make_hs_with_symbols(base, head)
        diff = hs.diff_snapshots("base", "head")

        assert len(diff["moved"]) == 1
        assert diff["moved"][0]["name"] == "foo"
        assert diff["moved"][0]["from"] == "old.py"
        assert diff["moved"][0]["to"] == "new.py"

    def test_no_changes(self):
        syms = [SnapshotEntry(ref="v1", name="foo", label="Function", file_path="a.py")]
        hs = self._make_hs_with_symbols(syms, syms)
        diff = hs.diff_snapshots("base", "head")

        assert diff["added"] == []
        assert diff["removed"] == []
        assert diff["moved"] == []
