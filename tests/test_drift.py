# Copyright CONFLICT LLC 2026 (weareconflict.com)
"""Tests for navegador.analysis.drift — architecture drift detection."""

import json
from unittest.mock import MagicMock

from navegador.analysis.drift import DriftChecker, DriftReport, DriftViolation

# ── Shared helpers ────────────────────────────────────────────────────────────


def _mock_store(result_set=None):
    """Return a mock GraphStore whose .query() returns the given result_set."""
    store = MagicMock()
    result = MagicMock()
    result.result_set = result_set or []
    store.query.return_value = result
    return store


def _multi_mock_store(*result_sets):
    """Mock store whose .query() returns successive result_sets."""
    store = MagicMock()
    results = []
    for rs in result_sets:
        r = MagicMock()
        r.result_set = rs
        results.append(r)
    store.query.side_effect = results
    return store


def _make_violation(check="TEST_CHECK", severity="error", message="test"):
    return DriftViolation(
        check=check,
        severity=severity,
        message=message,
        offending_node="some_fn",
        offending_file="app/svc.py",
        knowledge_node="rule-1",
        knowledge_type="Rule",
    )


# ── DriftViolation ───────────────────────────────────────────────────────────


class TestDriftViolation:
    def test_fields_stored_correctly(self):
        v = DriftViolation(
            check="MISSING_OWNER",
            severity="warning",
            message="no owner assigned",
            offending_node="process_payment",
            offending_file="payments/svc.py",
            knowledge_node="ownership-rule",
            knowledge_type="Rule",
        )
        assert v.check == "MISSING_OWNER"
        assert v.severity == "warning"
        assert v.offending_node == "process_payment"
        assert v.offending_file == "payments/svc.py"
        assert v.knowledge_node == "ownership-rule"
        assert v.knowledge_type == "Rule"

    def test_defaults_for_optional_fields(self):
        v = DriftViolation(check="X", severity="error", message="msg")
        assert v.offending_node == ""
        assert v.offending_file == ""
        assert v.knowledge_node == ""
        assert v.knowledge_type == ""


# ── DriftReport ──────────────────────────────────────────────────────────────


class TestDriftReport:
    def test_has_violations_true_when_non_empty(self):
        report = DriftReport(violations=[_make_violation()])
        assert report.has_violations is True

    def test_has_violations_false_when_empty(self):
        report = DriftReport()
        assert report.has_violations is False

    def test_to_dict_contains_expected_keys(self):
        report = DriftReport(checks_run=3)
        d = report.to_dict()
        for key in ("checks_run", "violations", "warnings", "summary"):
            assert key in d
        assert d["checks_run"] == 3

    def test_to_dict_summary_counts_match_lists(self):
        report = DriftReport(
            violations=[_make_violation(severity="error")],
            warnings=[
                _make_violation(severity="warning"),
                _make_violation(severity="warning"),
            ],
            checks_run=2,
        )
        d = report.to_dict()
        assert d["summary"]["violations"] == 1
        assert d["summary"]["warnings"] == 2
        assert len(d["violations"]) == 1
        assert len(d["warnings"]) == 2

    def test_to_json_roundtrips(self):
        report = DriftReport(
            violations=[_make_violation(severity="error", message="bad dep")],
            warnings=[_make_violation(severity="warning", message="stale ref")],
            checks_run=3,
        )
        data = json.loads(report.to_json())
        assert data["checks_run"] == 3
        assert len(data["violations"]) == 1
        assert data["violations"][0]["message"] == "bad dep"
        assert len(data["warnings"]) == 1
        assert data["warnings"][0]["message"] == "stale ref"

    def test_to_markdown_violations_section_when_present(self):
        report = DriftReport(
            violations=[
                DriftViolation(
                    check="FORBIDDEN_DEP",
                    severity="error",
                    message="illegal dependency",
                    offending_node="bad_import",
                    offending_file="app/hack.py",
                    knowledge_node="no-hack-rule",
                    knowledge_type="Rule",
                )
            ],
            checks_run=1,
        )
        md = report.to_markdown()
        assert "## Violations (1)" in md
        assert "FORBIDDEN_DEP" in md
        assert "`bad_import`" in md
        assert "`app/hack.py`" in md
        assert "_No violations found._" not in md

    def test_to_markdown_no_violations_message(self):
        report = DriftReport(checks_run=3)
        md = report.to_markdown()
        assert "_No violations found._" in md
        assert "## Violations" not in md

    def test_to_markdown_warnings_section_when_present(self):
        report = DriftReport(
            warnings=[
                DriftViolation(
                    check="STALE_MEMORY_REF",
                    severity="warning",
                    message="stale reference",
                    offending_node="old_sym",
                    knowledge_node="mem-1",
                    knowledge_type="feedback",
                )
            ],
            checks_run=1,
        )
        md = report.to_markdown()
        assert "## Warnings (1)" in md
        assert "STALE_MEMORY_REF" in md
        assert "`old_sym`" in md

    def test_to_markdown_no_warnings_section_when_empty(self):
        report = DriftReport(checks_run=1)
        md = report.to_markdown()
        assert "## Warnings" not in md

    def test_to_markdown_shows_checks_run_count(self):
        report = DriftReport(checks_run=5)
        md = report.to_markdown()
        assert "**Checks run:** 5" in md

    def test_to_markdown_knowledge_ref_in_violation(self):
        """Violation lines should include the knowledge node reference."""
        report = DriftReport(
            violations=[
                DriftViolation(
                    check="REQUIRED_LAYER",
                    severity="error",
                    message="wrong call order",
                    offending_node="controller_fn",
                    knowledge_node="ADR-001",
                    knowledge_type="Decision",
                )
            ],
            checks_run=1,
        )
        md = report.to_markdown()
        assert "_Decision_" in md
        assert "`ADR-001`" in md


