"""
Tests for FossilWikiSync — bidirectional Fossil ↔ GitHub wiki sync.
"""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from navegador.ingestion.fossil import FossilWikiSync

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_sync(gh_repo="owner/repo", token="ghp_test", repo_path="/repos/myproject"):
    adapter = MagicMock()
    adapter.repo_path = repo_path
    return FossilWikiSync(adapter, gh_repo, token=token)


# ── Name mapping ──────────────────────────────────────────────────────────────


class TestNameMapping:
    def test_fossil_name_to_github_filename(self):
        assert FossilWikiSync.fossil_name_to_github_filename("Auth Setup") == "Auth-Setup.md"

    def test_fossil_name_single_word(self):
        assert FossilWikiSync.fossil_name_to_github_filename("Home") == "Home.md"

    def test_github_filename_to_fossil_name(self):
        assert FossilWikiSync.github_filename_to_fossil_name("Auth-Setup.md") == "Auth Setup"

    def test_github_filename_single_word(self):
        assert FossilWikiSync.github_filename_to_fossil_name("Home.md") == "Home"

    def test_round_trip(self):
        original = "JWT Token Auth"
        assert (
            FossilWikiSync.github_filename_to_fossil_name(
                FossilWikiSync.fossil_name_to_github_filename(original)
            )
            == original
        )


# ── Clone URL ─────────────────────────────────────────────────────────────────


class TestCloneUrl:
    def test_with_token(self):
        sync = _make_sync(gh_repo="acme/docs", token="ghp_abc")
        assert sync._clone_url() == "https://x-access-token:ghp_abc@github.com/acme/docs.wiki.git"

    def test_without_token(self):
        sync = _make_sync(gh_repo="acme/docs", token="")
        assert sync._clone_url() == "https://github.com/acme/docs.wiki.git"


# ── fossil_to_github ──────────────────────────────────────────────────────────


class TestFossilToGithub:
    def test_writes_md_files_and_pushes(self, tmp_path):
        sync = _make_sync()
        sync.fossil.wiki_pages.return_value = ["Home", "Auth Setup"]
        sync.fossil.wiki_export.side_effect = lambda name: f"# {name}\nContent."

        wiki_clone = tmp_path / "wiki"
        wiki_clone.mkdir()

        with patch.object(sync, "_clone_gh_wiki", return_value=wiki_clone), \
             patch.object(sync, "_git_commit_push") as mock_push:
            stats = sync.fossil_to_github(work_dir=tmp_path)

        assert stats["pages"] == 2
        assert stats["skipped"] == 0
        assert (wiki_clone / "Home.md").read_text() == "# Home\nContent."
        assert (wiki_clone / "Auth-Setup.md").read_text() == "# Auth Setup\nContent."
        mock_push.assert_called_once()
        commit_msg = mock_push.call_args[0][1]
        assert "2" in commit_msg

    def test_skips_empty_pages(self, tmp_path):
        sync = _make_sync()
        sync.fossil.wiki_pages.return_value = ["Home", "Empty"]
        sync.fossil.wiki_export.side_effect = lambda name: "" if name == "Empty" else "content"

        wiki_clone = tmp_path / "wiki"
        wiki_clone.mkdir()

        with patch.object(sync, "_clone_gh_wiki", return_value=wiki_clone), \
             patch.object(sync, "_git_commit_push"):
            stats = sync.fossil_to_github(work_dir=tmp_path)

        assert stats["pages"] == 1
        assert stats["skipped"] == 1

    def test_no_push_when_no_pages(self, tmp_path):
        sync = _make_sync()
        sync.fossil.wiki_pages.return_value = []

        wiki_clone = tmp_path / "wiki"
        wiki_clone.mkdir()

        with patch.object(sync, "_clone_gh_wiki", return_value=wiki_clone), \
             patch.object(sync, "_git_commit_push") as mock_push:
            stats = sync.fossil_to_github(work_dir=tmp_path)

        assert stats["pages"] == 0
        mock_push.assert_not_called()

    def test_clone_failure_propagates(self, tmp_path):
        sync = _make_sync()
        sync.fossil.wiki_pages.return_value = ["Home"]

        err = subprocess.CalledProcessError(128, "git clone", stderr="repo not found")
        with patch.object(sync, "_clone_gh_wiki", side_effect=err):
            with pytest.raises(subprocess.CalledProcessError):
                sync.fossil_to_github(work_dir=tmp_path)

    def test_cleans_up_tmpdir_on_success(self):
        sync = _make_sync()
        sync.fossil.wiki_pages.return_value = []
        captured = []

        def fake_clone(parent):
            captured.append(parent)
            parent.mkdir(parents=True, exist_ok=True)
            wiki = parent / "wiki"
            wiki.mkdir()
            return wiki

        with patch.object(sync, "_clone_gh_wiki", side_effect=fake_clone), \
             patch.object(sync, "_git_commit_push"):
            sync.fossil_to_github()

        # temp dir should be cleaned up
        assert captured and not captured[0].exists()


# ── github_to_fossil ──────────────────────────────────────────────────────────


