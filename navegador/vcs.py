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

import logging
import subprocess
from abc import ABC, abstractmethod
from pathlib import Path

logger = logging.getLogger(__name__)

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
        result = self._run(
            [
                "log",
                f"--max-count={limit}",
                f"--format={fmt}",
                "--",
                file_path,
            ]
        )

        entries: list[dict] = []
        for line in result.stdout.strip().splitlines():
            if not line:
                continue
            parts = line.split("\x1f", 3)
            if len(parts) == 4:
                entries.append(
                    {
                        "hash": parts[0],
                        "author": parts[1],
                        "date": parts[2],
                        "message": parts[3],
                    }
                )
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
                    current_author = kv[len("author ") :]
                i += 1
            # The content line starts with a tab
            if i < len(lines) and lines[i].startswith("\t"):
                content = lines[i][1:]  # strip leading tab
                entries.append(
                    {
                        "line": line_number,
                        "hash": current_hash,
                        "author": current_author,
                        "content": content,
                    }
                )
                i += 1
        else:
            i += 1

    return entries


# ── Fossil ─────────────────────────────────────────────────────────────────────


class FossilAdapter(VCSAdapter):
    """
    VCS adapter for Fossil repositories.

    ``is_repo()`` checks for ``.fslckout`` / ``_FOSSIL_`` sentinel files.
    All other methods run ``fossil`` sub-commands via subprocess.
    """

    def _run(
        self,
        args: list[str],
        check: bool = True,
        input: str | None = None,
    ) -> subprocess.CompletedProcess:
        """Run a fossil sub-command inside *repo_path* and return the result."""
        return subprocess.run(
            ["fossil", *args],
            cwd=self.repo_path,
            capture_output=True,
            text=True,
            check=check,
            input=input,
        )

    def is_repo(self) -> bool:
        """Return True when *repo_path* looks like a Fossil checkout."""
        return (self.repo_path / ".fslckout").exists() or (self.repo_path / "_FOSSIL_").exists()

    def current_branch(self) -> str:
        """
        Return the name of the current Fossil branch.

        Runs ``fossil branch current`` and returns its output stripped of
        whitespace.
        """
        result = self._run(["branch", "current"])
        return result.stdout.strip()

    def changed_files(self, since: str = "") -> list[str]:
        """
        Return a list of file paths that have changed.

        Runs ``fossil changes --differ`` which reports files that differ
        from the current check-in.  The *since* parameter is not used by
        Fossil's change model and is accepted for interface compatibility.
        """
        result = self._run(["changes", "--differ"])
        files: list[str] = []
        for line in result.stdout.splitlines():
            # fossil changes output: "<STATUS>  <path>"
            parts = line.split(None, 1)
            if len(parts) == 2:
                files.append(parts[1].strip())
            elif parts:
                files.append(parts[0].strip())
        return [f for f in files if f]

    def file_history(self, file_path: str, limit: int = 10) -> list[dict]:
        """
        Return up to *limit* timeline entries for *file_path*.

        Runs ``fossil timeline --limit <n> --type ci --path <file>`` and
        parses the output into a list of dicts with keys:
        ``hash``, ``author``, ``date``, ``message``.
        """
        result = self._run(
            [
                "timeline",
                "--limit",
                str(limit),
                "--type",
                "ci",
                "--path",
                file_path,
            ]
        )
        return _parse_fossil_timeline(result.stdout)

    def blame(self, file_path: str) -> list[dict]:
        """
        Return per-line blame data for *file_path*.

        Runs ``fossil annotate --log <file>`` and returns a list of dicts with
        keys: ``line``, ``hash``, ``author``, ``content``.
        """
        result = self._run(["annotate", "--log", file_path])
        return _parse_fossil_annotate(result.stdout)

    def wiki_pages(self) -> list[str]:
        """Return list of all wiki page names in this Fossil repo."""
        result = self._run(["wiki", "list"], check=False)
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]

    def wiki_export(self, page_name: str) -> str:
        """Return the raw content of a wiki page."""
        result = self._run(["wiki", "export", page_name], check=False)
        return result.stdout

    def wiki_commit(self, page_name: str, content: str, mimetype: str = "text/x-markdown") -> None:
        """
        Write or update a Fossil wiki page.

        Runs ``fossil wiki commit --mimetype MIME PAGE_NAME`` with *content*
        supplied on stdin.  Uses ``text/x-markdown`` by default so Fossil
        renders content as Markdown rather than WikiCreole.

        Falls back to committing without ``--mimetype`` if the installed
        Fossil version does not support that flag (pre-2.12).
        """
        result = self._run(
            ["wiki", "commit", "--mimetype", mimetype, page_name],
            check=False,
            input=content,
        )
        if result.returncode != 0 and "--mimetype" in (result.stderr or ""):
            # Older Fossil: retry without --mimetype
            self._run(["wiki", "commit", page_name], check=True, input=content)
        elif result.returncode != 0:
            logger.warning("fossil wiki commit failed for %r: %s", page_name, result.stderr.strip())

    def ticket_list(self, limit: int = 200) -> list[dict]:
        """
        Return tickets from this Fossil repo as a list of dicts.

        Runs ``fossil ticket show 0`` which outputs all tickets from
        report #0 (All Tickets) as tab-separated rows.  The first row
        is the column header.

        Falls back to an empty list if the command fails (e.g. no tickets
        defined or fossil not installed).
        """
        result = self._run(["ticket", "show", "0", "--limit", str(limit)], check=False)
        if result.returncode != 0 or not result.stdout.strip():
            # Try without --limit (older fossil versions)
            result = self._run(["ticket", "show", "0"], check=False)
        if result.returncode != 0 or not result.stdout.strip():
            return []
        return _parse_fossil_tickets(result.stdout, limit=limit)


