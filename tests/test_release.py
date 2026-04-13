"""Tests for navegador.analysis.release — ReleaseChecker, ReleaseReport, ReleaseItem."""

import json
from unittest.mock import MagicMock, patch

from navegador.analysis.release import ReleaseChecker, ReleaseItem, ReleaseReport

# ── Helpers ──────────────────────────────────────────────────────────────────


def _item(category="changed_symbol", severity="info", symbol="fn_a", **kw):
    return ReleaseItem(category=category, severity=severity, symbol=symbol, **kw)


def _mock_store():
    store = MagicMock()
    store.query.return_value = MagicMock(result_set=[])
    return store


def _mock_diff_report(affected_files=None, new_symbols=None, changed_symbols=None):
    """Build a mock DiffGraphReport with StructuralChange-like objects."""
    from navegador.analysis.diffgraph import StructuralChange

    report = MagicMock()
    report.affected_files = affected_files or []
    report.new_symbols = [
        StructuralChange(kind="new_symbol", symbol=s[0], file_path=s[1])
        for s in (new_symbols or [])
    ]
    report.changed_symbols = [
        StructuralChange(kind="changed_symbol", symbol=s[0], file_path=s[1])
        for s in (changed_symbols or [])
    ]
    return report


# ── ReleaseReport.passed ─────────────────────────────────────────────────────


class TestReleaseReportPassed:
    def test_passed_when_no_items(self):
        report = ReleaseReport(base_ref="main", head_ref="HEAD")
        assert report.passed is True

    def test_passed_when_only_info_items(self):
        report = ReleaseReport(
            base_ref="main",
            head_ref="HEAD",
            items=[_item(severity="info"), _item(severity="info")],
        )
        assert report.passed is True

    def test_passed_when_only_warnings(self):
        report = ReleaseReport(
            base_ref="main",
            head_ref="HEAD",
            items=[_item(severity="warning"), _item(severity="warning")],
        )
        assert report.passed is True

    def test_not_passed_when_has_errors(self):
        report = ReleaseReport(
            base_ref="main",
            head_ref="HEAD",
            items=[_item(severity="error", symbol="bad_fn")],
        )
        assert report.passed is False

    def test_not_passed_with_mixed_severities(self):
        report = ReleaseReport(
            base_ref="main",
            head_ref="HEAD",
            items=[
                _item(severity="info"),
                _item(severity="warning"),
                _item(severity="error"),
            ],
        )
        assert report.passed is False


# ── ReleaseReport.errors / .warnings properties ─────────────────────────────


class TestReleaseReportFilterProperties:
    def test_errors_returns_only_errors(self):
        report = ReleaseReport(
            base_ref="main",
            head_ref="HEAD",
            items=[
                _item(severity="info", symbol="a"),
                _item(severity="error", symbol="b"),
                _item(severity="warning", symbol="c"),
                _item(severity="error", symbol="d"),
            ],
        )
        assert [e.symbol for e in report.errors] == ["b", "d"]

    def test_warnings_returns_only_warnings(self):
        report = ReleaseReport(
            base_ref="main",
            head_ref="HEAD",
            items=[
                _item(severity="info", symbol="a"),
                _item(severity="warning", symbol="b"),
                _item(severity="error", symbol="c"),
            ],
        )
        assert [w.symbol for w in report.warnings] == ["b"]


# ── ReleaseReport.to_markdown ────────────────────────────────────────────────


