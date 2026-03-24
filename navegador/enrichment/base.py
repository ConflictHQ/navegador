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
        """Import module names that indicate this framework is in use.

        Detection queries Import nodes for exact module matches, so use
        the actual package name (e.g. 'django', 'fastapi', 'express').
        """

    @property
    def detection_files(self) -> list[str]:
        """Filenames whose presence confirms the framework.

        Override this to add file-based detection (e.g. 'manage.py' for
        Django, 'Gemfile' for Rails). File names are matched exactly
        against File node names — not as substrings.
        """
        return []

    @abstractmethod
    def enrich(self) -> EnrichmentResult:
        """Run enrichment on the current graph."""

    def detect(self) -> bool:
        """Check if the framework is present by looking for real imports and marker files."""
        # Check Import nodes for actual framework imports
        for pattern in self.detection_patterns:
            result = self.store.query(
                "MATCH (n:Import) WHERE n.name = $name OR n.module = $name "
                "RETURN count(n) AS c",
                {"name": pattern},
            )
            rows = result.result_set or []
            if rows and rows[0][0] > 0:
                return True

        # Check for marker files by exact filename match
        for filename in self.detection_files:
            result = self.store.query(
                "MATCH (f:File) WHERE f.name = $name "
                "RETURN count(f) AS c",
                {"name": filename},
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
