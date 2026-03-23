# Copyright CONFLICT LLC 2026 (weareconflict.com)
"""
Impact analysis — blast-radius: given a named node, what does changing it affect?

Traverses CALLS, REFERENCES, INHERITS, IMPLEMENTS, ANNOTATES edges outward
from the named node to find everything downstream that would be affected by
a change to the named symbol.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from navegador.graph import GraphStore

# Cypher: traverse outward across structural edges to find affected nodes
_BLAST_RADIUS_QUERY = """
MATCH (root)
WHERE root.name = $name AND ($file_path = '' OR root.file_path = $file_path)
CALL {
    WITH root
    MATCH (root)-[:CALLS|REFERENCES|INHERITS|IMPLEMENTS|ANNOTATES*1..$depth]->(affected)
    RETURN DISTINCT affected
}
RETURN DISTINCT
    labels(affected)[0] AS node_type,
    affected.name AS node_name,
    coalesce(affected.file_path, '') AS node_file_path,
    affected.line_start AS line_start
"""

# Simpler fallback without CALL subquery (FalkorDB compatibility)
_BLAST_RADIUS_SIMPLE = """
MATCH (root)-[:CALLS|REFERENCES|INHERITS|IMPLEMENTS|ANNOTATES*1..$depth]->(affected)
WHERE root.name = $name AND ($file_path = '' OR root.file_path = $file_path)
RETURN DISTINCT
    labels(affected)[0] AS node_type,
    affected.name AS node_name,
    coalesce(affected.file_path, '') AS node_file_path,
    affected.line_start AS line_start
"""

# Knowledge nodes affected (concepts, rules annotated from this node)
_AFFECTED_KNOWLEDGE_QUERY = """
MATCH (root)-[:ANNOTATES|IMPLEMENTS|GOVERNS*1..2]->(kn)
WHERE root.name = $name AND ($file_path = '' OR root.file_path = $file_path)
  AND (kn:Concept OR kn:Rule OR kn:Decision OR kn:WikiPage)
RETURN DISTINCT labels(kn)[0] AS type, kn.name AS name
"""


@dataclass
class ImpactResult:
    """Result of a blast-radius analysis."""

    name: str
    file_path: str
    depth: int
    affected_nodes: list[dict[str, Any]] = field(default_factory=list)
    affected_files: list[str] = field(default_factory=list)
    affected_knowledge: list[dict[str, str]] = field(default_factory=list)
    depth_reached: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "file_path": self.file_path,
            "depth": self.depth,
            "depth_reached": self.depth_reached,
            "affected_nodes": self.affected_nodes,
            "affected_files": self.affected_files,
            "affected_knowledge": self.affected_knowledge,
        }


class ImpactAnalyzer:
    """
    Blast-radius analysis: find everything downstream of a given node.

    Usage::

        store = GraphStore.sqlite()
        analyzer = ImpactAnalyzer(store)
        result = analyzer.blast_radius("validate_token", depth=3)
        print(result.affected_files)
    """

    def __init__(self, store: GraphStore) -> None:
        self.store = store

    def blast_radius(
        self,
        name: str,
        file_path: str = "",
        depth: int = 3,
    ) -> ImpactResult:
        """
        Compute the blast radius of changing a named node.

        Traverses CALLS, REFERENCES, INHERITS, IMPLEMENTS, ANNOTATES edges
        outward up to *depth* hops and returns all affected nodes/files.

        Args:
            name:      Symbol name (function, class, etc.)
            file_path: Narrow to a specific file (optional).
            depth:     Maximum traversal depth.

        Returns:
            ImpactResult with affected_nodes, affected_files, affected_knowledge.
        """
        params: dict[str, Any] = {"name": name, "file_path": file_path, "depth": depth}

        try:
            result = self.store.query(_BLAST_RADIUS_SIMPLE, params)
            rows = result.result_set or []
        except Exception:
            rows = []

        affected_nodes: list[dict[str, Any]] = []
        affected_files: set[str] = set()
        depth_reached = 0

        for row in rows:
            node_type = row[0] or "Unknown"
            node_name = row[1] or ""
            node_file = row[2] or ""
            line = row[3]

            affected_nodes.append(
                {
                    "type": node_type,
                    "name": node_name,
                    "file_path": node_file,
                    "line_start": line,
                }
            )
            if node_file:
                affected_files.add(node_file)

        if affected_nodes:
            depth_reached = depth

        # Knowledge layer
        affected_knowledge: list[dict[str, str]] = []
        try:
            k_result = self.store.query(
                _AFFECTED_KNOWLEDGE_QUERY, {"name": name, "file_path": file_path}
            )
            for row in k_result.result_set or []:
                affected_knowledge.append({"type": row[0] or "", "name": row[1] or ""})
        except Exception:
            pass

        return ImpactResult(
            name=name,
            file_path=file_path,
            depth=depth,
            affected_nodes=affected_nodes,
            affected_files=sorted(affected_files),
            affected_knowledge=affected_knowledge,
            depth_reached=depth_reached,
        )
