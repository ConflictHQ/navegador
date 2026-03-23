"""
VCS submodule traversal — ingest a parent repo and all its git submodules as
linked Repository nodes.

Issue: #61

Usage::

    from navegador.submodules import SubmoduleIngester

    ing = SubmoduleIngester(store)
    submodules = ing.detect_submodules("/path/to/repo")
    stats = ing.ingest_with_submodules("/path/to/repo", clear=False)
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from navegador.graph.schema import EdgeType, NodeLabel
from navegador.graph.store import GraphStore

logger = logging.getLogger(__name__)


class SubmoduleIngester:
    """
    Detects and ingests git submodules as linked Repository nodes.

    After ingesting the parent repository (using RepoIngester) each submodule
    is also ingested and a DEPENDS_ON edge is created from the parent
    Repository node to each submodule Repository node.
    """

    def __init__(self, store: GraphStore) -> None:
        self.store = store

    # ── Submodule detection ───────────────────────────────────────────────────

    def detect_submodules(self, repo_path: str | Path) -> list[dict[str, Any]]:
        """
        Parse ``.gitmodules`` and return a list of submodule descriptors.

        Parameters
        ----------
        repo_path:
            Root of the parent repository.

        Returns
        -------
        list of dicts with keys:
          ``name``  — submodule logical name
          ``path``  — relative path within the parent repo
          ``url``   — remote URL
          ``abs_path`` — absolute filesystem path (``repo_path / path``)
        """
        repo_root = Path(repo_path).resolve()
        gitmodules = repo_root / ".gitmodules"

        if not gitmodules.exists():
            return []

        text = gitmodules.read_text(encoding="utf-8")
        return _parse_gitmodules(text, repo_root)

    # ── Ingestion ─────────────────────────────────────────────────────────────

    def ingest_with_submodules(
        self,
        repo_path: str | Path,
        clear: bool = False,
    ) -> dict[str, Any]:
        """
        Ingest the parent repository and all discovered submodules.

        For each submodule whose ``abs_path`` exists on disk the full code
        ingestion pipeline (``RepoIngester``) is run.  Repository nodes are
        linked with DEPENDS_ON edges.

        Parameters
        ----------
        repo_path:
            Root of the parent (super-project) repository.
        clear:
            If ``True``, wipe the graph before ingesting the parent.

        Returns
        -------
        dict with keys:
          ``parent``     — ingestion stats for the parent repo
          ``submodules`` — dict keyed by submodule name → stats or error
          ``total_files`` — aggregate file count
        """
        from navegador.ingestion.parser import RepoIngester

        repo_root = Path(repo_path).resolve()
        parent_name = repo_root.name

        ingester = RepoIngester(self.store)

        # Ingest parent
        logger.info("SubmoduleIngester: ingesting parent %s", repo_root)
        parent_stats = ingester.ingest(str(repo_root), clear=clear)

        # Ensure parent Repository node exists
        self.store.create_node(
            NodeLabel.Repository,
            {
                "name": parent_name,
                "path": str(repo_root),
                "language": "",
                "description": "parent repository",
            },
        )

        submodules = self.detect_submodules(repo_root)
        sub_results: dict[str, Any] = {}
        total_files = parent_stats.get("files", 0)

        for sub in submodules:
            sub_name = sub["name"]
            sub_path = sub["abs_path"]

            if not Path(sub_path).exists():
                logger.warning(
                    "SubmoduleIngester: submodule %s not found at %s (not initialised?)",
                    sub_name,
                    sub_path,
                )
                sub_results[sub_name] = {"error": f"path not found: {sub_path}"}
                continue

            logger.info("SubmoduleIngester: ingesting submodule %s → %s", sub_name, sub_path)
            try:
                sub_stats = ingester.ingest(str(sub_path), clear=False)
                sub_results[sub_name] = sub_stats
                total_files += sub_stats.get("files", 0)
            except Exception as exc:  # noqa: BLE001
                logger.error("SubmoduleIngester: failed to ingest %s: %s", sub_name, exc)
                sub_results[sub_name] = {"error": str(exc)}
                continue

            # Create submodule Repository node
            self.store.create_node(
                NodeLabel.Repository,
                {
                    "name": sub_name,
                    "path": str(sub_path),
                    "language": "",
                    "description": sub.get("url", ""),
                },
            )

            # parent -DEPENDS_ON-> submodule
            self.store.create_edge(
                NodeLabel.Repository,
                {"name": parent_name},
                EdgeType.DEPENDS_ON,
                NodeLabel.Repository,
                {"name": sub_name},
            )

        return {
            "parent": parent_stats,
            "submodules": sub_results,
            "total_files": total_files,
        }


# ── Parser ────────────────────────────────────────────────────────────────────


def _parse_gitmodules(text: str, repo_root: Path) -> list[dict[str, Any]]:
    """
    Parse a ``.gitmodules`` file into a list of submodule dicts.

    ``.gitmodules`` format::

        [submodule "name"]
            path = relative/path
            url  = https://...
    """
    submodules: list[dict[str, Any]] = []
    current: dict[str, str] = {}

    for line in text.splitlines():
        line = line.strip()

        header = re.match(r'^\[submodule\s+"([^"]+)"\]$', line)
        if header:
            if current.get("name"):
                submodules.append(_finalise(current, repo_root))
            current = {"name": header.group(1)}
            continue

        kv = re.match(r"^(\w+)\s*=\s*(.+)$", line)
        if kv:
            current[kv.group(1).strip()] = kv.group(2).strip()

    if current.get("name"):
        submodules.append(_finalise(current, repo_root))

    return submodules


def _finalise(raw: dict[str, str], repo_root: Path) -> dict[str, Any]:
    rel_path = raw.get("path", raw.get("name", ""))
    return {
        "name": raw["name"],
        "path": rel_path,
        "url": raw.get("url", ""),
        "abs_path": str(repo_root / rel_path),
    }
