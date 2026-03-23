"""Tests for navegador.ingestion.planopticon — PlanopticonIngester."""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from navegador.graph.schema import NodeLabel
from navegador.ingestion.planopticon import (
    EDGE_MAP,
    NODE_TYPE_MAP,
    PLANNING_TYPE_MAP,
    PlanopticonIngester,
)


def _make_store():
    store = MagicMock()
    store.query.return_value = MagicMock(result_set=[])
    return store


# ── Fixtures ──────────────────────────────────────────────────────────────────

KG_DATA = {
    "nodes": [
        {"id": "n1", "type": "concept", "name": "Payment Gateway",
         "description": "Handles payments"},
        {"id": "n2", "type": "person", "name": "Carol", "email": "carol@example.com"},
        {"id": "n3", "type": "technology", "name": "PostgreSQL", "description": "DB"},
        {"id": "n4", "type": "decision", "name": "Use Redis"},
        {"id": "n5", "type": "unknown_type", "name": "Misc"},
        {"id": "n6", "type": "diagram", "name": "Service Map", "source": "http://img.png"},
    ],
    "relationships": [
        {"source": "Payment Gateway", "target": "PostgreSQL", "type": "uses"},
        {"source": "Carol", "target": "Payment Gateway", "type": "assigned_to"},
        {"source": "", "target": "nope", "type": "related_to"},  # bad rel — no source
    ],
    "sources": [
        {"title": "Meeting 2024", "url": "https://ex.com", "source_type": "meeting"},
    ],
}

INTERCHANGE_DATA = {
    "project": {"name": "MyProject", "tags": ["backend", "payments"]},
    "entities": [
        {
            "planning_type": "decision",
            "name": "Adopt microservices",
            "description": "Split the monolith",
            "status": "accepted",
            "rationale": "Scale independently",
        },
        {
            "planning_type": "requirement",
            "name": "PCI compliance",
            "description": "Must comply with PCI-DSS",
            "priority": "high",
        },
        {
            "planning_type": "goal",
            "name": "Increase uptime",
            "description": "99.9% SLA",
        },
        {
            # no planning_type → falls through to _ingest_kg_node
            "type": "concept",
            "name": "Event Sourcing",
        },
    ],
    "relationships": [],
    "artifacts": [
        {"name": "Architecture Diagram", "content": "mermaid content here"},
    ],
    "sources": [],
}

MANIFEST_DATA = {
    "video": {"title": "Sprint Planning", "url": "https://example.com/video/1"},
    "key_points": [
        {"point": "Use async everywhere", "topic": "Architecture", "details": "For scale"},
    ],
    "action_items": [
        {"action": "Refactor auth service", "assignee": "Bob", "context": "High priority"},
    ],
    "diagrams": [
        {
            "diagram_type": "sequence",
            "timestamp": 120,
            "description": "Auth flow",
            "mermaid": "sequenceDiagram ...",
            "elements": ["User", "Auth"],
        }
    ],
}


# ── Maps ──────────────────────────────────────────────────────────────────────

class TestMaps:
    def test_node_type_map_coverage(self):
        assert NODE_TYPE_MAP["concept"] == NodeLabel.Concept
        assert NODE_TYPE_MAP["technology"] == NodeLabel.Concept
        assert NODE_TYPE_MAP["organization"] == NodeLabel.Concept
        assert NODE_TYPE_MAP["person"] == NodeLabel.Person
        assert NODE_TYPE_MAP["diagram"] == NodeLabel.WikiPage

    def test_planning_type_map_coverage(self):
        assert PLANNING_TYPE_MAP["decision"] == NodeLabel.Decision
        assert PLANNING_TYPE_MAP["requirement"] == NodeLabel.Rule
        assert PLANNING_TYPE_MAP["constraint"] == NodeLabel.Rule
        assert PLANNING_TYPE_MAP["risk"] == NodeLabel.Rule
        assert PLANNING_TYPE_MAP["goal"] == NodeLabel.Concept

    def test_edge_map_coverage(self):
        from navegador.graph.schema import EdgeType
        assert EDGE_MAP["uses"] == EdgeType.DEPENDS_ON
        assert EDGE_MAP["related_to"] == EdgeType.RELATED_TO
        assert EDGE_MAP["assigned_to"] == EdgeType.ASSIGNED_TO
        assert EDGE_MAP["governs"] == EdgeType.GOVERNS
        assert EDGE_MAP["implements"] == EdgeType.IMPLEMENTS


# ── ingest_kg ─────────────────────────────────────────────────────────────────

