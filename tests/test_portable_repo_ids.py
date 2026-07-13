"""Regression tests for #145 — Repository nodes must be keyed by a portable
identity, never the machine-local absolute checkout path. Absolute paths in
committed conflict-kg exports leaked the contributor's filesystem layout and
churned ids across machines.
"""

from pathlib import Path

import pytest

from navegador.graph.interchange import collect_graph
from navegador.graph.store import GraphStore
from navegador.ingestion.parser import RepoIngester
from navegador.submodules import SubmoduleIngester


@pytest.fixture()
def store(tmp_path_factory):
    s = GraphStore.sqlite(str(tmp_path_factory.mktemp("portable") / "graph.db"))
    yield s
    s.close()


def _repositories(store) -> dict[str, str]:
    result = store.query("MATCH (r:Repository) RETURN r.name, r.path")
    return {row[0]: row[1] for row in result.result_set or []}


def _write(root: Path, rel: str, content: str = "x = 1\n") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class TestRepoIngesterPortableIds:
    def test_repository_path_is_repo_name(self, store, tmp_path):
        repo = tmp_path / "myrepo"
        _write(repo, "app.py")
        RepoIngester(store).ingest(repo, clear=True)

        assert _repositories(store) == {"myrepo": "myrepo"}

    def test_repo_key_override(self, store, tmp_path):
        repo = tmp_path / "core"
        _write(repo, "app.py")
        RepoIngester(store).ingest(repo, clear=True, repo_key="libs/core")

        assert _repositories(store) == {"core": "libs/core"}

    def test_export_contains_no_absolute_paths(self, store, tmp_path):
        repo = tmp_path / "myrepo"
        _write(repo, "app.py")
        RepoIngester(store).ingest(repo, clear=True)

        nodes, _ = collect_graph(store)
        repo_nodes = [n for n in nodes if n["type"] == "Repository"]
        assert repo_nodes, "export should contain the Repository node"
        for node in repo_nodes:
            assert node["id"] == "Repository:myrepo:myrepo"
            assert str(tmp_path) not in node["id"]
            assert str(tmp_path) not in str(node["props"])

    def test_exports_reproducible_across_checkout_locations(self, store, tmp_path):
        """Two checkouts of the same repo at different paths produce
        identical Repository nodes."""
        checkout_a = tmp_path / "home-alice" / "myrepo"
        checkout_b = tmp_path / "work-bob" / "myrepo"
        for checkout in (checkout_a, checkout_b):
            _write(checkout, "app.py")

        RepoIngester(store).ingest(checkout_a, clear=True)
        nodes_a, _ = collect_graph(store)

        RepoIngester(store).ingest(checkout_b, clear=True)
        nodes_b, _ = collect_graph(store)

        assert nodes_a == nodes_b


class TestSubmodulePortableIds:
    def _metarepo(self, root: Path) -> Path:
        meta = root / "meta"
        _write(meta, "deploy.py")
        (meta / ".git").mkdir(parents=True, exist_ok=True)
        (meta / ".gitmodules").write_text(
            '[submodule "libs/core"]\n'
            "    path = libs/core\n"
            "    url = https://example.com/core.git\n",
            encoding="utf-8",
        )
        sub = meta / "libs" / "core"
        _write(sub, "core.py")
        (sub / ".git").mkdir(parents=True, exist_ok=True)
        return meta

    def test_submodule_keyed_by_relative_path(self, store, tmp_path):
        meta = self._metarepo(tmp_path)
        SubmoduleIngester(store).ingest_with_submodules(meta, clear=True)

        repos = _repositories(store)
        assert repos["meta"] == "meta"
        assert repos["libs/core"] == "libs/core"
        assert not any(path.startswith(str(tmp_path)) for path in repos.values())

    def test_depends_on_edge_survives(self, store, tmp_path):
        meta = self._metarepo(tmp_path)
        SubmoduleIngester(store).ingest_with_submodules(meta, clear=True)

        result = store.query(
            "MATCH (a:Repository)-[:DEPENDS_ON]->(b:Repository) RETURN a.path, b.path"
        )
        assert [tuple(r) for r in result.result_set or []] == [("meta", "libs/core")]

    def test_no_duplicate_repository_node_per_submodule(self, store, tmp_path):
        """RepoIngester's node (via repo_key) and SubmoduleIngester's node
        must merge onto one key."""
        meta = self._metarepo(tmp_path)
        SubmoduleIngester(store).ingest_with_submodules(meta, clear=True)

        result = store.query("MATCH (r:Repository) RETURN count(r)")
        assert result.result_set[0][0] == 2  # parent + one submodule
