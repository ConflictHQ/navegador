"""Tests for navegador.diff — DiffAnalyzer and the CLI 'diff' command."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from navegador.cli.commands import main
from navegador.diff import (
    DiffAnalyzer,
    _lines_overlap,
    _parse_unified_diff_hunks,
)

# ── Helpers ────────────────────────────────────────────────────────────────────


def _mock_store(result_set: list | None = None):
    """Return a MagicMock GraphStore whose .query() yields *result_set*."""
    store = MagicMock()
    store.query.return_value = MagicMock(result_set=result_set or [])
    return store


def _analyzer(store=None, repo_path: Path | None = None, changed: list[str] | None = None):
    """Build a DiffAnalyzer with the given store, patching GitAdapter.changed_files."""
    if store is None:
        store = _mock_store()
    if repo_path is None:
        repo_path = Path("/fake/repo")
    analyzer = DiffAnalyzer(store, repo_path)
    if changed is not None:
        analyzer._git = MagicMock()
        analyzer._git.changed_files.return_value = changed
    return analyzer


# ── _parse_unified_diff_hunks ─────────────────────────────────────────────────


class TestParseUnifiedDiffHunks:
    SAMPLE_DIFF = """\
diff --git a/foo.py b/foo.py
index 0000000..1111111 100644
--- a/foo.py
+++ b/foo.py
@@ -10,3 +10,5 @@
 unchanged
+added line 1
+added line 2
diff --git a/bar.py b/bar.py
index 0000000..2222222 100644
--- a/bar.py
+++ b/bar.py
@@ -5 +5,2 @@
-old line
+new line A
+new line B
"""

    def test_returns_dict(self):
        result = _parse_unified_diff_hunks(self.SAMPLE_DIFF)
        assert isinstance(result, dict)

    def test_detects_both_files(self):
        result = _parse_unified_diff_hunks(self.SAMPLE_DIFF)
        assert "foo.py" in result
        assert "bar.py" in result

    def test_correct_range_for_foo(self):
        result = _parse_unified_diff_hunks(self.SAMPLE_DIFF)
        # hunk: +10,5 → start=10, end=14
        ranges = result["foo.py"]
        assert len(ranges) == 1
        start, end = ranges[0]
        assert start == 10
        assert end == 14  # 10 + 5 - 1

    def test_correct_range_for_bar(self):
        result = _parse_unified_diff_hunks(self.SAMPLE_DIFF)
        # hunk: +5,2 → start=5, end=6
        ranges = result["bar.py"]
        assert len(ranges) == 1
        start, end = ranges[0]
        assert start == 5
        assert end == 6

    def test_empty_diff_returns_empty_dict(self):
        result = _parse_unified_diff_hunks("")
        assert result == {}

    def test_deleted_file_not_included(self):
        diff = """\
--- a/deleted.py
+++ /dev/null
@@ -1 +0,0 @@
-old
"""
        result = _parse_unified_diff_hunks(diff)
        assert "deleted.py" not in result

    def test_multiple_hunks_same_file(self):
        diff = """\
diff --git a/multi.py b/multi.py
--- a/multi.py
+++ b/multi.py
@@ -1,2 +1,3 @@
+first
 unchanged
