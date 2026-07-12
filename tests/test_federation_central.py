"""Regression tests for #124 — aggregate must roll up per-repo graphs that
live as named graphs in the connected (central) FalkorDB, matching the
navegador_<name> convention of `workspace ingest --mode federated`.

Run against a real embedded FalkorDB with multiple named graphs."""

from unittest.mock import patch

import pytest
from click.testing import CliRunner

from navegador.cli.commands import main
from navegador.federation import SuperGraphAggregator
from navegador.graph.store import GraphStore

# ── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def central(tmp_path_factory):
    s = GraphStore.sqlite(str(tmp_path_factory.mktemp("central") / "graph.db"))
    yield s
    s.close()


@pytest.fixture(autouse=True)
def _clean(central):
    central.clear()
    for shard in ("navegador_repoa", "navegador_repob"):
        central.with_graph(shard).clear()


def _seed_shard(central, graph_name: str, fn_name: str) -> None:
    shard = central.with_graph(graph_name)
    shard.create_node("File", {"name": "app.py", "path": "app.py", "language": "python"})
    shard.create_node("Function", {"name": fn_name, "file_path": "app.py"})
    shard.create_node("Concept", {"name": "SharedConcept", "description": "x"})
    shard.create_edge(
        "File", {"path": "app.py"}, "CONTAINS", "Function", {"name": fn_name, "file_path": "app.py"}
    )


def _central_function_repos(central) -> dict[str, str]:
    result = central.query("MATCH (f:Function) RETURN f.name, f.repo ORDER BY f.name")
    return {row[0]: row[1] for row in result.result_set or []}


# ── Name resolution ────────────────────────────────────────────────────────


class TestCentralResidentSources:
    def test_bare_name_resolves_navegador_prefixed_graph(self, central):
        _seed_shard(central, "navegador_repoa", "alpha_fn")
        summary = SuperGraphAggregator(central).aggregate({"repoa": "repoa"})

        assert "error" not in summary["repoa"]
        assert summary["repoa"]["nodes"] == 3
        assert _central_function_repos(central) == {"alpha_fn": "repoa"}

    def test_exact_graph_name_also_resolves(self, central):
        _seed_shard(central, "navegador_repoa", "alpha_fn")
        summary = SuperGraphAggregator(central).aggregate({"repoa": "navegador_repoa"})
        assert "error" not in summary["repoa"]

    def test_multiple_shards_roll_up_with_knowledge_dedup(self, central):
        _seed_shard(central, "navegador_repoa", "alpha_fn")
        _seed_shard(central, "navegador_repob", "beta_fn")
        summary = SuperGraphAggregator(central).aggregate({"repoa": "repoa", "repob": "repob"})

        assert summary["repoa"]["deduped"] == 1
        assert summary["repob"]["deduped"] == 1
        assert _central_function_repos(central) == {"alpha_fn": "repoa", "beta_fn": "repob"}
        result = central.query("MATCH (c:Concept {name: 'SharedConcept'}) RETURN c.repos, count(c)")
        repos, count = result.result_set[0]
        assert count == 1
        assert repos == "repoa,repob"

    def test_missing_source_reports_both_lookups(self, central):
        summary = SuperGraphAggregator(central).aggregate({"ghost": "ghost"})
        err = summary["ghost"]["error"]
        assert "ghost" in err and "navegador_ghost" in err

    def test_source_equal_to_target_graph_is_rejected(self, central):
        supergraph = central.with_graph("navegador_repoa")
        _seed_shard(central, "navegador_repoa", "alpha_fn")
        summary = SuperGraphAggregator(supergraph).aggregate({"repoa": "repoa"})
        assert "aggregation target" in summary["repoa"]["error"]

    def test_local_path_still_takes_precedence(self, central, tmp_path):
        local = GraphStore.sqlite(str(tmp_path / ".navegador" / "graph.db"))
        local.create_node("Function", {"name": "local_fn", "file_path": "l.py"})
        local.close()

        summary = SuperGraphAggregator(central).aggregate({"localrepo": str(tmp_path)})
        assert "error" not in summary["localrepo"]
        assert _central_function_repos(central) == {"local_fn": "localrepo"}


# ── CLI ────────────────────────────────────────────────────────────────────


class TestAggregateCli:
    def test_aggregate_into_named_supergraph(self, central):
        _seed_shard(central, "navegador_repoa", "alpha_fn")
        _seed_shard(central, "navegador_repob", "beta_fn")

        runner = CliRunner()
        with patch("navegador.cli.commands._get_store", return_value=central):
            result = runner.invoke(
                main, ["aggregate", "repoa", "repob", "--graph", "navegador_supergraph"]
            )

        assert result.exit_code == 0, result.output
        super_store = central.with_graph("navegador_supergraph")
        assert _central_function_repos(super_store) == {
            "alpha_fn": "repoa",
            "beta_fn": "repob",
        }
        # per-repo shards are untouched and the default graph stays empty
        assert central.with_graph("navegador_repoa").node_count() == 3
        assert central.node_count() == 0
        super_store.clear()
