"""Regression tests for #143 — a single ingest pass must reach the
call-graph fixpoint. Forward references (a caller parsed before its callee's
node exists) were silently dropped by the MATCH...MERGE edge query, so repeat
passes kept adding CALLS edges.

Uses a real embedded FalkorDB graph — the bug lives in MERGE no-op semantics
that mocked stores cannot observe.
"""

from pathlib import Path

import pytest

from navegador.graph.store import GraphStore
from navegador.ingestion.parser import RepoIngester


@pytest.fixture()
def store(tmp_path_factory):
    s = GraphStore.sqlite(str(tmp_path_factory.mktemp("fixpoint") / "graph.db"))
    yield s
    s.close()


def _calls(store) -> set[tuple[str, str]]:
    result = store.query(
        "MATCH (a)-[:CALLS]->(b) RETURN a.name + ':' + a.file_path, b.name + ':' + b.file_path"
    )
    return {(row[0], row[1]) for row in result.result_set or []}


def _all_edges(store) -> set[tuple]:
    result = store.query(
        "MATCH (a)-[r]->(b) RETURN labels(a)[0], a.name, type(r), labels(b)[0], b.name, "
        "coalesce(a.file_path, a.path, ''), coalesce(b.file_path, b.path, '')"
    )
    return {tuple(row) for row in result.result_set or []}


def _write(root: Path, rel: str, content: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class TestCallGraphFixpoint:
    def test_forward_reference_within_file_resolved_in_one_pass(self, store, tmp_path):
        """A function calling a helper defined *below* it gets its CALLS edge
        on the first pass."""
        _write(
            tmp_path,
            "app.py",
            "def caller():\n    return helper()\n\n\ndef helper():\n    return 1\n",
        )
        RepoIngester(store).ingest(tmp_path, clear=True)
        assert ("caller:app.py", "helper:app.py") in _calls(store)

    def test_call_to_undefined_function_stays_absent(self, store, tmp_path):
        """Calls to builtins/library functions never materialize an edge."""
        _write(tmp_path, "app.py", "def caller():\n    return len([1])\n")
        RepoIngester(store).ingest(tmp_path, clear=True)
        assert _calls(store) == set()

    def test_single_pass_reaches_fixpoint(self, store, tmp_path):
        """Re-running ingest on the resulting graph adds zero edges."""
        _write(
            tmp_path,
            "app.py",
            "def a():\n    b()\n    c()\n\n\ndef b():\n    c()\n\n\ndef c():\n    pass\n",
        )
        _write(
            tmp_path,
            "util.py",
            "def top():\n    return bottom()\n\n\ndef bottom():\n    return 0\n",
        )
        ingester = RepoIngester(store)
        ingester.ingest(tmp_path, clear=True)
        first_pass = _all_edges(store)

        ingester.ingest(tmp_path)  # same cycle again, no clear
        assert _all_edges(store) == first_pass

    def test_clean_rebuild_matches_repeat_pass_graph(self, store, tmp_path):
        """clean rebuild == maintained graph: --clear rebuild produces the
        same edge set as a graph that went through repeated passes."""
        _write(
            tmp_path,
            "app.py",
            "def a():\n    b()\n\n\ndef b():\n    a()\n",
        )
        ingester = RepoIngester(store)
        ingester.ingest(tmp_path, clear=True)
        ingester.ingest(tmp_path)
        maintained = _all_edges(store)

        ingester.ingest(tmp_path, clear=True)
        assert _all_edges(store) == maintained

    def test_stats_report_resolved_forward_references(self, store, tmp_path):
        _write(
            tmp_path,
            "app.py",
            "def caller():\n    return helper()\n\n\ndef helper():\n    return 1\n",
        )
        stats = RepoIngester(store).ingest(tmp_path, clear=True)
        assert stats["edges_resolved"] == 1
