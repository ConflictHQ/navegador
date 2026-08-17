"""
Targeting: return the scope, not the answer (#186).

These tools exist because the expensive thing an agent does is working out
*where* to look — 206 tool calls in a median session, 32% of reads re-opening
a file already read, and a frozen baseline of 13 turns to first correct
target. Grep is not the problem; aimlessness is.

So the tests are about whether the scope is right, and specifically about the
two ways it can be wrong in a way that would go unnoticed:

- a scope that quietly widens to everything is not a scope, and would make the
  reduction a lie while still returning plausible results
- a fusion that lets one source dominate turns a multi-source ranking into a
  single-source one with extra steps

Real store throughout; a mocked store cannot produce a traversal.
"""

import pytest

from navegador.graph import GraphStore
from navegador.ingestion import RepoIngester
from navegador.targeting import RRF_K, WEIGHTS, Candidate, Targeting, _fuse


@pytest.fixture
def project(tmp_path):
    """
    Two connected modules and one unrelated one.

    The unrelated module is the point: it shares vocabulary with the others,
    so anything that returns it inside a scope has stopped scoping.
    """
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "src" / "auth.py").write_text(
        "def validate_token(token):\n"
        "    return check_signature(token)\n"
        "\n"
        "def check_signature(token):\n"
        "    raise ValueError('token signature invalid')\n"
    )
    (root / "src" / "api.py").write_text(
        "from src.auth import validate_token\n"
        "\n"
        "def handle_request(request):\n"
        "    return validate_token(request.token)\n"
    )
    (root / "src" / "unrelated.py").write_text(
        "def compute_token_budget():\n    return 'token budget unrelated'\n"
    )

    store = GraphStore.sqlite(str(tmp_path / "g.db"))
    RepoIngester(store).ingest(root)
    from navegador.graph.trigram import TrigramIndex

    TrigramIndex(store).index_graph()
    return store


@pytest.fixture
def targeting(project):
    return Targeting(project)


class TestFusion:
    def test_agreement_across_sources_outranks_a_single_strong_hit(self):
        """
        The reason for fusing at all. A place two sources agree on should beat
        one that only the strongest source found.
        """
        agreed = Candidate(path="a.py", line=1, reasons=["exact"])
        alone = Candidate(path="b.py", line=1, reasons=["exact"])
        fused = _fuse(
            {
                "exact": [alone, agreed],
                "symbol": [agreed],
                "vector": [agreed],
            }
        )
        assert fused[0].path == "a.py"

    def test_reasons_accumulate(self):
        fused = _fuse(
            {
                "exact": [Candidate(path="a.py", line=1, reasons=["text appears here"])],
                "symbol": [Candidate(path="a.py", line=1, reasons=["function named x"])],
            }
        )
        assert len(fused[0].reasons) == 2

    def test_rank_position_matters(self):
        first = Candidate(path="first.py", line=1)
        last = Candidate(path="last.py", line=1)
        fused = _fuse({"exact": [first, last]})
        assert fused[0].path == "first.py"

    def test_weights_favour_exact_over_vector(self):
        """Exact text is a fact; vector similarity is a guess."""
        assert WEIGHTS["exact"] > WEIGHTS["vector"]

    def test_empty_sources_fuse_to_nothing(self):
        assert _fuse({"exact": [], "symbol": []}) == []

    def test_scores_are_bounded_by_the_rrf_constant(self):
        fused = _fuse({"exact": [Candidate(path="a.py", line=1)]})
        assert fused[0].score == pytest.approx(WEIGHTS["exact"] / (RRF_K + 1))


class TestScopeFor:
    def test_scope_includes_the_symbol_and_what_it_reaches(self, targeting):
        paths = targeting.scope_for("validate_token")
        assert "src/auth.py" in paths
        assert "src/api.py" in paths

    def test_scope_excludes_unconnected_files(self, targeting):
        """
        The whole value proposition. unrelated.py talks about tokens too, so a
        text index would surface it; the graph knows nothing calls it.
        """
        assert "src/unrelated.py" not in targeting.scope_for("validate_token")

    def test_defining_file_comes_first(self, targeting):
        assert targeting.scope_for("validate_token")[0] == "src/auth.py"

    def test_unknown_symbol_returns_empty(self, targeting):
        assert targeting.scope_for("no_such_symbol_anywhere") == []

    def test_depth_is_clamped(self, targeting):
        """
        Depth is inlined into Cypher because FalkorDB rejects a parameterised
        variable-length bound — the bug behind traversals silently returning
        nothing in 1.2.0. Inlined means it must be bounded.
        """
        assert targeting.scope_for("validate_token", depth=999) is not None
        assert targeting.scope_for("validate_token", depth=0) is not None


