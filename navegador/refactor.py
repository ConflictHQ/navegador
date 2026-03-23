"""
Coordinated rename — graph-assisted multi-file symbol refactoring.

Usage:
    from navegador.refactor import SymbolRenamer

    renamer = SymbolRenamer(store)
    preview = renamer.preview_rename("old_name", "new_name")
    print(preview.affected_files)
    result = renamer.apply_rename("old_name", "new_name")
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from navegador.graph.store import GraphStore

logger = logging.getLogger(__name__)


# ── Data models ───────────────────────────────────────────────────────────────


@dataclass
class RenamePreview:
    """Shows what would change if the rename were applied."""

    old_name: str
    new_name: str
    affected_files: list[str] = field(default_factory=list)
    affected_nodes: list[dict[str, Any]] = field(default_factory=list)
    edges_updated: int = 0


@dataclass
class RenameResult:
    """Records what actually changed after applying the rename."""

    old_name: str
    new_name: str
    affected_files: list[str] = field(default_factory=list)
    affected_nodes: list[dict[str, Any]] = field(default_factory=list)
    edges_updated: int = 0


# ── Core class ────────────────────────────────────────────────────────────────


class SymbolRenamer:
    """
    Graph-assisted multi-file symbol refactoring.

    Operates entirely on the graph: it finds nodes whose ``name`` matches the
    symbol and updates them in place.  It does *not* edit source files on disk
    (that is left to the editor / agent layer).
    """

    def __init__(self, store: GraphStore) -> None:
        self.store = store

    # ── Public API ────────────────────────────────────────────────────────────

    def find_references(self, name: str, file_path: str = "") -> list[dict[str, Any]]:
        """
        Return all graph nodes whose name matches *name*.

        Optionally filter to a specific file with *file_path*.
        """
        if file_path:
            cypher = (
                "MATCH (n) "
                "WHERE n.name = $name AND n.file_path = $fp "
                "RETURN labels(n)[0] AS label, n.name AS name, "
                "       coalesce(n.file_path, '') AS file_path, "
                "       coalesce(n.line_start, 0) AS line_start"
            )
            result = self.store.query(cypher, {"name": name, "fp": file_path})
        else:
            cypher = (
                "MATCH (n) "
                "WHERE n.name = $name "
                "RETURN labels(n)[0] AS label, n.name AS name, "
                "       coalesce(n.file_path, '') AS file_path, "
                "       coalesce(n.line_start, 0) AS line_start"
            )
            result = self.store.query(cypher, {"name": name})

        rows = result.result_set or []
        return [
            {
                "label": row[0],
                "name": row[1],
                "file_path": row[2],
                "line_start": row[3],
            }
            for row in rows
        ]

    def preview_rename(self, old_name: str, new_name: str) -> RenamePreview:
        """
        Return a RenamePreview showing what would change without modifying
        anything.
        """
        refs = self.find_references(old_name)
        affected_files = sorted({r["file_path"] for r in refs if r["file_path"]})

        # Count edges that touch these nodes
        edges_updated = self._count_edges(old_name)

        return RenamePreview(
            old_name=old_name,
            new_name=new_name,
            affected_files=affected_files,
            affected_nodes=refs,
            edges_updated=edges_updated,
        )

    def apply_rename(self, old_name: str, new_name: str) -> RenameResult:
        """
        Update all graph nodes named *old_name* to *new_name*.

        Returns a RenameResult describing what was changed.
        """
        refs = self.find_references(old_name)
        affected_files = sorted({r["file_path"] for r in refs if r["file_path"]})
        edges_updated = self._count_edges(old_name)

        # Update every node whose name matches
        cypher = "MATCH (n) WHERE n.name = $old SET n.name = $new"
        self.store.query(cypher, {"old": old_name, "new": new_name})
        logger.info(
            "SymbolRenamer: renamed %r → %r (%d nodes, %d edges)",
            old_name,
            new_name,
            len(refs),
            edges_updated,
        )

        return RenameResult(
            old_name=old_name,
            new_name=new_name,
            affected_files=affected_files,
            affected_nodes=refs,
            edges_updated=edges_updated,
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _count_edges(self, name: str) -> int:
        """Count edges incident on nodes named *name*."""
        cypher = "MATCH (n)-[r]-() WHERE n.name = $name RETURN count(r) AS c"
        result = self.store.query(cypher, {"name": name})
        rows = result.result_set or []
        if rows:
            return rows[0][0] or 0
        return 0
