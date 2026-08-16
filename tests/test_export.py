# Copyright CONFLICT LLC 2026 (weareconflict.com)
"""
Tests for navegador.graph.export — text-based graph export and import.

Real embedded stores throughout. The previous version of this file mocked the
store, so `create_edge` returned a truthy MagicMock and every assertion about
edge counts passed — while an actual round trip produced a graph with all of
its nodes and none of its edges (#173). What matters here is what ends up in
the destination graph, not which calls were made.
"""

import json
from pathlib import Path

import pytest

from navegador.graph.export import ExportError, export_graph, import_graph
from navegador.graph.store import GraphStore

# ── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def source(tmp_path_factory):
    store = GraphStore.sqlite(str(tmp_path_factory.mktemp("exp-src") / "graph.db"))
    yield store
    store.close()


@pytest.fixture(scope="module")
def dest(tmp_path_factory):
    store = GraphStore.sqlite(str(tmp_path_factory.mktemp("exp-dst") / "graph.db"))
    yield store
    store.close()


@pytest.fixture(autouse=True)
def _clean(source, dest):
    source.clear()
    dest.clear()
    yield


def seed(store):
    """A graph spanning both key styles: path-keyed files and name-keyed symbols."""
    store.query("CREATE (:File {path: 'app.py', name: 'app.py', lines: 42})")
    store.query("CREATE (:Repository {path: '/repo', name: 'repo'})")
    for name in ("alpha", "beta", "gamma"):
        store.query("CREATE (:Function {name: $n, file_path: 'app.py'})", {"n": name})
    store.query("MATCH (f:File), (fn:Function) CREATE (f)-[:CONTAINS]->(fn)")
    store.query("MATCH (f:File), (r:Repository) CREATE (f)-[:BELONGS_TO]->(r)")
    store.query(
        "MATCH (a:Function {name: 'alpha'}), (b:Function {name: 'beta'}) "
        "CREATE (a)-[:CALLS {count: 7}]->(b)"
    )


# ── Round trip ─────────────────────────────────────────────────────────────


class TestRoundTrip:
    def test_every_edge_survives(self, source, dest, tmp_path):
        """The #173 regression: edges must arrive, not just be counted."""
        seed(source)
        assert source.edge_count() > 0
        path = tmp_path / "graph.jsonl"
        export_graph(source, path)
        import_graph(dest, path)
        assert dest.edge_count() == source.edge_count()

    def test_every_node_survives(self, source, dest, tmp_path):
        seed(source)
        path = tmp_path / "graph.jsonl"
        export_graph(source, path)
        import_graph(dest, path)
        assert dest.node_count() == source.node_count()

    def test_reported_counts_match_reality(self, source, dest, tmp_path):
        """Counts are of what was created, not of lines read."""
        seed(source)
        path = tmp_path / "graph.jsonl"
        export_graph(source, path)
        stats = import_graph(dest, path)
        assert stats["nodes"] == dest.node_count()
        assert stats["edges"] == dest.edge_count()

    def test_labels_preserved(self, source, dest, tmp_path):
        seed(source)
        path = tmp_path / "graph.jsonl"
        export_graph(source, path)
        import_graph(dest, path)

        def histogram(store):
            rows = store.query(
                "MATCH (n) RETURN labels(n)[0] AS l, count(n) AS c ORDER BY l"
            ).result_set
            return {r[0]: r[1] for r in rows}

        assert histogram(dest) == histogram(source)

    def test_edge_types_preserved(self, source, dest, tmp_path):
        seed(source)
        path = tmp_path / "graph.jsonl"
        export_graph(source, path)
        import_graph(dest, path)

        def histogram(store):
            rows = store.query(
                "MATCH ()-[r]->() RETURN type(r) AS t, count(r) AS c ORDER BY t"
            ).result_set
            return {r[0]: r[1] for r in rows}

        assert histogram(dest) == histogram(source)

    def test_node_properties_preserved(self, source, dest, tmp_path):
        seed(source)
        path = tmp_path / "graph.jsonl"
        export_graph(source, path)
        import_graph(dest, path)
        rows = dest.query("MATCH (f:File {path: 'app.py'}) RETURN f.lines").result_set
        assert rows[0][0] == 42

    def test_edge_properties_preserved(self, source, dest, tmp_path):
        seed(source)
        path = tmp_path / "graph.jsonl"
        export_graph(source, path)
        import_graph(dest, path)
        rows = dest.query("MATCH ()-[r:CALLS]->() RETURN r.count").result_set
        assert rows[0][0] == 7

    def test_path_keyed_and_name_keyed_endpoints_both_resolve(self, source, dest, tmp_path):
        """
        File→Repository is path-keyed on both ends; File→Function is not.

        The original importer keyed the source on file_path and the target on
        path, so any edge whose endpoints did not share a key style was lost.
        """
        seed(source)
        path = tmp_path / "graph.jsonl"
        export_graph(source, path)
        import_graph(dest, path)
        assert dest.query("MATCH ()-[r:BELONGS_TO]->() RETURN count(r)").result_set[0][0] == 1
        assert dest.query("MATCH ()-[r:CONTAINS]->() RETURN count(r)").result_set[0][0] == 3

    def test_empty_graph_round_trips(self, source, dest, tmp_path):
        path = tmp_path / "graph.jsonl"
        assert export_graph(source, path) == {"nodes": 0, "edges": 0}
        assert import_graph(dest, path) == {"nodes": 0, "edges": 0}

    def test_repeat_import_is_idempotent(self, source, dest, tmp_path):
        seed(source)
        path = tmp_path / "graph.jsonl"
        export_graph(source, path)
        first = import_graph(dest, path)
        second = import_graph(dest, path)
        assert first == second
        assert dest.edge_count() == source.edge_count()