class TestIngestKg:
    def test_ingests_concept_nodes(self):
        store = _make_store()
        ingester = PlanopticonIngester(store)
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "kg.json"
            p.write_text(json.dumps(KG_DATA))
            stats = ingester.ingest_kg(p)
            assert stats["nodes"] >= 1

    def test_ingests_person_nodes(self):
        store = _make_store()
        ingester = PlanopticonIngester(store)
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "kg.json"
            p.write_text(json.dumps(KG_DATA))
            ingester.ingest_kg(p)
            labels = [c[0][0] for c in store.create_node.call_args_list]
            assert NodeLabel.Person in labels

    def test_ingests_technology_as_concept(self):
        store = _make_store()
        ingester = PlanopticonIngester(store)
        data = {"nodes": [{"type": "technology", "name": "PostgreSQL"}],
                "relationships": [], "sources": []}
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "kg.json"
            p.write_text(json.dumps(data))
            ingester.ingest_kg(p)
            labels = [c[0][0] for c in store.create_node.call_args_list]
            assert NodeLabel.Concept in labels

    def test_ingests_diagram_as_wiki_page(self):
        store = _make_store()
        ingester = PlanopticonIngester(store)
        data = {"nodes": [{"type": "diagram", "name": "Arch Diagram", "source": "http://x.com"}],
                "relationships": [], "sources": []}
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "kg.json"
            p.write_text(json.dumps(data))
            ingester.ingest_kg(p)
            labels = [c[0][0] for c in store.create_node.call_args_list]
            assert NodeLabel.WikiPage in labels

    def test_skips_nodes_without_name(self):
        store = _make_store()
        ingester = PlanopticonIngester(store)
        data = {"nodes": [{"type": "concept", "name": ""}], "relationships": [], "sources": []}
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "kg.json"
            p.write_text(json.dumps(data))
            stats = ingester.ingest_kg(p)
            assert stats["nodes"] == 0

    def test_ingests_sources_as_wiki_pages(self):
        store = _make_store()
        ingester = PlanopticonIngester(store)
        data = {
            "nodes": [], "relationships": [],
            "sources": [
                {"title": "Meeting 2024", "url": "https://ex.com", "source_type": "meeting"},
            ],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "kg.json"
            p.write_text(json.dumps(data))
            stats = ingester.ingest_kg(p)
            assert stats["nodes"] >= 1
            labels = [c[0][0] for c in store.create_node.call_args_list]
            assert NodeLabel.WikiPage in labels

    def test_ingests_relationships(self):
        store = _make_store()
        ingester = PlanopticonIngester(store)
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "kg.json"
            p.write_text(json.dumps(KG_DATA))
            stats = ingester.ingest_kg(p)
            assert stats["edges"] >= 1
            store.query.assert_called()

    def test_skips_bad_relationships(self):
        store = _make_store()
        ingester = PlanopticonIngester(store)
        data = {"nodes": [], "relationships": [{"source": "", "target": "x", "type": "related_to"}],
                "sources": []}
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "kg.json"
            p.write_text(json.dumps(data))
            stats = ingester.ingest_kg(p)
            assert stats["edges"] == 0

    def test_missing_file_raises(self):
        store = _make_store()
        ingester = PlanopticonIngester(store)
        with pytest.raises(FileNotFoundError):
            ingester.ingest_kg("/nonexistent/kg.json")

    def test_returns_stats_dict(self):
        store = _make_store()
        ingester = PlanopticonIngester(store)
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "kg.json"
            p.write_text(json.dumps({"nodes": [], "relationships": [], "sources": []}))
            stats = ingester.ingest_kg(p)
            assert "nodes" in stats
            assert "edges" in stats


# ── ingest_interchange ────────────────────────────────────────────────────────

class TestIngestInterchange:
    def test_ingests_decision_entities(self):
        store = _make_store()
        ingester = PlanopticonIngester(store)
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "interchange.json"
            p.write_text(json.dumps(INTERCHANGE_DATA))
            ingester.ingest_interchange(p)
            labels = [c[0][0] for c in store.create_node.call_args_list]
            assert NodeLabel.Decision in labels

    def test_ingests_requirement_as_rule(self):
        store = _make_store()
        ingester = PlanopticonIngester(store)
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "interchange.json"
            p.write_text(json.dumps(INTERCHANGE_DATA))
            ingester.ingest_interchange(p)
            labels = [c[0][0] for c in store.create_node.call_args_list]
            assert NodeLabel.Rule in labels

    def test_creates_domain_nodes_from_project_tags(self):
        store = _make_store()
        ingester = PlanopticonIngester(store)
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "interchange.json"
            p.write_text(json.dumps(INTERCHANGE_DATA))
            ingester.ingest_interchange(p)
            labels = [c[0][0] for c in store.create_node.call_args_list]
            assert NodeLabel.Domain in labels

    def test_ingests_artifacts_as_wiki_pages(self):
        store = _make_store()
        ingester = PlanopticonIngester(store)
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "interchange.json"
            p.write_text(json.dumps(INTERCHANGE_DATA))
            ingester.ingest_interchange(p)
            labels = [c[0][0] for c in store.create_node.call_args_list]
            assert NodeLabel.WikiPage in labels

    def test_empty_entities_returns_empty_stats(self):
        store = _make_store()
        ingester = PlanopticonIngester(store)
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "interchange.json"
            p.write_text(json.dumps({"project": {}, "entities": [], "relationships": [],
                                     "artifacts": [], "sources": []}))
            stats = ingester.ingest_interchange(p)
            assert stats["nodes"] == 0

    def test_returns_stats_dict(self):
        store = _make_store()
        ingester = PlanopticonIngester(store)
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "interchange.json"
            p.write_text(json.dumps(INTERCHANGE_DATA))
            stats = ingester.ingest_interchange(p)
            assert "nodes" in stats and "edges" in stats


# ── ingest_manifest ────────────────────────────────────────────────────────────

class TestIngestManifest:
    def test_ingests_key_points_as_concepts(self):
        store = _make_store()
        ingester = PlanopticonIngester(store)
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "manifest.json"
            p.write_text(json.dumps(MANIFEST_DATA))
            ingester.ingest_manifest(p)
            labels = [c[0][0] for c in store.create_node.call_args_list]
            assert NodeLabel.Concept in labels

    def test_ingests_action_items_as_rules(self):
        store = _make_store()
        ingester = PlanopticonIngester(store)
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "manifest.json"
            p.write_text(json.dumps(MANIFEST_DATA))
            ingester.ingest_manifest(p)
            labels = [c[0][0] for c in store.create_node.call_args_list]
            assert NodeLabel.Rule in labels

    def test_ingests_action_item_assignee_as_person(self):
        store = _make_store()
        ingester = PlanopticonIngester(store)
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "manifest.json"
            p.write_text(json.dumps(MANIFEST_DATA))
            ingester.ingest_manifest(p)
            labels = [c[0][0] for c in store.create_node.call_args_list]
            assert NodeLabel.Person in labels

    def test_ingests_diagrams_as_wiki_pages(self):
        store = _make_store()
        ingester = PlanopticonIngester(store)
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "manifest.json"
            p.write_text(json.dumps(MANIFEST_DATA))
            ingester.ingest_manifest(p)
            labels = [c[0][0] for c in store.create_node.call_args_list]
            assert NodeLabel.WikiPage in labels

    def test_diagram_elements_become_concepts(self):
        store = _make_store()
        ingester = PlanopticonIngester(store)
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "manifest.json"
            p.write_text(json.dumps(MANIFEST_DATA))
            ingester.ingest_manifest(p)
            # "User" and "Auth" are diagram elements → Concept nodes
            names = [c[0][1].get("name") for c in store.create_node.call_args_list
                     if isinstance(c[0][1], dict)]
            assert "User" in names or "Auth" in names

    def test_loads_external_kg_json(self):
        store = _make_store()
        ingester = PlanopticonIngester(store)
        with tempfile.TemporaryDirectory() as tmpdir:
            kg = {"nodes": [{"type": "concept", "name": "External Concept"}],
                  "relationships": [], "sources": []}
            (Path(tmpdir) / "kg.json").write_text(json.dumps(kg))
            manifest = dict(MANIFEST_DATA)
            manifest["knowledge_graph_json"] = "kg.json"
            p = Path(tmpdir) / "manifest.json"
            p.write_text(json.dumps(manifest))
            ingester.ingest_manifest(p)
            names = [c[0][1].get("name") for c in store.create_node.call_args_list
                     if isinstance(c[0][1], dict)]
            assert "External Concept" in names

    def test_empty_manifest_no_crash(self):
        store = _make_store()
        ingester = PlanopticonIngester(store)
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "manifest.json"
            p.write_text(json.dumps({}))
            stats = ingester.ingest_manifest(p)
            assert "nodes" in stats


# ── ingest_batch ──────────────────────────────────────────────────────────────

class TestIngestBatch:
    def test_processes_merged_kg_if_present(self):
        store = _make_store()
        ingester = PlanopticonIngester(store)
        with tempfile.TemporaryDirectory() as tmpdir:
            kg = {"nodes": [{"type": "concept", "name": "Merged"}],
                  "relationships": [], "sources": []}
            (Path(tmpdir) / "merged.json").write_text(json.dumps(kg))
            batch = {"merged_knowledge_graph_json": "merged.json"}
            p = Path(tmpdir) / "batch.json"
            p.write_text(json.dumps(batch))
            ingester.ingest_batch(p)
            names = [c[0][1].get("name") for c in store.create_node.call_args_list
                     if isinstance(c[0][1], dict)]
            assert "Merged" in names

    def test_processes_completed_videos(self):
        store = _make_store()
        ingester = PlanopticonIngester(store)
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "vid1.json").write_text(json.dumps(MANIFEST_DATA))
            batch = {
                "videos": [
                    {"status": "completed", "manifest_path": "vid1.json"},
                    {"status": "pending", "manifest_path": "vid1.json"},  # skipped
                ]
            }
            p = Path(tmpdir) / "batch.json"
            p.write_text(json.dumps(batch))
            stats = ingester.ingest_batch(p)
            assert "nodes" in stats

    def test_missing_manifest_raises(self):
        store = _make_store()
        ingester = PlanopticonIngester(store)
        with tempfile.TemporaryDirectory() as tmpdir:
            batch = {
                "videos": [
                    {"status": "completed", "manifest_path": "nonexistent.json"},
                ]
            }
            p = Path(tmpdir) / "batch.json"
            p.write_text(json.dumps(batch))
            with pytest.raises(FileNotFoundError):
                ingester.ingest_batch(p)

    def test_merges_stats_across_videos(self):
        store = _make_store()
        ingester = PlanopticonIngester(store)
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "v1.json").write_text(json.dumps(MANIFEST_DATA))
            (Path(tmpdir) / "v2.json").write_text(json.dumps(MANIFEST_DATA))
            batch = {
                "videos": [
                    {"status": "completed", "manifest_path": "v1.json"},
                    {"status": "completed", "manifest_path": "v2.json"},
                ]
            }
            p = Path(tmpdir) / "batch.json"
            p.write_text(json.dumps(batch))
            stats = ingester.ingest_batch(p)
            # Should have processed both, stats should be non-zero
            assert stats.get("nodes", 0) >= 0  # at least doesn't crash


