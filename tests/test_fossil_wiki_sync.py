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


# ── Clone command ─────────────────────────────────────────────────────────────


class TestCloneCommand:
    def test_with_token_uses_extra_header(self, tmp_path):
        sync = _make_sync(gh_repo="acme/docs", token="ghp_abc")
        wiki_dir = tmp_path / "wiki"

        def fake_run(cmd, **kwargs):
            wiki_dir.mkdir(parents=True, exist_ok=True)
            return MagicMock(returncode=0)

        with patch("subprocess.run", side_effect=fake_run) as mock_run:
            sync._clone_gh_wiki(tmp_path)

        cmd = mock_run.call_args[0][0]
        assert "-c" in cmd
        assert "http.extraHeader=Authorization: token ghp_abc" in cmd
        # Token must NOT appear in the URL
        url = cmd[-2]
        assert "ghp_abc" not in url
        assert url == "https://github.com/acme/docs.wiki.git"

    def test_without_token_no_extra_header(self, tmp_path):
        sync = _make_sync(gh_repo="acme/docs", token="")
        wiki_dir = tmp_path / "wiki"

        def fake_run(cmd, **kwargs):
            wiki_dir.mkdir(parents=True, exist_ok=True)
            return MagicMock(returncode=0)

        with patch("subprocess.run", side_effect=fake_run) as mock_run:
            sync._clone_gh_wiki(tmp_path)

        cmd = mock_run.call_args[0][0]
        assert "-c" not in cmd
        url = cmd[-2]
        assert url == "https://github.com/acme/docs.wiki.git"


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

        assert call_count == 6  # config×2, add, diff, commit, push


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


# ── _content_hash ─────────────────────────────────────────────────────────────


class TestContentHash:
    def test_same_content_same_hash(self):
        from navegador.ingestion.fossil import _content_hash
        assert _content_hash("hello") == _content_hash("hello")

    def test_different_content_different_hash(self):
        from navegador.ingestion.fossil import _content_hash
        assert _content_hash("hello") != _content_hash("world")

    def test_returns_16_char_hex(self):
        from navegador.ingestion.fossil import _content_hash
        h = _content_hash("test")
        assert len(h) == 16
        assert all(c in "0123456789abcdef" for c in h)


# ── Cursor helpers ────────────────────────────────────────────────────────────


class TestCursorHelpers:
    def test_load_returns_empty_dict_for_missing_file(self, tmp_path):
        cursor = FossilWikiSync._load_cursor(tmp_path / "nonexistent.json")
        assert cursor == {}

    def test_load_returns_empty_dict_for_corrupt_file(self, tmp_path):
        p = tmp_path / "cursor.json"
        p.write_text("not json{{{")
        cursor = FossilWikiSync._load_cursor(p)
        assert cursor == {}

    def test_save_and_load_round_trip(self, tmp_path):
        p = tmp_path / "sub" / "cursor.json"
        data = {"Home": {"fossil_hash": "abc", "github_hash": "def", "synced_at": "2024-01-01"}}
        FossilWikiSync._save_cursor(p, data)
        assert FossilWikiSync._load_cursor(p) == data

    def test_save_creates_parent_dirs(self, tmp_path):
        p = tmp_path / "a" / "b" / "c" / "cursor.json"
        FossilWikiSync._save_cursor(p, {})
        assert p.exists()


# ── sync() — first sync ───────────────────────────────────────────────────────


