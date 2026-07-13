"""Regression tests for #144 — submodules/monorepo ingest must attribute
nodes to their repository: BELONGS_TO edges from every File/Document to its
Repository, and workspace-relative node paths so two repos owning the same
relative path (README.md) don't silently merge.
"""

from pathlib import Path

import pytest

from navegador.graph.store import GraphStore
from navegador.ingestion.parser import RepoIngester
from navegador.submodules import SubmoduleIngester


@pytest.fixture()
def store(tmp_path_factory):
    s = GraphStore.sqlite(str(tmp_path_factory.mktemp("attribution") / "graph.db"))
    yield s
    s.close()


def _write(root: Path, rel: str, content: str = "x = 1\n") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _belongs_to(store) -> set[tuple[str, str]]:
    result = store.query(
        "MATCH (n)-[:BELONGS_TO]->(r:Repository) RETURN coalesce(n.path, ''), r.path"
    )
    return {(row[0], row[1]) for row in result.result_set or []}


def _metarepo(root: Path) -> Path:
    """Parent repo + one submodule, both owning a README.md and an app.py."""
    meta = root / "meta"
    _write(meta, "app.py", "def deploy():\n    pass\n")
    (meta / "README.md").write_text("# Meta\n", encoding="utf-8")
    (meta / ".git").mkdir(parents=True, exist_ok=True)
    (meta / ".gitmodules").write_text(
        '[submodule "libs/core"]\n    path = libs/core\n    url = https://example.com/core.git\n',
        encoding="utf-8",
    )
    sub = meta / "libs" / "core"
    _write(sub, "app.py", "def run():\n    pass\n")
    (sub / "README.md").write_text("# Core\n", encoding="utf-8")
    (sub / ".git").mkdir(parents=True, exist_ok=True)
    return meta


class TestPlainIngestAttribution:
    def test_files_and_documents_link_to_repository(self, store, tmp_path):
        repo = tmp_path / "myrepo"
        _write(repo, "app.py")
        (repo / "README.md").write_text("# Readme\n", encoding="utf-8")
        RepoIngester(store).ingest(repo, clear=True)

        assert _belongs_to(store) == {("app.py", "myrepo"), ("README.md", "myrepo")}

    def test_plain_ingest_paths_stay_repo_relative(self, store, tmp_path):
        repo = tmp_path / "myrepo"
        _write(repo, "src/app.py")
        RepoIngester(store).ingest(repo, clear=True)

        result = store.query("MATCH (f:File) RETURN f.path")
        assert [row[0] for row in result.result_set] == ["src/app.py"]

    def test_rel_root_must_be_ancestor(self, store, tmp_path):
        repo = tmp_path / "myrepo"
        _write(repo, "app.py")
        with pytest.raises(ValueError):
            RepoIngester(store).ingest(repo, rel_root=tmp_path / "elsewhere")


class TestSubmoduleAttribution:
    def test_same_named_files_do_not_merge(self, store, tmp_path):
        meta = _metarepo(tmp_path)
        SubmoduleIngester(store).ingest_with_submodules(meta, clear=True)

        result = store.query("MATCH (d:Document) RETURN d.path ORDER BY d.path")
        assert [row[0] for row in result.result_set] == ["README.md", "libs/core/README.md"]

        result = store.query("MATCH (f:File) RETURN f.path ORDER BY f.path")
        assert [row[0] for row in result.result_set] == ["app.py", "libs/core/app.py"]

    def test_every_node_attributable_to_its_repo(self, store, tmp_path):
        meta = _metarepo(tmp_path)
        SubmoduleIngester(store).ingest_with_submodules(meta, clear=True)

        assert _belongs_to(store) == {
            ("app.py", "meta"),
            ("README.md", "meta"),
            ("libs/core/app.py", "libs/core"),
            ("libs/core/README.md", "libs/core"),
        }

    def test_symbols_attributable_via_containing_file(self, store, tmp_path):
        """which repo is this function from — File CONTAINS + BELONGS_TO."""
        meta = _metarepo(tmp_path)
        SubmoduleIngester(store).ingest_with_submodules(meta, clear=True)

        result = store.query(
            "MATCH (f:File)-[:CONTAINS]->(fn:Function {name: 'run'}), "
            "(f)-[:BELONGS_TO]->(r:Repository) RETURN r.path"
        )
        assert [row[0] for row in result.result_set] == ["libs/core"]

    def test_incremental_reingest_keeps_attribution(self, store, tmp_path):
        meta = _metarepo(tmp_path)
        ingester = SubmoduleIngester(store)
        ingester.ingest_with_submodules(meta, clear=True)
        before = _belongs_to(store)

        (meta / "libs" / "core" / "app.py").write_text(
            "def run():\n    return 2\n", encoding="utf-8"
        )
        ingester.ingest_with_submodules(meta, clear=False)

        assert _belongs_to(store) == before