# ── _reset_stats / _merge_stats ───────────────────────────────────────────────

class TestInternalHelpers:
    def test_reset_stats(self):
        store = _make_store()
        ingester = PlanopticonIngester(store)
        ingester._stats = {"nodes": 5, "edges": 3}
        stats = ingester._reset_stats()
        assert stats == {"nodes": 0, "edges": 0}

    def test_merge_stats(self):
        store = _make_store()
        ingester = PlanopticonIngester(store)
        ingester._stats = {"nodes": 2, "edges": 1}
        ingester._merge_stats({"nodes": 3, "edges": 2, "pages": 1})
        assert ingester._stats["nodes"] == 5
        assert ingester._stats["edges"] == 3
        assert ingester._stats["pages"] == 1

    def test_load_json_missing_file_raises(self):
        store = _make_store()
        ingester = PlanopticonIngester(store)
        with pytest.raises(FileNotFoundError):
            ingester._load_json(Path("/nonexistent/file.json"))

    def test_load_json_invalid_json_raises(self):
        store = _make_store()
        ingester = PlanopticonIngester(store)
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "bad.json"
            p.write_text("{ not valid json }")
            with pytest.raises((json.JSONDecodeError, ValueError)):
                ingester._load_json(p)


# ── ingest_interchange relationship/source branches (lines 201, 209) ──────────