+second
@@ -20 +21,2 @@
-old
+new1
+new2
"""
        result = _parse_unified_diff_hunks(diff)
        assert "multi.py" in result
        assert len(result["multi.py"]) == 2


# ── _lines_overlap ─────────────────────────────────────────────────────────────


class TestLinesOverlap:
    def test_exact_overlap(self):
        assert _lines_overlap([(10, 20)], 10, 20) is True

    def test_symbol_inside_range(self):
        assert _lines_overlap([(5, 30)], 10, 15) is True

    def test_range_inside_symbol(self):
        assert _lines_overlap([(12, 14)], 10, 20) is True

    def test_no_overlap_before(self):
        assert _lines_overlap([(20, 30)], 5, 10) is False

    def test_no_overlap_after(self):
        assert _lines_overlap([(1, 5)], 10, 20) is False

    def test_adjacent_not_overlapping(self):
        assert _lines_overlap([(1, 9)], 10, 20) is False

    def test_none_line_start_returns_false(self):
        assert _lines_overlap([(1, 100)], None, None) is False

    def test_no_line_end_uses_start(self):
        # line_end=None → treated as single-line symbol
        assert _lines_overlap([(10, 20)], 15, None) is True

    def test_empty_ranges_returns_false(self):
        assert _lines_overlap([], 10, 20) is False

    def test_multiple_ranges_one_hits(self):
        assert _lines_overlap([(1, 5), (50, 60)], 52, 55) is True


# ── DiffAnalyzer.changed_files ────────────────────────────────────────────────


class TestDiffAnalyzerChangedFiles:
    def test_delegates_to_git_adapter(self):
        analyzer = _analyzer(changed=["a.py", "b.py"])
        assert analyzer.changed_files() == ["a.py", "b.py"]

    def test_empty_when_no_changes(self):
        analyzer = _analyzer(changed=[])
        assert analyzer.changed_files() == []

    def test_returns_list(self):
        analyzer = _analyzer(changed=["x.py"])
        assert isinstance(analyzer.changed_files(), list)

    def test_uses_subprocess_via_git_adapter(self, tmp_path):
        """Verify changed_files() relies on subprocess (through GitAdapter._run)."""
        repo = tmp_path / "repo"
        repo.mkdir()
        store = _mock_store()
        analyzer = DiffAnalyzer(store, repo)

        fake_result = MagicMock()
        fake_result.stdout = "changed.py\n"
        fake_result.returncode = 0

        with patch("subprocess.run", return_value=fake_result):
            files = analyzer.changed_files()

        assert "changed.py" in files


# ── DiffAnalyzer.changed_lines ────────────────────────────────────────────────


class TestDiffAnalyzerChangedLines:
    def test_returns_dict(self, tmp_path):
        analyzer = _analyzer(changed=["f.py"])
        fake = MagicMock()
        fake.returncode = 0
        fake.stdout = "+++ b/f.py\n@@ -1 +1,3 @@\n+a\n+b\n+c\n"
        with patch("subprocess.run", return_value=fake):
            result = analyzer.changed_lines()
        assert isinstance(result, dict)

    def test_fallback_on_no_output(self):
        """No diff output → full-file sentinel range for each changed file."""
        analyzer = _analyzer(changed=["x.py", "y.py"])
        fake = MagicMock()
        fake.returncode = 0
        fake.stdout = ""
        with patch("subprocess.run", return_value=fake):
            result = analyzer.changed_lines()
        assert "x.py" in result
        assert "y.py" in result
        assert result["x.py"] == [(1, 999_999)]

    def test_fallback_on_nonzero_exit(self):
        """Non-zero exit (e.g. no HEAD) → full-file sentinel for all changed files."""
        analyzer = _analyzer(changed=["z.py"])
        fake = MagicMock()
        fake.returncode = 128
        fake.stdout = ""
        with patch("subprocess.run", return_value=fake):
            result = analyzer.changed_lines()
        assert result["z.py"] == [(1, 999_999)]

    def test_missing_files_get_sentinel(self):
        """Files in changed_files() but absent from diff get sentinel range."""
        analyzer = _analyzer(changed=["in_diff.py", "not_in_diff.py"])
        fake = MagicMock()
        fake.returncode = 0
        fake.stdout = "+++ b/in_diff.py\n@@ -5 +5,2 @@ \n+x\n+y\n"
        with patch("subprocess.run", return_value=fake):
            result = analyzer.changed_lines()
        assert "not_in_diff.py" in result
        assert result["not_in_diff.py"] == [(1, 999_999)]


# ── DiffAnalyzer.affected_symbols ─────────────────────────────────────────────


class TestDiffAnalyzerAffectedSymbols:
    def _sym_rows(self):
        """Return fake graph rows: (type, name, file_path, line_start, line_end)."""
        return [
            ("Function", "do_thing", "app.py", 10, 25),
            ("Class", "MyClass", "app.py", 30, 80),
            ("Method", "helper", "utils.py", 5, 15),
        ]

    def test_returns_list(self):
        store = _mock_store(result_set=self._sym_rows())
        analyzer = _analyzer(store=store, changed=["app.py"])
        with patch.object(analyzer, "changed_lines", return_value={"app.py": [(1, 999_999)]}):
            result = analyzer.affected_symbols()
        assert isinstance(result, list)

    def test_symbols_overlap_returned(self):
        store = _mock_store(result_set=self._sym_rows())
        analyzer = _analyzer(store=store, changed=["app.py"])
        # Changed lines 15-20 overlap do_thing (10-25)
        with patch.object(analyzer, "changed_lines", return_value={"app.py": [(15, 20)]}):
            result = analyzer.affected_symbols()
        names = [s["name"] for s in result]
        assert "do_thing" in names

    def test_non_overlapping_symbols_excluded(self):
        store = _mock_store(result_set=self._sym_rows())
        analyzer = _analyzer(store=store, changed=["app.py"])
        # Changed lines 50-60 overlap MyClass (30-80) but not do_thing (10-25)
        with patch.object(analyzer, "changed_lines", return_value={"app.py": [(50, 60)]}):
            result = analyzer.affected_symbols()
        names = [s["name"] for s in result]
        assert "MyClass" in names
        assert "do_thing" not in names

    def test_empty_when_no_graph_nodes(self):
        store = _mock_store(result_set=[])
        analyzer = _analyzer(store=store, changed=["app.py"])
        with patch.object(analyzer, "changed_lines", return_value={"app.py": [(1, 50)]}):
            result = analyzer.affected_symbols()
        assert result == []

    def test_empty_when_no_changed_files(self):
        store = _mock_store(result_set=self._sym_rows())
        analyzer = _analyzer(store=store, changed=[])
        with patch.object(analyzer, "changed_lines", return_value={}):
            result = analyzer.affected_symbols()
        assert result == []

    def test_symbol_dict_has_required_keys(self):
        store = _mock_store(result_set=[("Function", "foo", "a.py", 1, 10)])
        analyzer = _analyzer(store=store, changed=["a.py"])
        with patch.object(analyzer, "changed_lines", return_value={"a.py": [(1, 10)]}):
            result = analyzer.affected_symbols()
        assert len(result) == 1
        sym = result[0]
        assert "type" in sym
        assert "name" in sym
        assert "file_path" in sym
        assert "line_start" in sym
        assert "line_end" in sym

    def test_no_duplicate_symbols(self):
        """Same symbol matched by two hunk ranges must appear only once."""
        rows = [("Function", "foo", "a.py", 5, 20)]
        store = _mock_store(result_set=rows)
        analyzer = _analyzer(store=store, changed=["a.py"])
        with patch.object(analyzer, "changed_lines", return_value={"a.py": [(5, 10), (15, 20)]}):
            result = analyzer.affected_symbols()
        assert len(result) == 1


# ── DiffAnalyzer.affected_knowledge ───────────────────────────────────────────


class TestDiffAnalyzerAffectedKnowledge:
    def _k_rows(self):
        """Fake knowledge rows: (type, name, description, domain, status)."""
        return [
            ("Concept", "Billing", "Handles money", "finance", "stable"),
            ("Rule", "no_refund_after_30d", "30 day rule", "finance", "active"),
        ]

    def test_returns_list(self):
        store = _mock_store(result_set=self._k_rows())
        analyzer = _analyzer(store=store)
        sym = [{"name": "charge", "file_path": "billing.py"}]
        with patch.object(analyzer, "affected_symbols", return_value=sym):
            result = analyzer.affected_knowledge()
        assert isinstance(result, list)

    def test_knowledge_nodes_returned(self):
        store = _mock_store(result_set=self._k_rows())
        analyzer = _analyzer(store=store)
        sym = [{"name": "charge", "file_path": "billing.py"}]
        with patch.object(analyzer, "affected_symbols", return_value=sym):
            result = analyzer.affected_knowledge()
        names = [k["name"] for k in result]
        assert "Billing" in names
        assert "no_refund_after_30d" in names

    def test_empty_when_no_symbols(self):
        store = _mock_store(result_set=self._k_rows())
        analyzer = _analyzer(store=store)
        with patch.object(analyzer, "affected_symbols", return_value=[]):
            result = analyzer.affected_knowledge()
        assert result == []

    def test_empty_when_no_graph_knowledge(self):
        store = _mock_store(result_set=[])
        analyzer = _analyzer(store=store)
        sym = [{"name": "foo", "file_path": "a.py"}]
        with patch.object(analyzer, "affected_symbols", return_value=sym):
            result = analyzer.affected_knowledge()
        assert result == []

    def test_no_duplicate_knowledge_nodes(self):
        """Two symbols linking to the same knowledge node → deduplicated."""
        rows = [("Concept", "SharedConcept", "desc", "core", "stable")]
        store = _mock_store(result_set=rows)
        analyzer = _analyzer(store=store)
        syms = [
            {"name": "alpha", "file_path": "a.py"},
            {"name": "beta", "file_path": "b.py"},
        ]
        with patch.object(analyzer, "affected_symbols", return_value=syms):
            result = analyzer.affected_knowledge()
        assert len([k for k in result if k["name"] == "SharedConcept"]) == 1

    def test_knowledge_dict_has_required_keys(self):
        rows = [("Rule", "my_rule", "some desc", "payments", "")]
        store = _mock_store(result_set=rows)
        analyzer = _analyzer(store=store)
        sym = [{"name": "process", "file_path": "pay.py"}]
        with patch.object(analyzer, "affected_symbols", return_value=sym):
            result = analyzer.affected_knowledge()
        assert len(result) == 1
        k = result[0]
        assert "type" in k
        assert "name" in k
        assert "description" in k
        assert "domain" in k
        assert "status" in k


# ── DiffAnalyzer.impact_summary ───────────────────────────────────────────────


class TestDiffAnalyzerImpactSummary:
    def _build(self, files=None, symbols=None, knowledge=None):
        store = _mock_store()
        analyzer = _analyzer(store=store)
        with (
            patch.object(analyzer, "changed_files", return_value=files or []),
            patch.object(analyzer, "affected_symbols", return_value=symbols or []),
            patch.object(analyzer, "affected_knowledge", return_value=knowledge or []),
        ):
            return analyzer.impact_summary()

    def test_returns_dict(self):
        result = self._build()
        assert isinstance(result, dict)

    def test_has_all_top_level_keys(self):
        result = self._build()
        assert "files" in result
        assert "symbols" in result
        assert "knowledge" in result
        assert "counts" in result

    def test_counts_match_lengths(self):
        files = ["a.py", "b.py"]
        symbols = [{"type": "Function", "name": "f", "file_path": "a.py",
                    "line_start": 1, "line_end": 5}]
        knowledge = [{"type": "Concept", "name": "X", "description": "",
                      "domain": "", "status": ""}]
        result = self._build(files=files, symbols=symbols, knowledge=knowledge)
        assert result["counts"]["files"] == 2
        assert result["counts"]["symbols"] == 1
        assert result["counts"]["knowledge"] == 1

    def test_empty_summary_all_zeros(self):
        result = self._build()
        assert result["counts"]["files"] == 0
        assert result["counts"]["symbols"] == 0
        assert result["counts"]["knowledge"] == 0

    def test_files_list_propagated(self):
        result = self._build(files=["x.py", "y.py"])
        assert result["files"] == ["x.py", "y.py"]


# ── DiffAnalyzer.to_json ──────────────────────────────────────────────────────


class TestDiffAnalyzerToJson:
    def test_returns_valid_json(self):
        store = _mock_store()
        analyzer = _analyzer(store=store)
        summary = {"files": [], "symbols": [], "knowledge": [], "counts": {"files": 0}}
        with patch.object(analyzer, "impact_summary", return_value=summary):
            output = analyzer.to_json()
        parsed = json.loads(output)
        assert isinstance(parsed, dict)

    def test_json_contains_summary_keys(self):
        store = _mock_store()
        analyzer = _analyzer(store=store)
        summary = {"files": ["f.py"], "symbols": [], "knowledge": [], "counts": {"files": 1}}
        with patch.object(analyzer, "impact_summary", return_value=summary):
            output = analyzer.to_json()
        parsed = json.loads(output)
        assert "files" in parsed


# ── DiffAnalyzer.to_markdown ──────────────────────────────────────────────────


class TestDiffAnalyzerToMarkdown:
    def _md(self, files=None, symbols=None, knowledge=None):
        store = _mock_store()
        analyzer = _analyzer(store=store)
        summary = {
            "files": files or [],
            "symbols": symbols or [],
            "knowledge": knowledge or [],
            "counts": {
                "files": len(files or []),
                "symbols": len(symbols or []),
                "knowledge": len(knowledge or []),
            },
        }
        with patch.object(analyzer, "impact_summary", return_value=summary):
            return analyzer.to_markdown()

    def test_returns_string(self):
        assert isinstance(self._md(), str)

    def test_contains_heading(self):
        assert "Diff Impact Summary" in self._md()

    def test_lists_changed_file(self):
        md = self._md(files=["src/main.py"])
        assert "src/main.py" in md

    def test_lists_affected_symbol(self):
        syms = [{"type": "Function", "name": "pay", "file_path": "billing.py",
                 "line_start": 10, "line_end": 20}]
        md = self._md(symbols=syms)
        assert "pay" in md

    def test_lists_knowledge_node(self):
        know = [{"type": "Rule", "name": "no_double_charge",
                 "description": "desc", "domain": "", "status": ""}]
        md = self._md(knowledge=know)
        assert "no_double_charge" in md

    def test_empty_sections_show_placeholder(self):
        md = self._md()
        assert "No changed files" in md
        assert "No affected symbols" in md
        assert "No linked knowledge" in md


# ── CLI: navegador diff ────────────────────────────────────────────────────────


class TestCLIDiffCommand:
    def _runner(self):
        return CliRunner()

    def _mock_analyzer(self, summary=None):
        """Patch DiffAnalyzer so it never touches git or the graph."""
        if summary is None:
            summary = {
                "files": ["app.py"],
                "symbols": [{"type": "Function", "name": "run",
                              "file_path": "app.py", "line_start": 1, "line_end": 10}],
                "knowledge": [],
                "counts": {"files": 1, "symbols": 1, "knowledge": 0},
            }
        mock_inst = MagicMock()
        mock_inst.impact_summary.return_value = summary
        mock_inst.to_json.return_value = json.dumps(summary, indent=2)
        mock_inst.to_markdown.return_value = "# Diff Impact Summary\n\n## Changed Files (1)"
        return mock_inst

    def test_command_exists(self):
        runner = self._runner()
        result = runner.invoke(main, ["diff", "--help"])
        assert result.exit_code == 0

    def test_markdown_output_by_default(self, tmp_path):
        runner = self._runner()
        mock_inst = self._mock_analyzer()
        with (
            runner.isolated_filesystem(),
            patch("navegador.cli.commands._get_store", return_value=_mock_store()),
            patch("navegador.diff.DiffAnalyzer", return_value=mock_inst),
        ):
            result = runner.invoke(main, ["diff", "--repo", str(tmp_path)])
        assert result.exit_code == 0
        assert "Diff Impact Summary" in result.output

    def test_json_output_flag(self, tmp_path):
        runner = self._runner()
        mock_inst = self._mock_analyzer()
        with (
            runner.isolated_filesystem(),
            patch("navegador.cli.commands._get_store", return_value=_mock_store()),
            patch("navegador.diff.DiffAnalyzer", return_value=mock_inst),
        ):
            result = runner.invoke(main, ["diff", "--format", "json", "--repo", str(tmp_path)])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert "files" in parsed

    def test_json_is_valid(self, tmp_path):
        runner = self._runner()
        summary = {
            "files": ["x.py"],
            "symbols": [],
            "knowledge": [],
            "counts": {"files": 1, "symbols": 0, "knowledge": 0},
        }
        mock_inst = self._mock_analyzer(summary=summary)
        with (
            runner.isolated_filesystem(),
            patch("navegador.cli.commands._get_store", return_value=_mock_store()),
            patch("navegador.diff.DiffAnalyzer", return_value=mock_inst),
        ):
            result = runner.invoke(main, ["diff", "--format", "json", "--repo", str(tmp_path)])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["files"] == ["x.py"]
        assert data["counts"]["files"] == 1
