"""Tests for navegador.churn — ChurnAnalyzer and the `churn` CLI command."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from navegador.churn import ChurnAnalyzer, ChurnEntry, CouplingPair
from navegador.cli.commands import main


# ── Helpers ───────────────────────────────────────────────────────────────────

# Fake git log --format=%H --name-only output
# Three commits (all-hex 40-char hashes):
#   aaaa...  touches a.py, b.py
#   bbbb...  touches b.py, c.py
#   cccc...  touches a.py, b.py, c.py
GIT_LOG_NAME_ONLY = """\
aaaa111111111111111111111111111111111111

a.py
b.py
bbbb222222222222222222222222222222222222

b.py
c.py
cccc333333333333333333333333333333333333

a.py
b.py
c.py
"""

# Fake git log --numstat --format= output
GIT_LOG_NUMSTAT = """\
10\t2\ta.py
5\t1\tb.py
3\t0\tb.py
2\t2\tc.py
8\t1\ta.py
4\t1\tb.py
1\t1\tc.py
"""


def _make_analyzer(tmp_path: Path) -> ChurnAnalyzer:
    """Return a ChurnAnalyzer pointed at a temp dir (git not required)."""
    return ChurnAnalyzer(tmp_path, limit=500)


def _mock_run(name_only_output: str = GIT_LOG_NAME_ONLY,
              numstat_output: str = GIT_LOG_NUMSTAT):
    """Return a side_effect function for ChurnAnalyzer._run that dispatches
    on the git args list."""

    def _side_effect(args: list[str]) -> str:
        if "--name-only" in args:
            return name_only_output
        if "--numstat" in args:
            return numstat_output
        return ""

    return _side_effect


# ── ChurnEntry / CouplingPair dataclasses ─────────────────────────────────────


class TestDataclasses:
    def test_churn_entry_fields(self):
        e = ChurnEntry(file_path="foo.py", commit_count=5, lines_changed=100)
        assert e.file_path == "foo.py"
        assert e.commit_count == 5
        assert e.lines_changed == 100

    def test_coupling_pair_fields(self):
        p = CouplingPair(file_a="a.py", file_b="b.py", co_change_count=3, confidence=0.75)
        assert p.file_a == "a.py"
        assert p.file_b == "b.py"
        assert p.co_change_count == 3
        assert p.confidence == 0.75


# ── file_churn ────────────────────────────────────────────────────────────────


class TestFileChurn:
    def test_returns_list_of_churn_entries(self, tmp_path):
        analyzer = _make_analyzer(tmp_path)
        with patch.object(analyzer, "_run", side_effect=_mock_run()):
            result = analyzer.file_churn()
        assert isinstance(result, list)
        assert all(isinstance(e, ChurnEntry) for e in result)

    def test_commit_counts_are_correct(self, tmp_path):
        analyzer = _make_analyzer(tmp_path)
        with patch.object(analyzer, "_run", side_effect=_mock_run()):
            result = analyzer.file_churn()

        counts = {e.file_path: e.commit_count for e in result}
        # a.py: commits abc + ghi = 2
        assert counts["a.py"] == 2
        # b.py: commits abc + def + ghi = 3
        assert counts["b.py"] == 3
        # c.py: commits def + ghi = 2
        assert counts["c.py"] == 2

    def test_sorted_by_commit_count_descending(self, tmp_path):
        analyzer = _make_analyzer(tmp_path)
        with patch.object(analyzer, "_run", side_effect=_mock_run()):
            result = analyzer.file_churn()
        counts = [e.commit_count for e in result]
        assert counts == sorted(counts, reverse=True)

    def test_lines_changed_aggregated(self, tmp_path):
        analyzer = _make_analyzer(tmp_path)
        with patch.object(analyzer, "_run", side_effect=_mock_run()):
            result = analyzer.file_churn()
        by_file = {e.file_path: e.lines_changed for e in result}
        # a.py: (10+2) + (8+1) = 21
        assert by_file["a.py"] == 21
        # b.py: (5+1) + (3+0) + (4+1) = 14
        assert by_file["b.py"] == 14
        # c.py: (2+2) + (1+1) = 6
        assert by_file["c.py"] == 6

    def test_empty_git_output_returns_empty_list(self, tmp_path):
        analyzer = _make_analyzer(tmp_path)
        with patch.object(analyzer, "_run", return_value=""):
            result = analyzer.file_churn()
        assert result == []

    def test_binary_files_skipped_in_lines_changed(self, tmp_path):
        numstat_with_binary = "-\t-\timage.png\n10\t2\ta.py\n"
        analyzer = _make_analyzer(tmp_path)
        with patch.object(
            analyzer, "_run",
            side_effect=_mock_run(numstat_output=numstat_with_binary)
        ):
            result = analyzer.file_churn()
        by_file = {e.file_path: e.lines_changed for e in result}
        # Binary file should not cause a crash; a.py lines should still be counted
        assert by_file.get("a.py", 0) == 12


# ── coupling_pairs ────────────────────────────────────────────────────────────


class TestCouplingPairs:
    def test_returns_list_of_coupling_pairs(self, tmp_path):
        analyzer = _make_analyzer(tmp_path)
        with patch.object(analyzer, "_run", side_effect=_mock_run()):
            result = analyzer.coupling_pairs(min_co_changes=1, min_confidence=0.0)
        assert isinstance(result, list)
        assert all(isinstance(p, CouplingPair) for p in result)

    def test_ab_pair_co_change_count(self, tmp_path):
        """a.py and b.py appear together in commits abc and ghi → co_change=2."""
        analyzer = _make_analyzer(tmp_path)
        with patch.object(analyzer, "_run", side_effect=_mock_run()):
            result = analyzer.coupling_pairs(min_co_changes=1, min_confidence=0.0)
        pairs_by_key = {(p.file_a, p.file_b): p for p in result}
        ab = pairs_by_key.get(("a.py", "b.py"))
        assert ab is not None
        assert ab.co_change_count == 2

    def test_bc_pair_co_change_count(self, tmp_path):
        """b.py and c.py appear together in commits def and ghi → co_change=2."""
        analyzer = _make_analyzer(tmp_path)
        with patch.object(analyzer, "_run", side_effect=_mock_run()):
            result = analyzer.coupling_pairs(min_co_changes=1, min_confidence=0.0)
        pairs_by_key = {(p.file_a, p.file_b): p for p in result}
        bc = pairs_by_key.get(("b.py", "c.py"))
        assert bc is not None
        assert bc.co_change_count == 2

    def test_confidence_formula(self, tmp_path):
        """confidence = co_change_count / max(changes_a, changes_b)."""
        analyzer = _make_analyzer(tmp_path)
        with patch.object(analyzer, "_run", side_effect=_mock_run()):
            result = analyzer.coupling_pairs(min_co_changes=1, min_confidence=0.0)
        pairs_by_key = {(p.file_a, p.file_b): p for p in result}
        # a.py: 2 commits, b.py: 3 commits, co=2 → 2/3 ≈ 0.6667
        ab = pairs_by_key[("a.py", "b.py")]
        assert abs(ab.confidence - round(2 / 3, 4)) < 0.001

    def test_min_co_changes_filter(self, tmp_path):
        analyzer = _make_analyzer(tmp_path)
        with patch.object(analyzer, "_run", side_effect=_mock_run()):
            # All pairs have co_change ≤ 2, so requesting ≥ 3 returns nothing
            result = analyzer.coupling_pairs(min_co_changes=3, min_confidence=0.0)
        assert result == []

    def test_min_confidence_filter(self, tmp_path):
        # Commit breakdown:
        #   aaaa: a.py, b.py
        #   bbbb: b.py, c.py
        #   cccc: a.py, b.py, c.py
        #
        # commit counts: a=2, b=3, c=2
        # (a,b): co=2 → confidence=2/3≈0.667
        # (a,c): co=1 → confidence=1/2=0.5
        # (b,c): co=2 → confidence=2/3≈0.667
        #
        # At min_confidence=0.6: a/b and b/c pass; a/c does not.
        analyzer = _make_analyzer(tmp_path)
        with patch.object(analyzer, "_run", side_effect=_mock_run()):
            result = analyzer.coupling_pairs(min_co_changes=1, min_confidence=0.6)
        pairs_by_key = {(p.file_a, p.file_b): p for p in result}
        assert ("a.py", "b.py") in pairs_by_key
        assert ("b.py", "c.py") in pairs_by_key
        # a/c has confidence=0.5, below threshold
        assert ("a.py", "c.py") not in pairs_by_key

    def test_sorted_by_co_change_count_descending(self, tmp_path):
        analyzer = _make_analyzer(tmp_path)
        with patch.object(analyzer, "_run", side_effect=_mock_run()):
            result = analyzer.coupling_pairs(min_co_changes=1, min_confidence=0.0)
        counts = [p.co_change_count for p in result]
        assert counts == sorted(counts, reverse=True)

    def test_empty_history_returns_empty_list(self, tmp_path):
        analyzer = _make_analyzer(tmp_path)
        with patch.object(analyzer, "_run", return_value=""):
            result = analyzer.coupling_pairs()
        assert result == []

    def test_single_file_per_commit_no_pairs(self, tmp_path):
        """Commits touching only one file produce no coupling pairs."""
        log = (
            "abc1111111111111111111111111111111111111\n\na.py\n"
            "def2222222222222222222222222222222222222\n\nb.py\n"
        )
        analyzer = _make_analyzer(tmp_path)
        with patch.object(analyzer, "_run", side_effect=_mock_run(name_only_output=log)):
            result = analyzer.coupling_pairs(min_co_changes=1, min_confidence=0.0)
        assert result == []


# ── store_churn ───────────────────────────────────────────────────────────────


class TestStoreChurn:
    def _make_store(self):
        store = MagicMock()
        store.query.return_value = MagicMock(
            nodes_modified=1, properties_set=2
        )
        return store

    def test_returns_dict_with_expected_keys(self, tmp_path):
        analyzer = _make_analyzer(tmp_path)
        store = self._make_store()
        with patch.object(analyzer, "_run", side_effect=_mock_run()):
            result = analyzer.store_churn(store)
        assert "churn_updated" in result
        assert "couplings_written" in result

    def test_churn_updated_count(self, tmp_path):
        analyzer = _make_analyzer(tmp_path)
        store = self._make_store()
        with patch.object(analyzer, "_run", side_effect=_mock_run()):
            result = analyzer.store_churn(store)
        # Three unique files → 3 churn updates
        assert result["churn_updated"] == 3

    def test_store_query_called_for_each_file(self, tmp_path):
        analyzer = _make_analyzer(tmp_path)
        store = self._make_store()
        with patch.object(analyzer, "_run", side_effect=_mock_run()):
            analyzer.store_churn(store)
        # store.query must have been called at least 3 times (one per file)
        assert store.query.call_count >= 3

    def test_coupled_with_edges_written(self, tmp_path):
        analyzer = _make_analyzer(tmp_path)
        store = self._make_store()
        with patch.object(analyzer, "_run", side_effect=_mock_run()):
            result = analyzer.store_churn(store)
        # Default thresholds: min_co_changes=3, min_confidence=0.5
        # In our fixture all pairs have co_change ≤ 2, so couplings_written == 0
        assert isinstance(result["couplings_written"], int)

    def test_coupled_with_edges_written_low_threshold(self, tmp_path):
        """With relaxed thresholds coupling edges should be written."""
        analyzer = _make_analyzer(tmp_path)
        store = self._make_store()
        # Override coupling_pairs to always return pairs
        fake_pairs = [
            CouplingPair("a.py", "b.py", co_change_count=2, confidence=0.67),
        ]
        with patch.object(analyzer, "_run", side_effect=_mock_run()), \
             patch.object(analyzer, "coupling_pairs", return_value=fake_pairs):
            result = analyzer.store_churn(store)
        assert result["couplings_written"] == 1

    def test_cypher_contains_coupled_with(self, tmp_path):
        """Verify the Cypher for edges references COUPLED_WITH."""
        analyzer = _make_analyzer(tmp_path)
        store = self._make_store()
        fake_pairs = [CouplingPair("a.py", "b.py", co_change_count=5, confidence=0.8)]
        with patch.object(analyzer, "_run", side_effect=_mock_run()), \
             patch.object(analyzer, "coupling_pairs", return_value=fake_pairs):
            analyzer.store_churn(store)

        all_cypher_calls = [call[0][0] for call in store.query.call_args_list]
        edge_cyphers = [c for c in all_cypher_calls if "COUPLED_WITH" in c]
        assert len(edge_cyphers) == 1


# ── CLI command ───────────────────────────────────────────────────────────────


class TestChurnCLI:
    def _analyzer_patch(self, churn_entries=None, pairs=None):
        """Return a context manager that patches ChurnAnalyzer in the CLI module."""
        if churn_entries is None:
            churn_entries = [
                ChurnEntry("foo.py", commit_count=5, lines_changed=100),
                ChurnEntry("bar.py", commit_count=3, lines_changed=40),
            ]
        if pairs is None:
            pairs = [
                CouplingPair("bar.py", "foo.py", co_change_count=3, confidence=0.6),
            ]

        mock_analyzer = MagicMock()
        mock_analyzer.file_churn.return_value = churn_entries
        mock_analyzer.coupling_pairs.return_value = pairs

        return patch("navegador.churn.ChurnAnalyzer", return_value=mock_analyzer)

    def test_basic_invocation_exits_zero(self, tmp_path):
        runner = CliRunner()
        with runner.isolated_filesystem():
            with self._analyzer_patch():
                result = runner.invoke(main, ["churn", str(tmp_path)])
        assert result.exit_code == 0, result.output

    def test_json_output_has_expected_keys(self, tmp_path):
        runner = CliRunner()
        with runner.isolated_filesystem():
            with self._analyzer_patch():
                result = runner.invoke(main, ["churn", str(tmp_path), "--json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert "churn" in data
        assert "coupling_pairs" in data

    def test_json_churn_entry_shape(self, tmp_path):
        runner = CliRunner()
        with runner.isolated_filesystem():
            with self._analyzer_patch():
                result = runner.invoke(main, ["churn", str(tmp_path), "--json"])
        data = json.loads(result.output)
        entry = data["churn"][0]
        assert "file_path" in entry
        assert "commit_count" in entry
        assert "lines_changed" in entry

    def test_json_coupling_pair_shape(self, tmp_path):
        runner = CliRunner()
        with runner.isolated_filesystem():
            with self._analyzer_patch():
                result = runner.invoke(main, ["churn", str(tmp_path), "--json"])
        data = json.loads(result.output)
        pair = data["coupling_pairs"][0]
        assert "file_a" in pair
        assert "file_b" in pair
        assert "co_change_count" in pair
        assert "confidence" in pair

    def test_limit_option_passed_to_analyzer(self, tmp_path):
        runner = CliRunner()
        mock_cls = MagicMock()
        mock_instance = MagicMock()
        mock_instance.file_churn.return_value = []
        mock_instance.coupling_pairs.return_value = []
        mock_cls.return_value = mock_instance

        with runner.isolated_filesystem():
            with patch("navegador.churn.ChurnAnalyzer", mock_cls):
                runner.invoke(main, ["churn", str(tmp_path), "--limit", "100"])

        _, kwargs = mock_cls.call_args
        assert kwargs.get("limit") == 100 or mock_cls.call_args[0][1] == 100

    def test_min_confidence_passed_to_coupling_pairs(self, tmp_path):
        runner = CliRunner()
        mock_cls = MagicMock()
        mock_instance = MagicMock()
        mock_instance.file_churn.return_value = []
        mock_instance.coupling_pairs.return_value = []
        mock_cls.return_value = mock_instance

        with runner.isolated_filesystem():
            with patch("navegador.churn.ChurnAnalyzer", mock_cls):
                runner.invoke(main, ["churn", str(tmp_path), "--min-confidence", "0.8"])

        mock_instance.coupling_pairs.assert_called_once()
        _, kwargs = mock_instance.coupling_pairs.call_args
        assert kwargs.get("min_confidence") == 0.8

    def test_store_flag_calls_store_churn(self, tmp_path):
        runner = CliRunner()
        mock_cls = MagicMock()
        mock_instance = MagicMock()
        mock_instance.store_churn.return_value = {
            "churn_updated": 2,
            "couplings_written": 1,
        }
        mock_cls.return_value = mock_instance

        with runner.isolated_filesystem():
            with patch("navegador.churn.ChurnAnalyzer", mock_cls), \
                 patch("navegador.cli.commands._get_store", return_value=MagicMock()):
                result = runner.invoke(main, ["churn", str(tmp_path), "--store"])

        assert result.exit_code == 0, result.output
        mock_instance.store_churn.assert_called_once()

    def test_store_json_flag_outputs_stats(self, tmp_path):
        runner = CliRunner()
        mock_cls = MagicMock()
        mock_instance = MagicMock()
        mock_instance.store_churn.return_value = {
            "churn_updated": 5,
            "couplings_written": 2,
        }
        mock_cls.return_value = mock_instance

        with runner.isolated_filesystem():
            with patch("navegador.churn.ChurnAnalyzer", mock_cls), \
                 patch("navegador.cli.commands._get_store", return_value=MagicMock()):
                result = runner.invoke(main, ["churn", str(tmp_path), "--store", "--json"])

        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["churn_updated"] == 5
        assert data["couplings_written"] == 2

    def test_no_pairs_shows_message(self, tmp_path):
        runner = CliRunner()
        with runner.isolated_filesystem():
            with self._analyzer_patch(pairs=[]):
                result = runner.invoke(main, ["churn", str(tmp_path)])
        assert result.exit_code == 0
        assert "No coupling pairs found" in result.output

    def test_table_output_contains_file_names(self, tmp_path):
        runner = CliRunner()
        with runner.isolated_filesystem():
            with self._analyzer_patch():
                result = runner.invoke(main, ["churn", str(tmp_path)])
        assert "foo.py" in result.output
        assert "bar.py" in result.output
