"""Tests for graph schema — node labels, edge types, and node properties."""

from navegador.graph.schema import NODE_PROPS, EdgeType, NodeLabel


class TestNodeLabel:
    def test_code_labels(self):
        assert NodeLabel.Repository == "Repository"
        assert NodeLabel.File == "File"
        assert NodeLabel.Module == "Module"
        assert NodeLabel.Class == "Class"
        assert NodeLabel.Function == "Function"
        assert NodeLabel.Method == "Method"
        assert NodeLabel.Variable == "Variable"
        assert NodeLabel.Import == "Import"
        assert NodeLabel.Decorator == "Decorator"

    def test_knowledge_labels(self):
        assert NodeLabel.Domain == "Domain"
        assert NodeLabel.Concept == "Concept"
        assert NodeLabel.Rule == "Rule"
        assert NodeLabel.Decision == "Decision"
        assert NodeLabel.WikiPage == "WikiPage"
        assert NodeLabel.Person == "Person"

    def test_is_str(self):
        assert isinstance(NodeLabel.Function, str)

    def test_total_count(self):
        assert len(set(NodeLabel)) == 18  # 9 code + 7 knowledge + 1 tracker (Ticket) + 1 history (Snapshot)


class TestEdgeType:
    def test_code_edges(self):
        assert EdgeType.CONTAINS == "CONTAINS"
        assert EdgeType.DEFINES == "DEFINES"
        assert EdgeType.IMPORTS == "IMPORTS"
        assert EdgeType.DEPENDS_ON == "DEPENDS_ON"
        assert EdgeType.CALLS == "CALLS"
        assert EdgeType.REFERENCES == "REFERENCES"
        assert EdgeType.INHERITS == "INHERITS"
        assert EdgeType.IMPLEMENTS == "IMPLEMENTS"
        assert EdgeType.DECORATES == "DECORATES"

    def test_knowledge_edges(self):
        assert EdgeType.BELONGS_TO == "BELONGS_TO"
        assert EdgeType.RELATED_TO == "RELATED_TO"
        assert EdgeType.GOVERNS == "GOVERNS"
        assert EdgeType.DOCUMENTS == "DOCUMENTS"
        assert EdgeType.ANNOTATES == "ANNOTATES"
        assert EdgeType.ASSIGNED_TO == "ASSIGNED_TO"
        assert EdgeType.DECIDED_BY == "DECIDED_BY"

    def test_total_count(self):
        assert len(set(EdgeType)) == 17  # 9 code + 7 knowledge + 1 history (SNAPSHOT_OF)


class TestNodeProps:
    def test_all_labels_have_props(self):
        for label in NodeLabel:
            assert label in NODE_PROPS, f"Missing NODE_PROPS entry for {label}"

    def test_function_props(self):
        assert "signature" in NODE_PROPS[NodeLabel.Function]
        assert "docstring" in NODE_PROPS[NodeLabel.Function]

    def test_concept_props(self):
        assert "description" in NODE_PROPS[NodeLabel.Concept]
        assert "domain" in NODE_PROPS[NodeLabel.Concept]

    def test_rule_props(self):
        assert "severity" in NODE_PROPS[NodeLabel.Rule]
        assert "rationale" in NODE_PROPS[NodeLabel.Rule]

    def test_decision_props(self):
        assert "status" in NODE_PROPS[NodeLabel.Decision]
        assert "rationale" in NODE_PROPS[NodeLabel.Decision]
        assert "alternatives" in NODE_PROPS[NodeLabel.Decision]

    def test_person_props(self):
        assert "role" in NODE_PROPS[NodeLabel.Person]
        assert "email" in NODE_PROPS[NodeLabel.Person]

    def test_wiki_props(self):
        assert "source" in NODE_PROPS[NodeLabel.WikiPage]
        assert "content" in NODE_PROPS[NodeLabel.WikiPage]
