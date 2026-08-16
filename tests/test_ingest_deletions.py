# Copyright CONFLICT LLC 2026 (weareconflict.com)
"""
Regression tests for #168 — ingest must remove nodes for deleted files.

Nothing walked the set of previously-known paths, so whole-file deletion went
unnoticed: a maintained graph kept every File node, every symbol it contained
and all their edges, forever. The drift is quiet and cumulative — the export
keeps validating and the ghosts are still returned by queries — so the property
under test is that a maintained graph equals a clean rebuild of the same tree.

Real embedded stores: the bug is about graph state, not call sequences.
"""

import pytest

from navegador.graph.store import GraphStore
from navegador.ingestion.parser import RepoIngester


@pytest.fixture()
def store(tmp_path_factory):
    s = GraphStore.sqlite(str(tmp_path_factory.mktemp("deletions") / "graph.db"))
    yield s
    s.close()


@pytest.fixture()
def rebuilt(tmp_path_factory):
    s = GraphStore.sqlite(str(tmp_path_factory.mktemp("deletions-clean") / "graph.db"))
    yield s
    s.close()


def write_repo(root):
    """Two python modules, one of which will be deleted."""
    pkg = root / "pkg"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "zeta.py").write_text("def zeta_compute():\n    return 1\n", encoding="utf-8")
    (pkg / "alpha.py").write_text("def alpha():\n    return 2\n", encoding="utf-8")
    return root


def shape(store):
    return (store.node_count(), store.edge_count())


def names(store):
    rows = store.query("MATCH (n) RETURN labels(n)[0], n.name ORDER BY n.name").result_set or []
    return {(r[0], r[1]) for r in rows}


# ── Deletion is noticed ────────────────────────────────────────────────────


class TestDeletedFilesAreRemoved:
    def test_incremental_removes_the_file_node(self, store, tmp_path):
        repo = write_repo(tmp_path)
        ing = RepoIngester(store)
        ing.ingest(repo)
        (repo / "pkg" / "zeta.py").unlink()
        ing.ingest(repo, incremental=True)

        rows = store.query("MATCH (f:File) RETURN f.path ORDER BY f.path").result_set or []
        assert [r[0] for r in rows] == ["pkg/alpha.py"]

    def test_incremental_removes_contained_symbols(self, store, tmp_path):
        repo = write_repo(tmp_path)
        ing = RepoIngester(store)
        ing.ingest(repo)
        (repo / "pkg" / "zeta.py").unlink()
        ing.ingest(repo, incremental=True)

        assert not [n for n in names(store) if n[1] == "zeta_compute"]

    def test_full_ingest_also_removes(self, store, tmp_path):
        """A plain re-ingest leaked too, not only --incremental."""
        repo = write_repo(tmp_path)
        ing = RepoIngester(store)
        ing.ingest(repo)
        (repo / "pkg" / "zeta.py").unlink()
        ing.ingest(repo)

        assert not [n for n in names(store) if n[1] == "zeta_compute"]

    def test_removed_count_is_reported(self, store, tmp_path):
        repo = write_repo(tmp_path)
        ing = RepoIngester(store)
        ing.ingest(repo)
        (repo / "pkg" / "zeta.py").unlink()
        stats = ing.ingest(repo, incremental=True)
        assert stats["removed"] == 1

    def test_nothing_removed_when_nothing_deleted(self, store, tmp_path):
        repo = write_repo(tmp_path)
        ing = RepoIngester(store)
        ing.ingest(repo)
        assert ing.ingest(repo, incremental=True)["removed"] == 0

    def test_maintained_graph_equals_clean_rebuild(self, store, rebuilt, tmp_path):
        """The acceptance criterion: no drift versus a fresh ingest."""
        repo = write_repo(tmp_path)
        RepoIngester(store).ingest(repo)
        (repo / "pkg" / "zeta.py").unlink()
        RepoIngester(store).ingest(repo, incremental=True)

        RepoIngester(rebuilt).ingest(repo, clear=True)

        assert shape(store) == shape(rebuilt)
        assert names(store) == names(rebuilt)

    def test_deleting_every_file_empties_the_repo_scope(self, store, tmp_path):
        repo = write_repo(tmp_path)
        ing = RepoIngester(store)
        ing.ingest(repo)
        (repo / "pkg" / "zeta.py").unlink()
        (repo / "pkg" / "alpha.py").unlink()
        ing.ingest(repo, incremental=True)
        assert store.query("MATCH (f:File) RETURN count(f)").result_set[0][0] == 0

    def test_markdown_documents_are_removed_too(self, store, tmp_path):
        repo = write_repo(tmp_path)
        (repo / "README.md").write_text("# Title\n\nSome prose.\n", encoding="utf-8")
        ing = RepoIngester(store)
        ing.ingest(repo)
        assert store.query("MATCH (d:Document) RETURN count(d)").result_set[0][0] == 1

        (repo / "README.md").unlink()
        ing.ingest(repo, incremental=True)
        assert store.query("MATCH (d:Document) RETURN count(d)").result_set[0][0] == 0


# ── What must NOT be removed ───────────────────────────────────────────────


class TestPruneIsConservative:
    def test_unparseable_files_are_kept(self, store, tmp_path, monkeypatch):
        """
        A file whose optional grammar is missing is still on disk.

        Pruning against the set of successfully parsed files would delete every
        Rust/Go/Swift node the moment someone installed navegador without the
        language extras.
        """
        repo = write_repo(tmp_path)
        ing = RepoIngester(store)
        ing.ingest(repo)
        before = names(store)

        real_get_parser = ing._get_parser
        monkeypatch.setattr(ing, "_get_parser", lambda language: None)
        stats = ing.ingest(repo)
        monkeypatch.setattr(ing, "_get_parser", real_get_parser)

        assert stats["removed"] == 0
        assert names(store) == before

    def test_another_repo_in_the_same_graph_is_untouched(self, store, tmp_path):
        """
        Pruning is scoped by BELONGS_TO.

        A shared or federated graph holds many repositories; ingesting one must
        not remove another's files just because they are not on this disk path.
        """
        first = write_repo(tmp_path / "first")
        second = tmp_path / "second"
        (second / "pkg").mkdir(parents=True)
        (second / "pkg" / "other.py").write_text("def other():\n    return 3\n", encoding="utf-8")

        ing = RepoIngester(store)
        ing.ingest(first)
        ing.ingest(second)
        assert any(n[1] == "other" for n in names(store))

        (first / "pkg" / "zeta.py").unlink()
        ing.ingest(first, incremental=True)

        assert any(n[1] == "other" for n in names(store)), "other repo's symbols were pruned"
        assert not any(n[1] == "zeta_compute" for n in names(store))

    def test_surviving_files_keep_their_symbols(self, store, tmp_path):
        repo = write_repo(tmp_path)
        ing = RepoIngester(store)
        ing.ingest(repo)
        (repo / "pkg" / "zeta.py").unlink()
        ing.ingest(repo, incremental=True)
        assert any(n[1] == "alpha" for n in names(store))

    def test_clear_ingest_still_works(self, store, tmp_path):
        repo = write_repo(tmp_path)
        ing = RepoIngester(store)
        ing.ingest(repo)
        stats = ing.ingest(repo, clear=True)
        assert stats["removed"] == 0
        assert any(n[1] == "zeta_compute" for n in names(store))