def _parse_fossil_timeline(output: str) -> list[dict]:
    """
    Parse ``fossil timeline`` output into a list of entry dicts.

    Fossil timeline lines look like::

        === 2024-01-15 ===
        14:23:07 [abc123def456] Commit message here. (user: alice, tags: trunk)

    We emit one dict per timeline entry.
    """
    entries: list[dict] = []
    current_date = ""

    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue

        # Date header: "=== 2024-01-15 ==="
        if line.startswith("===") and line.endswith("==="):
            current_date = line.strip("= ").strip()
            continue

        # Entry line: "HH:MM:SS [hashprefix] message (user: ..., tags: ...)"
        import re

        m = re.match(
            r"(\d{2}:\d{2}:\d{2})\s+\[([0-9a-f]+)\]\s+(.*?)(?:\s+\(user:\s*(\w+).*\))?$",
            line,
        )
        if m:
            time_part, hash_part, message, author = m.groups()
            entries.append(
                {
                    "hash": hash_part,
                    "author": author or "",
                    "date": f"{current_date} {time_part}".strip(),
                    "message": message.rstrip(),
                }
            )

    return entries


def _parse_fossil_annotate(output: str) -> list[dict]:
    """
    Parse ``fossil annotate --log`` output into a list of blame dicts.

    Each line looks like::

        1.1          alice 2024-01-15:  actual line content

    We return one dict per source line with keys:
    ``line``, ``hash``, ``author``, ``content``.
    """
    import re

    entries: list[dict] = []
    line_number = 0

    for raw in output.splitlines():
        # Pattern: "<version>  <author> <date>:  <content>"
        m = re.match(r"(\S+)\s+(\S+)\s+\S+:\s+(.*)", raw)
        if m:
            version, author, content = m.groups()
            line_number += 1
            entries.append(
                {
                    "line": line_number,
                    "hash": version,
                    "author": author,
                    "content": content,
                }
            )

    return entries


def _parse_fossil_tickets(output: str, limit: int = 200) -> list[dict]:
    """
    Parse ``fossil ticket show 0`` tab-separated output into a list of dicts.

    The first non-empty line is treated as the column header row.
    Subsequent lines are data rows.  Each dict maps column name → value.
    Unknown or empty columns are kept as-is.
    """
    lines = [line for line in output.splitlines() if line.strip()]
    if len(lines) < 2:
        return []

    headers = [h.strip().lower() for h in lines[0].split("\t")]
    tickets: list[dict] = []

    for line in lines[1 : limit + 1]:
        parts = line.split("\t")
        row: dict = {}
        for i, header in enumerate(headers):
            row[header] = parts[i].strip() if i < len(parts) else ""
        tickets.append(row)

    return tickets


# ── Factory ────────────────────────────────────────────────────────────────────


def detect_vcs(repo_path: Path) -> VCSAdapter:
    """
    Auto-detect the VCS used in *repo_path* and return the matching adapter.

    Detection order:
    1. ``.git`` (directory or file) → :class:`GitAdapter`
    2. ``.fslckout`` or ``_FOSSIL_`` → :class:`FossilAdapter`

    When a directory contains *both* a ``.git`` and a Fossil checkout marker
    (a Fossil-mirrored Git repo), this returns the :class:`GitAdapter` —
    use :func:`detect_fossil` to also get the :class:`FossilAdapter` in that
    case and ingest wiki pages + tickets from the Fossil side.

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


def detect_fossil(repo_path: Path) -> "FossilAdapter | None":
    """
    Return a :class:`FossilAdapter` if *repo_path* contains a Fossil checkout,
    or ``None`` otherwise.

    Intentionally independent of :func:`detect_vcs` so that callers can check
    for a Fossil mirror even when the primary VCS is Git.  The typical pattern
    is::

        primary = detect_vcs(path)          # GitAdapter (or FossilAdapter)
        fossil  = detect_fossil(path)       # FossilAdapter | None
        if fossil and fossil is not primary:
            # ingest wiki + tickets from the Fossil mirror
            FossilIngester(store, fossil).ingest_wiki()
            FossilIngester(store, fossil).ingest_tickets()
    """
    adapter = FossilAdapter(Path(repo_path))
    return adapter if adapter.is_repo() else None
