# Copyright CONFLICT LLC 2026 (weareconflict.com)
"""
Regression tests for #166 — testmap must not invent cross-repository TESTS edges.

The heuristic stripped ``test_``, tried ever-shorter prefixes, and took the
first symbol with that name *anywhere in the graph*. In a federated graph
``test_request_returns_200`` degraded to the candidate ``request`` and linked to
an unrelated repository's method with full confidence.

A wrong TESTS edge is worse than a missing one: impact and context queries cite
it confidently, so users stop trusting the graph.
"""

import pytest

from navegador.analysis.testmap import (
    GENERIC_NAMES,
    _is_test_path,
    _shared_prefix_depth,
)

# Aliased: pytest tries to collect any class named Test* found in a test module.
from navegador.analysis.testmap import TestMapper as Mapper
from navegador.graph.store import GraphStore


@pytest.fixture()
def store(tmp_path_factory):
    s = GraphStore.sqlite(str(tmp_path_factory.mktemp("testmap") / "graph.db"))
    yield s
    s.close()


def add_file(store, path, repo):
    store.query(
        "MERGE (f:File {path: $p}) SET f.name = $p "
        "MERGE (r:Repository {path: $repo}) SET r.name = $repo "
        "MERGE (f)-[:BELONGS_TO]->(r)",
        {"p": path, "repo": repo},
    )


def add_symbol(store, name, path, repo, label="Function"):
    add_file(store, path, repo)
    store.query(
        f"MERGE (n:{label} {{name: $n, file_path: $p}})",
        {"n": name, "p": path},
    )


def links_for(result, test_name):
    return [lnk for lnk in result.links if lnk.test_name == test_name]


# ── The reported collision ─────────────────────────────────────────────────


class TestCrossRepositoryCollisions:
    @pytest.fixture(autouse=True)
    def fixture(self, store):
        """Three repositories sharing generic verb names."""
        # Repository A — production methods with generic names
        for name in ("request", "update", "delete", "publish", "resolve"):
            add_symbol(store, name, f"repo_a/service/{name}.py", "org/repo-a")
        # Repository B — tests exercising unrelated modules
        add_symbol(
            store, "test_request_returns_200", "repo_b/tests/test_api.py", "org/repo-b"
        )
        add_symbol(store, "test_publish_event", "repo_b/tests/test_bus.py", "org/repo-b")
        # Repository C — shell/infra helpers with the same names
        for name in ("request", "publish"):
            add_symbol(store, name, f"repo_c/scripts/{name}.sh", "org/repo-c")

    def test_generic_name_does_not_link_across_repositories(self, store):
        result = Mapper(store).map_tests()
        for link in result.links:
            assert link.prod_name not in GENERIC_NAMES or link.confidence >= 0.5

    def test_no_edge_is_created_from_a_bare_generic_verb(self, store):
        """`test_request_returns_200` must not attach to some other repo's `request`."""
        result = Mapper(store).map_tests()
        assert not links_for(result, "test_request_returns_200")

    def test_unresolved_tests_are_reported_not_guessed(self, store):
        result = Mapper(store).map_tests()
        reported = {t["name"] for t in result.unmatched_tests} | {
            a["test_name"] for a in result.ambiguous
        }
        assert "test_request_returns_200" in reported

    def test_no_tests_edges_land_in_the_graph(self, store):
        Mapper(store).map_tests()
        rows = store.query("MATCH ()-[r:TESTS]->() RETURN count(r)").result_set
        assert rows[0][0] == 0


# ── What must still work ───────────────────────────────────────────────────


class TestLegitimateMappingsSurvive:
    def test_direct_call_evidence_is_taken(self, store):
        add_symbol(store, "validate_token", "src/auth.py", "org/app")
        add_symbol(store, "test_validate_token", "tests/test_auth.py", "org/app")
        store.query(
            "MATCH (t {name: 'test_validate_token'}), (p {name: 'validate_token'}) "
            "MERGE (t)-[:CALLS]->(p)"
        )
        result = Mapper(store).map_tests()
        assert links_for(result, "test_validate_token")[0].source == "calls"

    def test_call_evidence_is_full_confidence(self, store):
        add_symbol(store, "validate_token", "src/auth.py", "org/app")
        add_symbol(store, "test_validate_token", "tests/test_auth.py", "org/app")
        store.query(
            "MATCH (t {name: 'test_validate_token'}), (p {name: 'validate_token'}) "
            "MERGE (t)-[:CALLS]->(p)"
        )
        result = Mapper(store).map_tests()
        assert links_for(result, "test_validate_token")[0].confidence == 1.0

    def test_distinctive_name_in_the_same_repo_still_links(self, store):
        add_symbol(store, "reconcile_ledger_entries", "src/ledger.py", "org/app")
        add_symbol(
            store, "test_reconcile_ledger_entries", "tests/test_ledger.py", "org/app"
        )
        result = Mapper(store).map_tests()
        assert links_for(result, "test_reconcile_ledger_entries")

    def test_heuristic_links_carry_evidence(self, store):
        add_symbol(store, "reconcile_ledger_entries", "src/ledger.py", "org/app")
        add_symbol(
            store, "test_reconcile_ledger_entries", "tests/test_ledger.py", "org/app"
        )
        link = links_for(Mapper(store).map_tests(), "test_reconcile_ledger_entries")[0]
        assert link.evidence
        assert 0 < link.confidence <= 1.0

    def test_edge_records_confidence_and_evidence(self, store):
        add_symbol(store, "reconcile_ledger_entries", "src/ledger.py", "org/app")
        add_symbol(
            store, "test_reconcile_ledger_entries", "tests/test_ledger.py", "org/app"
        )
        Mapper(store).map_tests()
        rows = store.query(
            "MATCH ()-[r:TESTS]->() RETURN r.confidence, r.evidence"
        ).result_set
        assert rows and rows[0][0] is not None and rows[0][1]


