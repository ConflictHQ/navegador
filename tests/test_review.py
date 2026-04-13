"""Tests for navegador.analysis.review — ReviewComment, ReviewReport, ReviewGenerator."""

import json
from unittest.mock import MagicMock

from navegador.analysis.review import (
    ReviewComment,
    ReviewGenerator,
    ReviewReport,
    _map_severity,
)

# ── Helpers ──────────────────────────────────────────────────────────────────


def _mock_store():
    store = MagicMock()
    store.query.return_value = MagicMock(result_set=[])
    return store


def _comment(
    severity="warning",
    title="Test",
    body="detail",
    symbol="foo",
    file_path="a.py",
    line_start=None,
    knowledge_ref="",
    knowledge_type="",
    confidence=1.0,
):
    return ReviewComment(
        severity=severity,
        title=title,
        body=body,
        symbol=symbol,
        file_path=file_path,
        line_start=line_start,
        knowledge_ref=knowledge_ref,
        knowledge_type=knowledge_type,
        confidence=confidence,
    )


# ── _map_severity ────────────────────────────────────────────────────────────


class TestMapSeverity:
    def test_critical_maps_to_error(self):
        assert _map_severity("critical") == "error"

    def test_warning_maps_to_warning(self):
        assert _map_severity("warning") == "warning"

    def test_other_maps_to_suggestion(self):
        assert _map_severity("info") == "suggestion"
        assert _map_severity("low") == "suggestion"

    def test_none_maps_to_suggestion(self):
        assert _map_severity(None) == "suggestion"

    def test_case_insensitive(self):
        assert _map_severity("Critical") == "error"
        assert _map_severity("WARNING") == "warning"


# ── ReviewReport property filters ────────────────────────────────────────────


class TestReviewReportFilters:
    def test_errors_returns_only_errors(self):
        report = ReviewReport(
            comments=[
                _comment(severity="error"),
                _comment(severity="warning"),
                _comment(severity="suggestion"),
            ]
        )
        assert len(report.errors) == 1
        assert report.errors[0].severity == "error"

    def test_warnings_returns_only_warnings(self):
        report = ReviewReport(
            comments=[
                _comment(severity="error"),
                _comment(severity="warning"),
                _comment(severity="suggestion"),
            ]
        )
        assert len(report.warnings) == 1
        assert report.warnings[0].severity == "warning"

    def test_suggestions_returns_only_suggestions(self):
        report = ReviewReport(
            comments=[
                _comment(severity="error"),
                _comment(severity="warning"),
                _comment(severity="suggestion"),
            ]
        )
        assert len(report.suggestions) == 1
        assert report.suggestions[0].severity == "suggestion"

    def test_empty_report_returns_empty_lists(self):
        report = ReviewReport()
        assert report.errors == []
        assert report.warnings == []
        assert report.suggestions == []


# ── ReviewReport.to_markdown ─────────────────────────────────────────────────


class TestReviewReportMarkdown:
    def test_includes_header_with_count(self):
        report = ReviewReport(comments=[_comment(severity="error")])
        md = report.to_markdown()
        assert "## Review — 1 comment(s)" in md

    def test_includes_error_section(self):
        report = ReviewReport(
            comments=[
                _comment(severity="error", title="Bad thing", body="broken"),
            ]
        )
        md = report.to_markdown()
        assert "### Errors (1)" in md
        assert "> broken" in md

    def test_skips_empty_sections(self):
        report = ReviewReport(
            comments=[
                _comment(severity="warning"),
            ]
        )
        md = report.to_markdown()
        assert "### Errors" not in md
        assert "### Suggestions" not in md
        assert "### Warnings (1)" in md

    def test_includes_file_and_knowledge_refs(self):
        report = ReviewReport(
            comments=[
                _comment(
                    severity="error",
                    file_path="auth.py",
                    line_start=42,
                    knowledge_ref="no-raw-sql",
                    knowledge_type="Rule",
                ),
            ]
        )
        md = report.to_markdown()
        assert "- File: `auth.py:42`" in md
        assert "- Knowledge: Rule `no-raw-sql`" in md

    def test_file_without_line_start(self):
        report = ReviewReport(
            comments=[
                _comment(severity="warning", file_path="models.py", line_start=None),
            ]
        )
        md = report.to_markdown()
        assert "- File: `models.py`" in md

    def test_empty_report_markdown(self):
        report = ReviewReport()
        md = report.to_markdown()
        assert "## Review — 0 comment(s)" in md


