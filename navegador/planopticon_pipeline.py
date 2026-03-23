"""
PlanopticonPipeline — first-class pipeline for meeting recordings → knowledge graph.

Orchestrates: detect input type → ingest → link to code → return stats.

Issues: #7 (first-class pipeline), #18 (action items, decision timeline, auto-linking)

Usage::

    from navegador.planopticon_pipeline import PlanopticonPipeline

    pipeline = PlanopticonPipeline(store)
    stats = pipeline.run("planopticon-output/", source_tag="Q4 Planning")

    items = pipeline.extract_action_items(kg_data)
    timeline = pipeline.build_decision_timeline(store)
    linked = pipeline.auto_link_to_code(store)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from navegador.graph.schema import EdgeType, NodeLabel
from navegador.graph.store import GraphStore

logger = logging.getLogger(__name__)


# ── Data models ───────────────────────────────────────────────────────────────


@dataclass
class ActionItem:
    """A single action item extracted from planopticon KG data."""

    action: str
    assignee: str = ""
    context: str = ""
    priority: str = "info"
    source: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "assignee": self.assignee,
            "context": self.context,
            "priority": self.priority,
            "source": self.source,
        }


# ── Pipeline ──────────────────────────────────────────────────────────────────


class PlanopticonPipeline:
    """
    Orchestrates the full planopticon → knowledge graph pipeline.

    Steps:
      1. Detect input type from path (manifest, interchange, batch, kg)
      2. Delegate to PlanopticonIngester
      3. Run auto-link step to connect knowledge nodes to code symbols
      4. Return merged stats
    """

    def __init__(self, store: GraphStore, source_tag: str = "") -> None:
        self.store = store
        self.source_tag = source_tag

    # ── Main entry point ──────────────────────────────────────────────────────

    def run(self, input_path: str | Path, source_tag: str = "") -> dict[str, Any]:
        """
        Ingest a planopticon output and auto-link knowledge to code.

        Parameters
        ----------
        input_path:
            Path to a manifest.json, interchange.json, batch manifest,
            knowledge_graph.json, or a planopticon output directory.
        source_tag:
            Optional label for provenance tracking (overrides self.source_tag).

        Returns
        -------
        dict with keys: nodes, edges, linked (code links created)
        """
        from navegador.ingestion.planopticon import PlanopticonIngester

        tag = source_tag or self.source_tag
        ingester = PlanopticonIngester(self.store, source_tag=tag)

        p = Path(input_path)
        input_type, resolved = self._detect_input(p)

        dispatch = {
            "manifest": ingester.ingest_manifest,
            "interchange": ingester.ingest_interchange,
            "batch": ingester.ingest_batch,
            "kg": ingester.ingest_kg,
        }
        stats = dispatch[input_type](resolved)

        # Auto-link newly ingested knowledge nodes to code symbols
        linked = self.auto_link_to_code(self.store)
        stats["linked"] = linked

        logger.info(
            "PlanopticonPipeline.run: type=%s nodes=%d edges=%d linked=%d",
            input_type,
            stats.get("nodes", 0),
            stats.get("edges", 0),
            linked,
        )
        return stats

    # ── Action items ──────────────────────────────────────────────────────────

    @staticmethod
    def extract_action_items(kg_data: dict[str, Any]) -> list[ActionItem]:
        """
        Extract action items from raw planopticon KG data dict.

        Looks at ``action_items`` (manifest format) as well as nodes whose
        planning_type is ``"task"`` or whose type is ``"action_item"``.

        Parameters
        ----------
        kg_data:
            A dict as loaded from manifest.json, interchange.json,
            knowledge_graph.json, or any combination that may contain an
            ``action_items`` list or ``entities``/``nodes`` with task types.
        """
        items: list[ActionItem] = []

        source = kg_data.get("video", {}).get("title", "") or kg_data.get(
            "project", {}
        ).get("name", "")

        # Explicit action_items list (manifest format)
        for raw in kg_data.get("action_items", []):
            action = (raw.get("action") or "").strip()
            if not action:
                continue
            items.append(
                ActionItem(
                    action=action,
                    assignee=(raw.get("assignee") or "").strip(),
                    context=raw.get("context", ""),
                    priority=raw.get("priority", "info"),
                    source=source,
                )
            )

        # Entities / nodes with task/action_item planning type
        for entity in kg_data.get("entities", []) + kg_data.get("nodes", []):
            ptype = entity.get("planning_type", "") or entity.get("type", "")
            if ptype not in ("task", "action_item"):
                continue
            name = (entity.get("name") or "").strip()
            if not name:
                continue
            items.append(
                ActionItem(
                    action=name,
                    assignee=(entity.get("assignee") or "").strip(),
                    context=entity.get("description", ""),
                    priority=entity.get("priority", "info"),
                    source=source,
                )
            )

        return items

    # ── Decision timeline ─────────────────────────────────────────────────────

    @staticmethod
    def build_decision_timeline(store: GraphStore) -> list[dict[str, Any]]:
        """
        Return all Decision nodes ordered chronologically by their ``date`` property.

        Nodes without a date are placed last, sorted by name.

        Returns
        -------
        list of dicts with keys: name, description, domain, status, rationale, date
        """
        cypher = (
            "MATCH (d:Decision) "
            "RETURN d.name, d.description, d.domain, d.status, d.rationale, d.date "
            "ORDER BY d.date, d.name"
        )
        try:
            result = store.query(cypher)
            rows = result.result_set or []
        except Exception:
            logger.warning("build_decision_timeline: query failed", exc_info=True)
            return []

        timeline = []
        for row in rows:
            timeline.append({
                "name": row[0] or "",
                "description": row[1] or "",
                "domain": row[2] or "",
                "status": row[3] or "",
                "rationale": row[4] or "",
                "date": row[5] or "",
            })
        return timeline

    # ── Auto-link to code ─────────────────────────────────────────────────────

    @staticmethod
    def auto_link_to_code(store: GraphStore) -> int:
        """
        Match knowledge nodes to code symbols by name similarity and create
        ANNOTATES edges.

        Strategy:
          For each Concept / Decision / Rule node, look for Function, Class, or
          Method nodes whose ``name`` contains a significant word from the
          knowledge node's name (case-insensitive, length ≥ 4 chars).

        Returns
        -------
        int — number of new ANNOTATES edges created
        """
        # Fetch all knowledge nodes
        knowledge_cypher = (
            "MATCH (k) "
            "WHERE k:Concept OR k:Decision OR k:Rule "
            "RETURN labels(k)[0], k.name"
        )
        code_cypher = (
            "MATCH (c) "
            "WHERE c:Function OR c:Class OR c:Method "
            "RETURN labels(c)[0], c.name"
        )

        try:
            k_result = store.query(knowledge_cypher)
            c_result = store.query(code_cypher)
        except Exception:
            logger.warning("auto_link_to_code: initial queries failed", exc_info=True)
            return 0

        knowledge_nodes: list[tuple[str, str]] = [
            (str(row[0]), str(row[1]))
            for row in (k_result.result_set or [])
            if row[0] and row[1]
        ]
        code_nodes: list[tuple[str, str]] = [
            (str(row[0]), str(row[1]))
            for row in (c_result.result_set or [])
            if row[0] and row[1]
        ]

        if not knowledge_nodes or not code_nodes:
            return 0

        linked = 0
        for k_label, k_name in knowledge_nodes:
            # Extract significant tokens (length >= 4) from the knowledge name
            tokens = [
                w.lower()
                for w in k_name.replace("_", " ").replace("-", " ").split()
                if len(w) >= 4
            ]
            if not tokens:
                continue

            for c_label, c_name in code_nodes:
                c_lower = c_name.lower()
                if any(tok in c_lower for tok in tokens):
                    # Create ANNOTATES edge from knowledge node to code node
                    cypher = (
                        "MATCH (k:"
                        + k_label
                        + " {name: $kn}), (c:"
                        + c_label
                        + " {name: $cn}) "
                        "MERGE (k)-[r:ANNOTATES]->(c)"
                    )
                    try:
                        store.query(cypher, {"kn": k_name, "cn": c_name})
                        linked += 1
                    except Exception:
                        logger.debug(
                            "auto_link_to_code: could not link %s → %s", k_name, c_name
                        )

        return linked

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _detect_input(p: Path) -> tuple[str, Path]:
        """
        Detect input type and resolve to a concrete file path.

        Returns
        -------
        (input_type, resolved_path) where input_type is one of:
        "manifest", "interchange", "batch", "kg"
        """
        if p.is_dir():
            candidates = [
                ("manifest", p / "manifest.json"),
                ("interchange", p / "interchange.json"),
                ("batch", p / "batch_manifest.json"),
                ("kg", p / "results" / "knowledge_graph.json"),
                ("kg", p / "knowledge_graph.json"),
            ]
            for itype, candidate in candidates:
                if candidate.exists():
                    return itype, candidate
            raise FileNotFoundError(
                f"No recognised planopticon file found in {p}. "
                "Expected manifest.json, interchange.json, "
                "batch_manifest.json, or knowledge_graph.json."
            )

        name = p.name.lower()
        if "manifest" in name and "batch" not in name:
            return "manifest", p
        if "interchange" in name:
            return "interchange", p
        if "batch" in name:
            return "batch", p
        # Default: treat as knowledge_graph.json
        return "kg", p
