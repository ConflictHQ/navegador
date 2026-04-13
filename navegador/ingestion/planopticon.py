"""
PlanopticonIngester — loads planopticon output into the navegador knowledge graph.

Planopticon extracts structured knowledge from videos, meetings, and documents:
entities, relationships, key points, action items, and diagrams. This ingester
maps that output onto navegador's knowledge layer so agents can query business
context alongside code.

Supported input:
  - manifest.json       (single video — primary entry point)
  - interchange.json    (canonical planopticon exchange format)
  - knowledge_graph.json (raw KG export, no manifest required)

Mapping:
  planopticon                   → navegador
  ─────────────────────────────────────────
  node type=person              → Person
  node type=concept/technology  → Concept
  node type=organization        → Concept  (domain = "organization")
  PlanningEntity type=decision  → Decision
  PlanningEntity type=requirement/constraint → Rule
  diagram                       → WikiPage  (content = mermaid/description)
  key_point                     → Concept   (tagged from source)
  action_item.assignee          → Person + ASSIGNED_TO edge
  relationship.type             → mapped EdgeType (see EDGE_MAP)

Usage:
    from navegador.ingestion.planopticon import PlanopticonIngester
    store = GraphStore.sqlite(".navegador/graph.db")
    ing = PlanopticonIngester(store)

    stats = ing.ingest_manifest("planopticon-output/manifest.json")
    stats = ing.ingest_kg("planopticon-output/results/knowledge_graph.json")
    stats = ing.ingest_interchange("planopticon-output/interchange.json")
"""

import json
import logging
from pathlib import Path
from typing import Any

from navegador.graph.schema import EdgeType, NodeLabel
from navegador.graph.store import GraphStore

logger = logging.getLogger(__name__)

# ── Relationship type mapping ─────────────────────────────────────────────────

EDGE_MAP: dict[str, EdgeType] = {
    "related_to": EdgeType.RELATED_TO,
    "uses": EdgeType.DEPENDS_ON,
    "depends_on": EdgeType.DEPENDS_ON,
    "built_on": EdgeType.DEPENDS_ON,
    "implements": EdgeType.IMPLEMENTS,
    "requires": EdgeType.DEPENDS_ON,
    "blocked_by": EdgeType.DEPENDS_ON,
    "has_risk": EdgeType.RELATED_TO,
    "addresses": EdgeType.RELATED_TO,
    "has_tradeoff": EdgeType.RELATED_TO,
    "delivers": EdgeType.IMPLEMENTS,
    "parent_of": EdgeType.CONTAINS,
    "assigned_to": EdgeType.ASSIGNED_TO,
    "owned_by": EdgeType.ASSIGNED_TO,
    "owns": EdgeType.ASSIGNED_TO,
    "employed_by": EdgeType.ASSIGNED_TO,
    "works_with": EdgeType.RELATED_TO,
    "governs": EdgeType.GOVERNS,
    "documents": EdgeType.DOCUMENTS,
}

# planopticon node type → navegador NodeLabel
NODE_TYPE_MAP: dict[str, NodeLabel] = {
    "concept": NodeLabel.Concept,
    "technology": NodeLabel.Concept,
    "organization": NodeLabel.Concept,
    "diagram": NodeLabel.WikiPage,
    "time": NodeLabel.Concept,
    "person": NodeLabel.Person,
}

# planning_type → navegador NodeLabel
PLANNING_TYPE_MAP: dict[str, NodeLabel] = {
    "decision": NodeLabel.Decision,
    "requirement": NodeLabel.Rule,
    "constraint": NodeLabel.Rule,
    "risk": NodeLabel.Rule,
    "goal": NodeLabel.Concept,
    "assumption": NodeLabel.Concept,
    "feature": NodeLabel.Concept,
    "milestone": NodeLabel.Concept,
    "task": NodeLabel.Concept,
    "dependency": NodeLabel.Concept,
}


