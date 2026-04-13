"""Tests for the SEMANTIC strategy in navegador.intelligence.doclink."""

import math
from unittest.mock import MagicMock

import pytest

from navegador.intelligence.doclink import DocLinker


def _mock_store(*result_sets):
    """Build a mock store whose .query() returns successive result_sets."""
    store = MagicMock()
    results = []
    for rs in result_sets:
        r = MagicMock()
        r.result_set = rs
        results.append(r)
    store.query.side_effect = results
    return store


def _mock_provider(embed_vectors):
    """Build a mock LLMProvider that returns vectors from a list in order.

    embed_vectors: list of (list[float] | Exception) — if Exception, embed() raises it.
    """
    provider = MagicMock()
    call_idx = [0]

    def _embed(text):
        idx = call_idx[0]
        call_idx[0] += 1
        if idx < len(embed_vectors):
            val = embed_vectors[idx]
            if isinstance(val, Exception):
                raise val
            return val
        return [0.0, 0.0, 0.0]

    provider.embed.side_effect = _embed
    return provider


# ── _cos helper ─────────────────────────────────────────────────────────────
# The _cos function is local to _semantic_candidates, but we can test cosine
# math through the full pipeline. We also test it directly for edge cases.


class TestCosineViaPipeline:
    """Test cosine similarity behavior through the semantic candidates pipeline."""

    def test_identical_vectors_yield_score_1(self):
        """Two identical embeddings should produce cosine similarity of 1.0."""
        vec = [1.0, 0.0, 0.0]
        store = _mock_store(
            # doc nodes
            [["Document", "Guide", "authentication"]],
            # code nodes
            [["Function", "auth_fn", "auth.py", "auth function"]],
            # existing links
            [],
        )
        # Provider returns identical vectors for doc and code
        provider = _mock_provider([vec, vec])
        linker = DocLinker(store, provider=provider)
        candidates = linker.suggest_links(min_confidence=0.0)

        semantic = [c for c in candidates if c.strategy == "SEMANTIC"]
        assert len(semantic) >= 1
        assert semantic[0].confidence == pytest.approx(1.0, abs=0.01)

    def test_orthogonal_vectors_yield_score_0(self):
        """Orthogonal embeddings should produce cosine similarity near 0.0 (below threshold)."""
        store = _mock_store(
            [["Document", "Doc", "some content"]],
            [["Function", "fn", "f.py", "function"]],
            [],
        )
        # Orthogonal vectors: [1,0,0] dot [0,1,0] = 0
        provider = _mock_provider([[0.0, 1.0, 0.0], [1.0, 0.0, 0.0]])
        linker = DocLinker(store, provider=provider)
        candidates = linker.suggest_links(min_confidence=0.0)

        semantic = [c for c in candidates if c.strategy == "SEMANTIC"]
        # Score is 0.0, below SEMANTIC_THRESHOLD (0.70), so no candidates
        assert len(semantic) == 0

    def test_high_similarity_above_threshold(self):
        """Vectors with cosine > 0.70 should produce a SEMANTIC candidate."""
        # Two similar but not identical vectors
        vec_a = [0.9, 0.3, 0.1]
        vec_b = [0.85, 0.35, 0.15]
        # Pre-compute expected cosine
        dot = sum(a * b for a, b in zip(vec_a, vec_b))
        mag = math.sqrt(sum(x * x for x in vec_a)) * math.sqrt(sum(x * x for x in vec_b))
        expected_cos = dot / mag
        assert expected_cos > 0.70, "Test vectors must have cosine > 0.70"

        store = _mock_store(
            [["Document", "Doc", "content"]],
            [["Function", "fn", "f.py", "text"]],
            [],
        )
        # First embed call = code node, second = doc node
        provider = _mock_provider([vec_a, vec_b])
        linker = DocLinker(store, provider=provider)
        candidates = linker.suggest_links(min_confidence=0.0)

        semantic = [c for c in candidates if c.strategy == "SEMANTIC"]
        assert len(semantic) == 1
        assert semantic[0].confidence == pytest.approx(expected_cos, abs=0.01)
        assert semantic[0].strategy == "SEMANTIC"
        assert "semantic similarity" in semantic[0].rationale

    def test_below_threshold_excluded(self):
        """Vectors with cosine < 0.70 should not produce a SEMANTIC candidate."""
        # Vectors that are similar but below 0.70
        vec_a = [1.0, 0.0, 0.0]
        vec_b = [0.5, 0.8, 0.3]
        dot = sum(a * b for a, b in zip(vec_a, vec_b))
        mag = math.sqrt(sum(x * x for x in vec_a)) * math.sqrt(sum(x * x for x in vec_b))
        cos = dot / mag
        assert cos < 0.70, f"Test vectors cosine {cos} must be < 0.70"

        store = _mock_store(
            [["Document", "Doc", "content"]],
            [["Function", "fn", "f.py", "text"]],
            [],
        )
        provider = _mock_provider([vec_a, vec_b])
        linker = DocLinker(store, provider=provider)
        candidates = linker.suggest_links(min_confidence=0.0)

        semantic = [c for c in candidates if c.strategy == "SEMANTIC"]
        assert len(semantic) == 0


