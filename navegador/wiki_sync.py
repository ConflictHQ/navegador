"""
Wiki sync abstraction — provider-based bidirectional wiki synchronisation.

Defines a ``WikiProvider`` ABC and three concrete implementations:

- ``FossilWikiProvider`` — reads/writes Fossil wiki pages via FossilAdapter
- ``GitHubWikiProvider`` — clones a GitHub wiki repo, reads/writes .md files
- ``LocalMarkdownProvider`` — reads/writes .md files in a local directory

``WikiSync`` orchestrates bidirectional sync between any two providers using
cursor-based three-way conflict detection.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
import tempfile
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from navegador.vcs import FossilAdapter

logger = logging.getLogger(__name__)

__all__ = [
    "WikiProvider",
    "FossilWikiProvider",
    "GitHubWikiProvider",
    "LocalMarkdownProvider",
    "WikiSync",
]


# ── Helpers ──────────────────────────────────────────────────────────────────


def _content_hash(content: str) -> str:
    """Return the first 16 hex chars of the SHA-256 digest of *content*."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


def _load_cursor(path: str | Path) -> dict[str, Any]:
    """Load a sync cursor from a JSON file. Returns ``{}`` if missing."""
    p = Path(path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_cursor(path: str | Path, cursor: dict[str, Any]) -> None:
    """Persist a sync cursor to a JSON file, creating parent dirs as needed."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cursor, indent=2) + "\n", encoding="utf-8")


# ── ABC ──────────────────────────────────────────────────────────────────────


class WikiProvider(ABC):
    """Abstract base for wiki content providers."""

    def open(self) -> None:
        """Called once before any read/write operations. Default: no-op."""

    def close(self) -> None:
        """Called once after all operations are complete. Default: no-op."""

    def commit(self, message: str) -> None:
        """Push/persist any batched changes. Default: no-op."""

    @abstractmethod
    def list_pages(self) -> list[str]:
        """Return page names (with spaces, not filenames)."""

    @abstractmethod
    def get_page(self, name: str) -> str:
        """Return the content of *name*, or ``""`` if the page does not exist."""

    @abstractmethod
    def put_page(self, name: str, content: str) -> None:
        """Create or overwrite *name* with *content*."""


# ── Fossil provider ─────────────────────────────────────────────────────────


class FossilWikiProvider(WikiProvider):
    """WikiProvider backed by a Fossil repository via ``FossilAdapter``."""

    def __init__(self, adapter: "FossilAdapter") -> None:
        self._adapter = adapter

    def list_pages(self) -> list[str]:
        return self._adapter.wiki_pages()

    def get_page(self, name: str) -> str:
        return self._adapter.wiki_export(name)

    def put_page(self, name: str, content: str) -> None:
        self._adapter.wiki_commit(name, content)

    # commit() is a no-op — fossil wiki commit is immediate


# ── GitHub wiki provider ────────────────────────────────────────────────────


class GitHubWikiProvider(WikiProvider):
    """
    WikiProvider that clones a GitHub wiki repo into a temporary directory.

    Pages are stored as ``Page-Name.md`` files. Spaces in page names map to
    hyphens in filenames.
    """

    def __init__(self, gh_repo: str, token: str = "") -> None:
        self._gh_repo = gh_repo
        self._token = token
        self._wiki_dir: Path | None = None
        self._tmpdir: str | None = None

    def open(self) -> None:
        """Clone the wiki repo into a temp directory."""
        self._tmpdir = tempfile.mkdtemp(prefix="navegador-wiki-sync-")
        wiki_dir = Path(self._tmpdir) / "wiki"

        url = f"https://github.com/{self._gh_repo}.wiki.git"
        cmd = ["git", "clone", "--depth=1", url, str(wiki_dir)]
        env: dict | None = None
        if self._token:
            env = {
                **os.environ,
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "http.extraHeader",
                "GIT_CONFIG_VALUE_0": f"Authorization: token {self._token}",
            }
        result = subprocess.run(cmd, capture_output=True, text=True, check=False, env=env)
        if result.returncode != 0:
            raise subprocess.CalledProcessError(
                result.returncode, "git clone", output=result.stdout, stderr=result.stderr
            )
        self._wiki_dir = wiki_dir

    def close(self) -> None:
        """Remove the temporary clone directory."""
        if self._tmpdir:
            shutil.rmtree(self._tmpdir, ignore_errors=True)
            self._tmpdir = None
            self._wiki_dir = None

    def list_pages(self) -> list[str]:
        if self._wiki_dir is None:
            return []
        return sorted(p.stem.replace("-", " ") for p in self._wiki_dir.glob("*.md"))

    def get_page(self, name: str) -> str:
        if self._wiki_dir is None:
            return ""
        filepath = self._wiki_dir / f"{name.replace(' ', '-')}.md"
        if not filepath.exists():
            return ""
        return filepath.read_text(encoding="utf-8")

    def put_page(self, name: str, content: str) -> None:
        if self._wiki_dir is None:
            return
        filepath = self._wiki_dir / f"{name.replace(' ', '-')}.md"
        filepath.write_text(content, encoding="utf-8")

    def commit(self, message: str) -> None:
        """Stage, commit, and push any changes in the wiki clone."""
        if self._wiki_dir is None:
            return
        cwd = str(self._wiki_dir)
        subprocess.run(
            ["git", "config", "user.name", "navegador"],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "navegador@localhost"],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
        )
        subprocess.run(
            ["git", "add", "-A"],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
        )
        # Check if there are staged changes
        diff = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
        )
        if diff.returncode == 0:
            # Nothing staged — skip commit
            return
        subprocess.run(
            ["git", "commit", "-m", message],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
        )
        subprocess.run(
            ["git", "push"],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
        )


# ── Local markdown provider ─────────────────────────────────────────────────


class LocalMarkdownProvider(WikiProvider):
    """WikiProvider backed by a directory of ``.md`` files on disk."""

    def __init__(self, directory: str | Path) -> None:
        self._dir = Path(directory)

    def list_pages(self) -> list[str]:
        if not self._dir.exists():
            return []
        return sorted(p.stem.replace("-", " ") for p in self._dir.glob("*.md"))

    def get_page(self, name: str) -> str:
        filepath = self._dir / f"{name.replace(' ', '-')}.md"
        if not filepath.exists():
            return ""
        return filepath.read_text(encoding="utf-8")

    def put_page(self, name: str, content: str) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        filepath = self._dir / f"{name.replace(' ', '-')}.md"
        filepath.write_text(content, encoding="utf-8")

    # commit() is a no-op — files are written directly


# ── WikiSync engine ─────────────────────────────────────────────────────────


class WikiSync:
    """
    Bidirectional wiki sync between two WikiProvider instances.

    Uses cursor-based three-way conflict detection:

    - On first sync, pages present on only one side are copied across.
      Pages present on both sides with different content are flagged as
      conflicts.
    - On subsequent syncs, the cursor records the last-known hash for each
      side. If only one side changed since the cursor, the change is
      propagated. If both sides changed, it is a conflict.

    The cursor format uses generic keys ``a_hash`` / ``b_hash`` (not
    provider-specific names). Cursor files from the old FossilWikiSync
    format (``fossil_hash`` / ``github_hash``) are NOT compatible.
    """

    def __init__(
        self,
        provider_a: WikiProvider,
        provider_b: WikiProvider,
    ) -> None:
        self.provider_a = provider_a
        self.provider_b = provider_b

    def sync(
        self,
        cursor_path: str | Path,
    ) -> dict[str, Any]:
        """
        Run a bidirectional sync and return a result dict.

        Returns:
            A dict with keys ``pushed_to_b``, ``pushed_to_a``, ``skipped``,
            and ``conflicts`` (list of page names).
        """
        cursor_path = Path(cursor_path)
        cursor = _load_cursor(cursor_path)

        pushed_to_b = 0
        pushed_to_a = 0
        skipped = 0
        conflicts: list[str] = []

        a_dirty = False
        b_dirty = False

        try:
            self.provider_a.open()
            self.provider_b.open()

            a_pages = set(self.provider_a.list_pages())
            b_pages = set(self.provider_b.list_pages())
            all_pages = sorted(a_pages | b_pages)

            for page in all_pages:
                a_content = self.provider_a.get_page(page)
                b_content = self.provider_b.get_page(page)

                a_hash = _content_hash(a_content) if a_content else ""
                b_hash = _content_hash(b_content) if b_content else ""

                # Skip pages that are blank on both sides
                if not a_content and not b_content:
                    skipped += 1
                    continue

                entry = cursor.get(page)

                if entry is None:
                    # First sync for this page
                    if a_content and not b_content:
                        # Only A has content — push to B
                        self.provider_b.put_page(page, a_content)
                        b_dirty = True
                        cursor[page] = {
                            "a_hash": a_hash,
                            "b_hash": a_hash,
                            "synced_at": _now_iso(),
                        }
                        pushed_to_b += 1
                    elif b_content and not a_content:
                        # Only B has content — push to A
                        self.provider_a.put_page(page, b_content)
                        a_dirty = True
                        cursor[page] = {
                            "a_hash": b_hash,
                            "b_hash": b_hash,
                            "synced_at": _now_iso(),
                        }
                        pushed_to_a += 1
                    elif a_hash == b_hash:
                        # Same content — just record the cursor
                        cursor[page] = {
                            "a_hash": a_hash,
                            "b_hash": b_hash,
                            "synced_at": _now_iso(),
                        }
                        skipped += 1
                    else:
                        # Different content, no cursor — conflict
                        conflicts.append(page)
                        skipped += 1
                else:
                    # Subsequent sync — compare against cursor
                    prev_a = entry.get("a_hash", "")
                    prev_b = entry.get("b_hash", "")

                    a_changed = a_hash != prev_a
                    b_changed = b_hash != prev_b

                    if a_changed and not b_changed:
                        # A changed, B did not — push A to B
                        self.provider_b.put_page(page, a_content)
                        b_dirty = True
                        cursor[page] = {
                            "a_hash": a_hash,
                            "b_hash": a_hash,
                            "synced_at": _now_iso(),
                        }
                        pushed_to_b += 1
                    elif b_changed and not a_changed:
                        # B changed, A did not — push B to A
                        self.provider_a.put_page(page, b_content)
                        a_dirty = True
                        cursor[page] = {
                            "a_hash": b_hash,
                            "b_hash": b_hash,
                            "synced_at": _now_iso(),
                        }
                        pushed_to_a += 1
                    elif a_changed and b_changed:
                        # Both changed — conflict, do NOT update cursor
                        conflicts.append(page)
                        skipped += 1
                    else:
                        # Neither changed — just update timestamp
                        cursor[page] = {
                            "a_hash": a_hash,
                            "b_hash": b_hash,
                            "synced_at": _now_iso(),
                        }
                        skipped += 1

            # Commit dirty providers
            if b_dirty:
                self.provider_b.commit("Wiki sync from provider A")
            if a_dirty:
                self.provider_a.commit("Wiki sync from provider B")

        finally:
            self.provider_a.close()
            self.provider_b.close()

        _save_cursor(cursor_path, cursor)

        return {
            "pushed_to_b": pushed_to_b,
            "pushed_to_a": pushed_to_a,
            "skipped": skipped,
            "conflicts": conflicts,
        }


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()