class TestReleaseReportToMarkdown:
    def test_includes_pass_status_when_no_errors(self):
        report = ReleaseReport(base_ref="main", head_ref="HEAD")
        md = report.to_markdown()
        assert "PASS" in md
        assert "FAIL" not in md

    def test_includes_fail_status_when_errors(self):
        report = ReleaseReport(
            base_ref="main",
            head_ref="HEAD",
            items=[_item(severity="error", symbol="broken")],
        )
        md = report.to_markdown()
        assert "FAIL" in md

    def test_includes_changed_files_section(self):
        report = ReleaseReport(
            base_ref="main",
            head_ref="HEAD",
            changed_files=["src/auth.py", "src/db.py"],
        )
        md = report.to_markdown()
        assert "Changed Files (2)" in md
        assert "`src/auth.py`" in md
        assert "`src/db.py`" in md

    def test_includes_errors_section(self):
        report = ReleaseReport(
            base_ref="v1.0",
            head_ref="v1.1",
            items=[
                _item(
                    category="missing_test",
                    severity="error",
                    symbol="validate",
                    file_path="auth.py",
                    detail="no tests found",
                    knowledge_node="auth-rule",
                ),
            ],
        )
        md = report.to_markdown()
        assert "Errors (1)" in md
        assert "`validate`" in md
        assert "`auth.py`" in md
        assert "no tests found" in md
        assert "auth-rule" in md

    def test_includes_warnings_section(self):
        report = ReleaseReport(
            base_ref="main",
            head_ref="HEAD",
            items=[
                _item(
                    category="stale_doc",
                    severity="warning",
                    symbol="process",
                    detail="review: API Guide",
                ),
            ],
        )
        md = report.to_markdown()
        assert "Warnings (1)" in md
        assert "`process`" in md
        assert "review: API Guide" in md

    def test_includes_signoffs_section(self):
        report = ReleaseReport(
            base_ref="main",
            head_ref="HEAD",
            owners_required=["Alice", "Bob"],
        )
        md = report.to_markdown()
        assert "Required Sign-offs (2)" in md
        assert "Alice" in md
        assert "Bob" in md

    def test_includes_ref_names(self):
        report = ReleaseReport(base_ref="v2.0", head_ref="release/v2.1")
        md = report.to_markdown()
        assert "`v2.0`" in md
        assert "`release/v2.1`" in md


# ── ReleaseReport.to_json ────────────────────────────────────────────────────


class TestReleaseReportToJson:
    def test_round_trips(self):
        report = ReleaseReport(
            base_ref="main",
            head_ref="HEAD",
            changed_files=["a.py"],
            owners_required=["Alice"],
            items=[
                _item(category="missing_test", severity="warning", symbol="fn_x"),
            ],
        )
        data = json.loads(report.to_json())
        assert data["base_ref"] == "main"
        assert data["head_ref"] == "HEAD"
        assert data["passed"] is True
        assert data["changed_files"] == ["a.py"]
        assert data["owners_required"] == ["Alice"]
        assert len(data["items"]) == 1
        assert data["items"][0]["category"] == "missing_test"
        assert data["summary"]["warnings"] == 1

    def test_to_dict_matches_to_json(self):
        report = ReleaseReport(
            base_ref="main",
            head_ref="HEAD",
            items=[_item(severity="error")],
        )
        assert json.loads(report.to_json()) == report.to_dict()


# ── ReleaseChecker.check — changed files ─────────────────────────────────────


class TestReleaseCheckerChangedFiles:
    @patch("navegador.analysis.diffgraph.DiffGraphAnalyzer")
    def test_changed_files_appear_in_report(self, MockAnalyzer):
        diff_report = _mock_diff_report(affected_files=["src/auth.py", "src/db.py"])
        MockAnalyzer.return_value.diff_refs.return_value = diff_report

        store = _mock_store()
        checker = ReleaseChecker(store, repo_path="/tmp/repo")
        report = checker.check(base="main", head="HEAD")

        assert report.changed_files == ["src/auth.py", "src/db.py"]


# ── ReleaseChecker.check — changed symbols ──────────────────────────────────


