# Copyright CONFLICT LLC 2026 (weareconflict.com)
"""
Tests for navegador.graph.interchange — the conflict-kg/v1 canonical format.

These run against real embedded FalkorDB stores (no graph mocks): seed a
graph, export, re-import, and assert on actual graph state.
"""

import json
import socket
import sqlite3
import urllib.request
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from navegador.cli.commands import main
from navegador.explorer import ExplorerServer
from navegador.graph.interchange import (
    FORMAT,
    collect_graph,
    export_conflict_kg,
    import_conflict_kg,
    is_conflict_kg_json,
    is_sqlite_file,
)
from navegador.graph.store import GraphStore

# ── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def source_store(tmp_path_factory):
    store = GraphStore.sqlite(str(tmp_path_factory.mktemp("kg-src") / "graph.db"))
    yield store
    store.close()


@pytest.fixture(scope="module")
def target_store(tmp_path_factory):
    store = GraphStore.sqlite(str(tmp_path_factory.mktemp("kg-dst") / "graph.db"))
    yield store
    store.close()


@pytest.fixture(autouse=True)
def _clean_stores(source_store, target_store):
    source_store.clear()
    target_store.clear()
    yield


def _seed(store: GraphStore) -> None:
    """Small graph: two functions, a concept, a call edge with props, an annotation."""
    store.create_node("Function", {"name": "foo", "file_path": "app.py", "line": 1})
    store.create_node("Function", {"name": "bar", "file_path": "app.py", "line": 9})
    store.create_node("Concept", {"name": "Payments"})
    store.create_edge(
        "Function",
        {"name": "foo", "file_path": "app.py"},
        "CALLS",
        "Function",
        {"name": "bar", "file_path": "app.py"},
        {"count": 2},
    )
    store.create_edge(
        "Concept",
        {"name": "Payments"},
        "ANNOTATES",
        "Function",
        {"name": "foo", "file_path": "app.py"},
    )


# ── collect_graph ──────────────────────────────────────────────────────────


class TestCollectGraph:
    def test_canonical_node_shape(self, source_store):
        _seed(source_store)
        nodes, _ = collect_graph(source_store)
        assert len(nodes) == 3
        for node in nodes:
            assert set(node) == {"id", "name", "type", "props"}

    def test_type_is_label_and_name_is_top_level(self, source_store):
        _seed(source_store)
        nodes, _ = collect_graph(source_store)
        foo = next(n for n in nodes if n["name"] == "foo")
        assert foo["type"] == "Function"
        assert "name" not in foo["props"]
        assert foo["props"]["line"] == 1

    def test_edges_reference_node_ids(self, source_store):
        _seed(source_store)
        nodes, edges = collect_graph(source_store)
        ids = {n["id"] for n in nodes}
        assert len(edges) == 2
        for edge in edges:
            assert edge["source"] in ids
            assert edge["target"] in ids

    def test_edge_props_preserved(self, source_store):
        _seed(source_store)
        _, edges = collect_graph(source_store)
        calls = next(e for e in edges if e["type"] == "CALLS")
        assert calls["props"] == {"count": 2}

    def test_ids_stable_across_exports(self, source_store):
        _seed(source_store)
        first, _ = collect_graph(source_store)
        second, _ = collect_graph(source_store)
        assert [n["id"] for n in first] == [n["id"] for n in second]

    def test_id_collision_gets_suffix(self, source_store):
        # Memory nodes are keyed (name, repo): same name in two repos is two
        # nodes whose content-derived base id collides.
        source_store.create_node(
            "Memory", {"name": "note", "memory_type": "fact", "repo": "repo-a"}
        )
        source_store.create_node(
            "Memory", {"name": "note", "memory_type": "fact", "repo": "repo-b"}
        )
        nodes, _ = collect_graph(source_store)
        ids = sorted(n["id"] for n in nodes)
        assert ids == ["Memory::note", "Memory::note#1"]


# ── JSON encoding ──────────────────────────────────────────────────────────