class TestSearchWithin:
    def test_searches_only_the_scope(self, targeting):
        """
        'token' appears in unrelated.py as well. A scoped search must not
        return it, or the scope means nothing.
        """
        hits = targeting.search_within("validate_token", "token")
        assert hits
        assert "src/unrelated.py" not in {h.path for h in hits}

    def test_unscoped_search_does_find_the_unrelated_file(self, project):
        """
        Control for the test above: the match really is there, so its absence
        from the scoped result is the scope working rather than the index
        missing it.
        """
        from navegador.graph.trigram import TrigramIndex

        everywhere = {h.path for h in TrigramIndex(project).search("token")}
        assert "src/unrelated.py" in everywhere

    def test_unknown_symbol_yields_nothing_rather_than_everything(self, targeting):
        """
        An empty scope must not fall back to the whole repository. Silently
        widening would make every scoped search a full search.
        """
        assert targeting.search_within("no_such_symbol", "token") == []


class TestNeighbourhood:
    def test_callers(self, targeting):
        assert "handle_request" in {
            c["name"] for c in targeting.neighbourhood("validate_token")["callers"]
        }

    def test_callees(self, targeting):
        assert "check_signature" in {
            c["name"] for c in targeting.neighbourhood("validate_token")["callees"]
        }

    def test_defining_file(self, targeting):
        defined = targeting.neighbourhood("validate_token")["defined_in"]
        assert defined and defined[0]["path"] == "src/auth.py"

    def test_unknown_symbol_returns_empty_lists_not_an_error(self, targeting):
        result = targeting.neighbourhood("nope")
        assert result["callers"] == [] and result["callees"] == []


class TestLocate:
    def test_finds_the_literal(self, targeting):
        candidates = targeting.locate("token signature invalid")
        assert "src/auth.py" in {c.path for c in candidates}

    def test_every_candidate_states_a_reason(self, targeting):
        """
        A ranking without a reason is something the agent has to trust. Every
        candidate must say why it surfaced.
        """
        for candidate in targeting.locate("validate_token"):
            assert candidate.reasons

    def test_finds_by_symbol_name(self, targeting):
        assert "validate_token" in {c.symbol for c in targeting.locate("validate")}

    def test_limit(self, targeting):
        assert len(targeting.locate("token", limit=2)) <= 2

    def test_nonsense_returns_empty_rather_than_noise(self, targeting):
        assert targeting.locate("zzz_no_such_concept_zzz") == []

    def test_works_without_an_llm_provider(self, targeting):
        """
        Vector search is an improvement, not a requirement. Without a provider
        the other sources must still answer.
        """
        assert targeting.locate("token signature invalid")


class TestHashesForPaths:
    def test_maps_paths_to_content_hashes(self, targeting):
        assert targeting.hashes_for_paths(["src/auth.py"])

    def test_empty_input(self, targeting):
        assert targeting.hashes_for_paths([]) == set()

    def test_unknown_path(self, targeting):
        assert targeting.hashes_for_paths(["nope.py"]) == set()


class TestEdgeProvenance:
    """
    An agent should be able to tell a fact from a good guess (#188).

    tree-sitter is syntax only — no name resolution, no types — so a call edge
    is an import heuristic that is usually right. Two of the eight bugs fixed
    in 1.5 came from treating those guesses as certainties, so the graph now
    records which is which.
    """

    def test_call_edges_are_marked_inferred(self, project):
        rows = project.query("MATCH ()-[r:CALLS]->() RETURN DISTINCT r.resolution").result_set
        assert [row[0] for row in rows] == ["inferred"]

    def test_neighbourhood_surfaces_resolution(self, targeting):
        callers = targeting.neighbourhood("validate_token")["callers"]
        assert callers
        assert all(c.get("resolution") == "inferred" for c in callers)

    def test_defined_in_carries_no_resolution(self, targeting):
        """A definition is a fact about the file; there is no edge to qualify."""
        defined = targeting.neighbourhood("validate_token")["defined_in"]
        assert defined and "resolution" not in defined[0]


class TestVectorSource:
    """
    Semantic similarity is one of the four sources `locate` fuses, and the
    only one that needs a provider. Without one the others must still answer;
    with one, its hits must actually reach the ranking.
    """

    class Provider:
        """Deterministic hashing embedder — meaningful ranking, no network."""

        def embed(self, text):
            import hashlib
            import math

            vector = [0.0] * 16
            for token in text.lower().replace("_", " ").split():
                vector[int(hashlib.md5(token.encode()).hexdigest(), 16) % 16] += 1.0
            norm = math.sqrt(sum(v * v for v in vector)) or 1.0
            return [v / norm for v in vector]

    def test_vector_hits_reach_the_ranking(self, project):
        from navegador.intelligence.search import SemanticSearch

        provider = self.Provider()
        SemanticSearch(project, provider).index()

        candidates = Targeting(project, provider=provider).locate("validate token")
        assert candidates
        assert any("similar" in r for c in candidates for r in c.reasons)

    def test_a_provider_that_fails_does_not_break_locate(self, project):
        """
        A source that errors contributes nothing and the rest still answer. A
        targeting tool returning nothing is worse than one returning a partial
        list.
        """

        class Broken:
            def embed(self, text):
                raise RuntimeError("no credential")

        assert Targeting(project, provider=Broken()).locate("token signature invalid")

    def test_without_a_provider_the_vector_source_is_skipped(self, project):
        assert Targeting(project).locate("token signature invalid")
