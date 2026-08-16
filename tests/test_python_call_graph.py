# Copyright CONFLICT LLC 2026 (weareconflict.com)
"""
Regression tests for #163 — Python call edges must cross file boundaries.

`_extract_calls` recorded every callee as living in the file that called it, so
an imported function resolved to a node that did not exist and the edge was
dropped. On a multi-package fixture the graph contained the right symbols and
*zero* CALLS edges, which made explain/trace/impact stop at file boundaries and
look like the code genuinely had no callers.

Real embedded stores and real files on disk: module resolution consults the
filesystem, which is how a first-party import is told apart from the standard
library.
"""

import pytest

from navegador.graph.store import GraphStore
from navegador.ingestion.parser import RepoIngester
from navegador.ingestion.python import _module_to_repo_file, _parse_import_from


@pytest.fixture()
def store(tmp_path_factory):
    s = GraphStore.sqlite(str(tmp_path_factory.mktemp("callgraph") / "graph.db"))
    yield s
    s.close()


@pytest.fixture()
def repo(tmp_path):
    """The multi-package fixture from the report."""
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "normalize.py").write_text(
        "def normalize_input(x):\n    return x\n\n\ndef resolve_entity(x):\n    return x\n",
        encoding="utf-8",
    )
    (pkg / "facts.py").write_text("def store_fact(f):\n    return f\n", encoding="utf-8")
    (pkg / "projection.py").write_text("def read_projection(q):\n    return q\n", encoding="utf-8")
    (pkg / "pipeline.py").write_text(
        "from pkg.normalize import normalize_input, resolve_entity\n\n\n"
        "def process(item):\n"
        "    a = normalize_input(item)\n"
        "    return resolve_entity(a)\n",
        encoding="utf-8",
    )
    (pkg / "worker.py").write_text(
        "import asyncio\nfrom pkg import pipeline\n\n\n"
        "async def consume(item):\n"
        "    return await asyncio.to_thread(pipeline.process, item)\n",
        encoding="utf-8",
    )
    (pkg / "settlement.py").write_text(
        "def record_fields(fields):\n"
        "    from pkg.facts import store_fact\n"
        "    return [store_fact(f) for f in fields]\n",
        encoding="utf-8",
    )
    (pkg / "queries.py").write_text(
        "class Reader:\n"
        "    def build_view(self, q):\n"
        "        from pkg.projection import read_projection\n"
        "        return read_projection(q)\n",
        encoding="utf-8",
    )
    return tmp_path


def calls(store):
    rows = store.query(
        "MATCH (a)-[:CALLS]->(b) RETURN a.name, b.name, b.file_path ORDER BY a.name, b.name"
    ).result_set
    return {(r[0], r[1], r[2]) for r in rows or []}


def references(store):
    rows = store.query(
        "MATCH (a)-[:REFERENCES]->(b) RETURN a.name, b.name, b.file_path"
    ).result_set
    return {(r[0], r[1], r[2]) for r in rows or []}


# ── The reported call forms ────────────────────────────────────────────────


class TestCrossModuleCalls:
    def test_module_level_import_is_resolved(self, store, repo):
        """`process` calls two functions imported from another module."""
        RepoIngester(store).ingest(repo)
        assert ("process", "normalize_input", "pkg/normalize.py") in calls(store)
        assert ("process", "resolve_entity", "pkg/normalize.py") in calls(store)

    def test_function_local_import_in_a_comprehension(self, store, repo):
        """The import is inside the function and the call inside a comprehension."""
        RepoIngester(store).ingest(repo)
        assert ("record_fields", "store_fact", "pkg/facts.py") in calls(store)

    def test_method_local_import(self, store, repo):
        RepoIngester(store).ingest(repo)
        assert ("build_view", "read_projection", "pkg/projection.py") in calls(store)

    def test_callable_passed_to_a_higher_order_helper(self, store, repo):
        """
        `asyncio.to_thread(pipeline.process)` never appears as a call of
        `process`, so flow analysis used to stop dead at the handoff.
        """
        RepoIngester(store).ingest(repo)
        assert ("consume", "process", "pkg/pipeline.py") in references(store)

    def test_the_fixture_produces_call_edges_at_all(self, store, repo):
        """It produced exactly zero before."""
        RepoIngester(store).ingest(repo)
        assert len(calls(store)) >= 4