class TestJsonExport:
    def test_writes_format_field(self, source_store, tmp_path):
        _seed(source_store)
        out = tmp_path / "kg.json"
        stats = export_conflict_kg(source_store, out)
        assert stats == {"nodes": 3, "edges": 2, "encoding": "json"}
        doc = json.loads(out.read_text())
        assert doc["format"] == FORMAT

    def test_deterministic_output(self, source_store, tmp_path):
        _seed(source_store)
        a, b = tmp_path / "a.json", tmp_path / "b.json"
        export_conflict_kg(source_store, a)
        export_conflict_kg(source_store, b)
        assert a.read_bytes() == b.read_bytes()

    def test_round_trip_restores_graph(self, source_store, target_store, tmp_path):
        _seed(source_store)
        out = tmp_path / "kg.json"
        export_conflict_kg(source_store, out)

        stats = import_conflict_kg(target_store, out)
        assert stats == {"nodes": 3, "edges": 2}
        assert target_store.node_count() == 3
        assert target_store.edge_count() == 2

        result = target_store.query(
            "MATCH (:Concept {name: 'Payments'})-[r:ANNOTATES]->(f:Function) RETURN f.name"
        )
        assert result.result_set == [["foo"]]
        result = target_store.query("MATCH ()-[r:CALLS]->() RETURN r.count")
        assert result.result_set == [[2]]


# ── SQLite encoding ────────────────────────────────────────────────────────


class TestSqliteExport:
    def test_db_extension_selects_sqlite(self, source_store, tmp_path):
        _seed(source_store)
        out = tmp_path / "kg.db"
        stats = export_conflict_kg(source_store, out)
        assert stats["encoding"] == "sqlite"
        assert is_sqlite_file(out)

    def test_schema_matches_contract(self, source_store, tmp_path):
        _seed(source_store)
        out = tmp_path / "kg.db"
        export_conflict_kg(source_store, out)
        conn = sqlite3.connect(out)
        try:
            tables = {
                r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            indexes = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx%'"
                )
            }
            node_cols = [r[1] for r in conn.execute("PRAGMA table_info(nodes)")]
            edge_cols = [r[1] for r in conn.execute("PRAGMA table_info(edges)")]
        finally:
            conn.close()
        assert {"nodes", "edges"} <= tables
        assert indexes == {"idx_edges_source", "idx_edges_target"}
        assert node_cols == ["id", "name", "type", "props"]
        assert edge_cols == ["source", "target", "type", "props"]

    def test_round_trip_restores_graph(self, source_store, target_store, tmp_path):
        _seed(source_store)
        out = tmp_path / "kg.db"
        export_conflict_kg(source_store, out)

        stats = import_conflict_kg(target_store, out)
        assert stats == {"nodes": 3, "edges": 2}
        assert target_store.node_count() == 3
        assert target_store.edge_count() == 2

    def test_overwrites_existing_file(self, source_store, tmp_path):
        _seed(source_store)
        out = tmp_path / "kg.db"
        export_conflict_kg(source_store, out)
        export_conflict_kg(source_store, out)  # must not fail on existing schema
        conn = sqlite3.connect(out)
        try:
            count = conn.execute("SELECT count(*) FROM nodes").fetchone()[0]
        finally:
            conn.close()
        assert count == 3


# ── Import edge cases ──────────────────────────────────────────────────────


class TestImportEdgeCases:
    def test_clear_wipes_target(self, source_store, target_store, tmp_path):
        _seed(source_store)
        target_store.create_node("Function", {"name": "stale", "file_path": "old.py"})
        out = tmp_path / "kg.json"
        export_conflict_kg(source_store, out)
        import_conflict_kg(target_store, out, clear=True)
        result = target_store.query("MATCH (n {name: 'stale'}) RETURN count(n)")
        assert result.result_set == [[0]]

    def test_no_clear_keeps_existing(self, source_store, target_store, tmp_path):
        _seed(source_store)
        target_store.create_node("Function", {"name": "keep", "file_path": "old.py"})
        out = tmp_path / "kg.json"
        export_conflict_kg(source_store, out)
        import_conflict_kg(target_store, out, clear=False)
        assert target_store.node_count() == 4

    def test_rejects_non_interchange_json(self, target_store, tmp_path):
        bad = tmp_path / "other.json"
        bad.write_text(json.dumps({"nodes": [], "edges": []}))
        with pytest.raises(ValueError, match="conflict-kg/v1"):
            import_conflict_kg(target_store, bad)

    def test_missing_file_raises(self, target_store, tmp_path):
        with pytest.raises(FileNotFoundError):
            import_conflict_kg(target_store, tmp_path / "nope.json")

    def test_edge_with_unknown_endpoint_skipped(self, target_store, tmp_path):
        doc = {
            "format": FORMAT,
            "nodes": [
                {
                    "id": "Function:a.py:f",
                    "name": "f",
                    "type": "Function",
                    "props": {"file_path": "a.py"},
                }
            ],
            "edges": [
                {
                    "source": "Function:a.py:f",
                    "target": "Function:a.py:ghost",
                    "type": "CALLS",
                    "props": {},
                }
            ],
        }
        path = tmp_path / "dangling.json"
        path.write_text(json.dumps(doc))
        stats = import_conflict_kg(target_store, path)
        assert stats == {"nodes": 1, "edges": 0}