class TestSemanticErrorHandling:
    """Test that embed() failures are handled gracefully."""

    def test_code_embed_failure_skips_node(self):
        """If embed() fails for a code node, it's skipped without crashing."""
        store = _mock_store(
            [["Document", "Doc", "content"]],
            [["Function", "fn1", "f.py", "text"], ["Function", "fn2", "g.py", "text2"]],
            [],
        )
        # First code embed fails, second succeeds, doc embed succeeds
        good_vec = [0.9, 0.3, 0.1]
        provider = _mock_provider([
            RuntimeError("API error"),  # fn1 embed fails
            good_vec,                   # fn2 embed succeeds
            good_vec,                   # doc embed succeeds (identical => cos=1.0)
        ])
        linker = DocLinker(store, provider=provider)
        candidates = linker.suggest_links(min_confidence=0.0)

        semantic = [c for c in candidates if c.strategy == "SEMANTIC"]
        # Only fn2 should have a candidate (fn1 was skipped)
        assert len(semantic) == 1
        assert semantic[0].target_name == "fn2"

    def test_doc_embed_failure_skips_doc(self):
        """If embed() fails for a doc node, that doc is skipped."""
        store = _mock_store(
            [
                ["Document", "Doc1", "content1"],
                ["Document", "Doc2", "content2"],
            ],
            [["Function", "fn", "f.py", "text"]],
            [],
        )
        good_vec = [0.9, 0.3, 0.1]
        provider = _mock_provider([
            good_vec,                   # code embed for fn
            RuntimeError("API error"),  # Doc1 embed fails
            good_vec,                   # Doc2 embed succeeds (identical => cos=1.0)
        ])
        linker = DocLinker(store, provider=provider)
        candidates = linker.suggest_links(min_confidence=0.0)

        semantic = [c for c in candidates if c.strategy == "SEMANTIC"]
        # Only Doc2 should have candidates (Doc1 was skipped)
        doc_names = {c.source_name for c in semantic}
        assert "Doc1" not in doc_names
        assert "Doc2" in doc_names

    def test_all_embeds_fail_returns_empty(self):
        """If all embed calls fail, no semantic candidates are returned."""
        store = _mock_store(
            [["Document", "Doc", "content"]],
            [["Function", "fn", "f.py", "text"]],
            [],
        )
        provider = _mock_provider([
            RuntimeError("fail1"),
            RuntimeError("fail2"),
        ])
        linker = DocLinker(store, provider=provider)
        candidates = linker.suggest_links(min_confidence=0.0)

        semantic = [c for c in candidates if c.strategy == "SEMANTIC"]
        assert len(semantic) == 0


class TestSemanticExistingLinksExcluded:
    """Existing links should be excluded from semantic results."""

    def test_already_linked_pair_excluded(self):
        """A doc-code pair that already has an edge is not returned."""
        good_vec = [0.9, 0.3, 0.1]
        store = _mock_store(
            [["Document", "Doc", "content"]],
            [["Function", "fn", "f.py", "text"]],
            # existing: Doc -> fn already linked
            [["Doc", "fn"]],
        )
        provider = _mock_provider([good_vec, good_vec])
        linker = DocLinker(store, provider=provider)
        candidates = linker.suggest_links(min_confidence=0.0)

        semantic = [c for c in candidates if c.strategy == "SEMANTIC"]
        assert len(semantic) == 0


class TestSemanticDeduplication:
    """Semantic candidates should be deduplicated with higher confidence winning."""

    def test_exact_match_wins_over_semantic(self):
        """When both exact and semantic match the same pair, highest confidence wins."""
        good_vec = [0.9, 0.3, 0.1]
        store = _mock_store(
            # Doc mentions exact name in backticks -> exact match
            [["Document", "Doc", "# fn_name\nUse `fn_name` for things."]],
            [["Function", "fn_name", "f.py", "text"]],
            [],
        )
        provider = _mock_provider([good_vec, good_vec])
        linker = DocLinker(store, provider=provider)
        candidates = linker.suggest_links(min_confidence=0.0)

        # Exact match at 0.95 should beat semantic at ~1.0 actually both are kept
        # but dedup keeps the highest confidence one
        pairs = [(c.source_name, c.target_name) for c in candidates]
        assert ("Doc", "fn_name") in pairs
        # Only one entry per pair after dedup
        assert pairs.count(("Doc", "fn_name")) == 1

    def test_no_provider_means_no_semantic_candidates(self):
        """Without a provider, no semantic candidates are generated."""
        store = _mock_store(
            [["Document", "Doc", "content"]],
            [["Function", "fn", "f.py", "text"]],
            [],
        )
        linker = DocLinker(store, provider=None)
        candidates = linker.suggest_links(min_confidence=0.0)

        semantic = [c for c in candidates if c.strategy == "SEMANTIC"]
        assert len(semantic) == 0


class TestCosineEdgeCases:
    """Test edge cases of cosine similarity through the pipeline."""

    def test_zero_magnitude_vectors(self):
        """Zero vectors should produce cosine 0.0 (mag=0, so division guarded)."""
        store = _mock_store(
            [["Document", "Doc", "content"]],
            [["Function", "fn", "f.py", "text"]],
            [],
        )
        provider = _mock_provider([[0.0, 0.0, 0.0], [0.0, 0.0, 0.0]])
        linker = DocLinker(store, provider=provider)
        candidates = linker.suggest_links(min_confidence=0.0)

        semantic = [c for c in candidates if c.strategy == "SEMANTIC"]
        # Zero vectors => cosine=0 => below threshold
        assert len(semantic) == 0

    def test_negative_components(self):
        """Vectors with negative components can still have high similarity."""
        vec_a = [-1.0, -1.0, 0.0]
        vec_b = [-1.0, -1.0, 0.0]
        # These are identical, cosine = 1.0
        store = _mock_store(
            [["Document", "Doc", "content"]],
            [["Function", "fn", "f.py", "text"]],
            [],
        )
        provider = _mock_provider([vec_a, vec_b])
        linker = DocLinker(store, provider=provider)
        candidates = linker.suggest_links(min_confidence=0.0)

        semantic = [c for c in candidates if c.strategy == "SEMANTIC"]
        assert len(semantic) == 1
        assert semantic[0].confidence == pytest.approx(1.0, abs=0.01)
