# Copyright CONFLICT LLC 2026 (weareconflict.com)
"""
Tests for navegador.federation — the federated super-graph aggregator.

Real embedded stores throughout: two seeded source repos with colliding
file paths and shared knowledge nodes roll up into a central graph.
"""

import json
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from navegador.cli.commands import main
from navegador.federation import (
    SuperGraphAggregator,
    repo_name_from_path,
    resolve_graph_path,
)
from navegador.graph.store import GraphStore

# ── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def source_a(tmp_path_factory):
    store = GraphStore.sqlite(str(tmp_path_factory.mktemp("fed-a") / "graph.db"))
    yield store
    store.close()


@pytest.fixture(scope="module")
def source_b(tmp_path_factory):
    store = GraphStore.sqlite(str(tmp_path_factory.mktemp("fed-b") / "graph.db"))
    yield store
    store.close()


@pytest.fixture(scope="module")
def central(tmp_path_factory):
    store = GraphStore.sqlite(str(tmp_path_factory.mktemp("fed-central") / "graph.db"))
    yield store
    store.close()


@pytest.fixture(autouse=True)
def _clean(source_a, source_b, central):
    source_a.clear()
    source_b.clear()
    central.clear()
    yield


def _seed_repo(store: GraphStore, impl_fn: str) -> None:
    """A repo graph: one file, one function, shared Concept + Person, a Decision."""
    store.create_node("File", {"name": "app.py", "path": "app.py", "language": "python"})
    store.create_node("Function", {"name": impl_fn, "file_path": "app.py", "line_start": 1})
    store.create_node("Concept", {"name": "Payments", "description": "money flow"})
    store.create_node("Person", {"name": "Ada"})
    store.create_node("Decision", {"name": "use falkordb"})
    store.create_edge(
        "File", {"path": "app.py"}, "CONTAINS", "Function", {"name": impl_fn, "file_path": "app.py"}
    )
    store.create_edge(
        "Function",
        {"name": impl_fn, "file_path": "app.py"},
        "IMPLEMENTS",
        "Concept",
        {"name": "Payments"},
    )
    store.create_edge(
        "Function",
        {"name": impl_fn, "file_path": "app.py"},
        "ASSIGNED_TO",
        "Person",
        {"name": "Ada"},
    )


def _aggregate_both(central, source_a, source_b):
    agg = SuperGraphAggregator(central)
    return agg.aggregate({"repo-a": source_a, "repo-b": source_b})


# ── Namespacing ────────────────────────────────────────────────────────────


class TestNamespacing:
    def test_colliding_paths_stay_distinct(self, central, source_a, source_b):
        _seed_repo(source_a, "charge")
        _seed_repo(source_b, "refund")
        _aggregate_both(central, source_a, source_b)

        result = central.query("MATCH (f:File) RETURN f.path ORDER BY f.path")
        paths = [row[0] for row in result.result_set]
        assert paths == ["repo-a/app.py", "repo-b/app.py"]

    def test_code_nodes_carry_repo_property(self, central, source_a, source_b):
        _seed_repo(source_a, "charge")
        _seed_repo(source_b, "refund")
        _aggregate_both(central, source_a, source_b)

        result = central.query(
            "MATCH (f:Function) RETURN f.name, f.repo, f.file_path ORDER BY f.name"
        )
        assert result.result_set == [
            ["charge", "repo-a", "repo-a/app.py"],
            ["refund", "repo-b", "repo-b/app.py"],
        ]

    def test_pathless_name_keyed_nodes_get_prefixed(self, central, source_a, source_b):
        _seed_repo(source_a, "charge")
        _seed_repo(source_b, "refund")
        _aggregate_both(central, source_a, source_b)

        result = central.query("MATCH (d:Decision) RETURN d.name, d.local_name ORDER BY d.name")
        assert result.result_set == [
            ["repo-a/use falkordb", "use falkordb"],
            ["repo-b/use falkordb", "use falkordb"],
        ]


# ── Knowledge dedup ────────────────────────────────────────────────────────


class TestKnowledgeDedup:
    def test_shared_concept_is_one_node(self, central, source_a, source_b):
        _seed_repo(source_a, "charge")
        _seed_repo(source_b, "refund")
        _aggregate_both(central, source_a, source_b)

        result = central.query("MATCH (c:Concept {name: 'Payments'}) RETURN count(c), c.repos")
        assert result.result_set == [[1, "repo-a,repo-b"]]

    def test_cross_repo_edges_resolve_on_shared_node(self, central, source_a, source_b):
        _seed_repo(source_a, "charge")
        _seed_repo(source_b, "refund")
        _aggregate_both(central, source_a, source_b)

        result = central.query(
            "MATCH (f:Function)-[:IMPLEMENTS]->(:Concept {name: 'Payments'}) "
            "RETURN f.repo ORDER BY f.repo"
        )
        assert result.result_set == [["repo-a"], ["repo-b"]]

    def test_person_unified(self, central, source_a, source_b):
        _seed_repo(source_a, "charge")
        _seed_repo(source_b, "refund")
        _aggregate_both(central, source_a, source_b)

        result = central.query(
            "MATCH (f:Function)-[:ASSIGNED_TO]->(p:Person {name: 'Ada'}) RETURN count(f)"
        )
        assert result.result_set == [[2]]
        result = central.query("MATCH (p:Person) RETURN count(p)")
        assert result.result_set == [[1]]