class TestResolutionIsConservative:
    def test_stdlib_calls_do_not_invent_edges(self, store, repo):
        """`asyncio.to_thread` is not in the repo, so there is nothing to point at."""
        RepoIngester(store).ingest(repo)
        assert not [c for c in calls(store) if c[1] == "to_thread"]

    def test_same_file_calls_still_resolve(self, store, tmp_path):
        (tmp_path / "solo.py").write_text(
            "def helper():\n    return 1\n\n\ndef caller():\n    return helper()\n",
            encoding="utf-8",
        )
        RepoIngester(store).ingest(tmp_path)
        assert ("caller", "helper", "solo.py") in calls(store)

    def test_method_calls_on_objects_stay_local(self, store, tmp_path):
        """`self.thing()` must not be resolved through an unrelated import."""
        (tmp_path / "obj.py").write_text(
            "class A:\n"
            "    def helper(self):\n"
            "        return 1\n\n"
            "    def run(self):\n"
            "        return self.helper()\n",
            encoding="utf-8",
        )
        RepoIngester(store).ingest(tmp_path)
        assert all(c[2] == "obj.py" for c in calls(store))


# ── Import statement parsing ───────────────────────────────────────────────


class TestParseImportFrom:
    """The grammar labels the module and its members identically."""

    @staticmethod
    def parse(code: str):
        from navegador.ingestion.python import _get_parser

        tree = _get_parser().parse(code.encode())
        node = tree.root_node.children[0]
        return _parse_import_from(node, code.encode())

    def test_single_member(self):
        assert self.parse("from pkg.mod import thing\n") == ("pkg.mod", [("thing", "thing")])

    def test_multiple_members(self):
        module, bindings = self.parse("from pkg.mod import a, b\n")
        assert module == "pkg.mod"
        assert bindings == [("a", "a"), ("b", "b")]

    def test_aliased_member(self):
        module, bindings = self.parse("from pkg.mod import thing as other\n")
        assert module == "pkg.mod"
        assert bindings == [("other", "thing")]

    def test_relative_import(self):
        module, bindings = self.parse("from .sibling import thing\n")
        assert module == ".sibling"
        assert bindings == [("thing", "thing")]

    def test_wildcard_binds_nothing(self):
        module, bindings = self.parse("from pkg.mod import *\n")
        assert module == "pkg.mod"
        assert bindings == []

    def test_from_import_creates_import_nodes(self, store, repo):
        """These produced none at all, because the matched node type never occurs."""
        RepoIngester(store).ingest(repo)
        rows = store.query(
            "MATCH (i:Import) WHERE i.file_path = 'pkg/pipeline.py' RETURN i.name ORDER BY i.name"
        ).result_set
        assert [r[0] for r in rows or []] == ["normalize_input", "resolve_entity"]


# ── Module → file resolution ───────────────────────────────────────────────


class TestModuleToRepoFile:
    def test_absolute_module(self, tmp_path):
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        (pkg / "mod.py").write_text("", encoding="utf-8")
        current = pkg / "other.py"
        assert _module_to_repo_file("pkg.mod", current, tmp_path) == "pkg/mod.py"

    def test_package_init(self, tmp_path):
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("", encoding="utf-8")
        assert _module_to_repo_file("pkg", tmp_path / "a.py", tmp_path) == "pkg/__init__.py"

    def test_relative_module(self, tmp_path):
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        (pkg / "sibling.py").write_text("", encoding="utf-8")
        current = pkg / "here.py"
        assert _module_to_repo_file(".sibling", current, tmp_path) == "pkg/sibling.py"

    def test_module_outside_the_repo_is_unresolved(self, tmp_path):
        assert _module_to_repo_file("asyncio", tmp_path / "a.py", tmp_path) is None

    def test_empty_module_is_unresolved(self, tmp_path):
        assert _module_to_repo_file("", tmp_path / "a.py", tmp_path) is None