# ── File handling ──────────────────────────────────────────────────────────


class TestFileHandling:
    def test_creates_output_file(self, source, tmp_path):
        path = tmp_path / "graph.jsonl"
        export_graph(source, path)
        assert path.exists()

    def test_creates_parent_directories(self, source, tmp_path):
        path = tmp_path / "deep" / "nested" / "graph.jsonl"
        export_graph(source, path)
        assert path.exists()

    def test_writes_one_json_object_per_line(self, source, tmp_path):
        seed(source)
        path = tmp_path / "graph.jsonl"
        stats = export_graph(source, path)
        lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        assert len(lines) == stats["nodes"] + stats["edges"]
        assert all(json.loads(ln)["kind"] in ("node", "edge") for ln in lines)

    def test_output_is_deterministic(self, source, tmp_path):
        """The format exists to be committed, so byte stability matters."""
        seed(source)
        first, second = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
        export_graph(source, first)
        export_graph(source, second)
        assert first.read_bytes() == second.read_bytes()

    def test_missing_input_raises(self, dest):
        with pytest.raises(FileNotFoundError):
            import_graph(dest, "/nonexistent/graph.jsonl")

    def test_blank_lines_are_skipped(self, dest, tmp_path):
        path = tmp_path / "graph.jsonl"
        record = json.dumps(
            {
                "kind": "node",
                "id": "Function::foo",
                "type": "Function",
                "name": "foo",
                "props": {"name": "foo", "file_path": "app.py"},
            }
        )
        path.write_text(f"\n{record}\n\n", encoding="utf-8")
        assert import_graph(dest, path)["nodes"] == 1

    def test_clear_wipes_existing_content(self, source, dest, tmp_path):
        dest.query("CREATE (:Stale {name: 'old'})")
        seed(source)
        path = tmp_path / "graph.jsonl"
        export_graph(source, path)
        import_graph(dest, path)
        assert dest.query("MATCH (n:Stale) RETURN count(n)").result_set[0][0] == 0

    def test_no_clear_is_additive(self, source, dest, tmp_path):
        dest.query("CREATE (:Stale {name: 'old'})")
        seed(source)
        path = tmp_path / "graph.jsonl"
        export_graph(source, path)
        import_graph(dest, path, clear=False)
        assert dest.query("MATCH (n:Stale) RETURN count(n)").result_set[0][0] == 1


# ── Legacy format ──────────────────────────────────────────────────────────