class TestSyncFirstRun:
    def _setup(self, tmp_path, fossil_pages, github_files):
        sync = _make_sync()
        sync.fossil.wiki_pages.return_value = list(fossil_pages.keys())
        sync.fossil.wiki_export.side_effect = lambda n: fossil_pages.get(n, "")

        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()
        for fname, content in github_files.items():
            (wiki_dir / fname).write_text(content)

        return sync, wiki_dir

    def test_fossil_only_page_pushed_to_github(self, tmp_path):
        sync, wiki_dir = self._setup(
            tmp_path,
            fossil_pages={"Home": "# Home\nWelcome."},
            github_files={},
        )
        cursor_path = tmp_path / "cursor.json"

        with patch.object(sync, "_clone_gh_wiki", return_value=wiki_dir), \
             patch.object(sync, "_git_commit_push"):
            stats = sync.sync(work_dir=tmp_path, cursor_path=cursor_path)

        assert stats["pushed_to_github"] == 1
        assert stats["pushed_to_fossil"] == 0
        assert stats["conflicts"] == []
        assert (wiki_dir / "Home.md").exists()

    def test_github_only_page_pushed_to_fossil(self, tmp_path):
        sync, wiki_dir = self._setup(
            tmp_path,
            fossil_pages={},
            github_files={"Setup.md": "# Setup\nStep 1."},
        )
        cursor_path = tmp_path / "cursor.json"

        with patch.object(sync, "_clone_gh_wiki", return_value=wiki_dir), \
             patch.object(sync, "_git_commit_push"):
            stats = sync.sync(work_dir=tmp_path, cursor_path=cursor_path)

        assert stats["pushed_to_fossil"] == 1
        assert stats["pushed_to_github"] == 0
        sync.fossil.wiki_commit.assert_called_once_with("Setup", "# Setup\nStep 1.")

    def test_identical_pages_on_both_sides_no_transfer(self, tmp_path):
        content = "# Same\nIdentical content."
        sync, wiki_dir = self._setup(
            tmp_path,
            fossil_pages={"Same": content},
            github_files={"Same.md": content},
        )
        cursor_path = tmp_path / "cursor.json"

        with patch.object(sync, "_clone_gh_wiki", return_value=wiki_dir), \
             patch.object(sync, "_git_commit_push") as mock_push:
            stats = sync.sync(work_dir=tmp_path, cursor_path=cursor_path)

        assert stats["pushed_to_github"] == 0
        assert stats["pushed_to_fossil"] == 0
        assert stats["conflicts"] == []
        mock_push.assert_not_called()

    def test_both_sides_different_content_is_conflict(self, tmp_path):
        sync, wiki_dir = self._setup(
            tmp_path,
            fossil_pages={"Auth": "# Auth\nFossil version."},
            github_files={"Auth.md": "# Auth\nGitHub version."},
        )
        cursor_path = tmp_path / "cursor.json"

        with patch.object(sync, "_clone_gh_wiki", return_value=wiki_dir), \
             patch.object(sync, "_git_commit_push"):
            stats = sync.sync(work_dir=tmp_path, cursor_path=cursor_path)

        assert "Auth" in stats["conflicts"]
        assert stats["pushed_to_github"] == 0
        assert stats["pushed_to_fossil"] == 0

    def test_cursor_written_after_sync(self, tmp_path):
        sync, wiki_dir = self._setup(
            tmp_path,
            fossil_pages={"Home": "# Home"},
            github_files={},
        )
        cursor_path = tmp_path / "cursor.json"

        with patch.object(sync, "_clone_gh_wiki", return_value=wiki_dir), \
             patch.object(sync, "_git_commit_push"):
            sync.sync(work_dir=tmp_path, cursor_path=cursor_path)

        cursor = FossilWikiSync._load_cursor(cursor_path)
        assert "Home" in cursor
        assert "fossil_hash" in cursor["Home"]
        assert "github_hash" in cursor["Home"]
        assert "synced_at" in cursor["Home"]

    def test_conflicts_not_written_to_cursor(self, tmp_path):
        sync, wiki_dir = self._setup(
            tmp_path,
            fossil_pages={"Conflict": "fossil content"},
            github_files={"Conflict.md": "github content"},
        )
        cursor_path = tmp_path / "cursor.json"

        with patch.object(sync, "_clone_gh_wiki", return_value=wiki_dir), \
             patch.object(sync, "_git_commit_push"):
            sync.sync(work_dir=tmp_path, cursor_path=cursor_path)

        cursor = FossilWikiSync._load_cursor(cursor_path)
        assert "Conflict" not in cursor


# ── sync() — subsequent runs ──────────────────────────────────────────────────