class TestReleaseCheckerChangedSymbols:
    @patch("navegador.analysis.diffgraph.DiffGraphAnalyzer")
    def test_changed_symbols_produce_info_items(self, MockAnalyzer):
        diff_report = _mock_diff_report(
            changed_symbols=[("validate_token", "auth.py"), ("connect", "db.py")]
        )
        MockAnalyzer.return_value.diff_refs.return_value = diff_report

        store = _mock_store()
        checker = ReleaseChecker(store, repo_path="/tmp/repo")
        report = checker.check()

        changed_items = [i for i in report.items if i.category == "changed_symbol"]
        assert len(changed_items) == 2
        assert all(i.severity == "info" for i in changed_items)
        symbols = {i.symbol for i in changed_items}
        assert symbols == {"validate_token", "connect"}


# ── ReleaseChecker.check — missing tests ────────────────────────────────────


class TestReleaseCheckerMissingTests:
    @patch("navegador.analysis.diffgraph.DiffGraphAnalyzer")
    def test_symbols_with_no_tests_produce_warnings(self, MockAnalyzer):
        diff_report = _mock_diff_report(changed_symbols=[("untested_fn", "core.py")])
        MockAnalyzer.return_value.diff_refs.return_value = diff_report

        store = _mock_store()
        # First query: test coverage returns count=0
        store.query.return_value = MagicMock(result_set=[[0]])

        checker = ReleaseChecker(store, repo_path="/tmp/repo")
        report = checker.check()

        missing = [i for i in report.items if i.category == "missing_test"]
        assert len(missing) == 1
        assert missing[0].severity == "warning"
        assert missing[0].symbol == "untested_fn"
        assert missing[0].detail == "no tests found"

    @patch("navegador.analysis.diffgraph.DiffGraphAnalyzer")
    def test_symbols_with_tests_no_warning(self, MockAnalyzer):
        diff_report = _mock_diff_report(changed_symbols=[("tested_fn", "core.py")])
        MockAnalyzer.return_value.diff_refs.return_value = diff_report

        store = _mock_store()
        # First query: test coverage returns count=5
        store.query.return_value = MagicMock(result_set=[[5]])

        checker = ReleaseChecker(store, repo_path="/tmp/repo")
        report = checker.check()

        missing = [i for i in report.items if i.category == "missing_test"]
        assert len(missing) == 0


# ── ReleaseChecker.check — stale docs ───────────────────────────────────────


class TestReleaseCheckerStaleDocs:
    @patch("navegador.analysis.diffgraph.DiffGraphAnalyzer")
    def test_symbols_with_knowledge_links_produce_warnings(self, MockAnalyzer):
        diff_report = _mock_diff_report(changed_symbols=[("process_order", "orders.py")])
        MockAnalyzer.return_value.diff_refs.return_value = diff_report

        store = _mock_store()

        # We need to track which query is being called.
        # Query 1 (test coverage): count=1 (has tests)
        # Query 2 (knowledge links): has a linked doc
        # Query 3 (owners): no owners
        call_count = {"n": 0}

        def side_effect_query(cypher, params=None):
            call_count["n"] += 1
            result = MagicMock()
            if "CALLS" in cypher:
                # test coverage query
                result.result_set = [[1]]
            elif "DOCUMENTS" in cypher:
                # knowledge links query
                result.result_set = [["WikiPage", "Order Processing Guide"]]
            else:
                result.result_set = []
            return result

        store.query.side_effect = side_effect_query

        checker = ReleaseChecker(store, repo_path="/tmp/repo")
        report = checker.check()

        stale = [i for i in report.items if i.category == "stale_doc"]
        assert len(stale) == 1
        assert stale[0].severity == "warning"
        assert stale[0].symbol == "process_order"
        assert stale[0].knowledge_node == "Order Processing Guide"
        assert "review" in stale[0].detail


# ── ReleaseChecker.check — owners ────────────────────────────────────────────