class TestLegacyFormat:
    """Exports written before edges referenced node ids must still import."""

    @staticmethod
    def write_legacy(path: Path) -> None:
        records = [
            {"kind": "node", "label": "File", "props": {"name": "app.py", "path": "app.py"}},
            {
                "kind": "node",
                "label": "Function",
                "props": {"name": "alpha", "file_path": "app.py"},
            },
            {
                "kind": "node",
                "label": "Function",
                "props": {"name": "beta", "file_path": "app.py"},
            },
            {
                "kind": "edge",
                "type": "CALLS",
                "from": {"label": "Function", "name": "alpha", "path": "app.py"},
                "to": {"label": "Function", "name": "beta", "path": "app.py"},
            },
            {
                "kind": "edge",
                "type": "CONTAINS",
                "from": {"label": "File", "name": "app.py", "path": "app.py"},
                "to": {"label": "Function", "name": "alpha", "path": "app.py"},
            },
        ]
        path.write_text(
            "\n".join(json.dumps(r, sort_keys=True) for r in records) + "\n", encoding="utf-8"
        )

    def test_legacy_nodes_import(self, dest, tmp_path):
        path = tmp_path / "legacy.jsonl"
        self.write_legacy(path)
        import_graph(dest, path)
        assert dest.node_count() == 3

    def test_legacy_edges_import(self, dest, tmp_path):
        """These are exactly the edges the original importer dropped."""
        path = tmp_path / "legacy.jsonl"
        self.write_legacy(path)
        import_graph(dest, path)
        assert dest.edge_count() == 2

    def test_legacy_symbol_to_symbol_edge_resolves(self, dest, tmp_path):
        path = tmp_path / "legacy.jsonl"
        self.write_legacy(path)
        import_graph(dest, path)
        rows = dest.query(
            "MATCH (a:Function)-[:CALLS]->(b:Function) RETURN a.name, b.name"
        ).result_set
        assert rows == [["alpha", "beta"]]


# ── Verification ───────────────────────────────────────────────────────────


class TestVerification:
    def test_unresolvable_edges_fail_loudly(self, dest, tmp_path):
        """
        A file describing edges whose endpoints are absent must not import
        quietly: the result is disconnected nodes that answer every structural
        query with an empty set, which reads as a valid negative.
        """
        path = tmp_path / "broken.jsonl"
        records = [
            {
                "kind": "node",
                "id": "Function::alpha",
                "type": "Function",
                "name": "alpha",
                "props": {"name": "alpha", "file_path": "app.py"},
            },
            {
                "kind": "edge",
                "type": "CALLS",
                "source": "Function::alpha",
                "target": "Function::missing",
                "props": {},
            },
        ]
        path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")

        with pytest.raises(ExportError, match="did not reproduce"):
            import_graph(dest, path)

    def test_additive_import_does_not_verify(self, dest, tmp_path):
        """With clear=False the destination legitimately differs; no verification."""
        path = tmp_path / "partial.jsonl"
        record = {
            "kind": "edge",
            "type": "CALLS",
            "source": "Function::a",
            "target": "Function::b",
            "props": {},
        }
        path.write_text(json.dumps(record) + "\n", encoding="utf-8")
        assert import_graph(dest, path, clear=False)["edges"] == 0


# ── Interop with the conflict-kg encoding ──────────────────────────────────


class TestFormatAgreement:
    def test_jsonl_and_conflict_kg_agree_on_the_graph(self, source, dest, tmp_path):
        """Both serializations share one identity scheme, so both round trip."""
        from navegador.graph.interchange import export_conflict_kg, import_conflict_kg

        seed(source)
        jsonl, kg = tmp_path / "g.jsonl", tmp_path / "g.json"
        export_graph(source, jsonl)
        export_conflict_kg(source, kg)

        import_graph(dest, jsonl)
        via_jsonl = (dest.node_count(), dest.edge_count())
        import_conflict_kg(dest, kg)
        via_kg = (dest.node_count(), dest.edge_count())

        assert via_jsonl == via_kg == (source.node_count(), source.edge_count())
