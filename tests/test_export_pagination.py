"""Regression tests for #129 — export must page past FalkorDB's default
RESULTSET_SIZE (10k rows/query) and be able to target a named central graph.

Run against real embedded FalkorDB stores (no graph mocks)."""

import json
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from navegador.cli.commands import main
from navegador.graph.export import export_graph
from navegador.graph.interchange import collect_graph
from navegador.graph.store import GraphStore, paged_query

# ── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def store(tmp_path_factory):
    s = GraphStore.sqlite(str(tmp_path_factory.mktemp("export-pg") / "graph.db"))
    yield s
    s.close()


@pytest.fixture(autouse=True)
def _clean(store):
    store.clear()


def _seed(store, count: int) -> None:
    for i in range(count):
        store.create_node("Function", {"name": f"fn_{i:03d}", "file_path": "app.py"})
    for i in range(count - 1):
        store.create_edge(
            "Function",
            {"name": f"fn_{i:03d}", "file_path": "app.py"},
            "CALLS",
            "Function",
            {"name": f"fn_{i + 1:03d}", "file_path": "app.py"},
        )


# ── paged_query ────────────────────────────────────────────────────────────


class TestPagedQuery:
    def test_collects_all_rows_across_pages(self, store):
        _seed(store, 7)
        rows = paged_query(store, "MATCH (n:Function) RETURN n.name ORDER BY n.name", page_size=3)
        assert [r[0] for r in rows] == [f"fn_{i:03d}" for i in range(7)]

    def test_exact_page_boundary(self, store):
        _seed(store, 6)
        rows = paged_query(store, "MATCH (n:Function) RETURN n.name ORDER BY n.name", page_size=3)
        assert len(rows) == 6

    def test_empty_graph(self, store):
        assert paged_query(store, "MATCH (n) RETURN n.name ORDER BY n.name") == []


# ── Exporters page below the resultset ceiling ────────────────────────────


class TestExportPagination:
    def test_collect_graph_beyond_page_size(self, store, monkeypatch):
        monkeypatch.setattr("navegador.graph.store._DEFAULT_PAGE_SIZE", 3)
        _seed(store, 8)
        nodes, edges = collect_graph(store)
        names = {n["name"] for n in nodes if n["type"] == "Function"}
        assert names == {f"fn_{i:03d}" for i in range(8)}
        assert len(edges) == 7

    def test_export_graph_beyond_page_size(self, store, monkeypatch, tmp_path):
        monkeypatch.setattr("navegador.graph.store._DEFAULT_PAGE_SIZE", 3)
        _seed(store, 8)
        stats = export_graph(store, tmp_path / "out.jsonl")
        assert stats["nodes"] == 8
        assert stats["edges"] == 7


# ── Named central graphs ───────────────────────────────────────────────────


class TestNamedGraph:
    def test_with_graph_isolates_named_graph(self, store):
        _seed(store, 2)
        named = store.with_graph("navegador_sidecar")
        named.clear()
        named.create_node("Function", {"name": "only_here", "file_path": "x.py"})

        assert named.graph_name == "navegador_sidecar"
        assert named.node_count() == 1
        main_names = paged_query(store, "MATCH (n:Function) RETURN n.name ORDER BY n.name")
        assert ["only_here"] not in main_names

    def test_list_graphs_sees_named_graph(self, store):
        named = store.with_graph("navegador_listed")
        named.create_node("Concept", {"name": "marker"})
        assert "navegador_listed" in store.list_graphs()

    def test_export_cli_targets_named_graph(self, store, tmp_path):
        _seed(store, 3)  # main graph: 3 nodes — must NOT be exported
        named = store.with_graph("navegador_cli")
        named.clear()
        named.create_node("Function", {"name": "central_fn", "file_path": "y.py"})

        out = tmp_path / "named.json"
        runner = CliRunner()
        with patch("navegador.cli.commands._get_store", return_value=store):
            result = runner.invoke(
                main,
                [
                    "export",
                    str(out),
                    "--format",
                    "conflict-kg",
                    "--graph",
                    "navegador_cli",
                    "--json",
                ],
            )

        assert result.exit_code == 0, result.output
        stats = json.loads(result.output)
        assert stats["nodes"] == 1
        payload = json.loads(out.read_text(encoding="utf-8"))
        assert payload["nodes"][0]["name"] == "central_fn"
