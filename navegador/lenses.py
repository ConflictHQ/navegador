# Copyright CONFLICT LLC 2026 (weareconflict.com)
"""
Architecture lenses — reusable named graph views.

A lens is a named, parameterized graph traversal that produces a focused
subgraph answering a common architectural question (request paths, ownership,
domain boundaries, dependency layers, framework components).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from navegador.graph.store import GraphStore


# ── Data models ──────────────────────────────────────────────────────────────


@dataclass
class LensNode:
    label: str
    name: str
    file_path: str = ""
    domain: str = ""
    owner: str = ""
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d: dict[str, Any] = {
            "label": self.label,
            "name": self.name,
            "file_path": self.file_path,
        }
        if self.domain:
            d["domain"] = self.domain
        if self.owner:
            d["owner"] = self.owner
        if self.extra:
            d["extra"] = self.extra
        return d


@dataclass
class LensEdge:
    source: str
    target: str
    type: str

    def to_dict(self) -> dict:
        return {"source": self.source, "target": self.target, "type": self.type}


@dataclass
class LensResult:
    lens: str
    nodes: list[LensNode]
    edges: list[LensEdge]
    params: dict

    def to_dict(self) -> dict:
        return {
            "lens": self.lens,
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [e.to_dict() for e in self.edges],
            "params": self.params,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    def to_markdown(self) -> str:
        lines = [
            f"# Lens: {self.lens}",
            f"**{len(self.nodes)} nodes, {len(self.edges)} edges**  params: {self.params}",
            "",
            "## Nodes",
        ]
        for n in self.nodes:
            parts = [f"[{n.label}] {n.name}"]
            if n.file_path:
                parts.append(f"({n.file_path})")
            meta = []
            if n.owner:
                meta.append(f"owner={n.owner}")
            if n.domain:
                meta.append(f"domain={n.domain}")
            if meta:
                parts.append(f"-- {', '.join(meta)}")
            lines.append(f"- {' '.join(parts)}")
        lines.append("")
        lines.append("## Edges")
        for e in self.edges:
            lines.append(f"- {e.source} -[{e.type}]-> {e.target}")
        return "\n".join(lines)


# ── Lens descriptions ────────────────────────────────────────────────────────

BUILTIN_LENSES = [
    "request_path",
    "ownership_map",
    "domain_boundaries",
    "dependency_layers",
    "framework_components",
]

_LENS_DESCRIPTIONS: dict[str, str] = {
    "request_path": ("Call chain from entry-point functions (controllers, routers, handlers)."),
    "ownership_map": ("All symbols with ASSIGNED_TO owners, grouped by Person."),
    "domain_boundaries": ("Domain nodes and their member symbols with cross-domain CALLS."),
    "dependency_layers": ("IMPORTS/DEPENDS_ON edges showing architectural layers."),
    "framework_components": ("Framework-specific nodes enriched by framework enrichers."),
}


# ── Engine ───────────────────────────────────────────────────────────────────


class LensEngine:
    """Apply named architecture lenses to a graph store."""

    def __init__(self, store: GraphStore) -> None:
        self.store = store
        self._custom: dict[str, dict] = {}

    def list_lenses(self) -> list[dict]:
        """Return list of {name, description, builtin} dicts."""
        result = []
        for name in BUILTIN_LENSES:
            result.append(
                {
                    "name": name,
                    "description": _LENS_DESCRIPTIONS.get(name, ""),
                    "builtin": True,
                }
            )
        for name, info in self._custom.items():
            result.append(
                {
                    "name": name,
                    "description": info.get("description", ""),
                    "builtin": False,
                }
            )
        return result

    def register(self, name: str, cypher: str, description: str = "") -> None:
        """Register a custom lens with raw Cypher."""
        self._custom[name] = {"cypher": cypher, "description": description}

    def apply(self, lens: str, **params: Any) -> LensResult:
        """Apply a named lens with optional params.

        Dispatches to ``_lens_<name>()`` for built-ins or runs raw Cypher
        for custom lenses.

        Raises:
            ValueError: If the lens name is unknown.
        """
        method = getattr(self, f"_lens_{lens}", None)
        if method is not None:
            return method(**params)

        if lens in self._custom:
            return self._apply_custom(lens, **params)

        raise ValueError(
            f"Unknown lens: {lens!r}. Available: {', '.join(BUILTIN_LENSES + list(self._custom))}"
        )

    # ── Built-in lenses ──────────────────────────────────────────────────────

    def _lens_request_path(self, symbol: str = "", **_kw: Any) -> LensResult:
        cypher = (
            "MATCH (n)-[:CALLS*1..5]->(m) "
            "WHERE ($symbol = '' OR n.name = $symbol) "
            "AND (n.name CONTAINS 'controller' OR n.name CONTAINS 'handler' "
            "OR n.name CONTAINS 'router' OR n.name CONTAINS 'view' "
            "OR labels(n)[0] IN ['Controller', 'Handler', 'View']) "
            "RETURN DISTINCT labels(n)[0], n.name, coalesce(n.file_path,''), "
            "labels(m)[0], m.name, coalesce(m.file_path,'') LIMIT 100"
        )
        rows = self._query(cypher, {"symbol": symbol})
        nodes, edges = self._build_pair_graph(rows)
        return LensResult(
            lens="request_path",
            nodes=nodes,
            edges=edges,
            params={"symbol": symbol},
        )

    def _lens_ownership_map(self, domain: str = "", **_kw: Any) -> LensResult:
        cypher = (
            "MATCH (n)-[:ASSIGNED_TO]->(p:Person) "
            "WHERE ($domain = '' OR (n)-[:BELONGS_TO]->(:Domain {name: $domain})) "
            "RETURN DISTINCT labels(n)[0], n.name, coalesce(n.file_path,''), p.name "
            "LIMIT 200"
        )
        rows = self._query(cypher, {"domain": domain})
        seen_nodes: dict[str, LensNode] = {}
        edges: list[LensEdge] = []
        for row in rows:
            label, name, file_path, owner = row[0] or "", row[1] or "", row[2] or "", row[3] or ""
            if name and name not in seen_nodes:
                seen_nodes[name] = LensNode(
                    label=label, name=name, file_path=file_path, owner=owner
                )
            if owner and owner not in seen_nodes:
                seen_nodes[owner] = LensNode(label="Person", name=owner)
            if name and owner:
                edges.append(LensEdge(source=name, target=owner, type="ASSIGNED_TO"))
        return LensResult(
            lens="ownership_map",
            nodes=list(seen_nodes.values()),
            edges=edges,
            params={"domain": domain},
        )

    def _lens_domain_boundaries(self, domain: str = "", **_kw: Any) -> LensResult:
        cypher = (
            "MATCH (a)-[:CALLS]->(b) "
            "WHERE (a)-[:BELONGS_TO]->(:Domain) AND (b)-[:BELONGS_TO]->(:Domain) "
            "AND ($domain = '' OR (a)-[:BELONGS_TO]->(:Domain {name: $domain})) "
            "WITH a, b "
            "MATCH (a)-[:BELONGS_TO]->(da:Domain) "
            "MATCH (b)-[:BELONGS_TO]->(db:Domain) "
            "WHERE da.name <> db.name "
            "RETURN DISTINCT labels(a)[0], a.name, coalesce(a.file_path,''), da.name, "
            "labels(b)[0], b.name, coalesce(b.file_path,''), db.name LIMIT 100"
        )
        rows = self._query(cypher, {"domain": domain})
        seen_nodes: dict[str, LensNode] = {}
        edges: list[LensEdge] = []
        for row in rows:
            a_label = row[0] or ""
            a_name = row[1] or ""
            a_file = row[2] or ""
            a_domain = row[3] or ""
            b_label = row[4] or ""
            b_name = row[5] or ""
            b_file = row[6] or ""
            b_domain = row[7] or ""
            if a_name and a_name not in seen_nodes:
                seen_nodes[a_name] = LensNode(
                    label=a_label, name=a_name, file_path=a_file, domain=a_domain
                )
            if b_name and b_name not in seen_nodes:
                seen_nodes[b_name] = LensNode(
                    label=b_label, name=b_name, file_path=b_file, domain=b_domain
                )
            if a_name and b_name:
                edges.append(LensEdge(source=a_name, target=b_name, type="CALLS"))
        return LensResult(
            lens="domain_boundaries",
            nodes=list(seen_nodes.values()),
            edges=edges,
            params={"domain": domain},
        )

    def _lens_dependency_layers(self, file_path: str = "", **_kw: Any) -> LensResult:
        cypher = (
            "MATCH (a)-[:IMPORTS|DEPENDS_ON]->(b) "
            "WHERE ($file_path = '' OR a.file_path = $file_path OR b.file_path = $file_path) "
            "RETURN DISTINCT labels(a)[0], a.name, coalesce(a.file_path,''), "
            "labels(b)[0], b.name, coalesce(b.file_path,'') LIMIT 200"
        )
        rows = self._query(cypher, {"file_path": file_path})
        nodes, edges = self._build_pair_graph(rows, edge_type="IMPORTS")
        return LensResult(
            lens="dependency_layers",
            nodes=nodes,
            edges=edges,
            params={"file_path": file_path},
        )

    def _lens_framework_components(self, label: str = "", **_kw: Any) -> LensResult:
        cypher = (
            "MATCH (n) "
            "WHERE ($label = '' OR labels(n)[0] = $label) "
            "AND (n:Controller OR n:Service OR n:Repository OR n:Model "
            "OR n:Middleware OR n:Serializer OR n:View OR n:Handler) "
            "RETURN DISTINCT labels(n)[0], n.name, coalesce(n.file_path,'') LIMIT 200"
        )
        rows = self._query(cypher, {"label": label})
        seen_nodes: dict[str, LensNode] = {}
        for row in rows:
            r_label, name, file_path = row[0] or "", row[1] or "", row[2] or ""
            if name and name not in seen_nodes:
                seen_nodes[name] = LensNode(label=r_label, name=name, file_path=file_path)
        return LensResult(
            lens="framework_components",
            nodes=list(seen_nodes.values()),
            edges=[],
            params={"label": label},
        )

    # ── Custom lens execution ────────────────────────────────────────────────

    def _apply_custom(self, lens: str, **params: Any) -> LensResult:
        info = self._custom[lens]
        rows = self._query(info["cypher"], params)
        seen_nodes: dict[str, LensNode] = {}
        for row in rows:
            r_label = row[0] if len(row) > 0 else ""
            name = row[1] if len(row) > 1 else ""
            file_path = row[2] if len(row) > 2 else ""
            r_label = r_label or ""
            name = name or ""
            file_path = file_path or ""
            if name and name not in seen_nodes:
                seen_nodes[name] = LensNode(label=r_label, name=name, file_path=file_path)
        return LensResult(
            lens=lens,
            nodes=list(seen_nodes.values()),
            edges=[],
            params=params,
        )

    # ── Shared helpers ───────────────────────────────────────────────────────

    def _query(self, cypher: str, params: dict[str, Any] | None = None) -> list:
        try:
            result = self.store.query(cypher, params or {})
            return result.result_set or []
        except Exception:
            return []

    def _build_pair_graph(
        self,
        rows: list,
        edge_type: str = "CALLS",
    ) -> tuple[list[LensNode], list[LensEdge]]:
        """Build nodes/edges from rows: [a_label, a_name, a_file, b_label, b_name, b_file]."""
        seen_nodes: dict[str, LensNode] = {}
        edges: list[LensEdge] = []
        for row in rows:
            a_label = row[0] or ""
            a_name = row[1] or ""
            a_file = row[2] or ""
            b_label = row[3] or ""
            b_name = row[4] or ""
            b_file = row[5] or ""
            if a_name and a_name not in seen_nodes:
                seen_nodes[a_name] = LensNode(label=a_label, name=a_name, file_path=a_file)
            if b_name and b_name not in seen_nodes:
                seen_nodes[b_name] = LensNode(label=b_label, name=b_name, file_path=b_file)
            if a_name and b_name:
                edges.append(LensEdge(source=a_name, target=b_name, type=edge_type))
        return list(seen_nodes.values()), edges