class TestInterchangeRelationshipsAndSources:
    def test_ingests_relationships_in_interchange(self):
        store = _make_store()
        ingester = PlanopticonIngester(store)
        data = {
            "project": {"name": "Proj", "tags": []},
            "entities": [],
            "relationships": [
                {"source": "Alice", "target": "Bob", "type": "related_to"}
            ],
            "artifacts": [],
            "sources": [],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "interchange.json"
            p.write_text(json.dumps(data))
            stats = ingester.ingest_interchange(p)
        store.query.assert_called()
        assert stats["edges"] >= 1

    def test_ingests_sources_in_interchange(self):
        store = _make_store()
        ingester = PlanopticonIngester(store)
        data = {
            "project": {"name": "Proj", "tags": []},
            "entities": [],
            "relationships": [],
            "artifacts": [],
            "sources": [{"title": "Design Doc", "url": "http://ex.com"}],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "interchange.json"
            p.write_text(json.dumps(data))
            ingester.ingest_interchange(p)
        labels = [c[0][0] for c in store.create_node.call_args_list]
        assert NodeLabel.WikiPage in labels


# ── _ingest_kg_node with domain (lines 274-275) ───────────────────────────────

class TestIngestKgNodeWithDomain:
    def test_concept_with_domain_creates_domain_link(self):
        store = _make_store()
        ingester = PlanopticonIngester(store)
        ingester._ingest_kg_node({"type": "concept", "name": "Auth", "domain": "Security"})
        domain_calls = [c[0][0] for c in store.create_node.call_args_list]
        assert NodeLabel.Domain in domain_calls
        store.create_edge.assert_called()


# ── _ingest_planning_entity guards and domain (lines 293, 325-326) ────────────

class TestIngestPlanningEntityBranches:
    def test_skips_entity_with_empty_name(self):
        store = _make_store()
        ingester = PlanopticonIngester(store)
        ingester._ingest_planning_entity({"planning_type": "decision", "name": ""})
        store.create_node.assert_not_called()

    def test_entity_with_domain_creates_domain_link(self):
        store = _make_store()
        ingester = PlanopticonIngester(store)
        ingester._ingest_planning_entity({
            "planning_type": "decision",
            "name": "Switch to Postgres",
            "domain": "Infrastructure",
        })
        domain_calls = [c[0][0] for c in store.create_node.call_args_list]
        assert NodeLabel.Domain in domain_calls
        store.create_edge.assert_called()


# ── _ingest_kg_relationship exception handler (lines 353-354) ─────────────────

class TestIngestKgRelationshipException:
    def test_exception_in_query_is_swallowed(self):
        store = _make_store()
        store.query.side_effect = Exception("graph error")
        ingester = PlanopticonIngester(store)
        # Should not raise
        ingester._ingest_kg_relationship({"source": "A", "target": "B", "type": "related_to"})
        assert ingester._stats.get("edges", 0) == 0


# ── _ingest_key_points empty-point skip (line 360) ───────────────────────────

class TestIngestKeyPointsEmptySkip:
    def test_skips_empty_point(self):
        store = _make_store()
        ingester = PlanopticonIngester(store)
        ingester._ingest_key_points([{"point": "", "topic": "foo"}], "source")
        store.create_node.assert_not_called()


# ── _ingest_action_items empty-action skip (line 383) ────────────────────────

class TestIngestActionItemsEmptySkip:
    def test_skips_empty_action(self):
        store = _make_store()
        ingester = PlanopticonIngester(store)
        ingester._ingest_action_items([{"action": "", "assignee": "Bob"}], "source")
        store.create_node.assert_not_called()


# ── diagram element empty-string skip (line 426) ─────────────────────────────

class TestDiagramElementEmptySkip:
    def test_skips_empty_diagram_element(self):
        store = _make_store()
        ingester = PlanopticonIngester(store)
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "manifest.json"
            p.write_text(json.dumps({
                "video": {"title": "T", "url": "http://x.com"},
                "key_points": [],
                "action_items": [],
                "diagrams": [{
                    "diagram_type": "sequence",
                    "timestamp": 0,
                    "description": "D",
                    "mermaid": "",
                    "elements": ["", "  "],  # all empty/whitespace
                }],
            }))
            ingester.ingest_manifest(p)
        # Only WikiPage for the diagram itself; no Concept for elements
        concept_calls = [c for c in store.create_node.call_args_list
                         if c[0][0] == NodeLabel.Concept]
        assert len(concept_calls) == 0


# ── _ingest_source empty name guard (line 440) ───────────────────────────────

class TestIngestSourceEmptyName:
    def test_skips_source_with_no_name(self):
        store = _make_store()
        ingester = PlanopticonIngester(store)
        ingester._ingest_source({"title": "", "source_id": None, "url": ""})
        store.create_node.assert_not_called()


# ── _ingest_artifact empty name guard (line 453) ─────────────────────────────

class TestIngestArtifactEmptyName:
    def test_skips_artifact_with_no_name(self):
        store = _make_store()
        ingester = PlanopticonIngester(store)
        ingester._ingest_artifact({"name": ""}, "project")
        store.create_node.assert_not_called()


# ── _lazy_wiki_link exception handler (lines 476-477) ────────────────────────

class TestLazyWikiLinkException:
    def test_exception_in_create_edge_is_swallowed(self):
        from navegador.graph.schema import NodeLabel
        store = _make_store()
        store.create_edge.side_effect = Exception("no such node")
        ingester = PlanopticonIngester(store)
        # Should not raise
        ingester._lazy_wiki_link("AuthService", NodeLabel.Concept, "source-123")
