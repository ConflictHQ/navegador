"""Regression tests for #142 — incremental ingest must not drop cross-document
REFERENCES edges when documents are re-parsed.

Uses a real embedded FalkorDB graph: the bug was a DETACH DELETE destroying
incoming edges, which mocked stores cannot observe.
"""

from pathlib import Path

import pytest

from navegador.graph.store import GraphStore
from navegador.ingestion.parser import RepoIngester


@pytest.fixture()
def store(tmp_path_factory):
    s = GraphStore.sqlite(str(tmp_path_factory.mktemp("incref") / "graph.db"))
    yield s
    s.close()


def _write_docs(root: Path) -> None:
    (root / "docs").mkdir(parents=True, exist_ok=True)
    (root / "README.md").write_text("# Conventions\n\nshared doc\n", encoding="utf-8")
    # guide1 -> guide2 -> guide3 -> guide1 cycle, all -> README
    for i in (1, 2, 3):
        nxt = i % 3 + 1
        (root / "docs" / f"guide{i}.md").write_text(
            f"# Guide {i}\n\nsee [conventions](../README.md) and [next](guide{nxt}.md)\n",
            encoding="utf-8",
        )


def _references(store) -> set[tuple[str, str]]:
    result = store.query("MATCH (a:Document)-[:REFERENCES]->(b:Document) RETURN a.path, b.path")
    return {(row[0], row[1]) for row in result.result_set or []}


FULL_EDGE_SET = {
    ("docs/guide1.md", "README.md"),
    ("docs/guide2.md", "README.md"),
    ("docs/guide3.md", "README.md"),
    ("docs/guide1.md", "docs/guide2.md"),
    ("docs/guide2.md", "docs/guide3.md"),
    ("docs/guide3.md", "docs/guide1.md"),
}


class TestIncrementalReferences:
    def test_full_ingest_builds_all_edges(self, store, tmp_path):
        _write_docs(tmp_path)
        RepoIngester(store).ingest(tmp_path, clear=True)
        assert _references(store) == FULL_EDGE_SET

    def test_reparse_keeps_incoming_edges_from_unchanged_docs(self, store, tmp_path):
        """Editing guide1 must not lose guide3 -> guide1 (guide3 is hash-skipped)."""
        _write_docs(tmp_path)
        ingester = RepoIngester(store)
        ingester.ingest(tmp_path, clear=True)

        guide1 = tmp_path / "docs" / "guide1.md"
        guide1.write_text(guide1.read_text(encoding="utf-8") + "\nedited\n", encoding="utf-8")
        stats = ingester.ingest(tmp_path, incremental=True)

        assert stats["skipped"] >= 2  # the other docs were hash-skipped
        assert _references(store) == FULL_EDGE_SET

    def test_reparse_of_two_linked_docs_keeps_edge_between_them(self, store, tmp_path):
        """Editing guide1 AND guide2: guide2's re-parse must not destroy the
        guide1 -> guide2 edge that guide1's earlier re-parse just rebuilt."""
        _write_docs(tmp_path)
        ingester = RepoIngester(store)
        ingester.ingest(tmp_path, clear=True)

        for name in ("guide1.md", "guide2.md"):
            doc = tmp_path / "docs" / name
            doc.write_text(doc.read_text(encoding="utf-8") + "\nedited\n", encoding="utf-8")
        ingester.ingest(tmp_path, incremental=True)

        assert _references(store) == FULL_EDGE_SET

    def test_removed_link_disappears_on_reparse(self, store, tmp_path):
        """Outgoing edges still track current content — dropping a link removes its edge."""
        _write_docs(tmp_path)
        ingester = RepoIngester(store)
        ingester.ingest(tmp_path, clear=True)

        (tmp_path / "docs" / "guide1.md").write_text(
            "# Guide 1\n\nsee [next](guide2.md)\n", encoding="utf-8"
        )
        ingester.ingest(tmp_path, incremental=True)

        assert _references(store) == FULL_EDGE_SET - {("docs/guide1.md", "README.md")}

    def test_incremental_matches_full_ingest_fixpoint(self, store, tmp_path):
        """After edits + incremental ingest, the edge set equals a from-scratch full ingest."""
        _write_docs(tmp_path)
        ingester = RepoIngester(store)
        ingester.ingest(tmp_path, clear=True)

        for name in ("guide1.md", "guide2.md", "guide3.md"):
            doc = tmp_path / "docs" / name
            doc.write_text(doc.read_text(encoding="utf-8") + "\nedited\n", encoding="utf-8")
        ingester.ingest(tmp_path, incremental=True)
        incremental_edges = _references(store)

        ingester.ingest(tmp_path, clear=True)
        assert incremental_edges == _references(store)