# ── Anchoring ──────────────────────────────────────────────────────────────


class TestAnchoring:
    def test_synthetic_repo_nodes_exist(self, central, source_a, source_b):
        _seed_repo(source_a, "charge")
        _seed_repo(source_b, "refund")
        _aggregate_both(central, source_a, source_b)

        result = central.query(
            "MATCH (r:Repository {description: 'federated-repo-anchor'}) "
            "RETURN r.name ORDER BY r.name"
        )
        assert result.result_set == [["repo-a"], ["repo-b"]]

    def test_anchor_contains_repo_files(self, central, source_a, source_b):
        _seed_repo(source_a, "charge")
        _seed_repo(source_b, "refund")
        _aggregate_both(central, source_a, source_b)

        result = central.query(
            "MATCH (:Repository {name: 'repo-a', path: 'repo-a'})-[:CONTAINS]->(f:File) "
            "RETURN f.path"
        )
        assert result.result_set == [["repo-a/app.py"]]


# ── Aggregate semantics ────────────────────────────────────────────────────


class TestAggregateSemantics:
    def test_stats(self, central, source_a, source_b):
        _seed_repo(source_a, "charge")
        stats = SuperGraphAggregator(central).aggregate({"repo-a": source_a})
        assert stats["repo-a"] == {"nodes": 5, "edges": 3, "deduped": 2}

    def test_idempotent_reaggregation(self, central, source_a, source_b):
        _seed_repo(source_a, "charge")
        _seed_repo(source_b, "refund")
        agg = SuperGraphAggregator(central)
        agg.aggregate({"repo-a": source_a, "repo-b": source_b})
        before = (central.node_count(), central.edge_count())
        agg.aggregate({"repo-a": source_a, "repo-b": source_b})
        assert (central.node_count(), central.edge_count()) == before

    def test_clear_flag(self, central, source_a, source_b):
        central.create_node("Concept", {"name": "Stale"})
        _seed_repo(source_a, "charge")
        SuperGraphAggregator(central).aggregate({"repo-a": source_a}, clear=True)
        result = central.query("MATCH (c:Concept {name: 'Stale'}) RETURN count(c)")
        assert result.result_set == [[0]]

    def test_missing_graph_reports_error(self, central, tmp_path):
        summary = SuperGraphAggregator(central).aggregate({"ghost": tmp_path / "nope"})
        assert "error" in summary["ghost"]

    def test_aggregate_from_rdb_file(self, central, tmp_path):
        db_path = tmp_path / ".navegador" / "graph.db"
        source = GraphStore.sqlite(str(db_path))
        _seed_repo(source, "charge")
        source.close()

        summary = SuperGraphAggregator(central).aggregate({"filerepo": tmp_path})
        assert summary["filerepo"]["nodes"] == 5
        result = central.query("MATCH (f:File) RETURN f.path")
        assert result.result_set == [["filerepo/app.py"]]


# ── Path helpers ───────────────────────────────────────────────────────────


class TestPathHelpers:
    def test_resolve_repo_root(self, tmp_path):
        graph = tmp_path / ".navegador" / "graph.db"
        graph.parent.mkdir()
        graph.write_bytes(b"")
        assert resolve_graph_path(tmp_path) == graph

    def test_resolve_missing_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="navegador ingest"):
            resolve_graph_path(tmp_path)

    def test_repo_name_from_graph_file(self, tmp_path):
        repo = tmp_path / "myrepo"
        graph = repo / ".navegador" / "graph.db"
        graph.parent.mkdir(parents=True)
        graph.write_bytes(b"")
        assert repo_name_from_path(graph) == "myrepo"
        assert repo_name_from_path(repo) == "myrepo"


# ── CLI ────────────────────────────────────────────────────────────────────


class TestCli:
    def test_aggregate_command(self, central, tmp_path):
        repo_root = tmp_path / "cli-repo"
        source = GraphStore.sqlite(str(repo_root / ".navegador" / "graph.db"))
        _seed_repo(source, "charge")
        source.close()

        runner = CliRunner()
        with patch("navegador.cli.commands._get_store", return_value=central):
            result = runner.invoke(main, ["aggregate", str(repo_root), "--json"])
        assert result.exit_code == 0, result.output
        summary = json.loads(result.output)
        assert summary["cli-repo"] == {"nodes": 5, "edges": 3, "deduped": 2}

    def test_aggregate_named_source(self, central, tmp_path):
        repo_root = tmp_path / "whatever"
        source = GraphStore.sqlite(str(repo_root / ".navegador" / "graph.db"))
        _seed_repo(source, "charge")
        source.close()

        runner = CliRunner()
        with patch("navegador.cli.commands._get_store", return_value=central):
            result = runner.invoke(main, ["aggregate", f"backend={repo_root}", "--json"])
        assert result.exit_code == 0, result.output
        assert "backend" in json.loads(result.output)