class TestGithubToFossil:
    def test_writes_pages_to_fossil(self, tmp_path):
        sync = _make_sync()

        wiki_clone = tmp_path / "wiki"
        wiki_clone.mkdir()
        (wiki_clone / "Home.md").write_text("# Home\nWelcome.")
        (wiki_clone / "Auth-Setup.md").write_text("# Auth\nDetails.")

        with patch.object(sync, "_clone_gh_wiki", return_value=wiki_clone):
            stats = sync.github_to_fossil(work_dir=tmp_path)

        assert stats["pages"] == 2
        assert stats["skipped"] == 0
        calls = sync.fossil.wiki_commit.call_args_list
        names = {c[0][0] for c in calls}
        assert names == {"Home", "Auth Setup"}

    def test_skips_empty_md_files(self, tmp_path):
        sync = _make_sync()

        wiki_clone = tmp_path / "wiki"
        wiki_clone.mkdir()
        (wiki_clone / "Home.md").write_text("content")
        (wiki_clone / "Empty.md").write_text("   \n")

        with patch.object(sync, "_clone_gh_wiki", return_value=wiki_clone):
            stats = sync.github_to_fossil(work_dir=tmp_path)

        assert stats["pages"] == 1
        assert stats["skipped"] == 1

    def test_empty_wiki_clone(self, tmp_path):
        sync = _make_sync()

        wiki_clone = tmp_path / "wiki"
        wiki_clone.mkdir()

        with patch.object(sync, "_clone_gh_wiki", return_value=wiki_clone):
            stats = sync.github_to_fossil(work_dir=tmp_path)

        assert stats["pages"] == 0
        sync.fossil.wiki_commit.assert_not_called()

    def test_clone_failure_propagates(self, tmp_path):
        sync = _make_sync()
        err = subprocess.CalledProcessError(128, "git clone", stderr="auth failed")
        with patch.object(sync, "_clone_gh_wiki", side_effect=err):
            with pytest.raises(subprocess.CalledProcessError):
                sync.github_to_fossil(work_dir=tmp_path)

    def test_correct_content_passed_to_wiki_commit(self, tmp_path):
        sync = _make_sync()

        wiki_clone = tmp_path / "wiki"
        wiki_clone.mkdir()
        (wiki_clone / "Setup-Guide.md").write_text("# Setup\nStep 1.")

        with patch.object(sync, "_clone_gh_wiki", return_value=wiki_clone):
            sync.github_to_fossil(work_dir=tmp_path)

        sync.fossil.wiki_commit.assert_called_once_with("Setup Guide", "# Setup\nStep 1.")


# ── _git_commit_push ──────────────────────────────────────────────────────────


class TestGitCommitPush:
    def test_skips_push_when_no_changes(self, tmp_path):
        sync = _make_sync()
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()

        with patch("subprocess.run") as mock_run:
            # git add succeeds, git diff --staged --quiet returns 0 (no changes)
            mock_run.return_value = MagicMock(returncode=0)
            sync._git_commit_push(wiki_dir, "test message")

        commands = [c[0][0] for c in mock_run.call_args_list]
        # Should run git add and git diff, but NOT git commit or git push
        assert any("add" in cmd for cmd in commands)
        assert any("diff" in cmd for cmd in commands)
        assert not any("commit" in cmd for cmd in commands)
        assert not any("push" in cmd for cmd in commands)

    def test_commits_and_pushes_when_changes_exist(self, tmp_path):
        sync = _make_sync()
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()

        call_count = 0

        def mock_run(cmd, **kwargs):
            nonlocal call_count
            call_count += 1
            # git diff --staged --quiet: returncode 1 means there ARE changes
            if "diff" in cmd:
                return MagicMock(returncode=1)
            return MagicMock(returncode=0)

        with patch("subprocess.run", side_effect=mock_run):
            sync._git_commit_push(wiki_dir, "sync pages")

        assert call_count == 4  # add, diff, commit, push


# ── FossilAdapter.wiki_commit ─────────────────────────────────────────────────


class TestFossilAdapterWikiCommit:
    def test_calls_fossil_wiki_commit(self):
        from navegador.vcs import FossilAdapter

        adapter = FossilAdapter(Path("/repo"))
        with patch.object(adapter, "_run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            adapter.wiki_commit("My Page", "# Content")

        mock_run.assert_called_once_with(
            ["wiki", "commit", "--mimetype", "text/x-markdown", "My Page"],
            check=False,
            input="# Content",
        )

    def test_falls_back_without_mimetype_on_old_fossil(self):
        from navegador.vcs import FossilAdapter

        adapter = FossilAdapter(Path("/repo"))
        calls = []

        def fake_run(args, check=True, input=None):
            calls.append(args)
            if "--mimetype" in args:
                return MagicMock(returncode=1, stderr="unknown option: --mimetype")
            return MagicMock(returncode=0)

        with patch.object(adapter, "_run", side_effect=fake_run):
            adapter.wiki_commit("My Page", "content")

        assert any("--mimetype" not in c for c in calls)
        assert len(calls) == 2
