"""
Registration and ingest must agree on repository identity (#179).

Registering a repo wrote a Repository node keyed by the absolute checkout
path; ingesting it wrote another under the portable identity from #145. Two
nodes per repo, and every File attached to only one of them.

That is not merely bloat. The registration copy has no members and no edges,
so a symbol resolved against it traverses zero CALLS edges and impact analysis
reports no callers — a confident, specific, wrong answer. `list_repos` showed
both, so a namespace that ingested nothing was indistinguishable from one that
worked.

Real stores and real git repositories throughout: identity is derived from the
git remote, so a mock would be testing the fixture rather than the behaviour.
"""

import subprocess

import pytest

from navegador.graph import GraphStore
from navegador.multirepo import MultiRepoManager, WorkspaceManager, WorkspaceMode


def git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def make_repo(root, name, remote=""):
    repo = root / name
    (repo / "src").mkdir(parents=True)
    (repo / "src" / f"{name}_mod.py").write_text(
        f"def {name}_entry():\n    return {name}_helper()\n\ndef {name}_helper():\n    return 1\n"
    )
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "t@example.com")
    git(repo, "config", "user.name", "t")
    if remote:
        git(repo, "remote", "add", "origin", remote)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "initial")
    return repo


def repositories(store):
    rows = store.query("MATCH (r:Repository) RETURN r.name, r.path ORDER BY r.path").result_set
    return [(row[0], row[1]) for row in rows or []]


def members(store, repo_path):
    rows = store.query(
        "MATCH (f)-[:BELONGS_TO]->(r:Repository {path: $p}) RETURN count(f)", {"p": repo_path}
    ).result_set
    return int(rows[0][0]) if rows else 0


@pytest.fixture
def store(tmp_path):
    return GraphStore.sqlite(str(tmp_path / "g.db"))


class TestWorkspaceManager:
    def test_one_repository_node_per_repo(self, store, tmp_path):
        repo = make_repo(tmp_path, "alpha", remote="git@github.com:acme/alpha.git")

        workspace = WorkspaceManager(store, mode=WorkspaceMode.UNIFIED)
        workspace.add_repo("alpha", repo)
        workspace.ingest_all()

        assert len(repositories(store)) == 1, (
            f"registration and ingest disagreed on identity: {repositories(store)}"
        )

    def test_the_surviving_node_owns_the_files(self, store, tmp_path):
        """
        The bug's teeth: the registration node had zero members, so anything
        resolving against it reported an empty repository.
        """
        repo = make_repo(tmp_path, "alpha", remote="git@github.com:acme/alpha.git")

        workspace = WorkspaceManager(store, mode=WorkspaceMode.UNIFIED)
        workspace.add_repo("alpha", repo)
        workspace.ingest_all()

        ((_, path),) = repositories(store)
        assert members(store, path) > 0

    def test_no_repository_is_left_empty(self, store, tmp_path):
        """`list_repos` must not show namespaces with nothing behind them."""
        make_repo(tmp_path, "alpha", remote="git@github.com:acme/alpha.git")
        make_repo(tmp_path, "beta", remote="git@github.com:acme/beta.git")

        workspace = WorkspaceManager(store, mode=WorkspaceMode.UNIFIED)
        workspace.add_repo("alpha", tmp_path / "alpha")
        workspace.add_repo("beta", tmp_path / "beta")
        workspace.ingest_all()

        empty = [(n, p) for n, p in repositories(store) if members(store, p) == 0]
        assert not empty, f"repositories with no members: {empty}"

    def test_identity_is_not_the_machine_local_path(self, store, tmp_path):
        """
        The whole point of #145: an absolute checkout path is not an identity,
        and leaks local layout into exports.
        """
        repo = make_repo(tmp_path, "alpha", remote="git@github.com:acme/alpha.git")

        workspace = WorkspaceManager(store, mode=WorkspaceMode.UNIFIED)
        workspace.add_repo("alpha", repo)

        ((_, path),) = repositories(store)
        assert str(tmp_path) not in path

    def test_repo_without_a_remote_still_registers_once(self, store, tmp_path):
        """Identity falls back to the directory name; both passes must agree."""
        repo = make_repo(tmp_path, "alpha")

        workspace = WorkspaceManager(store, mode=WorkspaceMode.UNIFIED)
        workspace.add_repo("alpha", repo)
        workspace.ingest_all()

        assert len(repositories(store)) == 1


class TestMultiRepoManager:
    def test_one_repository_node_per_repo(self, store, tmp_path):
        repo = make_repo(tmp_path, "gamma", remote="git@github.com:acme/gamma.git")

        manager = MultiRepoManager(store)
        manager.add_repo("gamma", repo)
        manager.ingest_all()

        assert len(repositories(store)) == 1

    def test_registered_repo_owns_its_files(self, store, tmp_path):
        repo = make_repo(tmp_path, "gamma", remote="git@github.com:acme/gamma.git")

        manager = MultiRepoManager(store)
        manager.add_repo("gamma", repo)
        manager.ingest_all()

        ((_, path),) = repositories(store)
        assert members(store, path) > 0


class TestCallGraphIsReachable:
    def test_calls_are_traversable_from_the_registered_repo(self, store, tmp_path):
        """
        The duplicate was structurally inert — every CALLS edge sat on one
        side and nothing crossed. Impact analysis against the other side
        returned nothing at all.
        """
        repo = make_repo(tmp_path, "alpha", remote="git@github.com:acme/alpha.git")

        workspace = WorkspaceManager(store, mode=WorkspaceMode.UNIFIED)
        workspace.add_repo("alpha", repo)
        workspace.ingest_all()

        ((_, path),) = repositories(store)
        rows = store.query(
            "MATCH (f)-[:BELONGS_TO]->(:Repository {path: $p}) "
            "MATCH (f)-[:CONTAINS]->(caller)-[:CALLS]->(callee) "
            "RETURN count(*)",
            {"p": path},
        ).result_set
        assert int(rows[0][0]) > 0, "no CALLS edges reachable from the registered repository"
