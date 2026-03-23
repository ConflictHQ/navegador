"""
ContextLoader — builds structured context bundles from the navegador graph.

Operates across both layers:
  CODE — files, functions, classes, call graphs, decorators, references
  KNOWLEDGE — concepts, rules, decisions, wiki pages, domains

Output can be dict, JSON string, or markdown for direct paste into AI chat.
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
    file_path: str = ""
    line_start: int | None = None
    docstring: str | None = None
    signature: str | None = None
    source: str | None = None
    description: str | None = None
    domain: str | None = None
    status: str | None = None
    rationale: str | None = None
    alternatives: str | None = None
    date: str | None = None


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
            f"**Type:** {self.target.type}",
        ]
        if self.target.file_path:
            lines.append(f"**File:** `{self.target.file_path}`")
        if self.target.domain:
            lines.append(f"**Domain:** {self.target.domain}")
        if self.target.status:
            lines.append(f"**Status:** {self.target.status}")
        if self.target.docstring or self.target.description:
            lines += ["", f"> {self.target.docstring or self.target.description}"]
        if self.target.signature:
            lines += ["", f"```python\n{self.target.signature}\n```"]

        if self.nodes:
            lines += ["", "## Related nodes", ""]
            for node in self.nodes:
                loc = f"`{node.file_path}`" if node.file_path else ""
                lines.append(f"- **{node.type}** `{node.name}` {loc}".strip())
                summary = node.docstring or node.description
                if summary:
                    lines.append(f"  > {summary}")

        if self.edges:
            lines += ["", "## Relationships", ""]
            for edge in self.edges:
                lines.append(f"- `{edge['from']}` **{edge['type']}** `{edge['to']}`")

        return "\n".join(lines)


class ContextLoader:
    """
    Loads context bundles from the navegador graph — code and knowledge layers.

    Usage:
        store = GraphStore.sqlite()
        loader = ContextLoader(store)

        bundle = loader.load_file("src/auth.py")
        bundle = loader.load_function("validate_token")
        bundle = loader.load_class("AuthService")
        bundle = loader.explain("validate_token")        # full picture
        bundle = loader.load_concept("JWT")
        bundle = loader.load_domain("auth")
    """

    def __init__(self, store: GraphStore) -> None:
        self.store = store

    # ── Code: file ────────────────────────────────────────────────────────────

    def load_file(self, file_path: str) -> ContextBundle:
        """All symbols in a file and their relationships."""
        result = self.store.query(queries.FILE_CONTENTS, {"path": file_path})
        target = ContextNode(type="File", name=Path(file_path).name, file_path=file_path)
        nodes = []
        for row in result.result_set or []:
            nodes.append(
                ContextNode(
                    type=row[0] or "Unknown",
                    name=row[1] or "",
                    file_path=file_path,
                    line_start=row[2],
                    docstring=row[3],
                    signature=row[4],
                )
            )
        return ContextBundle(target=target, nodes=nodes, metadata={"query": "file_contents"})

    # ── Code: function ────────────────────────────────────────────────────────

    def load_function(self, name: str, file_path: str = "", depth: int = 2) -> ContextBundle:
        """Callers, callees, decorators — everything touching this function."""
        target = ContextNode(type="Function", name=name, file_path=file_path)
        nodes: list[ContextNode] = []
        edges: list[dict[str, str]] = []

        params = {"name": name, "file_path": file_path, "depth": depth}

        callees = self.store.query(queries.CALLEES, params)
        for row in callees.result_set or []:
            nodes.append(ContextNode(type=row[0], name=row[1], file_path=row[2], line_start=row[3]))
            edges.append({"from": name, "type": "CALLS", "to": row[1]})

        callers = self.store.query(queries.CALLERS, params)
        for row in callers.result_set or []:
            nodes.append(ContextNode(type=row[0], name=row[1], file_path=row[2], line_start=row[3]))
            edges.append({"from": row[1], "type": "CALLS", "to": name})

        decorators = self.store.query(
            queries.DECORATORS_FOR, {"name": name, "file_path": file_path}
        )
        for row in decorators.result_set or []:
            nodes.append(ContextNode(type="Decorator", name=row[0], file_path=row[1]))
            edges.append({"from": row[0], "type": "DECORATES", "to": name})

        return ContextBundle(
            target=target,
            nodes=nodes,
            edges=edges,
            metadata={"depth": depth, "query": "function_context"},
        )

    # ── Code: class ───────────────────────────────────────────────────────────

    def load_class(self, name: str, file_path: str = "") -> ContextBundle:
        """Methods, parent classes, subclasses, references."""
        target = ContextNode(type="Class", name=name, file_path=file_path)
        nodes: list[ContextNode] = []
        edges: list[dict[str, str]] = []

        parents = self.store.query(queries.CLASS_HIERARCHY, {"name": name})
        for row in parents.result_set or []:
            nodes.append(ContextNode(type="Class", name=row[0], file_path=row[1]))
            edges.append({"from": name, "type": "INHERITS", "to": row[0]})

        subs = self.store.query(queries.SUBCLASSES, {"name": name})
        for row in subs.result_set or []:
            nodes.append(ContextNode(type="Class", name=row[0], file_path=row[1]))
            edges.append({"from": row[0], "type": "INHERITS", "to": name})

        refs = self.store.query(queries.REFERENCES_TO, {"name": name, "file_path": ""})
        for row in refs.result_set or []:
            nodes.append(ContextNode(type=row[0], name=row[1], file_path=row[2], line_start=row[3]))
            edges.append({"from": row[1], "type": "REFERENCES", "to": name})

        return ContextBundle(
            target=target, nodes=nodes, edges=edges, metadata={"query": "class_context"}
        )

    # ── Universal: explain ────────────────────────────────────────────────────

    def explain(self, name: str, file_path: str = "") -> ContextBundle:
        """
        Full picture: all inbound and outbound relationships for any node,
        across both code and knowledge layers.
        """
        params = {"name": name, "file_path": file_path}
        target = ContextNode(type="Node", name=name, file_path=file_path)
        nodes: list[ContextNode] = []
        edges: list[dict[str, str]] = []

        outbound = self.store.query(queries.OUTBOUND, params)
        for row in outbound.result_set or []:
            rel, ntype, nname, npath = row[0], row[1], row[2], row[3]
            nodes.append(ContextNode(type=ntype, name=nname, file_path=npath))
            edges.append({"from": name, "type": rel, "to": nname})

        inbound = self.store.query(queries.INBOUND, params)
        for row in inbound.result_set or []:
            rel, ntype, nname, npath = row[0], row[1], row[2], row[3]
            nodes.append(ContextNode(type=ntype, name=nname, file_path=npath))
            edges.append({"from": nname, "type": rel, "to": name})

        return ContextBundle(target=target, nodes=nodes, edges=edges, metadata={"query": "explain"})

    # ── Knowledge: concept ────────────────────────────────────────────────────

    def load_concept(self, name: str) -> ContextBundle:
        """Concept + governing rules + related concepts + implementing code + wiki pages."""
        result = self.store.query(queries.CONCEPT_CONTEXT, {"name": name})
        rows = result.result_set or []

        if not rows:
            return ContextBundle(
                target=ContextNode(type="Concept", name=name),
                metadata={"query": "concept_context", "found": False},
            )

        row = rows[0]
        target = ContextNode(
            type="Concept",
            name=row[0],
            description=row[1],
            status=row[2],
            domain=row[3],
        )
        nodes: list[ContextNode] = []
        edges: list[dict[str, str]] = []

        for cname in row[4] or []:
            nodes.append(ContextNode(type="Concept", name=cname))
            edges.append({"from": name, "type": "RELATED_TO", "to": cname})
        for rname in row[5] or []:
            nodes.append(ContextNode(type="Rule", name=rname))
            edges.append({"from": rname, "type": "GOVERNS", "to": name})
        for wname in row[6] or []:
            nodes.append(ContextNode(type="WikiPage", name=wname))
            edges.append({"from": wname, "type": "DOCUMENTS", "to": name})
        for iname in row[7] or []:
            nodes.append(ContextNode(type="Code", name=iname))
            edges.append({"from": iname, "type": "IMPLEMENTS", "to": name})

        return ContextBundle(
            target=target, nodes=nodes, edges=edges, metadata={"query": "concept_context"}
        )

    # ── Knowledge: domain ─────────────────────────────────────────────────────

    def load_domain(self, domain: str) -> ContextBundle:
        """Everything belonging to a domain — code and knowledge."""
        result = self.store.query(queries.DOMAIN_CONTENTS, {"domain": domain})
        target = ContextNode(type="Domain", name=domain)
        nodes = [
            ContextNode(type=row[0], name=row[1], file_path=row[2], description=row[3] or None)
            for row in (result.result_set or [])
        ]
        return ContextBundle(target=target, nodes=nodes, metadata={"query": "domain_contents"})

    # ── Search ────────────────────────────────────────────────────────────────

    def search(self, query: str, limit: int = 20) -> list[ContextNode]:
        """Search code symbols by name."""
        result = self.store.query(queries.SYMBOL_SEARCH, {"query": query, "limit": limit})
        return [
            ContextNode(
                type=row[0], name=row[1], file_path=row[2], line_start=row[3], docstring=row[4]
            )
            for row in (result.result_set or [])
        ]

    def search_all(self, query: str, limit: int = 20) -> list[ContextNode]:
        """Search everything — code symbols, concepts, rules, decisions, wiki."""
        result = self.store.query(queries.GLOBAL_SEARCH, {"query": query, "limit": limit})
        return [
            ContextNode(
                type=row[0], name=row[1], file_path=row[2], docstring=row[3], line_start=row[4]
            )
            for row in (result.result_set or [])
        ]

    def search_by_docstring(self, query: str, limit: int = 20) -> list[ContextNode]:
        """Search functions/classes whose docstring contains the query."""
        result = self.store.query(queries.DOCSTRING_SEARCH, {"query": query, "limit": limit})
        return [
            ContextNode(
                type=row[0], name=row[1], file_path=row[2], line_start=row[3], docstring=row[4]
            )
            for row in (result.result_set or [])
        ]

    # ── Knowledge: decision rationale ────────────────────────────────────────

    def load_decision(self, name: str) -> ContextBundle:
        """Decision rationale, alternatives, status, and related nodes."""
        result = self.store.query(queries.DECISION_RATIONALE, {"name": name})
        rows = result.result_set or []

        if not rows:
            return ContextBundle(
                target=ContextNode(type="Decision", name=name),
                metadata={"query": "decision_rationale", "found": False},
            )

        row = rows[0]
        target = ContextNode(
            type="Decision",
            name=row[0],
            description=row[1],
            status=row[4],
            domain=row[6],
        )
        target.rationale = row[2]
        target.alternatives = row[3]
        target.date = row[5]

        nodes: list[ContextNode] = []
        edges: list[dict[str, str]] = []

        for tname in row[7] or []:
            nodes.append(ContextNode(type="Node", name=tname))
            edges.append({"from": name, "type": "DOCUMENTS", "to": tname})
        for pname in row[8] or []:
            nodes.append(ContextNode(type="Person", name=pname))
            edges.append({"from": name, "type": "DECIDED_BY", "to": pname})

        return ContextBundle(
            target=target, nodes=nodes, edges=edges, metadata={"query": "decision_rationale"}
        )

    # ── Knowledge: find owners ────────────────────────────────────────────────

    def find_owners(self, name: str, file_path: str = "") -> list[ContextNode]:
        """Find people assigned to a named node."""
        result = self.store.query(queries.FIND_OWNERS, {"name": name, "file_path": file_path})
        return [
            ContextNode(
                type="Person",
                name=row[2],
                description=f"role={row[4]}, team={row[5]}",
            )
            for row in (result.result_set or [])
        ]

    # ── Knowledge: search ────────────────────────────────────────────────────

    def search_knowledge(self, query: str, limit: int = 20) -> list[ContextNode]:
        """Search concepts, rules, decisions, and wiki pages."""
        result = self.store.query(queries.KNOWLEDGE_SEARCH, {"query": query, "limit": limit})
        return [
            ContextNode(
                type=row[0],
                name=row[1],
                description=row[2],
                domain=row[3],
                status=row[4],
            )
            for row in (result.result_set or [])
        ]

    def decorated_by(self, decorator_name: str) -> list[ContextNode]:
        """All functions/methods carrying a given decorator."""
        result = self.store.query(queries.DECORATED_BY, {"decorator_name": decorator_name})
        return [
            ContextNode(type=row[0], name=row[1], file_path=row[2], line_start=row[3])
            for row in (result.result_set or [])
        ]
