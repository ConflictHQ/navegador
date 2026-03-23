"""
FrameworkEnricher base class.

Post-processing step that runs after AST ingestion. Examines existing graph
nodes and promotes generic Function/Class nodes to semantic framework types
by adding labels/properties.
"""

from abc import ABC, abstractmethod

from navegador.graph.store import GraphStore


class EnrichmentResult:
    """Result of an enrichment pass."""

    def __init__(self):
        self.promoted: int = 0  # nodes that got framework-specific labels
        self.edges_added: int = 0  # new semantic edges added
        self.patterns_found: dict[str, int] = {}  # pattern_name -> count


class FrameworkEnricher(ABC):
    """Base class for framework-specific graph enrichment."""

    def __init__(self, store: GraphStore):
        self.store = store

    @property
    @abstractmethod
    def framework_name(self) -> str:
        """Name of the framework (e.g. 'django', 'fastapi')."""

    @property
    @abstractmethod
    def detection_patterns(self) -> list[str]:
        """File/import patterns that indicate this framework is in use.

        E.g. ['manage.py', 'django.conf.settings'] for Django.
        """

    @abstractmethod
    def enrich(self) -> EnrichmentResult:
        """Run enrichment on the current graph."""

    def detect(self) -> bool:
        """Check if the framework is present in the graph by looking for detection patterns."""
        for pattern in self.detection_patterns:
            result = self.store.query(
                "MATCH (n) WHERE n.name CONTAINS $pattern OR "
                "(n.file_path IS NOT NULL AND n.file_path CONTAINS $pattern) "
                "RETURN count(n) AS c LIMIT 1",
                {"pattern": pattern},
            )
            rows = result.result_set or []
            if rows and rows[0][0] > 0:
                return True
        return False

    def _promote_node(
        self, name: str, file_path: str, semantic_type: str, props: dict = None
    ) -> None:
        """Add a semantic_type property to an existing node."""
        extra = ""
        if props:
            extra = ", " + ", ".join(f"n.{k} = ${k}" for k in props)
        self.store.query(
            f"MATCH (n) WHERE n.name = $name AND n.file_path = $file_path "
            f"SET n.semantic_type = $semantic_type{extra}",
            {"name": name, "file_path": file_path, "semantic_type": semantic_type, **(props or {})},
        )

    def _add_semantic_edge(
        self, from_name: str, edge_type: str, to_name: str, props: dict = None
    ) -> None:
        """Create a semantic edge between two nodes by name."""
        extra = ""
        if props:
            extra = " SET " + ", ".join(f"r.{k} = ${k}" for k in props)
        self.store.query(
            f"MATCH (a), (b) WHERE a.name = $from_name AND b.name = $to_name "
            f"MERGE (a)-[r:{edge_type}]->(b){extra}",
            {"from_name": from_name, "to_name": to_name, **(props or {})},
        )