# ── ReviewReport.to_json ─────────────────────────────────────────────────────


class TestReviewReportJson:
    def test_round_trips(self):
        report = ReviewReport(
            changed_files=["a.py"],
            comments=[
                _comment(severity="error", knowledge_ref="r1", knowledge_type="Rule"),
                _comment(severity="suggestion"),
            ],
        )
        data = json.loads(report.to_json())
        assert data["changed_files"] == ["a.py"]
        assert len(data["comments"]) == 2
        assert data["summary"]["errors"] == 1
        assert data["summary"]["suggestions"] == 1
        assert data["summary"]["total"] == 2

    def test_to_dict_matches_to_json(self):
        report = ReviewReport(comments=[_comment()])
        assert json.loads(report.to_json()) == report.to_dict()


# ── ReviewGenerator ──────────────────────────────────────────────────────────


class TestReviewGeneratorRuleViolations:
    def test_rule_violation_creates_comment_with_mapped_severity(self):
        store = _mock_store()
        # Pass 1 returns a rule row, passes 2-3 return empty
        store.query.side_effect = [
            MagicMock(result_set=[["no-raw-sql", "critical", "Use parameterized queries"]]),
            MagicMock(result_set=[]),
            MagicMock(result_set=[]),
        ]
        gen = ReviewGenerator(store)
        report = gen.review_diff([{"name": "execute_sql", "file_path": "db.py"}])

        rule_comments = [c for c in report.comments if c.knowledge_type == "Rule"]
        assert len(rule_comments) == 1
        assert rule_comments[0].severity == "error"
        assert rule_comments[0].knowledge_ref == "no-raw-sql"
        assert rule_comments[0].symbol == "execute_sql"

    def test_warning_severity_mapped(self):
        store = _mock_store()
        store.query.side_effect = [
            MagicMock(result_set=[["soft-delete", "warning", "Always soft-delete"]]),
            MagicMock(result_set=[]),
            MagicMock(result_set=[]),
        ]
        gen = ReviewGenerator(store)
        report = gen.review_diff([{"name": "delete_user", "file_path": ""}])

        rule_comments = [c for c in report.comments if c.knowledge_type == "Rule"]
        assert len(rule_comments) == 1
        assert rule_comments[0].severity == "warning"


class TestReviewGeneratorADRConflicts:
    def test_adr_creates_suggestion_with_confidence(self):
        store = _mock_store()
        store.query.side_effect = [
            MagicMock(result_set=[]),  # pass 1
            MagicMock(result_set=[["ADR-005", "Use event sourcing for audit"]]),  # pass 2
            MagicMock(result_set=[]),  # pass 3
        ]
        gen = ReviewGenerator(store)
        report = gen.review_diff([{"name": "save_audit", "file_path": "audit.py"}])

        adr_comments = [c for c in report.comments if c.knowledge_type == "Decision"]
        assert len(adr_comments) == 1
        assert adr_comments[0].severity == "suggestion"
        assert adr_comments[0].confidence == 0.7
        assert adr_comments[0].knowledge_ref == "ADR-005"


