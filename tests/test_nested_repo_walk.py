"""Regression tests for #128 — the ingest file walk must treat nested git
repositories as boundaries (never descend into them) and prune skip dirs
before entering, so metarepo roots cannot hang workspace ingest."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

from navegador.ingestion.parser import RepoIngester


def _store():
    store = MagicMock()
    store.query.return_value = MagicMock(result_set=[])
    return store


def _metarepo(tmpdir: str) -> Path:
    """A repo root containing its own code plus a nested git clone."""
    root = Path(tmpdir)
    (root / ".git").mkdir()
    (root / "own.py").write_text("def mine():\n    return 1\n", encoding="utf-8")

    nested = root / "vendored-clone"
    (nested / ".git").mkdir(parents=True)
    (nested / "theirs.py").write_text("def theirs():\n    return 2\n", encoding="utf-8")
    (nested / "deep").mkdir()
    (nested / "deep" / "more.py").write_text("def more():\n    return 3\n", encoding="utf-8")
    return root


class TestNestedRepoBoundary:
    def test_nested_git_dir_is_not_descended(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = _metarepo(tmpdir)
            files = {p.name for p in RepoIngester(_store())._iter_source_files(root)}
        assert files == {"own.py"}

    def test_nested_git_file_is_boundary_too(self):
        """.git can be a file (worktrees, submodule pointers) — still a boundary."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "own.py").write_text("x = 1\n", encoding="utf-8")
            worktree = root / "linked-worktree"
            worktree.mkdir()
            (worktree / ".git").write_text("gitdir: /elsewhere\n", encoding="utf-8")
            (worktree / "wt.py").write_text("y = 2\n", encoding="utf-8")

            files = {p.name for p in RepoIngester(_store())._iter_source_files(root)}
        assert files == {"own.py"}

    def test_ingest_root_with_its_own_git_is_still_ingested(self):
        """Only *nested* repos are boundaries — the ingest root itself has .git."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = _metarepo(tmpdir)
            stats = RepoIngester(_store()).ingest(root)
        assert stats["files"] == 1

    def test_skip_dirs_are_pruned(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "app.py").write_text("a = 1\n", encoding="utf-8")
            for skip in ("node_modules", "dist", ".venv", "vendor"):
                d = root / skip / "sub"
                d.mkdir(parents=True)
                (d / "junk.py").write_text("j = 1\n", encoding="utf-8")

            files = {p.name for p in RepoIngester(_store())._iter_source_files(root)}
        assert files == {"app.py"}

    def test_skip_dir_name_in_ancestors_of_root_does_not_hide_repo(self):
        """A repo living under a directory *named* like a skip dir (e.g.
        /home/x/build/myrepo) must still be ingested — only dirs below the
        ingest root are pruned. The old parts-based filter got this wrong."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir) / "build" / "myrepo"
            repo.mkdir(parents=True)
            (repo / "app.py").write_text("a = 1\n", encoding="utf-8")

            files = {p.name for p in RepoIngester(_store())._iter_source_files(repo)}
        assert files == {"app.py"}

    def test_ansible_pass_respects_nested_repo_boundary(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "playbook.yml").write_text(
                "---\n- hosts: all\n  tasks:\n    - name: ping\n      ping:\n", encoding="utf-8"
            )
            nested = root / "clone"
            (nested / ".git").mkdir(parents=True)
            (nested / "playbook.yml").write_text(
                "---\n- hosts: all\n  tasks:\n    - name: pong\n      ping:\n", encoding="utf-8"
            )

            ingester = RepoIngester(_store())
            stats = {"files": 0, "functions": 0, "classes": 0, "edges": 0, "skipped": 0}
            ingester._ingest_ansible(root, stats, incremental=False)
        assert stats["files"] == 1