# ── DriftChecker.check() — structure ────────────────────────────────────────


class TestDriftCheckerStructure:
    def test_checks_run_equals_builtin_count(self):
        """With no extras, checks_run should equal the number of built-in checks (3)."""
        store = _mock_store()
        report = DriftChecker(store).check()
        assert report.checks_run == 3

    def test_register_check_increments_checks_run(self):
        """Registering a custom check should increment checks_run by 1."""
        store = _mock_store()
        checker = DriftChecker(store)
        checker.register_check(lambda: [])
        report = checker.check()
        assert report.checks_run == 4

    def test_custom_check_error_appears_in_violations(self):
        store = _mock_store()
        checker = DriftChecker(store)

        def custom_check():
            return [
                DriftViolation(
                    check="CUSTOM_ERROR",
                    severity="error",
                    message="custom error found",
                    offending_node="bad_fn",
                )
            ]

        checker.register_check(custom_check)
        report = checker.check()

        custom_violations = [v for v in report.violations if v.check == "CUSTOM_ERROR"]
        assert len(custom_violations) == 1
        assert custom_violations[0].message == "custom error found"

    def test_custom_check_warning_appears_in_warnings(self):
        store = _mock_store()
        checker = DriftChecker(store)

        def custom_check():
            return [
                DriftViolation(
                    check="CUSTOM_WARN",
                    severity="warning",
                    message="custom warning",
                    offending_node="iffy_fn",
                )
            ]

        checker.register_check(custom_check)
        report = checker.check()

        custom_warnings = [w for w in report.warnings if w.check == "CUSTOM_WARN"]
        assert len(custom_warnings) == 1
        assert custom_warnings[0].offending_node == "iffy_fn"


# ── Built-in check: _check_stale_memory_refs ────────────────────────────────


class TestStaleMemoryRefs:
    def test_violations_created_from_query_rows(self):
        """Each row from the stale-memory query should produce a STALE_MEMORY_REF warning."""
        store = _mock_store(
            result_set=[
                ["feedback", "auth-rule", "Function", "old_authenticate"],
                ["decision", "ADR-005", "Class", "LegacyWidget"],
            ]
        )
        checker = DriftChecker(store)
        violations = checker._check_stale_memory_refs()

        assert len(violations) == 2
        assert all(v.check == "STALE_MEMORY_REF" for v in violations)
        assert all(v.severity == "warning" for v in violations)
        assert violations[0].offending_node == "old_authenticate"
        assert violations[0].knowledge_node == "auth-rule"
        assert violations[0].knowledge_type == "feedback"
        assert violations[1].offending_node == "LegacyWidget"
        assert violations[1].knowledge_node == "ADR-005"

    def test_empty_result_no_violations(self):
        store = _mock_store(result_set=[])
        violations = DriftChecker(store)._check_stale_memory_refs()
        assert violations == []

    def test_query_exception_returns_empty(self):
        """Database errors should be swallowed, returning no violations."""
        store = MagicMock()
        store.query.side_effect = RuntimeError("connection lost")
        violations = DriftChecker(store)._check_stale_memory_refs()
        assert violations == []


# ── Built-in check: _check_undocumented_domain_symbols ──────────────────────