class TestReleaseCheckerOwners:
    @patch("navegador.analysis.diffgraph.DiffGraphAnalyzer")
    def test_owners_appear_in_owners_required(self, MockAnalyzer):
        diff_report = _mock_diff_report(
            changed_symbols=[
                ("auth_validate", "auth.py"),
                ("auth_refresh", "auth.py"),
            ]
        )
        MockAnalyzer.return_value.diff_refs.return_value = diff_report

        store = _mock_store()

        def side_effect_query(cypher, params=None):
            result = MagicMock()
            if "CALLS" in cypher:
                result.result_set = [[1]]
            elif "DOCUMENTS" in cypher:
                result.result_set = []
            elif "ASSIGNED_TO" in cypher:
                result.result_set = [["Alice"], ["Bob"]]
            else:
                result.result_set = []
            return result

        store.query.side_effect = side_effect_query

        checker = ReleaseChecker(store, repo_path="/tmp/repo")
        report = checker.check()

        assert "Alice" in report.owners_required
        assert "Bob" in report.owners_required

    @patch("navegador.analysis.diffgraph.DiffGraphAnalyzer")
    def test_owners_are_deduplicated(self, MockAnalyzer):
        diff_report = _mock_diff_report(
            changed_symbols=[
                ("fn_a", "a.py"),
                ("fn_b", "b.py"),
            ]
        )
        MockAnalyzer.return_value.diff_refs.return_value = diff_report

        store = _mock_store()

        def side_effect_query(cypher, params=None):
            result = MagicMock()
            if "ASSIGNED_TO" in cypher:
                # Both symbols owned by same person
                result.result_set = [["Alice"]]
            elif "CALLS" in cypher:
                result.result_set = [[1]]
            else:
                result.result_set = []
            return result

        store.query.side_effect = side_effect_query

        checker = ReleaseChecker(store, repo_path="/tmp/repo")
        report = checker.check()

        assert report.owners_required == ["Alice"]


# ── ReleaseChecker.check — base/head refs ───────────────────────────────────


class TestReleaseCheckerRefs:
    @patch("navegador.analysis.diffgraph.DiffGraphAnalyzer")
    def test_passes_refs_to_diff_analyzer(self, MockAnalyzer):
        diff_report = _mock_diff_report()
        MockAnalyzer.return_value.diff_refs.return_value = diff_report

        store = _mock_store()
        checker = ReleaseChecker(store, repo_path="/tmp/repo")
        report = checker.check(base="v1.0", head="v1.1")

        MockAnalyzer.return_value.diff_refs.assert_called_once_with("v1.0", "v1.1")
        assert report.base_ref == "v1.0"
        assert report.head_ref == "v1.1"


# ── ReleaseChecker.check — empty diff ───────────────────────────────────────


class TestReleaseCheckerEmptyDiff:
    @patch("navegador.analysis.diffgraph.DiffGraphAnalyzer")
    def test_empty_diff_produces_passing_report(self, MockAnalyzer):
        diff_report = _mock_diff_report()
        MockAnalyzer.return_value.diff_refs.return_value = diff_report

        store = _mock_store()
        checker = ReleaseChecker(store, repo_path="/tmp/repo")
        report = checker.check()

        assert report.passed is True
        assert report.items == []
        assert report.changed_files == []
        assert report.owners_required == []


# ── ReleaseChecker.check — symbol cap ────────────────────────────────────────


class TestReleaseCheckerSymbolCap:
    @patch("navegador.analysis.diffgraph.DiffGraphAnalyzer")
    def test_caps_at_fifty_symbols(self, MockAnalyzer):
        # 60 changed symbols -- should be capped to 50
        diff_report = _mock_diff_report(
            changed_symbols=[(f"fn_{i}", f"file_{i}.py") for i in range(60)]
        )
        MockAnalyzer.return_value.diff_refs.return_value = diff_report

        store = _mock_store()
        store.query.return_value = MagicMock(result_set=[[1]])

        checker = ReleaseChecker(store, repo_path="/tmp/repo")
        report = checker.check()

        changed_items = [i for i in report.items if i.category == "changed_symbol"]
        assert len(changed_items) == 50
