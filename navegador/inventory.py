"""
Discover navegador projects on a machine and report where their graphs live.

Two questions this answers that nothing else could:

  1. Which projects declare a backend they are not actually using? A project
     whose config says ``backend = "redis"`` while a populated ``graph.db`` sits
     on disk was ingested somewhere no reader looks — a success message and an
     invisible result (#169).
  2. Is this machine big enough to want a shared server? Many projects, or a few
     large graphs, is the point at which per-project embedded graphs stop paying
     for themselves and a central in-memory server starts to.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

from navegador.config import (
    NAV_DIRNAME,
    StorageConfig,
    resolve_storage,
    storage_from_file,
)

#: A graph past this size is worth holding in a shared server rather than
#: re-reading from disk in every checkout.
LARGE_GRAPH_BYTES = 5 * 1024 * 1024

#: Having at least this many projects makes one resident server cheaper than
#: N embedded ones, each paying its own startup and page-cache cost.
MANY_PROJECTS = 5

#: Directories never worth descending into when looking for projects.
_SKIP_DIRS = frozenset(
    {
        ".git",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "dist",
        "build",
        "target",
        "vendor",
        "site-packages",
    }
)


@dataclass
class ProjectRecord:
    """One discovered project and the state of its graph."""

    root: Path
    config_path: Path | None = None
    declared: StorageConfig | None = None
    effective: StorageConfig | None = None
    db_path: Path | None = None
    db_bytes: int = 0
    db_mtime: float = 0.0
    graphs: list[str] = field(default_factory=list)

    @property
    def declared_backend(self) -> str:
        return self.declared.backend if self.declared else "unset"

    @property
    def has_local_data(self) -> bool:
        """True when a local graph file exists with more than an empty snapshot."""
        # An empty falkordblite RDB is a few hundred bytes; real graphs are KBs up.
        return self.db_bytes > 4096

    @property
    def is_stranded(self) -> bool:
        """
        Declares a shared backend, yet holds a populated local graph.

        This is the #169 signature: the ingest succeeded, the data is real, and
        every reader looking at the shared server disagrees.
        """
        return self.declared_backend == "redis" and self.has_local_data

    def to_dict(self) -> dict:
        return {
            "root": str(self.root),
            "config": str(self.config_path) if self.config_path else None,
            "declared_backend": self.declared_backend,
            "redis_url": self.declared.redis_url if self.declared else "",
            "effective": self.effective.describe() if self.effective else "",
            "db_path": str(self.db_path) if self.db_path else None,
            "db_bytes": self.db_bytes,
            "stranded": self.is_stranded,
        }


def find_projects(root: str | Path, max_depth: int = 6) -> list[Path]:
    """
    Find every directory under *root* containing a ``.navegador/`` directory.

    Descends at most *max_depth* levels and skips build/dependency directories,
    so scanning a whole repo tree stays fast.
    """
    root = Path(root).expanduser().resolve()
    found: list[Path] = []
    if not root.is_dir():
        return found

    root_depth = len(root.parts)
    for dirpath, dirnames, _ in os.walk(root):
        current = Path(dirpath)
        depth = len(current.parts) - root_depth

        if depth >= max_depth:
            dirnames[:] = []
            continue

        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".venv")]

        if NAV_DIRNAME in dirnames:
            found.append(current)
            # A project's own .navegador is never worth descending into, but
            # nested repos below it still are.
            dirnames.remove(NAV_DIRNAME)

    return sorted(found)


def inspect_project(root: str | Path) -> ProjectRecord:
    """Build a :class:`ProjectRecord` for a single project directory."""
    root = Path(root).expanduser().resolve()
    record = ProjectRecord(root=root)

    config_path = root / NAV_DIRNAME / "config.toml"
    if config_path.is_file():
        record.config_path = config_path
        record.declared = storage_from_file(config_path, "project config")

    record.effective = resolve_storage(target=root)

    db_path = root / NAV_DIRNAME / "graph.db"
    if db_path.is_file():
        stat = db_path.stat()
        record.db_path = db_path
        record.db_bytes = stat.st_size
        record.db_mtime = stat.st_mtime

    return record


def scan(root: str | Path, max_depth: int = 6) -> list[ProjectRecord]:
    """Inventory every navegador project under *root*."""
    return [inspect_project(p) for p in find_projects(root, max_depth=max_depth)]


def recommend_central_server(records: list[ProjectRecord]) -> tuple[bool, list[str]]:
    """
    Decide whether this machine would benefit from a shared graph server.

    Returns ``(recommended, reasons)``. Reasons are phrased for direct display.
    """
    reasons: list[str] = []

    stranded = [r for r in records if r.is_stranded]
    if stranded:
        reasons.append(
            f"{len(stranded)} project(s) declare a Redis backend but hold a populated "
            f"local graph — those ingests are invisible to anything reading the server"
        )

    large = [r for r in records if r.db_bytes >= LARGE_GRAPH_BYTES]
    if large:
        biggest = max(large, key=lambda r: r.db_bytes)
        reasons.append(
            f"{len(large)} graph(s) exceed {LARGE_GRAPH_BYTES // (1024 * 1024)} MB "
            f"(largest: {biggest.root.name} at {biggest.db_bytes / 1024 / 1024:.0f} MB) — "
            f"large graphs are re-read from disk by every agent that queries them"
        )

    if len(records) >= MANY_PROJECTS:
        reasons.append(
            f"{len(records)} projects on this machine — one resident server answers "
            f"across all of them instead of each paying its own startup and page-cache cost"
        )

    return bool(reasons), reasons