def _manifest_input_type(manifest_path: Path) -> str:
    """
    Distinguish a single-run ``manifest.json`` from a batch ``manifest.json``.

    PlanOpticon now uses ``manifest.json`` for both single-video and batch
    outputs, so filename-based detection is not enough.
    """
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return "manifest"

    if not isinstance(data, dict):
        return "manifest"

    if (
        isinstance(data.get("videos"), list)
        or "merged_knowledge_graph_json" in data
        or "merged_knowledge_graph_db" in data
        or "total_videos" in data
        or "completed_videos" in data
        or "failed_videos" in data
    ):
        return "batch"

    return "manifest"


def resolve_planopticon_input(path: str | Path, input_type: str = "auto") -> tuple[str, Path]:
    """
    Resolve a PlanOpticon input path to a concrete file path and normalized type.

    Supports both the current batch ``manifest.json`` shape and the older
    ``batch_manifest.json`` filename.
    """
    p = Path(path)

    if p.is_dir():
        if input_type == "auto":
            manifest = p / "manifest.json"
            if manifest.exists():
                return _manifest_input_type(manifest), manifest

            candidates = [
                ("batch", p / "batch_manifest.json"),
                ("interchange", p / "exchange.json"),
                ("interchange", p / "interchange.json"),
                ("kg", p / "results" / "knowledge_graph.json"),
                ("kg", p / "knowledge_graph.json"),
            ]
            for detected_type, candidate in candidates:
                if candidate.exists():
                    return detected_type, candidate
            raise FileNotFoundError(
                f"No recognised planopticon file found in {p}. "
                "Expected manifest.json, exchange.json, interchange.json, "
                "knowledge_graph.json, or legacy batch_manifest.json."
            )

        if input_type == "manifest":
            candidate = p / "manifest.json"
            if candidate.exists():
                return input_type, candidate
        elif input_type == "batch":
            current = p / "manifest.json"
            if current.exists():
                return input_type, current
            legacy = p / "batch_manifest.json"
            if legacy.exists():
                return input_type, legacy
        elif input_type == "interchange":
            for candidate in (p / "exchange.json", p / "interchange.json"):
                if candidate.exists():
                    return input_type, candidate
        elif input_type == "kg":
            for candidate in (p / "results" / "knowledge_graph.json", p / "knowledge_graph.json"):
                if candidate.exists():
                    return input_type, candidate

        raise FileNotFoundError(
            f"Could not resolve planopticon {input_type} input from directory {p}"
        )

    if input_type != "auto":
        return input_type, p

    name = p.name.lower()
    if name == "manifest.json":
        return _manifest_input_type(p), p
    if "interchange" in name or "exchange" in name:
        return "interchange", p
    if "batch" in name:
        return "batch", p
    return "kg", p


