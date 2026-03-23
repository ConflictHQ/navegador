"""
CODEOWNERS integration — map ownership to Person and Domain nodes.

Parses GitHub/GitLab CODEOWNERS files and creates Person nodes with
ASSIGNED_TO edges linking file paths to owners.

Usage:
    from navegador.codeowners import CodeownersIngester

    ingester = CodeownersIngester(store)
    stats = ingester.ingest("/path/to/repo")
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from navegador.graph.schema import EdgeType, NodeLabel
from navegador.graph.store import GraphStore

logger = logging.getLogger(__name__)

# CODEOWNERS files can live in the root, .github/, or .gitlab/
_CODEOWNERS_CANDIDATES = [
    "CODEOWNERS",
    ".github/CODEOWNERS",
    ".gitlab/CODEOWNERS",
    "docs/CODEOWNERS",
]


class CodeownersIngester:
    """
    Parse a CODEOWNERS file and write Person + File nodes into the graph.

    Each owner mentioned in CODEOWNERS becomes a Person node.
    Each pattern entry creates an ASSIGNED_TO edge from the File node (keyed
    by the pattern string) to the Person node.
    """

    def __init__(self, store: GraphStore) -> None:
        self.store = store

    # ── Public API ────────────────────────────────────────────────────────────

    def ingest(self, repo_path: str | Path) -> dict[str, Any]:
        """
        Parse the CODEOWNERS file found inside *repo_path* and populate the
        graph.

        Returns a stats dict with keys: owners, patterns, edges.
        """
        repo_path = Path(repo_path)
        codeowners_path = self._find_codeowners(repo_path)
        if codeowners_path is None:
            logger.warning("CodeownersIngester: no CODEOWNERS file found in %s", repo_path)
            return {"owners": 0, "patterns": 0, "edges": 0}

        entries = self._parse_codeowners(codeowners_path)
        owners_seen: set[str] = set()
        edges = 0

        for pattern, owners in entries:
            # Ensure a File node exists for this pattern
            self.store.create_node(
                NodeLabel.File,
                {
                    "name": pattern,
                    "path": pattern,
                    "file_path": pattern,
                    "language": "",
                    "size": 0,
                    "line_count": 0,
                    "content_hash": "",
                },
            )

            for owner in owners:
                display_name = owner.lstrip("@")
                email = owner if "@" in owner and not owner.startswith("@") else ""

                if display_name not in owners_seen:
                    self.store.create_node(
                        NodeLabel.Person,
                        {
                            "name": display_name,
                            "email": email,
                            "role": "owner",
                            "team": "",
                            "file_path": "",
                        },
                    )
                    owners_seen.add(display_name)

                # File pattern -ASSIGNED_TO-> Person
                self.store.create_edge(
                    NodeLabel.File,
                    {"name": pattern},
                    EdgeType.ASSIGNED_TO,
                    NodeLabel.Person,
                    {"name": display_name},
                )
                edges += 1

        stats = {
            "owners": len(owners_seen),
            "patterns": len(entries),
            "edges": edges,
        }
        logger.info("CodeownersIngester: %s", stats)
        return stats

    # ── Parsing ───────────────────────────────────────────────────────────────

    def _parse_codeowners(self, path: Path) -> list[tuple[str, list[str]]]:
        """
        Parse a CODEOWNERS file at *path*.

        Returns a list of (pattern, [owner, ...]) tuples.  Comment lines and
        blank lines are ignored.
        """
        entries: list[tuple[str, list[str]]] = []
        text = path.read_text(encoding="utf-8", errors="replace")

        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            pattern = parts[0]
            owners = parts[1:]
            entries.append((pattern, owners))

        return entries

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _find_codeowners(self, repo_path: Path) -> Path | None:
        for candidate in _CODEOWNERS_CANDIDATES:
            p = repo_path / candidate
            if p.exists():
                return p
        return None
