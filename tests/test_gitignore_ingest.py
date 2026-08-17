"""
Ingest respects .gitignore (#180).

The bug these cover: the walk pruned on a fixed ``_SKIP_DIRS`` list and never
read .gitignore, so a project whose build output sat in a directory outside
that list was indexed wholesale. On one real graph 54,543 of 54,552 File nodes
were gitignored build output under ``bundled/``.

Every test builds a real git repository on disk and runs the real walk. Git is
the oracle for what is ignored, so the assertions here are about the ingester
asking it and honouring the answer.
"""

import subprocess

import pytest

from navegador.graph import GraphStore
from navegador.ingestion import RepoIngester


def git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    """A git repo with tracked source and a gitignored build directory."""
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "bundled" / "deep").mkdir(parents=True)

    (root / "src" / "app.py").write_text("def real_function():\n    return 1\n")
    (root / "src" / "util.py").write_text("def helper():\n    return 2\n")
    (root / "bundled" / "vendor.py").write_text("def generated_thing():\n    return 3\n")
    (root / "bundled" / "deep" / "more.py").write_text("def deeper_generated():\n    return 4\n")
    (root / ".gitignore").write_text("bundled/\n")

    git(root, "init", "-q")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "t")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "initial")
    return root


def files_in(store):
    result = store.query("MATCH (f:File) RETURN f.path")
    return {row[0] for row in (result.result_set or [])}


def functions_in(store):
    result = store.query("MATCH (n:Function) RETURN n.name")
    return {row[0] for row in (result.result_set or [])}


class TestGitignoreIsHonoured:
    def test_ignored_directory_is_not_indexed(self, repo, tmp_path):
        store = GraphStore.sqlite(str(tmp_path / "g.db"))
        RepoIngester(store).ingest(repo)

        paths = files_in(store)
        assert "src/app.py" in paths
        assert "src/util.py" in paths
        assert not [p for p in paths if p.startswith("bundled/")], (
            f"gitignored files were indexed: {sorted(p for p in paths if 'bundled' in p)}"
        )

    def test_symbols_from_ignored_files_are_absent(self, repo, tmp_path):
        """
        The File node is the visible half; the real damage was agents being
        offered generated symbols as though they were source.
        """
        store = GraphStore.sqlite(str(tmp_path / "g.db"))
        RepoIngester(store).ingest(repo)

        names = functions_in(store)
        assert "real_function" in names
        assert "generated_thing" not in names
        assert "deeper_generated" not in names

    def test_nested_ignore_file_is_honoured(self, repo, tmp_path):
        """
        A .gitignore deeper in the tree also applies. Hand-rolled matchers
        routinely miss this, which is why git is the oracle.
        """
        (repo / "src" / "gen").mkdir()
        (repo / "src" / "gen" / "out.py").write_text("def nested_generated():\n    return 5\n")
        (repo / "src" / ".gitignore").write_text("gen/\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-qm", "nested ignore")

        store = GraphStore.sqlite(str(tmp_path / "g.db"))
        RepoIngester(store).ingest(repo)

        assert "nested_generated" not in functions_in(store)
        assert "real_function" in functions_in(store)

    def test_negation_pattern_is_honoured(self, repo, tmp_path):
        """
        ``!bundled/keep.py`` re-includes one file from an ignored tree.

        The pattern is ``bundled/*`` rather than ``bundled/``: git refuses to
        re-include a path whose parent directory is itself excluded, because
        it never descends into it. Exactly the kind of rule a hand-rolled
        matcher gets wrong, and the reason git is the oracle here.
        """
        (repo / "bundled" / "keep.py").write_text("def kept_on_purpose():\n    return 6\n")
        (repo / ".gitignore").write_text("bundled/*\n!bundled/keep.py\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-qm", "negation")

        store = GraphStore.sqlite(str(tmp_path / "g.db"))
        RepoIngester(store).ingest(repo)

        names = functions_in(store)
        assert "kept_on_purpose" in names
        assert "generated_thing" not in names

    def test_untracked_but_not_ignored_is_indexed(self, repo, tmp_path):
        """
        A brand new file nobody has committed is still source. Indexing only
        tracked files would hide work in progress from the agent.
        """
        (repo / "src" / "brand_new.py").write_text("def just_written():\n    return 7\n")

        store = GraphStore.sqlite(str(tmp_path / "g.db"))
        RepoIngester(store).ingest(repo)

        assert "just_written" in functions_in(store)


class TestOptOut:
    def test_no_gitignore_indexes_everything(self, repo, tmp_path):
        store = GraphStore.sqlite(str(tmp_path / "g.db"))
        RepoIngester(store, respect_gitignore=False).ingest(repo)

        names = functions_in(store)
        assert "generated_thing" in names
        assert "deeper_generated" in names
        assert "real_function" in names


class TestOutsideGit:
    def test_plain_directory_still_ingests(self, tmp_path):
        """
        No git repo means no opinion — the walk must fall back to indexing
        everything rather than silently producing an empty graph.
        """
        root = tmp_path / "plain"
        (root / "src").mkdir(parents=True)
        (root / "src" / "thing.py").write_text("def plain_function():\n    return 1\n")

        store = GraphStore.sqlite(str(tmp_path / "g.db"))
        RepoIngester(store).ingest(root)

        assert "plain_function" in functions_in(store)

    def test_skip_dirs_still_apply_without_git(self, tmp_path):
        """The fixed skip list remains the floor outside a git checkout."""
        root = tmp_path / "plain"
        (root / "node_modules").mkdir(parents=True)
        (root / "node_modules" / "dep.py").write_text("def vendored():\n    return 1\n")
        (root / "app.py").write_text("def mine():\n    return 2\n")

        store = GraphStore.sqlite(str(tmp_path / "g.db"))
        RepoIngester(store).ingest(root)

        names = functions_in(store)
        assert "mine" in names
        assert "vendored" not in names


class TestPruningNotFiltering:
    def test_ignored_tree_is_never_descended_into(self, repo, tmp_path, monkeypatch):
        """
        Correctness alone could be had by filtering files after the walk, but
        that reintroduces the #128 hang: a huge ignored tree would still be
        enumerated. Assert the walk never enters it.
        """
        import os as os_module

        entered: list[str] = []
        real_walk = os_module.walk

        def tracking_walk(top, *a, **kw):
            for dirpath, dirnames, filenames in real_walk(top, *a, **kw):
                entered.append(str(dirpath))
                yield dirpath, dirnames, filenames

        monkeypatch.setattr("navegador.ingestion.parser.os.walk", tracking_walk)

        store = GraphStore.sqlite(str(tmp_path / "g.db"))
        RepoIngester(store).ingest(repo)

        assert not [d for d in entered if "bundled" in d], (
            f"walk descended into an ignored tree: {[d for d in entered if 'bundled' in d]}"
        )