# ── Format detection ───────────────────────────────────────────────────────


class TestDetection:
    def test_is_sqlite_file(self, source_store, tmp_path):
        _seed(source_store)
        db = tmp_path / "kg.db"
        export_conflict_kg(source_store, db)
        assert is_sqlite_file(db)
        text = tmp_path / "kg.json"
        export_conflict_kg(source_store, text)
        assert not is_sqlite_file(text)

    def test_is_conflict_kg_json(self, source_store, tmp_path):
        _seed(source_store)
        kg = tmp_path / "kg.json"
        export_conflict_kg(source_store, kg)
        assert is_conflict_kg_json(kg)

    def test_jsonl_is_not_conflict_kg(self, tmp_path):
        jsonl = tmp_path / "graph.jsonl"
        jsonl.write_text('{"kind": "node", "label": "Function", "props": {"name": "f"}}\n')
        assert not is_conflict_kg_json(jsonl)


# ── Explorer /api/graph?format=conflict-kg ─────────────────────────────────


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _fetch_json(url: str):
    with urllib.request.urlopen(url, timeout=5) as resp:
        return resp.status, json.loads(resp.read().decode())


class TestExplorerFormat:
    def test_conflict_kg_format(self, source_store):
        _seed(source_store)
        port = _free_port()
        with ExplorerServer(source_store, port=port):
            status, data = _fetch_json(f"http://127.0.0.1:{port}/api/graph?format=conflict-kg")
        assert status == 200
        assert data["format"] == FORMAT
        assert {n["type"] for n in data["nodes"]} == {"Function", "Concept"}
        ids = {n["id"] for n in data["nodes"]}
        assert all(e["source"] in ids and e["target"] in ids for e in data["edges"])

    def test_default_shape_unchanged(self, source_store):
        _seed(source_store)
        port = _free_port()
        with ExplorerServer(source_store, port=port):
            status, data = _fetch_json(f"http://127.0.0.1:{port}/api/graph")
        assert status == 200
        assert "format" not in data
        assert all("label" in n for n in data["nodes"])


# ── CLI --format flag and import auto-detect ──────────────────────────────


class TestCli:
    def test_export_conflict_kg_and_reimport(self, source_store, target_store, tmp_path):
        _seed(source_store)
        runner = CliRunner()
        out = tmp_path / "kg.json"

        with patch("navegador.cli.commands._get_store", return_value=source_store):
            result = runner.invoke(main, ["export", str(out), "--format", "conflict-kg", "--json"])
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["encoding"] == "json"
        assert is_conflict_kg_json(out)

        with patch("navegador.cli.commands._get_store", return_value=target_store):
            result = runner.invoke(main, ["import", str(out), "--json"])
        assert result.exit_code == 0, result.output
        assert json.loads(result.output) == {"nodes": 3, "edges": 2}
        assert target_store.node_count() == 3

    def test_export_default_stays_jsonl(self, source_store, tmp_path):
        _seed(source_store)
        runner = CliRunner()
        out = tmp_path / "graph.jsonl"
        with patch("navegador.cli.commands._get_store", return_value=source_store):
            result = runner.invoke(main, ["export", str(out)])
        assert result.exit_code == 0, result.output
        first = json.loads(out.read_text().splitlines()[0])
        assert first["kind"] == "node"
