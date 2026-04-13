"""Tests for navegador.intelligence.doclink — confidence-ranked doc linking."""

from unittest.mock import MagicMock

import pytest

from navegador.intelligence.doclink import (
    DocLinker,
    LinkCandidate,
    _fuzzy_score,
    _terms_from_content,
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


# ── _terms_from_content ──────────────────────────────────────────────────────


class TestTermsFromContent:
    def test_extracts_headings(self):
        content = "# AuthService\n\nSome text\n## Validation Logic"
        terms = _terms_from_content(content)
        assert "AuthService" in terms
        assert "Validation Logic" in terms

    def test_extracts_bold_text(self):
        content = "Use the **UserManager** class and __SessionStore__."
        terms = _terms_from_content(content)
        assert "UserManager" in terms
        assert "SessionStore" in terms

    def test_extracts_backtick_text(self):
        content = "Call `validate_token` and then `refresh_session`."
        terms = _terms_from_content(content)
        assert "validate_token" in terms
        assert "refresh_session" in terms

    def test_extracts_first_line(self):
        content = "Important Function Guide\n\nMore text here."
        terms = _terms_from_content(content)
        assert "Important Function Guide" in terms

    def test_first_line_strips_heading_hashes(self):
        content = "# MyHeading\n\nBody text."
        terms = _terms_from_content(content)
        # The first line "# MyHeading" should be stripped of "#" and appear as "MyHeading"
        assert "MyHeading" in terms

    def test_deduplicates_terms(self):
        content = "# Foo\n\nThe **Foo** module uses `Foo`."
        terms = _terms_from_content(content)
        assert terms.count("Foo") == 1

    def test_empty_content(self):
        assert _terms_from_content("") == []
        assert _terms_from_content("   ") == []

    def test_mixed_content(self):
        content = "# Overview\n\nThe **PaymentService** uses `stripe_api`.\n## Details"
        terms = _terms_from_content(content)
        assert "Overview" in terms
        assert "PaymentService" in terms
        assert "stripe_api" in terms
        assert "Details" in terms


# ── _fuzzy_score ─────────────────────────────────────────────────────────────


class TestFuzzyScore:
    def test_identical_strings_return_one(self):
        assert _fuzzy_score("hello", "hello") == 1.0

    def test_identical_case_insensitive(self):
        assert _fuzzy_score("Hello", "hello") == 1.0

    def test_empty_string_returns_zero(self):
        assert _fuzzy_score("", "hello") == 0.0
        assert _fuzzy_score("hello", "") == 0.0

    def test_both_empty_returns_one(self):
        # Both are equal: a == b case
        assert _fuzzy_score("", "") == 1.0

    def test_substring_score(self):
        # "auth" in "authenticate" => substring match
        score = _fuzzy_score("auth", "authenticate")
        assert score == pytest.approx(4 / 12)  # min(len)/max(len) = 4/12

    def test_partial_bigram_overlap(self):
        # Not a substring, so bigram overlap path
        score = _fuzzy_score("validate", "validator")
        assert 0.5 < score < 1.0

    def test_completely_different_strings(self):
        score = _fuzzy_score("abc", "xyz")
        assert score == 0.0

    def test_single_char_strings_no_bigrams(self):
        # Single chars produce no bigrams, so falls through to bigram path => 0.0
        # But "a" in "a" is exact match => 1.0
        assert _fuzzy_score("a", "a") == 1.0
        # "a" vs "b": not substring of each other, no bigrams => 0.0
        assert _fuzzy_score("a", "b") == 0.0


# ── DocLinker.suggest_links ──────────────────────────────────────────────────


class TestSuggestLinks:
    def test_exact_match_produces_exact_confidence(self):
        store = _multi_mock_store(
            # _fetch_doc_nodes
            [["Document", "Auth Guide", "# validate_token\n\nHow to validate tokens."]],
            # _fetch_code_nodes
            [["Function", "validate_token", "auth.py", ""]],
            # _existing_links
            [],
        )
        linker = DocLinker(store)
        candidates = linker.suggest_links(min_confidence=0.5)

        exact = [c for c in candidates if c.strategy == "EXACT_NAME"]
        assert len(exact) >= 1
        assert exact[0].confidence == DocLinker.EXACT_CONFIDENCE
        assert exact[0].source_name == "Auth Guide"
        assert exact[0].target_name == "validate_token"

    def test_fuzzy_match_produces_lower_confidence(self):
        store = _multi_mock_store(
            # doc with backtick term "validate_tok" (fuzzy match to "validate_token")
            [["Document", "Token Docs", "Use `validate_tok` for auth."]],
            # code node
            [["Function", "validate_token", "auth.py", ""]],
            # existing links
            [],
        )
        linker = DocLinker(store)
        candidates = linker.suggest_links(min_confidence=0.3)

        fuzzy = [c for c in candidates if c.strategy == "FUZZY"]
        assert len(fuzzy) >= 1
        assert fuzzy[0].confidence < DocLinker.EXACT_CONFIDENCE
        assert fuzzy[0].confidence > 0.0

    def test_existing_links_excluded(self):
        store = _multi_mock_store(
            [["Document", "Auth Guide", "# validate_token"]],
            [["Function", "validate_token", "auth.py", ""]],
            # Already linked: (Auth Guide, validate_token)
            [["Auth Guide", "validate_token"]],
        )
        linker = DocLinker(store)
        candidates = linker.suggest_links(min_confidence=0.0)
        # The exact match should be excluded because it's already linked
        assert all(
            not (c.source_name == "Auth Guide" and c.target_name == "validate_token")
            for c in candidates
        )

    def test_results_sorted_by_confidence_descending(self):
        store = _multi_mock_store(
            # Two docs: one will exact match, one will fuzzy match
            [
                ["Document", "Doc A", "# handle_request"],
                ["Document", "Doc B", "Use `handle_requ` for handling."],
            ],
            # Code node
            [["Function", "handle_request", "views.py", ""]],
            # No existing links
            [],
        )
        linker = DocLinker(store)
        candidates = linker.suggest_links(min_confidence=0.3)

        if len(candidates) >= 2:
            for i in range(len(candidates) - 1):
                assert candidates[i].confidence >= candidates[i + 1].confidence

    def test_min_confidence_high_threshold_still_gets_exact(self):
        store = _multi_mock_store(
            [["Document", "Guide", "# exact_match_fn"]],
            [["Function", "exact_match_fn", "f.py", ""]],
            [],
        )
        linker = DocLinker(store)
        high = linker.suggest_links(min_confidence=0.9)
        assert len(high) >= 1
        assert high[0].confidence == DocLinker.EXACT_CONFIDENCE

    def test_min_confidence_above_exact_returns_empty(self):
        store = _multi_mock_store(
            [["Document", "Guide", "# exact_match_fn"]],
            [["Function", "exact_match_fn", "f.py", ""]],
            [],
        )
        linker = DocLinker(store)
        none_found = linker.suggest_links(min_confidence=0.99)
        assert len(none_found) == 0

    def test_no_doc_nodes_returns_empty(self):
        store = _multi_mock_store(
            [],  # no docs
            [["Function", "foo", "f.py", ""]],
            [],
        )
        linker = DocLinker(store)
        assert linker.suggest_links() == []

    def test_no_code_nodes_returns_empty(self):
        store = _multi_mock_store(
            [["Document", "Guide", "# foo"]],
            [],  # no code
            [],
        )
        linker = DocLinker(store)
        assert linker.suggest_links() == []


# ── DocLinker.accept ─────────────────────────────────────────────────────────


class TestAccept:
    def test_accept_calls_create_edge(self):
        store = _mock_store()
        linker = DocLinker(store)
        candidate = LinkCandidate(
            source_label="Document",
            source_name="Auth Guide",
            target_label="Function",
            target_name="validate_token",
            target_file="auth.py",
            edge_type="DOCUMENTS",
            confidence=0.95,
            strategy="EXACT_NAME",
        )
        linker.accept(candidate)

        store.create_edge.assert_called_once()
        args, kwargs = store.create_edge.call_args
        assert args[0] == "Document"
        assert args[1] == {"name": "Auth Guide"}
        # args[2] is the EdgeType enum
        assert args[3] == "Function"
        assert args[4] == {"name": "validate_token"}
        assert kwargs["props"]["confidence"] == 0.95
        assert kwargs["props"]["strategy"] == "EXACT_NAME"

    def test_accept_with_unknown_edge_type_defaults_to_documents(self):
        from navegador.graph.schema import EdgeType

        store = _mock_store()
        linker = DocLinker(store)
        candidate = LinkCandidate(
            source_label="WikiPage", source_name="Wiki",
            target_label="Class", target_name="Foo",
            edge_type="NONEXISTENT_TYPE",
            confidence=0.5, strategy="FUZZY",
        )
        linker.accept(candidate)

        args, _ = store.create_edge.call_args
        assert args[2] == EdgeType.DOCUMENTS


# ── DocLinker.accept_all ─────────────────────────────────────────────────────


class TestAcceptAll:
    def test_returns_count_of_written_links(self):
        store = _mock_store()
        linker = DocLinker(store)
        candidates = [
            LinkCandidate(
                source_label="Document", source_name="D1",
                target_label="Function", target_name="f1",
                confidence=0.95, strategy="EXACT_NAME",
            ),
            LinkCandidate(
                source_label="Document", source_name="D2",
                target_label="Function", target_name="f2",
                confidence=0.60, strategy="FUZZY",
            ),
            LinkCandidate(
                source_label="Document", source_name="D3",
                target_label="Function", target_name="f3",
                confidence=0.85, strategy="FUZZY",
            ),
        ]
        written = linker.accept_all(candidates, min_confidence=0.8)
        assert written == 2
        assert store.create_edge.call_count == 2

    def test_accept_all_skips_below_threshold(self):
        store = _mock_store()
        linker = DocLinker(store)
        candidates = [
            LinkCandidate(
                source_label="Document", source_name="D",
                target_label="Function", target_name="f",
                confidence=0.3, strategy="FUZZY",
            ),
        ]
        written = linker.accept_all(candidates, min_confidence=0.5)
        assert written == 0
        store.create_edge.assert_not_called()

    def test_accept_all_empty_list(self):
        store = _mock_store()
        linker = DocLinker(store)
        assert linker.accept_all([], min_confidence=0.5) == 0
