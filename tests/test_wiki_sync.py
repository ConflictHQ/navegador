"""Tests for navegador.wiki_sync — WikiProvider implementations and WikiSync engine."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from navegador.wiki_sync import (
    FossilWikiProvider,
    GitHubWikiProvider,
    LocalMarkdownProvider,
    WikiSync,
    _content_hash,
    _load_cursor,
    _save_cursor,
)

# ── Helpers ──────────────────────────────────────────────────────────────────


class TestContentHash:
    def test_returns_16_char_hex(self):
        h = _content_hash("hello")
        assert len(h) == 16
        assert all(c in "0123456789abcdef" for c in h)

    def test_deterministic(self):
        assert _content_hash("test") == _content_hash("test")

    def test_different_for_different_content(self):
        assert _content_hash("alpha") != _content_hash("beta")

    def test_empty_string(self):
        h = _content_hash("")
        assert len(h) == 16


class TestCursorIO:
    def test_load_missing_file(self, tmp_path):
        assert _load_cursor(tmp_path / "nonexistent.json") == {}

    def test_save_and_load_round_trip(self, tmp_path):
        cursor = {"page": {"a_hash": "abc", "b_hash": "def", "synced_at": "2026-01-01"}}
        path = tmp_path / "cursor.json"
        _save_cursor(path, cursor)
        loaded = _load_cursor(path)
        assert loaded == cursor

    def test_save_creates_parent_dirs(self, tmp_path):
        path = tmp_path / "sub" / "dir" / "cursor.json"
        _save_cursor(path, {"key": "value"})
        assert path.exists()
        assert _load_cursor(path) == {"key": "value"}

    def test_load_returns_empty_on_malformed_json(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("not json at all", encoding="utf-8")
        assert _load_cursor(path) == {}


# ── LocalMarkdownProvider ────────────────────────────────────────────────────


class TestLocalMarkdownProvider:
    def test_list_pages_empty_dir(self, tmp_path):
        provider = LocalMarkdownProvider(tmp_path)
        assert provider.list_pages() == []

    def test_list_pages_nonexistent_dir(self, tmp_path):
        provider = LocalMarkdownProvider(tmp_path / "nope")
        assert provider.list_pages() == []

    def test_list_pages_with_files(self, tmp_path):
        (tmp_path / "Home.md").write_text("# Home", encoding="utf-8")
        (tmp_path / "Getting-Started.md").write_text("# Guide", encoding="utf-8")
        (tmp_path / "notes.txt").write_text("ignored", encoding="utf-8")

        provider = LocalMarkdownProvider(tmp_path)
        pages = provider.list_pages()
        assert "Home" in pages
        assert "Getting Started" in pages
        assert len(pages) == 2

    def test_get_page_exists(self, tmp_path):
        (tmp_path / "My-Page.md").write_text("page content", encoding="utf-8")
        provider = LocalMarkdownProvider(tmp_path)
        assert provider.get_page("My Page") == "page content"

    def test_get_page_missing(self, tmp_path):
        provider = LocalMarkdownProvider(tmp_path)
        assert provider.get_page("No Such Page") == ""

    def test_put_page_creates_file(self, tmp_path):
        provider = LocalMarkdownProvider(tmp_path)
        provider.put_page("New Page", "new content")
        assert (tmp_path / "New-Page.md").read_text(encoding="utf-8") == "new content"

    def test_put_page_creates_directory(self, tmp_path):
        target = tmp_path / "sub" / "dir"
        provider = LocalMarkdownProvider(target)
        provider.put_page("Test", "content")
        assert (target / "Test.md").read_text(encoding="utf-8") == "content"

    def test_round_trip(self, tmp_path):
        provider = LocalMarkdownProvider(tmp_path)
        provider.put_page("Round Trip", "some text here")
        assert provider.get_page("Round Trip") == "some text here"
        assert "Round Trip" in provider.list_pages()

    def test_commit_is_noop(self, tmp_path):
        provider = LocalMarkdownProvider(tmp_path)
        # Should not raise
        provider.commit("anything")


# ── FossilWikiProvider ───────────────────────────────────────────────────────


class TestFossilWikiProvider:
    def _make_adapter(self):
        adapter = MagicMock()
        adapter.wiki_pages.return_value = ["Home", "Getting Started"]
        adapter.wiki_export.return_value = "page content"
        return adapter

    def test_list_pages(self):
        adapter = self._make_adapter()
        provider = FossilWikiProvider(adapter)
        assert provider.list_pages() == ["Home", "Getting Started"]
        adapter.wiki_pages.assert_called_once()

    def test_get_page(self):
        adapter = self._make_adapter()
        provider = FossilWikiProvider(adapter)
        assert provider.get_page("Home") == "page content"
        adapter.wiki_export.assert_called_once_with("Home")

    def test_put_page(self):
        adapter = self._make_adapter()
        provider = FossilWikiProvider(adapter)
        provider.put_page("Home", "updated")
        adapter.wiki_commit.assert_called_once_with("Home", "updated")

    def test_commit_is_noop(self):
        adapter = self._make_adapter()
        provider = FossilWikiProvider(adapter)
        provider.commit("msg")
        # No error; commit is a no-op for fossil (wiki_commit is immediate)


# ── GitHubWikiProvider ───────────────────────────────────────────────────────


class TestGitHubWikiProvider:
    def test_open_clones_repo(self, tmp_path):
        provider = GitHubWikiProvider("owner/repo", token="tok123")

        captured: dict = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["env"] = kwargs.get("env")
            if cmd[1] == "clone":
                target = Path(cmd[-1])
                target.mkdir(parents=True, exist_ok=True)
                (target / "Home.md").write_text("# Home", encoding="utf-8")
            result = MagicMock()
            result.returncode = 0
            return result

        with patch("navegador.wiki_sync.subprocess.run", side_effect=fake_run):
            provider.open()

        assert provider._wiki_dir is not None
        assert provider._wiki_dir.exists()

        # Token must NOT appear in argv
        assert "tok123" not in " ".join(captured["cmd"])
        # Token must be passed via GIT_CONFIG env vars, not embedded in URL
        env = captured["env"]
        assert env is not None
        assert env.get("GIT_CONFIG_VALUE_0") == "Authorization: token tok123"
        # Plain URL with no credentials embedded
        assert "tok123" not in captured["cmd"][-2]

        provider.close()

    def test_open_raises_on_clone_failure(self):
        provider = GitHubWikiProvider("owner/repo")

        failed = MagicMock()
        failed.returncode = 128
        failed.stdout = ""
        failed.stderr = "repo not found"

        with patch(
            "navegador.wiki_sync.subprocess.run",
            return_value=failed,
        ):
            with pytest.raises(subprocess.CalledProcessError):
                provider.open()

    def test_list_pages_after_open(self, tmp_path):
        provider = GitHubWikiProvider("owner/repo")
        # Simulate a cloned wiki directory
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()
        (wiki_dir / "Home.md").write_text("# Home", encoding="utf-8")
        (wiki_dir / "Getting-Started.md").write_text("# Guide", encoding="utf-8")
        provider._wiki_dir = wiki_dir

        pages = provider.list_pages()
        assert "Home" in pages
        assert "Getting Started" in pages

    def test_list_pages_before_open(self):
        provider = GitHubWikiProvider("owner/repo")
        assert provider.list_pages() == []

    def test_get_page_exists(self, tmp_path):
        provider = GitHubWikiProvider("owner/repo")
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()
        (wiki_dir / "My-Page.md").write_text("content here", encoding="utf-8")
        provider._wiki_dir = wiki_dir

        assert provider.get_page("My Page") == "content here"

    def test_get_page_missing(self, tmp_path):
        provider = GitHubWikiProvider("owner/repo")
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()
        provider._wiki_dir = wiki_dir

        assert provider.get_page("Missing") == ""

    def test_get_page_before_open(self):
        provider = GitHubWikiProvider("owner/repo")
        assert provider.get_page("anything") == ""

    def test_put_page(self, tmp_path):
        provider = GitHubWikiProvider("owner/repo")
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()
        provider._wiki_dir = wiki_dir

        provider.put_page("New Page", "new content")
        assert (wiki_dir / "New-Page.md").read_text(encoding="utf-8") == "new content"

    def test_put_page_before_open(self):
        provider = GitHubWikiProvider("owner/repo")
        # Should silently no-op when wiki_dir is None
        provider.put_page("Test", "content")

    def test_commit_with_changes(self, tmp_path):
        provider = GitHubWikiProvider("owner/repo")
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()
        provider._wiki_dir = wiki_dir

        call_index = {"n": 0}

        def fake_run(cmd, **kwargs):
            call_index["n"] += 1
            result = MagicMock()
            # "git diff --cached --quiet" should return non-zero (changes exist)
            if "diff" in cmd and "--cached" in cmd:
                result.returncode = 1
            else:
                result.returncode = 0
            return result

        with patch("navegador.wiki_sync.subprocess.run", side_effect=fake_run) as mock_run:
            provider.commit("sync changes")

        # Should have called: config user.name, config user.email, add -A,
        # diff --cached --quiet, commit, push
        cmds = [c[0][0] for c in mock_run.call_args_list]
        assert any("config" in c and "user.name" in c for c in cmds)
        assert any("add" in c for c in cmds)
        assert any("commit" in c for c in cmds)
        assert any("push" in c for c in cmds)

    def test_commit_skips_when_no_changes(self, tmp_path):
        provider = GitHubWikiProvider("owner/repo")
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()
        provider._wiki_dir = wiki_dir

        def fake_run(cmd, **kwargs):
            result = MagicMock()
            # "git diff --cached --quiet" returns 0 → nothing staged
            result.returncode = 0
            return result

        with patch("navegador.wiki_sync.subprocess.run", side_effect=fake_run) as mock_run:
            provider.commit("no changes")

        cmds = [c[0][0] for c in mock_run.call_args_list]
        # Should NOT have called commit or push
        assert not any("commit" in c and "-m" in c for c in cmds)
        assert not any(c == ["git", "push"] for c in cmds)

    def test_commit_before_open(self):
        provider = GitHubWikiProvider("owner/repo")
        # Should silently no-op
        provider.commit("anything")

    def test_close_cleans_tmpdir(self, tmp_path):
        provider = GitHubWikiProvider("owner/repo")
        # Set up a fake tmpdir
        tmpdir = tmp_path / "tmpdir"
        tmpdir.mkdir()
        wiki_dir = tmpdir / "wiki"
        wiki_dir.mkdir()
        provider._tmpdir = str(tmpdir)
        provider._wiki_dir = wiki_dir

        provider.close()
        assert not tmpdir.exists()
        assert provider._wiki_dir is None
        assert provider._tmpdir is None

    def test_close_before_open(self):
        provider = GitHubWikiProvider("owner/repo")
        # Should not raise
        provider.close()


# ── WikiSync ─────────────────────────────────────────────────────────────────


def _make_provider(pages: dict[str, str] | None = None) -> MagicMock:
    """Create a mock WikiProvider with configurable page content."""
    provider = MagicMock()
    pages = pages or {}

    provider.list_pages.return_value = list(pages.keys())
    provider.get_page.side_effect = lambda name: pages.get(name, "")
    return provider


class TestWikiSyncFirstSync:
    """Tests for first-time sync (no cursor entries)."""

    def test_a_only_pushes_to_b(self, tmp_path):
        a = _make_provider({"Home": "content A"})
        b = _make_provider({})

        engine = WikiSync(a, b)
        result = engine.sync(cursor_path=tmp_path / "cursor.json")

        assert result["pushed_to_b"] == 1
        assert result["pushed_to_a"] == 0
        b.put_page.assert_called_once_with("Home", "content A")
        b.commit.assert_called_once()

    def test_b_only_pushes_to_a(self, tmp_path):
        a = _make_provider({})
        b = _make_provider({"Guide": "content B"})

        engine = WikiSync(a, b)
        result = engine.sync(cursor_path=tmp_path / "cursor.json")

        assert result["pushed_to_a"] == 1
        assert result["pushed_to_b"] == 0
        a.put_page.assert_called_once_with("Guide", "content B")
        a.commit.assert_called_once()

    def test_identical_content_skipped(self, tmp_path):
        a = _make_provider({"Home": "same content"})
        b = _make_provider({"Home": "same content"})

        engine = WikiSync(a, b)
        result = engine.sync(cursor_path=tmp_path / "cursor.json")

        assert result["pushed_to_a"] == 0
        assert result["pushed_to_b"] == 0
        assert result["skipped"] == 1
        a.put_page.assert_not_called()
        b.put_page.assert_not_called()

    def test_different_content_is_conflict(self, tmp_path):
        a = _make_provider({"Home": "version A"})
        b = _make_provider({"Home": "version B"})

        engine = WikiSync(a, b)
        result = engine.sync(cursor_path=tmp_path / "cursor.json")

        assert result["conflicts"] == ["Home"]
        assert result["skipped"] == 1
        a.put_page.assert_not_called()
        b.put_page.assert_not_called()

    def test_conflict_not_written_to_cursor(self, tmp_path):
        a = _make_provider({"Conflict": "A version"})
        b = _make_provider({"Conflict": "B version"})

        cursor_path = tmp_path / "cursor.json"
        engine = WikiSync(a, b)
        engine.sync(cursor_path=cursor_path)

        cursor = json.loads(cursor_path.read_text(encoding="utf-8"))
        assert "Conflict" not in cursor

    def test_both_blank_skipped(self, tmp_path):
        # Pages in list but empty content
        a = _make_provider({"Empty": ""})
        b = _make_provider({"Empty": ""})

        engine = WikiSync(a, b)
        result = engine.sync(cursor_path=tmp_path / "cursor.json")

        assert result["skipped"] == 1
        assert result["pushed_to_a"] == 0
        assert result["pushed_to_b"] == 0

    def test_multiple_pages_mixed(self, tmp_path):
        a = _make_provider({"Only A": "a stuff", "Both": "same", "Diff": "a ver"})
        b = _make_provider({"Only B": "b stuff", "Both": "same", "Diff": "b ver"})

        engine = WikiSync(a, b)
        result = engine.sync(cursor_path=tmp_path / "cursor.json")

        assert result["pushed_to_b"] == 1  # Only A
        assert result["pushed_to_a"] == 1  # Only B
        assert result["conflicts"] == ["Diff"]

    def test_cursor_written_for_synced_pages(self, tmp_path):
        a = _make_provider({"Synced": "content"})
        b = _make_provider({})

        cursor_path = tmp_path / "cursor.json"
        engine = WikiSync(a, b)
        engine.sync(cursor_path=cursor_path)

        cursor = json.loads(cursor_path.read_text(encoding="utf-8"))
        assert "Synced" in cursor
        entry = cursor["Synced"]
        assert "a_hash" in entry
        assert "b_hash" in entry
        assert "synced_at" in entry


class TestWikiSyncSubsequent:
    """Tests for subsequent syncs (cursor already exists)."""

    def _write_cursor(self, path, pages_data):
        """Write a cursor with the given page hash data."""
        cursor = {}
        for name, a_hash, b_hash in pages_data:
            cursor[name] = {
                "a_hash": a_hash,
                "b_hash": b_hash,
                "synced_at": "2026-01-01T00:00:00+00:00",
            }
        _save_cursor(path, cursor)

    def test_a_changed_pushes_to_b(self, tmp_path):
        old_hash = _content_hash("original")
        new_content = "updated by A"

        cursor_path = tmp_path / "cursor.json"
        self._write_cursor(cursor_path, [("Page", old_hash, old_hash)])

        a = _make_provider({"Page": new_content})
        b = _make_provider({"Page": "original"})

        engine = WikiSync(a, b)
        result = engine.sync(cursor_path=cursor_path)

        assert result["pushed_to_b"] == 1
        b.put_page.assert_called_once_with("Page", new_content)

    def test_b_changed_pushes_to_a(self, tmp_path):
        old_hash = _content_hash("original")
        new_content = "updated by B"

        cursor_path = tmp_path / "cursor.json"
        self._write_cursor(cursor_path, [("Page", old_hash, old_hash)])

        a = _make_provider({"Page": "original"})
        b = _make_provider({"Page": new_content})

        engine = WikiSync(a, b)
        result = engine.sync(cursor_path=cursor_path)

        assert result["pushed_to_a"] == 1
        a.put_page.assert_called_once_with("Page", new_content)

    def test_both_changed_is_conflict(self, tmp_path):
        old_hash = _content_hash("original")

        cursor_path = tmp_path / "cursor.json"
        self._write_cursor(cursor_path, [("Page", old_hash, old_hash)])

        a = _make_provider({"Page": "A's new version"})
        b = _make_provider({"Page": "B's new version"})

        engine = WikiSync(a, b)
        result = engine.sync(cursor_path=cursor_path)

        assert result["conflicts"] == ["Page"]
        a.put_page.assert_not_called()
        b.put_page.assert_not_called()

    def test_neither_changed_is_noop(self, tmp_path):
        content = "unchanged"
        ch = _content_hash(content)

        cursor_path = tmp_path / "cursor.json"
        self._write_cursor(cursor_path, [("Page", ch, ch)])

        a = _make_provider({"Page": content})
        b = _make_provider({"Page": content})

        engine = WikiSync(a, b)
        result = engine.sync(cursor_path=cursor_path)

        assert result["pushed_to_a"] == 0
        assert result["pushed_to_b"] == 0
        assert result["skipped"] == 1
        a.put_page.assert_not_called()
        b.put_page.assert_not_called()

    def test_conflict_does_not_update_cursor(self, tmp_path):
        old_hash = _content_hash("original")

        cursor_path = tmp_path / "cursor.json"
        self._write_cursor(cursor_path, [("Page", old_hash, old_hash)])

        a = _make_provider({"Page": "A changed"})
        b = _make_provider({"Page": "B changed"})

        engine = WikiSync(a, b)
        engine.sync(cursor_path=cursor_path)

        cursor = json.loads(cursor_path.read_text(encoding="utf-8"))
        # The cursor entry should still have the OLD hashes
        assert cursor["Page"]["a_hash"] == old_hash
        assert cursor["Page"]["b_hash"] == old_hash

    def test_stale_entries_preserved(self, tmp_path):
        """Pages not seen this run should stay in the cursor."""
        old_hash = _content_hash("content")

        cursor_path = tmp_path / "cursor.json"
        self._write_cursor(
            cursor_path,
            [
                ("Active", old_hash, old_hash),
                ("Stale", "deadbeef12345678", "deadbeef12345678"),
            ],
        )

        a = _make_provider({"Active": "content"})
        b = _make_provider({"Active": "content"})

        engine = WikiSync(a, b)
        engine.sync(cursor_path=cursor_path)

        cursor = json.loads(cursor_path.read_text(encoding="utf-8"))
        assert "Stale" in cursor
        assert cursor["Stale"]["a_hash"] == "deadbeef12345678"


class TestWikiSyncLifecycle:
    """Tests for open/close/commit lifecycle."""

    def test_providers_opened_and_closed(self, tmp_path):
        a = _make_provider({})
        b = _make_provider({})

        engine = WikiSync(a, b)
        engine.sync(cursor_path=tmp_path / "cursor.json")

        a.open.assert_called_once()
        b.open.assert_called_once()
        a.close.assert_called_once()
        b.close.assert_called_once()

    def test_providers_closed_on_error(self, tmp_path):
        a = _make_provider({})
        b = _make_provider({})
        a.list_pages.side_effect = RuntimeError("boom")

        engine = WikiSync(a, b)
        with pytest.raises(RuntimeError, match="boom"):
            engine.sync(cursor_path=tmp_path / "cursor.json")

        a.close.assert_called_once()
        b.close.assert_called_once()

    def test_commit_only_called_when_dirty(self, tmp_path):
        a = _make_provider({"A Only": "content"})
        b = _make_provider({})

        engine = WikiSync(a, b)
        engine.sync(cursor_path=tmp_path / "cursor.json")

        # B should be committed (received a page)
        b.commit.assert_called_once()
        # A should NOT be committed (nothing changed on A side)
        a.commit.assert_not_called()

    def test_no_commits_when_nothing_changed(self, tmp_path):
        a = _make_provider({})
        b = _make_provider({})

        engine = WikiSync(a, b)
        engine.sync(cursor_path=tmp_path / "cursor.json")

        a.commit.assert_not_called()
        b.commit.assert_not_called()

    def test_both_committed_when_both_dirty(self, tmp_path):
        a = _make_provider({"From A": "a content"})
        b = _make_provider({"From B": "b content"})

        engine = WikiSync(a, b)
        engine.sync(cursor_path=tmp_path / "cursor.json")

        a.commit.assert_called_once()
        b.commit.assert_called_once()