class TestUndocumentedDomainSymbols:
    def test_violations_created_from_query_rows(self):
        """Each row should produce an UNDOCUMENTED_DOMAIN_SYMBOL warning."""
        store = _mock_store(
            result_set=[
                ["Function", "process_order", "orders/svc.py", "OrderDomain"],
                ["Class", "Invoice", "billing/models.py", "BillingDomain"],
            ]
        )
        checker = DriftChecker(store)
        violations = checker._check_undocumented_domain_symbols()

        assert len(violations) == 2
        assert all(v.check == "UNDOCUMENTED_DOMAIN_SYMBOL" for v in violations)
        assert all(v.severity == "warning" for v in violations)
        assert violations[0].offending_node == "process_order"
        assert violations[0].offending_file == "orders/svc.py"
        assert "OrderDomain" in violations[0].message
        assert violations[1].offending_node == "Invoice"

    def test_empty_result_no_violations(self):
        store = _mock_store(result_set=[])
        violations = DriftChecker(store)._check_undocumented_domain_symbols()
        assert violations == []

    def test_query_exception_returns_empty(self):
        store = MagicMock()
        store.query.side_effect = RuntimeError("timeout")
        violations = DriftChecker(store)._check_undocumented_domain_symbols()
        assert violations == []


# ── Built-in check: _check_required_owners ──────────────────────────────────


class TestRequiredOwners:
    def test_violations_when_rules_and_unowned_symbols_exist(self):
        """When feedback rules mention ownership and unowned symbols exist, produce warnings."""
        store = _multi_mock_store(
            # First query: rules mentioning ownership
            [["require-owners", "PaymentDomain"]],
            # Second query: unowned symbols in that domain
            [
                ["Function", "charge_card", "payments/charge.py"],
                ["Class", "Receipt", "payments/receipt.py"],
            ],
        )
        checker = DriftChecker(store)
        violations = checker._check_required_owners()

        assert len(violations) == 2
        assert all(v.check == "MISSING_OWNER" for v in violations)
        assert all(v.severity == "warning" for v in violations)
        assert violations[0].offending_node == "charge_card"
        assert violations[0].offending_file == "payments/charge.py"
        assert violations[0].knowledge_node == "require-owners"
        assert violations[0].knowledge_type == "Rule"
        assert violations[1].offending_node == "Receipt"

    def test_no_rules_no_violations(self):
        """When no feedback rules mention ownership, no violations should be generated."""
        store = _mock_store(result_set=[])
        violations = DriftChecker(store)._check_required_owners()
        assert violations == []

    def test_rules_exist_but_all_symbols_have_owners(self):
        """When rules exist but all symbols are owned, no violations generated."""
        store = _multi_mock_store(
            # Rules query returns a rule
            [["must-have-owners", "AuthDomain"]],
            # Unowned symbols query returns empty
            [],
        )
        violations = DriftChecker(store)._check_required_owners()
        assert violations == []

    def test_rule_with_no_domain_is_skipped(self):
        """Rules with empty domain should be skipped without querying for symbols."""
        store = _multi_mock_store(
            # Rule with empty domain
            [["owner-rule", ""]],
        )
        violations = DriftChecker(store)._check_required_owners()
        assert violations == []
        # Only one query call (the rules query); no unowned-symbols query
        assert store.query.call_count == 1

    def test_query_exception_returns_empty(self):
        store = MagicMock()
        store.query.side_effect = RuntimeError("network error")
        violations = DriftChecker(store)._check_required_owners()
        assert violations == []


# ── Full check() integration ─────────────────────────────────────────────────


class TestDriftCheckerIntegration:
    def test_all_builtins_contribute_to_single_report(self):
        """Violations from all built-in checks should be aggregated into one report."""
        store = MagicMock()
        results_iter = [
            # _check_stale_memory_refs
            MagicMock(result_set=[["feedback", "rule-1", "Function", "dead_fn"]]),
            # _check_undocumented_domain_symbols
            MagicMock(result_set=[["Class", "Undoc", "lib.py", "CoreDomain"]]),
            # _check_required_owners (rules query)
            MagicMock(result_set=[["own-rule", "CoreDomain"]]),
            # _check_required_owners (unowned symbols query)
            MagicMock(result_set=[["Function", "unowned_fn", "core.py"]]),
        ]
        store.query.side_effect = results_iter

        report = DriftChecker(store).check()

        # All three built-in checks produce warnings
        assert report.checks_run == 3
        assert len(report.warnings) == 3
        assert {w.check for w in report.warnings} == {
            "STALE_MEMORY_REF",
            "UNDOCUMENTED_DOMAIN_SYMBOL",
            "MISSING_OWNER",
        }

    def test_clean_graph_no_violations_no_warnings(self):
        """A graph with no drift issues should produce an empty report."""
        store = _mock_store(result_set=[])
        report = DriftChecker(store).check()

        assert report.checks_run == 3
        assert report.violations == []
        assert report.warnings == []
        assert report.has_violations is False