class TestReviewGeneratorUndocumented:
    def test_undocumented_symbol_flagged(self):
        store = _mock_store()
        store.query.side_effect = [
            MagicMock(result_set=[]),  # pass 1
            MagicMock(result_set=[]),  # pass 2
            MagicMock(result_set=[["new_func", "new.py"]]),  # pass 3
        ]
        gen = ReviewGenerator(store)
        report = gen.review_diff([{"name": "new_func", "file_path": "new.py"}])

        undoc = [c for c in report.comments if "No knowledge links" in c.title]
        assert len(undoc) == 1
        assert undoc[0].confidence == 0.5
        assert undoc[0].severity == "suggestion"


class TestReviewGeneratorDeduplication:
    def test_same_symbol_and_knowledge_ref_keeps_highest_confidence(self):
        store = _mock_store()
        # Two symbols, both hit the same rule — only one should survive
        store.query.side_effect = [
            # sym1 pass 1
            MagicMock(result_set=[["rule-A", "warning", "desc"]]),
            MagicMock(result_set=[]),  # sym1 pass 2
            MagicMock(result_set=[]),  # sym1 pass 3
            # sym2 pass 1
            MagicMock(result_set=[["rule-A", "critical", "desc"]]),
            MagicMock(result_set=[]),  # sym2 pass 2
            MagicMock(result_set=[]),  # sym2 pass 3
        ]
        gen = ReviewGenerator(store)
        report = gen.review_diff(
            [
                {"name": "handler", "file_path": "a.py"},
                {"name": "handler", "file_path": "a.py"},
            ]
        )
        rule_a = [c for c in report.comments if c.knowledge_ref == "rule-A"]
        # Both have symbol="handler" and knowledge_ref="rule-A", dedup keeps one
        assert len(rule_a) == 1

    def test_comments_without_knowledge_ref_are_not_deduped(self):
        store = _mock_store()
        store.query.side_effect = [
            MagicMock(result_set=[]),  # pass 1
            MagicMock(result_set=[]),  # pass 2
            MagicMock(result_set=[["a", "a.py"]]),  # pass 3 (undocumented)
            MagicMock(result_set=[]),  # pass 1
            MagicMock(result_set=[]),  # pass 2
            MagicMock(result_set=[["b", "b.py"]]),  # pass 3 (undocumented)
        ]
        gen = ReviewGenerator(store)
        report = gen.review_diff(
            [
                {"name": "a", "file_path": "a.py"},
                {"name": "b", "file_path": "b.py"},
            ]
        )
        undoc = [c for c in report.comments if "No knowledge links" in c.title]
        assert len(undoc) == 2


class TestReviewGeneratorEdgeCases:
    def test_empty_changed_symbols_returns_empty_report(self):
        gen = ReviewGenerator(_mock_store())
        report = gen.review_diff([])
        assert report.comments == []
        assert report.changed_files == []

    def test_file_level_knowledge_comments(self):
        store = _mock_store()
        # No symbols, just files — the file query returns results
        store.query.side_effect = [
            MagicMock(result_set=[["auth-rule", "Rule", "Require auth on all endpoints"]]),
        ]
        gen = ReviewGenerator(store)
        report = gen.review_diff([], changed_files=["auth.py"])

        assert len(report.comments) == 1
        assert report.comments[0].knowledge_ref == "auth-rule"
        assert report.comments[0].confidence == 0.6
        assert report.comments[0].file_path == "auth.py"

    def test_symbols_without_name_are_skipped(self):
        store = _mock_store()
        gen = ReviewGenerator(store)
        report = gen.review_diff([{"name": "", "file_path": "a.py"}])
        assert report.comments == []
        # No queries should have been made
        store.query.assert_not_called()

    def test_query_exception_does_not_crash(self):
        store = _mock_store()
        store.query.side_effect = RuntimeError("connection lost")
        gen = ReviewGenerator(store)
        report = gen.review_diff([{"name": "foo", "file_path": "a.py"}])
        assert report.comments == []

    def test_changed_files_preserved_in_report(self):
        gen = ReviewGenerator(_mock_store())
        report = gen.review_diff([], changed_files=["x.py", "y.py"])
        assert report.changed_files == ["x.py", "y.py"]
