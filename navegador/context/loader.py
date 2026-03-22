"""
ContextLoader — builds structured context bundles from the graph.

A context bundle contains:
- The target node (file / function / class)
- Its immediate neighbors up to a configurable depth
- Relationships between those nodes
- Source snippets (optional)

Output can be:
- dict (structured JSON-serialisable)
- markdown string (for direct paste into AI chat)
"""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from navegador.graph import GraphStore, queries

logger = logging.getLogger(__name__)


@dataclass
class ContextNode:
    type: str
    name: str
    file_path: str
    line_start: int | None = None
    docstring: str | None = None
    signature: str | None = None
    source: str | None = None


@dataclass
class ContextBundle:
    target: ContextNode
    nodes: list[ContextNode] = field(default_factory=list)
    edges: list[dict[str, str]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": vars(self.target),
            "nodes": [vars(n) for n in self.nodes],
            "edges": self.edges,
            "metadata": self.metadata,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    def to_markdown(self) -> str:
        lines = [
            f"# Context: `{self.target.name}`",
            f"**File:** `{self.target.file_path}`",
            f"**Type:** {self.target.type}",
        ]
        if self.target.docstring:
            lines += ["", f"> {self.target.docstring}"]
        if self.target.signature:
            lines += ["", f"```python\n{self.target.signature}\n```"]

        if self.nodes:
            lines += ["", "## Related nodes", ""]
            for node in self.nodes:
                lines.append(f"- **{node.type}** `{node.name}` — `{node.file_path}`")
                if node.docstring:
                    lines.append(f"  > {node.docstring}")

        if self.edges:
            lines += ["", "## Relationships", ""]
            for edge in self.edges:
                lines.append(f"- `{edge['from']}` **{edge['type']}** `{edge['to']}`")

        return "\n".join(lines)


class ContextLoader:
    """
    Loads structured context bundles from the navegador graph.

    Usage:
        store = GraphStore.sqlite()
        loader = ContextLoader(store)

        bundle = loader.load_file("src/auth.py")
        bundle = loader.load_function("get_user", file_path="src/auth.py")
        bundle = loader.load_class("AuthService", file_path="src/auth.py")
    """

    def __init__(self, store: GraphStore) -> None:
        self.store = store

    def load_file(self, file_path: str, depth: int = 2) -> ContextBundle:
        """Load context for an entire file — all its symbols + their dependencies."""
        result = self.store.query(queries.FILE_CONTENTS, {"path": file_path})
        target = ContextNode(type="File", name=Path(file_path).name, file_path=file_path)

        nodes = []
        for row in (result.result_set or []):
            nodes.append(ContextNode(
                type=row[0] or "Unknown",
                name=row[1] or "",
                file_path=file_path,
                line_start=row[2],
                docstring=row[3],
                signature=row[4],
            ))

        return ContextBundle(
            target=target,
            nodes=nodes,
            metadata={"depth": depth, "query": "file_contents"},
        )

    def load_function(self, name: str, file_path: str = "", depth: int = 2) -> ContextBundle:
        """Load context for a function — its callers and callees."""
        target = ContextNode(type="Function", name=name, file_path=file_path)
        nodes: list[ContextNode] = []
        edges: list[dict[str, str]] = []

        callees = self.store.query(
            queries.CALLEES, {"name": name, "file_path": file_path, "depth": depth}
        )
        for row in (callees.result_set or []):
            nodes.append(ContextNode(type=row[0], name=row[1], file_path=row[2], line_start=row[3]))
            edges.append({"from": name, "type": "CALLS", "to": row[1]})

        callers = self.store.query(
            queries.CALLERS, {"name": name, "file_path": file_path, "depth": depth}
        )
        for row in (callers.result_set or []):
            nodes.append(ContextNode(type=row[0], name=row[1], file_path=row[2], line_start=row[3]))
            edges.append({"from": row[1], "type": "CALLS", "to": name})

        return ContextBundle(target=target, nodes=nodes, edges=edges,
                             metadata={"depth": depth, "query": "function_context"})

    def load_class(self, name: str, file_path: str = "") -> ContextBundle:
        """Load context for a class — its methods, parent classes, and subclasses."""
        target = ContextNode(type="Class", name=name, file_path=file_path)
        nodes: list[ContextNode] = []
        edges: list[dict[str, str]] = []

        parents = self.store.query(queries.CLASS_HIERARCHY, {"name": name})
        for row in (parents.result_set or []):
            nodes.append(ContextNode(type="Class", name=row[0], file_path=row[1]))
            edges.append({"from": name, "type": "INHERITS", "to": row[0]})

        subs = self.store.query(queries.SUBCLASSES, {"name": name})
        for row in (subs.result_set or []):
            nodes.append(ContextNode(type="Class", name=row[0], file_path=row[1]))
            edges.append({"from": row[0], "type": "INHERITS", "to": name})

        return ContextBundle(target=target, nodes=nodes, edges=edges,
                             metadata={"query": "class_context"})

    def search(self, query: str, limit: int = 20) -> list[ContextNode]:
        """Fuzzy search symbols (functions, classes, methods) by name."""
        result = self.store.query(queries.SYMBOL_SEARCH, {"query": query, "limit": limit})
        return [
            ContextNode(type=row[0], name=row[1], file_path=row[2],
                        line_start=row[3], docstring=row[4])
            for row in (result.result_set or [])
        ]
