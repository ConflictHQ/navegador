# Copyright CONFLICT LLC 2026 (weareconflict.com)
"""
Tests for navegador.graph.transfer — lossless graph copying between stores.

Real embedded stores throughout. The property under test is that a copy is
*exactly* the source: same nodes, same edges, same properties, and a hard
failure rather than a success message when it is not.
"""

import pytest

from navegador.graph.store import GraphStore
from navegador.graph.transfer import (
    MIGRATION_KEY,
    TransferError,
    _safe_identifier,
    copy_graph,
)

# ── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def source(tmp_path_factory):
    store = GraphStore.sqlite(str(tmp_path_factory.mktemp("xfer-src") / "graph.db"))
    yield store
    store.close()


@pytest.fixture(scope="module")
def dest(tmp_path_factory):
    store = GraphStore.sqlite(str(tmp_path_factory.mktemp("xfer-dst") / "graph.db"))
    yield store
    store.close()


@pytest.fixture(autouse=True)
def _clean(source, dest):
    source.clear()
    dest.clear()
    yield


def seed(store, functions=3):
    """A small graph with two labels, two edge types, and edge properties."""
    store.query("CREATE (:File {path: 'app.py', name: 'app.py', lines: 120})")
    for i in range(functions):
        store.query(
            "CREATE (:Function {name: $name, file_path: 'app.py', complexity: $c})",
            {"name": f"fn_{i}", "c": i},
        )
    store.query(
        "MATCH (f:File {path: 'app.py'}), (fn:Function) "
        "CREATE (f)-[:CONTAINS {order: fn.complexity}]->(fn)"
    )
    store.query(
        "MATCH (a:Function {name: 'fn_0'}), (b:Function {name: 'fn_1'}) "
        "CREATE (a)-[:CALLS {count: 7}]->(b)"
    )


# ── Fidelity ───────────────────────────────────────────────────────────────


class TestCopyFidelity:
    def test_counts_match_source(self, source, dest):
        seed(source)
        stats = copy_graph(source, dest)
        assert stats["nodes"] == source.node_count()
        assert stats["edges"] == source.edge_count()

    def test_edges_actually_arrive(self, source, dest):
        """The regression that motivated this module: edges must not vanish."""
        seed(source)
        assert source.edge_count() > 0
        copy_graph(source, dest)
        assert dest.edge_count() == source.edge_count()

    def test_labels_preserved(self, source, dest):
        seed(source)
        copy_graph(source, dest)

        def histogram(store):
            rows = store.query(
                "MATCH (n) RETURN labels(n)[0] AS l, count(n) AS c ORDER BY l"
            ).result_set
            return {r[0]: r[1] for r in rows}

        assert histogram(dest) == histogram(source)

    def test_edge_types_preserved(self, source, dest):
        seed(source)
        copy_graph(source, dest)

        def histogram(store):
            rows = store.query(
                "MATCH ()-[r]->() RETURN type(r) AS t, count(r) AS c ORDER BY t"
            ).result_set
            return {r[0]: r[1] for r in rows}

        assert histogram(dest) == histogram(source)

    def test_node_properties_preserved(self, source, dest):
        seed(source)
        copy_graph(source, dest)
        rows = dest.query(
            "MATCH (n:Function {name: 'fn_2'}) RETURN n.file_path, n.complexity"
        ).result_set
        assert rows[0][0] == "app.py"
        assert rows[0][1] == 2

    def test_edge_properties_preserved(self, source, dest):
        seed(source)
        copy_graph(source, dest)
        rows = dest.query("MATCH ()-[r:CALLS]->() RETURN r.count").result_set
        assert rows[0][0] == 7

    def test_migration_key_is_removed(self, source, dest):
        """The temporary id must not leak into the migrated graph."""
        seed(source)
        copy_graph(source, dest)
        rows = dest.query(
            f"MATCH (n) WHERE n.{MIGRATION_KEY} IS NOT NULL RETURN count(n)"
        ).result_set
        assert rows[0][0] == 0

    def test_source_is_left_untouched(self, source, dest):
        seed(source)
        before = (source.node_count(), source.edge_count())
        copy_graph(source, dest)
        assert (source.node_count(), source.edge_count()) == before
        rows = source.query(
            f"MATCH (n) WHERE n.{MIGRATION_KEY} IS NOT NULL RETURN count(n)"
        ).result_set
        assert rows[0][0] == 0

    def test_repeat_copy_is_idempotent(self, source, dest):
        """Re-running a migration must not double the graph."""
        seed(source)
        first = copy_graph(source, dest)
        second = copy_graph(source, dest)
        assert first["nodes"] == second["nodes"]
        assert first["edges"] == second["edges"]

    def test_empty_source_copies_cleanly(self, source, dest):
        stats = copy_graph(source, dest)
        assert stats == {
            "nodes": 0,
            "edges": 0,
            "source_nodes": 0,
            "source_edges": 0,
            "nodes_written": 0,
            "edges_written": 0,
        }

    def test_batching_across_multiple_chunks(self, source, dest):
        seed(source, functions=25)
        stats = copy_graph(source, dest, batch_size=4)
        assert stats["nodes"] == source.node_count()
        assert stats["edges"] == source.edge_count()

    def test_nodes_without_edges_still_copy(self, source, dest):
        source.query("CREATE (:Concept {name: 'isolated'})")
        copy_graph(source, dest)
        rows = dest.query("MATCH (n:Concept {name: 'isolated'}) RETURN count(n)").result_set
        assert rows[0][0] == 1


class TestClearBehaviour:
    def test_clear_wipes_prior_destination_content(self, source, dest):
        dest.query("CREATE (:Stale {name: 'old'})")
        seed(source)
        copy_graph(source, dest)
        rows = dest.query("MATCH (n:Stale) RETURN count(n)").result_set
        assert rows[0][0] == 0

    def test_no_clear_is_additive(self, source, dest):
        dest.query("CREATE (:Stale {name: 'old'})")
        seed(source)
        copy_graph(source, dest, clear=False)
        rows = dest.query("MATCH (n:Stale) RETURN count(n)").result_set
        assert rows[0][0] == 1
        assert dest.node_count() == source.node_count() + 1


class TestSafety:
    @pytest.mark.parametrize(
        "bad",
        ["has space", "1leading", "with-dash", "back`tick", "drop`) DELETE (n", ""],
    )
    def test_unsafe_identifiers_rejected(self, bad):
        with pytest.raises(TransferError):
            _safe_identifier(bad, "label")

    @pytest.mark.parametrize("good", ["Function", "_private", "Node2", "CALLS"])
    def test_safe_identifiers_accepted(self, good):
        assert _safe_identifier(good, "label") == good

    def test_verification_failure_raises(self, source, dest, monkeypatch):
        """A short copy must fail loudly, never report success."""
        seed(source)
        monkeypatch.setattr("navegador.graph.transfer._copy_edges", lambda *a, **k: 0)
        with pytest.raises(TransferError, match="did not verify"):
            copy_graph(source, dest)
