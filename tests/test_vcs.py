"""Tests for navegador.vcs — VCSAdapter, GitAdapter, FossilAdapter, detect_vcs."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from navegador.vcs import (
    FossilAdapter,
    GitAdapter,
    VCSAdapter,
    detect_vcs,
)


# ── Fixtures ───────────────────────────────────────────────────────────────────


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    """Run a git command in *cwd*, raising on failure."""
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )


@pytest.fixture()
def git_repo(tmp_path: Path) -> Path:
    """
    Create a minimal, fully-initialised git repo with one commit.

    Returns the repo root path.
    """
    repo = tmp_path / "repo"
    repo.mkdir()

    _git(["init", "-b", "main"], cwd=repo)
    _git(["config", "user.email", "test@example.com"], cwd=repo)
    _git(["config", "user.name", "Test User"], cwd=repo)

    # Initial commit so HEAD exists
    readme = repo / "README.md"
    readme.write_text("# test repo\n")
    _git(["add", "README.md"], cwd=repo)
    _git(["commit", "-m", "initial commit"], cwd=repo)

    return repo


@pytest.fixture()
def empty_dir(tmp_path: Path) -> Path:
    """Return a temporary directory that is NOT a git repo."""
    d = tmp_path / "notarepo"
    d.mkdir()
    return d


@pytest.fixture()
def fossil_dir(tmp_path: Path) -> Path:
    """Return a directory that looks like a Fossil checkout (.fslckout present)."""
    d = tmp_path / "fossil_repo"
    d.mkdir()
    (d / ".fslckout").touch()
    return d


# ── VCSAdapter is abstract ─────────────────────────────────────────────────────


class TestVCSAdapterAbstract:
    def test_cannot_instantiate_directly(self):
        with pytest.raises(TypeError):
            VCSAdapter(Path("/tmp"))  # type: ignore[abstract]

    def test_git_adapter_is_subclass(self):
        assert issubclass(GitAdapter, VCSAdapter)

    def test_fossil_adapter_is_subclass(self):
        assert issubclass(FossilAdapter, VCSAdapter)


# ── GitAdapter.is_repo ─────────────────────────────────────────────────────────


class TestGitAdapterIsRepo:
    def test_true_for_git_repo(self, git_repo: Path):
        assert GitAdapter(git_repo).is_repo() is True

    def test_false_for_empty_dir(self, empty_dir: Path):
        assert GitAdapter(empty_dir).is_repo() is False

    def test_false_for_fossil_dir(self, fossil_dir: Path):
        assert GitAdapter(fossil_dir).is_repo() is False


# ── GitAdapter.current_branch ──────────────────────────────────────────────────


class TestGitAdapterCurrentBranch:
    def test_returns_main(self, git_repo: Path):
        branch = GitAdapter(git_repo).current_branch()
        # Accept "main" or "master" depending on git config defaults
        assert branch in ("main", "master")

    def test_returns_feature_branch(self, git_repo: Path):
        _git(["checkout", "-b", "feature/test-branch"], cwd=git_repo)
        branch = GitAdapter(git_repo).current_branch()
        assert branch == "feature/test-branch"

    def test_returns_string(self, git_repo: Path):
        result = GitAdapter(git_repo).current_branch()
        assert isinstance(result, str)
        assert len(result) > 0


# ── GitAdapter.changed_files ───────────────────────────────────────────────────


class TestGitAdapterChangedFiles:
    def test_no_changes_returns_empty(self, git_repo: Path):
        # Nothing changed after the initial commit
        files = GitAdapter(git_repo).changed_files()
        assert files == []

    def test_detects_modified_file(self, git_repo: Path):
        # Modify a tracked file without staging it
        readme = git_repo / "README.md"
        readme.write_text("# modified\n")

        files = GitAdapter(git_repo).changed_files()
        assert "README.md" in files

    def test_detects_staged_file(self, git_repo: Path):
        new_file = git_repo / "new.py"
        new_file.write_text("x = 1\n")
        _git(["add", "new.py"], cwd=git_repo)

        files = GitAdapter(git_repo).changed_files()
        assert "new.py" in files

    def test_since_commit_returns_changed_files(self, git_repo: Path):
        # Get the first commit hash
        result = _git(["rev-parse", "HEAD"], cwd=git_repo)
        first_hash = result.stdout.strip()

        # Add a second commit
        extra = git_repo / "extra.txt"
        extra.write_text("hello\n")
        _git(["add", "extra.txt"], cwd=git_repo)
        _git(["commit", "-m", "add extra.txt"], cwd=git_repo)

        files = GitAdapter(git_repo).changed_files(since=first_hash)
        assert "extra.txt" in files

    def test_returns_list(self, git_repo: Path):
        result = GitAdapter(git_repo).changed_files()
        assert isinstance(result, list)


# ── GitAdapter.file_history ────────────────────────────────────────────────────


class TestGitAdapterFileHistory:
    def test_returns_list(self, git_repo: Path):
        history = GitAdapter(git_repo).file_history("README.md")
        assert isinstance(history, list)

    def test_initial_commit_present(self, git_repo: Path):
        history = GitAdapter(git_repo).file_history("README.md")
        assert len(history) >= 1

    def test_entry_has_required_keys(self, git_repo: Path):
        history = GitAdapter(git_repo).file_history("README.md")
        entry = history[0]
        assert "hash" in entry
        assert "author" in entry
        assert "date" in entry
        assert "message" in entry

    def test_entry_message_matches(self, git_repo: Path):
        history = GitAdapter(git_repo).file_history("README.md")
        assert history[0]["message"] == "initial commit"

    def test_limit_is_respected(self, git_repo: Path):
        # Add several more commits to README.md
        readme = git_repo / "README.md"
        for i in range(5):
            readme.write_text(f"# revision {i}\n")
            _git(["add", "README.md"], cwd=git_repo)
            _git(["commit", "-m", f"revision {i}"], cwd=git_repo)

        history = GitAdapter(git_repo).file_history("README.md", limit=3)
        assert len(history) <= 3

    def test_nonexistent_file_returns_empty(self, git_repo: Path):
        history = GitAdapter(git_repo).file_history("does_not_exist.py")
        assert history == []


# ── GitAdapter.blame ──────────────────────────────────────────────────────────


class TestGitAdapterBlame:
    def test_returns_list(self, git_repo: Path):
        result = GitAdapter(git_repo).blame("README.md")
        assert isinstance(result, list)

    def test_entry_has_required_keys(self, git_repo: Path):
        result = GitAdapter(git_repo).blame("README.md")
        assert len(result) >= 1
        entry = result[0]
        assert "line" in entry
        assert "hash" in entry
        assert "author" in entry
        assert "content" in entry

    def test_content_matches_file(self, git_repo: Path):
        result = GitAdapter(git_repo).blame("README.md")
        # README.md contains "# test repo"
        contents = [e["content"] for e in result]
        assert any("test repo" in c for c in contents)

    def test_line_numbers_are_integers(self, git_repo: Path):
        result = GitAdapter(git_repo).blame("README.md")
        for entry in result:
            assert isinstance(entry["line"], int)


# ── FossilAdapter.is_repo ─────────────────────────────────────────────────────


class TestFossilAdapterIsRepo:
    def test_true_when_fslckout_present(self, fossil_dir: Path):
        assert FossilAdapter(fossil_dir).is_repo() is True

    def test_true_when_FOSSIL_present(self, tmp_path: Path):
        d = tmp_path / "fossil2"
        d.mkdir()
        (d / "_FOSSIL_").touch()
        assert FossilAdapter(d).is_repo() is True

    def test_false_for_empty_dir(self, empty_dir: Path):
        assert FossilAdapter(empty_dir).is_repo() is False

    def test_false_for_git_repo(self, git_repo: Path):
        assert FossilAdapter(git_repo).is_repo() is False


# ── FossilAdapter implemented methods (#55) ────────────────────────────────────
#
# These methods are now fully implemented; they call `fossil` via subprocess.
# Since fossil may not be installed in CI, we mock subprocess.run.


class TestFossilAdapterImplemented:
    """FossilAdapter methods are implemented — they call fossil via subprocess."""

    @pytest.fixture()
    def adapter(self, fossil_dir: Path) -> FossilAdapter:
        return FossilAdapter(fossil_dir)

    def test_current_branch_returns_string(self, adapter: FossilAdapter):
        from unittest.mock import MagicMock, patch

        mock_result = MagicMock()
        mock_result.stdout = "trunk\n"
        with patch("subprocess.run", return_value=mock_result):
            branch = adapter.current_branch()
        assert branch == "trunk"

    def test_changed_files_returns_list(self, adapter: FossilAdapter):
        from unittest.mock import MagicMock, patch

        mock_result = MagicMock()
        mock_result.stdout = "EDITED  src/main.py\n"
        with patch("subprocess.run", return_value=mock_result):
            files = adapter.changed_files()
        assert isinstance(files, list)
        assert "src/main.py" in files

    def test_file_history_returns_list(self, adapter: FossilAdapter):
        from unittest.mock import MagicMock, patch

        mock_result = MagicMock()
        mock_result.stdout = (
            "=== 2024-01-15 ===\n"
            "14:23:07 [abc123] Fix bug. (user: alice, tags: trunk)\n"
        )
        with patch("subprocess.run", return_value=mock_result):
            history = adapter.file_history("README.md")
        assert isinstance(history, list)

    def test_blame_returns_list(self, adapter: FossilAdapter):
        from unittest.mock import MagicMock, patch

        mock_result = MagicMock()
        mock_result.stdout = "1.1          alice 2024-01-15:  # line content\n"
        with patch("subprocess.run", return_value=mock_result):
            result = adapter.blame("README.md")
        assert isinstance(result, list)


# ── detect_vcs factory ─────────────────────────────────────────────────────────


class TestDetectVCS:
    def test_detects_git(self, git_repo: Path):
        adapter = detect_vcs(git_repo)
        assert isinstance(adapter, GitAdapter)

    def test_detects_fossil(self, fossil_dir: Path):
        adapter = detect_vcs(fossil_dir)
        assert isinstance(adapter, FossilAdapter)

    def test_raises_for_unknown(self, empty_dir: Path):
        with pytest.raises(ValueError, match="No supported VCS"):
            detect_vcs(empty_dir)

    def test_returned_adapter_has_correct_repo_path(self, git_repo: Path):
        adapter = detect_vcs(git_repo)
        assert adapter.repo_path == git_repo

    def test_detects_fossil_via_FOSSIL_file(self, tmp_path: Path):
        d = tmp_path / "fossil_alt"
        d.mkdir()
        (d / "_FOSSIL_").touch()
        adapter = detect_vcs(d)
        assert isinstance(adapter, FossilAdapter)