class TestSyncSubsequentRuns:
    def _make_cursor(self, tmp_path, entries: dict) -> Path:
        from navegador.ingestion.fossil import _content_hash
        cursor = {}
        for name, (fossil_content, github_content) in entries.items():
            cursor[name] = {
                "fossil_hash": _content_hash(fossil_content),
                "github_hash": _content_hash(github_content),
                "synced_at": "2024-01-01T00:00:00+00:00",
            }
        p = tmp_path / "cursor.json"
        FossilWikiSync._save_cursor(p, cursor)
        return p

    def test_fossil_changed_pushes_to_github(self, tmp_path):
        old = "# Home\nOld content."
        new = "# Home\nNew content from Fossil."

        sync = _make_sync()
        sync.fossil.wiki_pages.return_value = ["Home"]
        sync.fossil.wiki_export.return_value = new

        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()
        (wiki_dir / "Home.md").write_text(old)  # GitHub unchanged

        cursor_path = self._make_cursor(tmp_path, {"Home": (old, old)})

        with patch.object(sync, "_clone_gh_wiki", return_value=wiki_dir), \
             patch.object(sync, "_git_commit_push") as mock_push:
            stats = sync.sync(work_dir=tmp_path, cursor_path=cursor_path)

        assert stats["pushed_to_github"] == 1
        assert stats["pushed_to_fossil"] == 0
        assert stats["conflicts"] == []
        assert (wiki_dir / "Home.md").read_text() == new
        mock_push.assert_called_once()

    def test_github_changed_pushes_to_fossil(self, tmp_path):
        old = "# Setup\nOld."
        new_gh = "# Setup\nNew from GitHub."

        sync = _make_sync()
        sync.fossil.wiki_pages.return_value = ["Setup"]
        sync.fossil.wiki_export.return_value = old  # Fossil unchanged

        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()
        (wiki_dir / "Setup.md").write_text(new_gh)

        cursor_path = self._make_cursor(tmp_path, {"Setup": (old, old)})

        with patch.object(sync, "_clone_gh_wiki", return_value=wiki_dir), \
             patch.object(sync, "_git_commit_push"):
            stats = sync.sync(work_dir=tmp_path, cursor_path=cursor_path)

        assert stats["pushed_to_fossil"] == 1
        assert stats["pushed_to_github"] == 0
        sync.fossil.wiki_commit.assert_called_once_with("Setup", new_gh)

    def test_both_changed_is_conflict(self, tmp_path):
        old = "# Auth\nOriginal."

        sync = _make_sync()
        sync.fossil.wiki_pages.return_value = ["Auth"]
        sync.fossil.wiki_export.return_value = "# Auth\nFossil edit."

        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()
        (wiki_dir / "Auth.md").write_text("# Auth\nGitHub edit.")

        cursor_path = self._make_cursor(tmp_path, {"Auth": (old, old)})

        with patch.object(sync, "_clone_gh_wiki", return_value=wiki_dir), \
             patch.object(sync, "_git_commit_push"):
            stats = sync.sync(work_dir=tmp_path, cursor_path=cursor_path)

        assert "Auth" in stats["conflicts"]
        assert stats["pushed_to_github"] == 0
        assert stats["pushed_to_fossil"] == 0

    def test_unchanged_on_both_sides_is_noop(self, tmp_path):
        content = "# Stable\nUnchanged."

        sync = _make_sync()
        sync.fossil.wiki_pages.return_value = ["Stable"]
        sync.fossil.wiki_export.return_value = content

        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()
        (wiki_dir / "Stable.md").write_text(content)

        cursor_path = self._make_cursor(tmp_path, {"Stable": (content, content)})

        with patch.object(sync, "_clone_gh_wiki", return_value=wiki_dir), \
             patch.object(sync, "_git_commit_push") as mock_push:
            stats = sync.sync(work_dir=tmp_path, cursor_path=cursor_path)

        assert stats["pushed_to_github"] == 0
        assert stats["pushed_to_fossil"] == 0
        assert stats["conflicts"] == []
        mock_push.assert_not_called()
        sync.fossil.wiki_commit.assert_not_called()

    def test_cursor_updated_with_new_hashes_after_sync(self, tmp_path):
        old = "# Home\nOld."
        new = "# Home\nNew from Fossil."
        from navegador.ingestion.fossil import _content_hash

        sync = _make_sync()
        sync.fossil.wiki_pages.return_value = ["Home"]
        sync.fossil.wiki_export.return_value = new

        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()
        (wiki_dir / "Home.md").write_text(old)

        cursor_path = self._make_cursor(tmp_path, {"Home": (old, old)})

        with patch.object(sync, "_clone_gh_wiki", return_value=wiki_dir), \
             patch.object(sync, "_git_commit_push"):
            sync.sync(work_dir=tmp_path, cursor_path=cursor_path)

        cursor = FossilWikiSync._load_cursor(cursor_path)
        assert cursor["Home"]["fossil_hash"] == _content_hash(new)
        # GitHub now has the new content too
        assert cursor["Home"]["github_hash"] == _content_hash(new)

    def test_stale_cursor_entries_preserved(self, tmp_path):
        """Pages deleted from both sides keep their cursor entry (not our job to GC)."""

        sync = _make_sync()
        sync.fossil.wiki_pages.return_value = []

        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()

        old_content = "# Old Page\nGone."
        cursor_path = self._make_cursor(tmp_path, {"Old Page": (old_content, old_content)})

        with patch.object(sync, "_clone_gh_wiki", return_value=wiki_dir), \
             patch.object(sync, "_git_commit_push"):
            sync.sync(work_dir=tmp_path, cursor_path=cursor_path)

        cursor = FossilWikiSync._load_cursor(cursor_path)
        assert "Old Page" in cursor


# ── git author config ─────────────────────────────────────────────────────────


class TestGitAuthorConfig:
    def test_git_config_called_before_commit(self, tmp_path):
        sync = _make_sync()
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()

        config_calls = []

        def mock_run(cmd, **kwargs):
            if "config" in cmd:
                config_calls.append(cmd)
            # diff --staged returns 1 → there are changes
            if "diff" in cmd:
                return MagicMock(returncode=1)
            return MagicMock(returncode=0)

        with patch("subprocess.run", side_effect=mock_run):
            sync._git_commit_push(wiki_dir, "test")

        assert any("user.name" in c for c in config_calls)
        assert any("user.email" in c for c in config_calls)
