# Copyright CONFLICT LLC 2026 (weareconflict.com)
"""
Regression tests for #167 — Repository identity must survive being renamed.

The Repository node was keyed by the checkout directory's basename, so a
worktree, a renamed clone, or `git clone <url> <otherdir>` each produced an
*additional* Repository node indistinguishable from a real one, and files ended
up owned by every node they had been ingested under. Identity now comes from the
git remote, which is the only thing constant across all three.

Real embedded stores and real git repositories: the defect is about what git
reports for a checkout, which a mock cannot show.
"""

import subprocess

import pytest

from navegador.graph.store import GraphStore
from navegador.ingestion.parser import RepoIngester, repo_display_name, repo_identity
from navegador.vcs import normalize_remote_url


@pytest.fixture()
def store(tmp_path_factory):
    s = GraphStore.sqlite(str(tmp_path_factory.mktemp("repo-identity") / "graph.db"))
    yield s
    s.close()


def git(*args, cwd):
    return subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def make_repo(root, remote="git@github.com:ExampleOrg/myproj.git"):
    root.mkdir(parents=True, exist_ok=True)
    git("init", "-q", "-b", "main", ".", cwd=root)
    if remote:
        git("remote", "add", "origin", remote, cwd=root)
    (root / "a.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")
    git("add", "-A", cwd=root)
    git("commit", "-qm", "init", cwd=root)
    return root


def repositories(store):
    rows = store.query("MATCH (r:Repository) RETURN r.path, r.name ORDER BY r.path").result_set
    return [(r[0], r[1]) for r in rows or []]


# ── URL normalization ──────────────────────────────────────────────────────


class TestNormalizeRemoteUrl:
    @pytest.mark.parametrize(
        "url",
        [
            "git@github.com:ExampleOrg/myproj.git",
            "https://github.com/ExampleOrg/myproj.git",
            "https://github.com/ExampleOrg/myproj",
            "ssh://git@github.com:22/ExampleOrg/myproj",
            "https://github.com/ExampleOrg/myproj/",
        ],
    )
    def test_hosted_forms_agree(self, url):
        """An ssh clone and an https clone are the same repository."""
        assert normalize_remote_url(url) == "ExampleOrg/myproj"

    @pytest.mark.parametrize("url", ["/srv/git/myproj", "../myproj", "file:///srv/git/myproj.git"])
    def test_filesystem_remotes_reduce_to_the_name(self, url):
        """There is no owner in a local path, so owner/repo would be noise."""
        assert normalize_remote_url(url) == "myproj"

    @pytest.mark.parametrize("url", ["", "   ", "/", "///"])
    def test_unusable_input_yields_nothing(self, url):
        assert normalize_remote_url(url) == ""

    def test_host_is_not_part_of_identity(self):
        a = normalize_remote_url("git@gitlab.com:ExampleOrg/myproj.git")
        b = normalize_remote_url("https://github.com/ExampleOrg/myproj.git")
        assert a == b == "ExampleOrg/myproj"


# ── Identity derivation ────────────────────────────────────────────────────


class TestRepoIdentity:
    def test_uses_the_remote(self, tmp_path):
        repo = make_repo(tmp_path / "myproj")
        assert repo_identity(repo) == "ExampleOrg/myproj"

    def test_survives_a_renamed_directory(self, tmp_path):
        repo = make_repo(tmp_path / "some-other-name")
        assert repo_identity(repo) == "ExampleOrg/myproj"

    def test_falls_back_to_directory_name_without_a_remote(self, tmp_path):
        repo = make_repo(tmp_path / "standalone", remote="")
        assert repo_identity(repo) == "standalone"

    def test_falls_back_outside_a_git_repo(self, tmp_path):
        plain = tmp_path / "not-a-repo"
        plain.mkdir()
        assert repo_identity(plain) == "not-a-repo"

    def test_display_name_strips_the_owner(self):
        assert repo_display_name("ExampleOrg/myproj") == "myproj"
        assert repo_display_name("myproj") == "myproj"
        assert repo_display_name("") == ""


# ── The reported scenario ──────────────────────────────────────────────────


class TestPhantomRepositories:
    def test_renamed_clone_does_not_add_a_repository(self, store, tmp_path):
        """Two checkouts of one repository must remain one Repository node."""
        original = make_repo(tmp_path / "myproj")
        renamed = make_repo(tmp_path / "myproj-clone-renamed")

        ing = RepoIngester(store)
        ing.ingest(original)
        assert len(repositories(store)) == 1
        ing.ingest(renamed)
        assert len(repositories(store)) == 1

    def test_display_name_is_not_clobbered_by_the_directory(self, store, tmp_path):
        """
        `name` used to come from the directory even when `path` was pinned, so
        the last ingest silently renamed the correct node.
        """
        original = make_repo(tmp_path / "myproj")
        renamed = make_repo(tmp_path / "myproj-X.untFWa")

        ing = RepoIngester(store)
        ing.ingest(original)
        ing.ingest(renamed)
        assert repositories(store) == [("ExampleOrg/myproj", "myproj")]

    def test_files_belong_to_exactly_one_repository(self, store, tmp_path):
        original = make_repo(tmp_path / "myproj")
        renamed = make_repo(tmp_path / "myproj-clone-renamed")

        ing = RepoIngester(store)
        ing.ingest(original)
        ing.ingest(renamed)

        rows = store.query(
            "MATCH (f:File)-[:BELONGS_TO]->(r:Repository) RETURN f.path, count(r) ORDER BY f.path"
        ).result_set
        assert all(row[1] == 1 for row in rows or []), "a file is owned by several repo nodes"

    def test_distinct_repositories_stay_distinct(self, store, tmp_path):
        first = make_repo(tmp_path / "one", remote="git@github.com:ExampleOrg/one.git")
        second = make_repo(tmp_path / "two", remote="git@github.com:ExampleOrg/two.git")

        ing = RepoIngester(store)
        ing.ingest(first)
        ing.ingest(second)
        assert len(repositories(store)) == 2

    def test_explicit_repo_key_still_wins(self, store, tmp_path):
        repo = make_repo(tmp_path / "myproj")
        RepoIngester(store).ingest(repo, repo_key="libs/core")
        assert repositories(store) == [("libs/core", "core")]
