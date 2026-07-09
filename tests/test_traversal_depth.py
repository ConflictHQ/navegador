# Copyright CONFLICT LLC 2026 (weareconflict.com)
"""
Regression tests for #118 — FalkorDB rejects parameterized variable-length
bounds (*1..$depth), so every depth-bounded traversal must inline the depth
via queries.inline_depth().

These run against a real embedded store: before the fix, each of these
traversals raised "Encountered unhandled type in inlined properties" and the
callers silently returned empty results.
"""

import pytest

from navegador.analysis.crossrepo import CrossRepoImpactAnalyzer
from navegador.analysis.impact import ImpactAnalyzer
from navegador.context.loader import ContextLoader
from navegador.graph.queries import inline_depth
from navegador.graph.store import GraphStore
from navegador.taskpack import TaskPackBuilder


@pytest.fixture(scope="module")
def store(tmp_path_factory):
    """Call chain a -> b -> c in one file, plus a repo containing the file."""
    store = GraphStore.sqlite(str(tmp_path_factory.mktemp("depth") / "graph.db"))
    store.create_node("Repository", {"name": "repo", "path": "/repo"})
    store.create_node("File", {"name": "app.py", "path": "app.py"})
    for fn, line in (("a", 1), ("b", 5), ("c", 9)):
        store.create_node("Function", {"name": fn, "file_path": "app.py", "line_start": line})
    store.create_edge("Repository", {"name": "repo"}, "CONTAINS", "File", {"path": "app.py"})
    for src, dst in (("a", "b"), ("b", "c")):
        store.create_edge(
            "Function",
            {"name": src, "file_path": "app.py"},
            "CALLS",
            "Function",
            {"name": dst, "file_path": "app.py"},
        )
    yield store
    store.close()


class TestInlineDepth:
    def test_replaces_placeholder(self):
        assert inline_depth("MATCH (a)-[:CALLS*1..$depth]->(b)", 3) == (
            "MATCH (a)-[:CALLS*1..3]->(b)"
        )

    def test_coerces_to_int(self):
        assert "*1..2" in inline_depth("*1..$depth", 2.9)

    def test_rejects_non_numeric(self):
        with pytest.raises((TypeError, ValueError)):
            inline_depth("*1..$depth", "3; MATCH (n) DELETE n")


class TestBlastRadiusRealStore:
    def test_returns_transitive_calls(self, store):
        result = ImpactAnalyzer(store).blast_radius("a", depth=3)
        names = {n["name"] for n in result.affected_nodes}
        assert names == {"b", "c"}

    def test_depth_bounds_traversal(self, store):
        result = ImpactAnalyzer(store).blast_radius("a", depth=1)
        names = {n["name"] for n in result.affected_nodes}
        assert names == {"b"}


class TestCallersCalleesRealStore:
    def test_load_function_finds_callees_and_callers(self, store):
        bundle = ContextLoader(store).load_function("b", "app.py", depth=2)
        names = {(e["type"], e["from"], e["to"]) for e in bundle.edges}
        assert ("CALLS", "b", "c") in names
        assert ("CALLS", "a", "b") in names


class TestCrossRepoRealStore:
    def test_cross_repo_blast_finds_affected_and_repo(self, store):
        result = CrossRepoImpactAnalyzer(store).blast_radius("a", depth=3)
        names = {n["name"] for n in result.affected_nodes}
        assert names == {"b", "c"}


class TestTaskPackRealStore:
    def test_task_pack_callers_callees(self, store):
        pack = TaskPackBuilder(store).for_symbol("b", file_path="app.py", depth=2)
        assert {n.name for n in pack.callees} == {"c"}
        assert {n.name for n in pack.callers} == {"a"}
