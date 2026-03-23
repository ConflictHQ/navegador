"""
VCS abstraction layer for navegador.

Provides a uniform interface for version-control operations across different
backends (git, Fossil, …) so the rest of the codebase never calls git directly.

Usage::

    from navegador.vcs import detect_vcs

    adapter = detect_vcs(Path("/path/to/repo"))
    print(adapter.current_branch())
    print(adapter.changed_files())
"""

from __future__ import annotations

import subprocess
from abc import ABC, abstractmethod
from pathlib import Path


# ── Abstract base ──────────────────────────────────────────────────────────────


class VCSAdapter(ABC):
    """Abstract base class for VCS backends."""

    def __init__(self, repo_path: Path) -> None:
        self.repo_path = Path(repo_path)

    @abstractmethod
    def is_repo(self) -> bool:
        """Return True if *repo_path* is a valid repository for this backend."""

    @abstractmethod
    def current_branch(self) -> str:
        """Return the name of the currently checked-out branch."""

    @abstractmethod
    def changed_files(self, since: str = "") -> list[str]:
        """
        Return a list of file paths that have changed.

        Parameters
        ----------
        since:
            A commit reference (hash, tag, branch name).  When empty the
            implementation should fall back to uncommitted changes vs HEAD.
        """

    @abstractmethod
    def file_history(self, file_path: str, limit: int = 10) -> list[dict]:
        """
        Return the commit history for *file_path*.

        Each entry is a dict with at least the keys:
        ``hash``, ``author``, ``date``, ``message``.
        """

    @abstractmethod
    def blame(self, file_path: str) -> list[dict]:
        """
        Return per-line blame information for *file_path*.

        Each entry is a dict with at least the keys:
        ``line``, ``hash``, ``author``, ``content``.
        """


# ── Git ────────────────────────────────────────────────────────────────────────


class GitAdapter(VCSAdapter):
    """VCS adapter for Git repositories."""

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _run(self, args: list[str], check: bool = True) -> subprocess.CompletedProcess:
        """Run a git sub-command inside *repo_path* and return the result."""
        return subprocess.run(
            ["git", *args],
            cwd=self.repo_path,
            capture_output=True,
            text=True,
            check=check,
        )

    # ------------------------------------------------------------------
    # VCSAdapter interface
    # ------------------------------------------------------------------

    def is_repo(self) -> bool:
        """Return True when *repo_path* contains a ``.git`` directory or file."""
        return (self.repo_path / ".git").exists()

    def current_branch(self) -> str:
        """Return the name of the current branch (e.g. ``"main"``)."""
        result = self._run(["rev-parse", "--abbrev-ref", "HEAD"])
        return result.stdout.strip()

    def changed_files(self, since: str = "") -> list[str]:
        """
        Return file paths that differ from *since* (or from HEAD when empty).

        When *since* is given, runs ``git diff --name-only <since>``.
        When *since* is empty, runs ``git diff HEAD --name-only`` which
        includes both staged and unstaged changes relative to HEAD.
        """
        if since:
            args = ["diff", "--name-only", since]
        else:
            args = ["diff", "HEAD", "--name-only"]

        result = self._run(args)
        lines = result.stdout.strip().splitlines()
        return [line for line in lines if line]

    def file_history(self, file_path: str, limit: int = 10) -> list[dict]:
        """
        Return up to *limit* log entries for *file_path*.

        Each entry has the keys: ``hash``, ``author``, ``date``, ``message``.
        """
        fmt = "%H%x1f%an%x1f%ai%x1f%s"
        result = self._run([
            "log",
            f"--max-count={limit}",
            f"--format={fmt}",
            "--",
            file_path,
        ])

        entries: list[dict] = []
        for line in result.stdout.strip().splitlines():
            if not line:
                continue
            parts = line.split("\x1f", 3)
            if len(parts) == 4:
                entries.append({
                    "hash": parts[0],
                    "author": parts[1],
                    "date": parts[2],
                    "message": parts[3],
                })
        return entries

    def blame(self, file_path: str) -> list[dict]:
        """
        Return per-line blame data for *file_path*.

        Each entry has the keys: ``line``, ``hash``, ``author``, ``content``.

        Uses ``git blame --porcelain`` for machine-readable output.
        """
        result = self._run(["blame", "--porcelain", "--", file_path])
        return _parse_porcelain_blame(result.stdout)