class PlanopticonIngester:
    """
    Reads planopticon output and writes it into a GraphStore.

    All paths may be relative (resolved against the manifest's parent directory)
    or absolute.
    """

    def __init__(self, store: GraphStore, source_tag: str = "") -> None:
        self.store = store
        self.source_tag = source_tag  # optional label for provenance
        self._stats: dict[str, int] = {}

    # ── Entry points ──────────────────────────────────────────────────────────

    def ingest_manifest(self, manifest_path: str | Path) -> dict[str, int]:
        """
        Primary entry point — reads manifest.json and ingests everything:
        knowledge graph, key points, action items, and diagrams.
        """
        manifest_path = Path(manifest_path).resolve()
        base_dir = manifest_path.parent
        manifest = self._load_json(manifest_path)

        stats = self._reset_stats()
        title = manifest.get("video", {}).get("title", manifest_path.parent.name)
        self.source_tag = self.source_tag or title

        # 1. Knowledge graph (entities + relationships)
        kg_path = manifest.get("knowledge_graph_json")
        if kg_path:
            self.ingest_kg(base_dir / kg_path)

        # 2. Key points → Concept nodes
        kp_path = manifest.get("key_points_json")
        key_points = (
            self._load_json(base_dir / kp_path) if kp_path else manifest.get("key_points", [])
        )
        self._ingest_key_points(key_points, title)

        # 3. Action items → Person nodes + ASSIGNED_TO edges
        ai_path = manifest.get("action_items_json")
        action_items = (
            self._load_json(base_dir / ai_path) if ai_path else manifest.get("action_items", [])
        )
        self._ingest_action_items(action_items, title)

        # 4. Diagrams → WikiPage nodes
        for diagram in manifest.get("diagrams", []):
            self._ingest_diagram(diagram, base_dir, title)

        logger.info(
            "PlanopticonIngester (%s): nodes=%d edges=%d",
            title,
            stats.get("nodes", 0),
            stats.get("edges", 0),
        )
        return stats

    def ingest_kg(self, kg_path: str | Path) -> dict[str, int]:
        """
        Ingest a knowledge_graph.json (KnowledgeGraphData) directly.
        Can be used standalone without a manifest.
        """
        kg_path = Path(kg_path).resolve()
        data = self._load_json(kg_path)
        stats = self._reset_stats()

        for node in data.get("nodes", []):
            self._ingest_kg_node(node)

        for rel in data.get("relationships", []):
            self._ingest_kg_relationship(rel)

        # Ingest sources as WikiPage nodes
        for source in data.get("sources", []):
            self._ingest_source(source)

        return stats

    def ingest_interchange(self, interchange_path: str | Path) -> dict[str, int]:
        """
        Ingest a planopticon interchange.json (PlanOpticonExchange format).
        Includes planning taxonomy, artifacts, and full entity graph.
        """
        interchange_path = Path(interchange_path).resolve()
        data = self._load_json(interchange_path)
        stats = self._reset_stats()

        project = data.get("project", {})
        project_name = project.get("name", interchange_path.parent.name)
        self.source_tag = self.source_tag or project_name

        # Domain from project tags
        for tag in project.get("tags", []):
            self.store.create_node(NodeLabel.Domain, {"name": tag, "description": ""})

        # Entities (planning taxonomy takes priority over raw type)
        for entity in data.get("entities", []):
            planning_type = entity.get("planning_type")
            if planning_type and planning_type in PLANNING_TYPE_MAP:
                self._ingest_planning_entity(entity)
            else:
                self._ingest_kg_node(entity)

        # Relationships
        for rel in data.get("relationships", []):
            self._ingest_kg_relationship(rel)

        # Artifacts → WikiPage nodes
        for artifact in data.get("artifacts", []):
            self._ingest_artifact(artifact, project_name)

        # Sources
        for source in data.get("sources", []):
            self._ingest_source(source)

        return stats

    def ingest_batch(self, batch_manifest_path: str | Path) -> dict[str, int]:
        """
        Ingest a batch manifest — processes each video's manifest in turn,
        then the merged knowledge graph if present.
        """
        batch_manifest_path = Path(batch_manifest_path).resolve()
        base_dir = batch_manifest_path.parent
        batch = self._load_json(batch_manifest_path)
        stats = self._reset_stats()

        # Merged KG supersedes individual ones if present
        merged = batch.get("merged_knowledge_graph_json")
        if merged:
            sub = self.ingest_kg(base_dir / merged)
            self._merge_stats(sub)
        else:
            for video in batch.get("videos", []):
                if video.get("status") == "completed":
                    mp = video.get("manifest_path")
                    if mp:
                        sub = self.ingest_manifest(base_dir / mp)
                        self._merge_stats(sub)

        return stats

    # ── Node ingestion ────────────────────────────────────────────────────────

    def _ingest_kg_node(self, node: dict[str, Any]) -> None:
        raw_type = node.get("type", "concept")
        label = NODE_TYPE_MAP.get(raw_type, NodeLabel.Concept)

        name = (node.get("name") or node.get("id") or "").strip()
        if not name:
            return

        descriptions = node.get("descriptions", [])
        description = descriptions[0] if descriptions else node.get("description", "")

        if label == NodeLabel.Person:
            self.store.create_node(
                NodeLabel.Person,
                {
                    "name": name,
                    "email": "",
                    "role": node.get("role", ""),
                    "team": node.get("organization", ""),
                },
            )
        elif label == NodeLabel.WikiPage:
            self.store.create_node(
                NodeLabel.WikiPage,
                {
                    "name": name,
                    "url": node.get("source", ""),
                    "source": self.source_tag,
                    "content": description[:4000],
                },
            )
        else:
            domain = "organization" if raw_type == "organization" else node.get("domain", "")
            self.store.create_node(
                NodeLabel.Concept,
                {
                    "name": name,
                    "description": description,
                    "domain": domain,
                    "status": node.get("status", ""),
                },
            )
            if domain:
                self._ensure_domain(domain)
                self.store.create_edge(
                    NodeLabel.Concept,
                    {"name": name},
                    EdgeType.BELONGS_TO,
                    NodeLabel.Domain,
                    {"name": domain},
                )

        self._stats["nodes"] = self._stats.get("nodes", 0) + 1

        # Provenance: link to source WikiPage if present
        source_id = node.get("source")
        if source_id:
            self._lazy_wiki_link(name, label, source_id)

    def _ingest_planning_entity(self, entity: dict[str, Any]) -> None:
        planning_type = entity.get("planning_type", "concept")
        label = PLANNING_TYPE_MAP.get(planning_type, NodeLabel.Concept)
        name = (entity.get("name") or "").strip()
        if not name:
            return

        description = entity.get("description", "")
        domain = entity.get("domain", "")
        status = entity.get("status", "")
        priority = entity.get("priority", "")

        if label == NodeLabel.Decision:
            self.store.create_node(
                NodeLabel.Decision,
                {
                    "name": name,
                    "description": description,
                    "domain": domain,
                    "status": status or "accepted",
                    "rationale": entity.get("rationale", ""),
                },
            )
        elif label == NodeLabel.Rule:
            self.store.create_node(
                NodeLabel.Rule,
                {
                    "name": name,
                    "description": description,
                    "domain": domain,
                    "severity": "critical" if priority == "high" else "info",
                    "rationale": entity.get("rationale", ""),
                },
            )
        else:
            self.store.create_node(
                NodeLabel.Concept,
                {
                    "name": name,
                    "description": description,
                    "domain": domain,
                    "status": status,
                },
            )

        if domain:
            self._ensure_domain(domain)
            self.store.create_edge(
                label,
                {"name": name},
                EdgeType.BELONGS_TO,
                NodeLabel.Domain,
                {"name": domain},
            )

        self._stats["nodes"] = self._stats.get("nodes", 0) + 1

    def _ingest_kg_relationship(self, rel: dict[str, Any]) -> None:
        src = (rel.get("source") or "").strip()
        tgt = (rel.get("target") or "").strip()
        rel_type = (rel.get("type") or "related_to").lower().replace(" ", "_")

        if not src or not tgt:
            return

        edge_type = EDGE_MAP.get(rel_type, EdgeType.RELATED_TO)

        # We don't know the exact label of each node — use a label-agnostic match
        cypher = (
            """
        MATCH (a), (b)
        WHERE a.name = $src AND b.name = $tgt
        MERGE (a)-[r:"""
            + edge_type
            + """]->(b)
        """
        )
        try:
            self.store.query(cypher, {"src": src, "tgt": tgt})
            self._stats["edges"] = self._stats.get("edges", 0) + 1
        except Exception:
            logger.warning("Could not create edge %s -[%s]-> %s", src, edge_type, tgt)

    def _ingest_key_points(self, key_points: list[dict], source: str) -> None:
        for kp in key_points:
            point = (kp.get("point") or "").strip()
            if not point:
                continue
            topic = kp.get("topic") or ""
            name = point[:120]  # use the point text as the concept name
            self.store.create_node(
                NodeLabel.Concept,
                {
                    "name": name,
                    "description": kp.get("details", ""),
                    "domain": topic,
                    "status": "key_point",
                },
            )
            if topic:
                self._ensure_domain(topic)
                self.store.create_edge(
                    NodeLabel.Concept,
                    {"name": name},
                    EdgeType.BELONGS_TO,
                    NodeLabel.Domain,
                    {"name": topic},
                )
            self._stats["nodes"] = self._stats.get("nodes", 0) + 1

    def _ingest_action_items(self, action_items: list[dict], source: str) -> None:
        for item in action_items:
            action = (item.get("action") or "").strip()
            assignee = (item.get("assignee") or "").strip()
            if not action:
                continue

            # Action → Rule (it's a commitment / obligation)
            self.store.create_node(
                NodeLabel.Rule,
                {
                    "name": action[:120],
                    "description": item.get("context", ""),
                    "domain": source,
                    "severity": item.get("priority", "info"),
                    "rationale": f"Action item from {source}",
                },
            )
            self._stats["nodes"] = self._stats.get("nodes", 0) + 1

            if assignee:
                self.store.create_node(
                    NodeLabel.Person,
                    {
                        "name": assignee,
                        "email": "",
                        "role": "",
                        "team": "",
                    },
                )
                self.store.create_edge(
                    NodeLabel.Rule,
                    {"name": action[:120]},
                    EdgeType.ASSIGNED_TO,
                    NodeLabel.Person,
                    {"name": assignee},
                )
                self._stats["edges"] = self._stats.get("edges", 0) + 1

    def _ingest_diagram(self, diagram: dict[str, Any], base_dir: Path, source: str) -> None:
        dtype = diagram.get("diagram_type", "diagram")
        desc = diagram.get("description") or diagram.get("text_content") or ""
        mermaid = diagram.get("mermaid", "")
        ts = diagram.get("timestamp")
        name = f"{dtype.capitalize()} @ {ts:.0f}s" if ts is not None else f"{dtype.capitalize()}"

        content = mermaid or desc
        self.store.create_node(
            NodeLabel.WikiPage,
            {
                "name": name,
                "url": diagram.get("image_path", ""),
                "source": source,
                "content": content[:4000],
            },
        )
        self._stats["nodes"] = self._stats.get("nodes", 0) + 1

        # Link diagram elements as concepts
        for element in diagram.get("elements", []):
            element = element.strip()
            if not element:
                continue
            self.store.create_node(
                NodeLabel.Concept,
                {
                    "name": element,
                    "description": "",
                    "domain": source,
                    "status": "",
                },
            )
            self.store.create_edge(
                NodeLabel.WikiPage,
                {"name": name},
                EdgeType.DOCUMENTS,
                NodeLabel.Concept,
                {"name": element},
            )
            self._stats["edges"] = self._stats.get("edges", 0) + 1

    def _ingest_source(self, source: dict[str, Any]) -> None:
        name = (source.get("title") or source.get("source_id") or "").strip()
        if not name:
            return
        self.store.create_node(
            NodeLabel.WikiPage,
            {
                "name": name,
                "url": source.get("url") or source.get("path") or "",
                "source": source.get("source_type", ""),
                "content": "",
                "updated_at": source.get("ingested_at", ""),
            },
        )
        self._stats["nodes"] = self._stats.get("nodes", 0) + 1

    def _ingest_artifact(self, artifact: dict[str, Any], project_name: str) -> None:
        name = (artifact.get("name") or "").strip()
        if not name:
            return
        content = artifact.get("content", "")
        self.store.create_node(
            NodeLabel.WikiPage,
            {
                "name": name,
                "url": "",
                "source": project_name,
                "content": content[:4000],
            },
        )
        self._stats["nodes"] = self._stats.get("nodes", 0) + 1

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _ensure_domain(self, name: str) -> None:
        self.store.create_node(NodeLabel.Domain, {"name": name, "description": ""})

    def _lazy_wiki_link(self, name: str, label: NodeLabel, source_id: str) -> None:
        """Create a DOCUMENTS edge from a WikiPage to this node if the page exists."""
        try:
            self.store.create_edge(
                NodeLabel.WikiPage,
                {"name": source_id},
                EdgeType.DOCUMENTS,
                label,
                {"name": name},
            )
        except Exception:
            logger.debug("Could not link %s to wiki page %s", name, source_id)

    def _load_json(self, path: Path) -> Any:
        return json.loads(Path(path).read_text(encoding="utf-8"))

    def _reset_stats(self) -> dict[str, int]:
        self._stats = {"nodes": 0, "edges": 0}
        return self._stats

    def _merge_stats(self, other: dict[str, int]) -> None:
        for k, v in other.items():
            self._stats[k] = self._stats.get(k, 0) + v