class TestTestCodeIsNotATarget:
    def test_helper_in_a_test_module_is_never_the_target(self, store):
        """Naming alone made a test-module helper a valid production match."""
        add_symbol(store, "build_payload", "tests/helpers.py", "org/app")
        add_symbol(store, "test_build_payload", "tests/test_api.py", "org/app")
        result = Mapper(store).map_tests()
        assert not links_for(result, "test_build_payload")

    @pytest.mark.parametrize(
        "path",
        [
            "tests/helpers.py",
            "src/tests/util.py",
            "spec/thing.rb",
            "app/__tests__/util.js",
            "src/thing_test.go",
            "src/test_thing.py",
            "src/thing.spec.ts",
        ],
    )
    def test_test_paths_are_recognised(self, path):
        assert _is_test_path(path)

    @pytest.mark.parametrize(
        "path", ["src/app.py", "lib/latest/thing.py", "contest/entry.py", ""]
    )
    def test_production_paths_are_not(self, path):
        assert not _is_test_path(path)


class TestAmbiguityIsReported:
    def test_equally_plausible_candidates_produce_no_edge(self, store):
        """Two identical-scoring targets: report, do not pick one at random."""
        add_symbol(store, "reconcile_entries", "src/alpha/ledger.py", "org/app")
        add_symbol(store, "reconcile_entries", "src/beta/ledger.py", "org/app")
        add_symbol(store, "test_reconcile_entries", "tests/test_ledger.py", "org/app")

        result = Mapper(store).map_tests()
        assert not links_for(result, "test_reconcile_entries")
        assert any(a["test_name"] == "test_reconcile_entries" for a in result.ambiguous)

    def test_ambiguity_lists_the_candidates(self, store):
        add_symbol(store, "reconcile_entries", "src/alpha/ledger.py", "org/app")
        add_symbol(store, "reconcile_entries", "src/beta/ledger.py", "org/app")
        add_symbol(store, "test_reconcile_entries", "tests/test_ledger.py", "org/app")

        entry = [
            a
            for a in Mapper(store).map_tests().ambiguous
            if a["test_name"] == "test_reconcile_entries"
        ][0]
        assert len(entry["candidates"]) >= 2


class TestPathHelpers:
    def test_shared_prefix_depth(self):
        assert _shared_prefix_depth("src/app/api", "src/app/db") == 2
        assert _shared_prefix_depth("src/app", "lib/app") == 0
        assert _shared_prefix_depth("src", "src") == 1


class TestConfidenceFloor:
    def test_a_weak_cross_repo_match_is_below_the_floor(self, store):
        """Distinctive enough to be a candidate, too weak to assert by default."""
        add_symbol(store, "reconcile", "repo_a/service/ledger.py", "org/repo-a")
        add_symbol(
            store, "test_reconcile_entries", "repo_b/tests/test_ledger.py", "org/repo-b"
        )

        strict = Mapper(store).map_tests()
        assert not links_for(strict, "test_reconcile_entries")

        store.query("MATCH ()-[r:TESTS]->() DELETE r")
        loose = Mapper(store).map_tests(min_confidence=0.0)
        assert links_for(loose, "test_reconcile_entries")
        assert links_for(loose, "test_reconcile_entries")[0].confidence < 0.5

    def test_a_generic_verb_is_never_a_candidate_at_any_floor(self, store):
        """
        No confidence setting should resurrect `test_publish_event` → `publish`.
        A bare generic verb carries no signal to weigh in the first place.
        """
        add_symbol(store, "publish", "repo_a/service/bus.py", "org/repo-a")
        add_symbol(store, "test_publish_event", "repo_b/tests/test_bus.py", "org/repo-b")

        result = Mapper(store).map_tests(min_confidence=0.0)
        assert not links_for(result, "test_publish_event")