def _parse_porcelain_blame(output: str) -> list[dict]:
    """Parse the output of ``git blame --porcelain`` into a list of dicts."""
    entries: list[dict] = []
    current_hash = ""
    current_author = ""
    line_number = 0

    lines = output.splitlines()
    i = 0
    while i < len(lines):
        raw = lines[i]

        # Header line: "<40-char hash> <orig-line> <final-line> [<num-lines>]"
        parts = raw.split()
        if len(parts) >= 3 and len(parts[0]) == 40 and parts[0].isalnum():
            current_hash = parts[0]
            try:
                line_number = int(parts[2])
            except (IndexError, ValueError):
                line_number = 0
            i += 1
            # Read key-value pairs until we hit the content line (starts with \t)
            while i < len(lines) and not lines[i].startswith("\t"):
                kv = lines[i]
                if kv.startswith("author "):
                    current_author = kv[len("author "):]
                i += 1
            # The content line starts with a tab
            if i < len(lines) and lines[i].startswith("\t"):
                content = lines[i][1:]  # strip leading tab
                entries.append({
                    "line": line_number,
                    "hash": current_hash,
                    "author": current_author,
                    "content": content,
                })
                i += 1
        else:
            i += 1

    return entries


# ── Fossil ─────────────────────────────────────────────────────────────────────


class FossilAdapter(VCSAdapter):
    """
    VCS adapter stub for Fossil repositories.

    ``is_repo()`` is fully implemented; all other methods raise
    ``NotImplementedError`` until a full implementation is added.
    """

    _NOT_IMPLEMENTED_MSG = (
        "FossilAdapter.{method} is not yet implemented. "
        "Contributions welcome — see CONTRIBUTING.md for the VCS adapter guide."
    )

    def is_repo(self) -> bool:
        """Return True when *repo_path* looks like a Fossil checkout."""
        return (
            (self.repo_path / ".fslckout").exists()
            or (self.repo_path / "_FOSSIL_").exists()
        )

    def current_branch(self) -> str:
        raise NotImplementedError(
            self._NOT_IMPLEMENTED_MSG.format(method="current_branch")
        )

    def changed_files(self, since: str = "") -> list[str]:
        raise NotImplementedError(
            self._NOT_IMPLEMENTED_MSG.format(method="changed_files")
        )

    def file_history(self, file_path: str, limit: int = 10) -> list[dict]:
        raise NotImplementedError(
            self._NOT_IMPLEMENTED_MSG.format(method="file_history")
        )

    def blame(self, file_path: str) -> list[dict]:
        raise NotImplementedError(
            self._NOT_IMPLEMENTED_MSG.format(method="blame")
        )


# ── Factory ────────────────────────────────────────────────────────────────────


def detect_vcs(repo_path: Path) -> VCSAdapter:
    """
    Auto-detect the VCS used in *repo_path* and return the matching adapter.

    Detection order:
    1. ``.git`` (directory or file) → :class:`GitAdapter`
    2. ``.fslckout`` or ``_FOSSIL_`` → :class:`FossilAdapter`

    Raises
    ------
    ValueError
        If no supported VCS is detected at *repo_path*.
    """
    repo_path = Path(repo_path)

    git = GitAdapter(repo_path)
    if git.is_repo():
        return git

    fossil = FossilAdapter(repo_path)
    if fossil.is_repo():
        return fossil

    raise ValueError(
        f"No supported VCS detected at {repo_path!r}. "
        "Expected a git repository (.git) or a Fossil checkout (.fslckout / _FOSSIL_)."
    )
