"""Tests for KnowledgeIngester — manual knowledge curation."""

from unittest.mock import MagicMock

import pytest

from navegador.graph.schema import EdgeType, NodeLabel
from navegador.ingestion.knowledge import KnowledgeIngester


def _mock_store():
    return MagicMock()


class TestKnowledgeIngesterDomains:
    def test_add_domain(self):
        store = _mock_store()
        k = KnowledgeIngester(store)
        k.add_domain("auth", description="Authentication layer")
        store.create_node.assert_called_once_with(
            NodeLabel.Domain, {"name": "auth", "description": "Authentication layer"}
        )

    def test_add_domain_no_description(self):
        store = _mock_store()
        k = KnowledgeIngester(store)
        k.add_domain("billing")
        store.create_node.assert_called_once()


class TestKnowledgeIngesterConcepts:
    def test_add_concept_minimal(self):
        store = _mock_store()
        k = KnowledgeIngester(store)
        k.add_concept("JWT")
        store.create_node.assert_called_once_with(
            NodeLabel.Concept,
            {"name": "JWT", "description": "", "domain": "", "status": "",
             "rules": "", "examples": "", "wiki_refs": ""}
        )

    def test_add_concept_with_domain_creates_edge(self):
        store = _mock_store()
        k = KnowledgeIngester(store)
        k.add_concept("JWT", domain="auth")
        # Should create Domain node + BELONGS_TO edge
        assert store.create_node.call_count == 2
        store.create_edge.assert_called_once()
        edge_call = store.create_edge.call_args
        assert edge_call[0][2] == EdgeType.BELONGS_TO

    def test_relate_concepts(self):
        store = _mock_store()
        k = KnowledgeIngester(store)
        k.relate_concepts("JWT", "OAuth")
        store.create_edge.assert_called_once_with(
            NodeLabel.Concept, {"name": "JWT"},
            EdgeType.RELATED_TO,
            NodeLabel.Concept, {"name": "OAuth"},
        )


class TestKnowledgeIngesterRules:
    def test_add_rule(self):
        store = _mock_store()
        k = KnowledgeIngester(store)
        k.add_rule("Tokens must expire", severity="critical")
        call_args = store.create_node.call_args[0]
        assert call_args[0] == NodeLabel.Rule
        assert call_args[1]["severity"] == "critical"

    def test_rule_governs(self):
        store = _mock_store()
        k = KnowledgeIngester(store)
        k.rule_governs("Tokens must expire", "JWT", NodeLabel.Concept)
        store.create_edge.assert_called_once_with(
            NodeLabel.Rule, {"name": "Tokens must expire"},
            EdgeType.GOVERNS,
            NodeLabel.Concept, {"name": "JWT"},
        )


class TestKnowledgeIngesterDecisions:
    def test_add_decision(self):
        store = _mock_store()
        k = KnowledgeIngester(store)
        k.add_decision("Use JWT", status="accepted", rationale="Horizontal scaling")
        call_args = store.create_node.call_args[0]
        assert call_args[0] == NodeLabel.Decision
        assert call_args[1]["status"] == "accepted"
        assert call_args[1]["rationale"] == "Horizontal scaling"


class TestKnowledgeIngesterPeople:
    def test_add_person(self):
        store = _mock_store()
        k = KnowledgeIngester(store)
        k.add_person("Alice", email="alice@example.com", role="lead", team="auth")
        store.create_node.assert_called_once_with(
            NodeLabel.Person,
            {"name": "Alice", "email": "alice@example.com", "role": "lead", "team": "auth"}
        )

    def test_assign(self):
        store = _mock_store()
        k = KnowledgeIngester(store)
        k.assign("validate_token", NodeLabel.Function, "Alice")
        store.create_edge.assert_called_once_with(
            NodeLabel.Function, {"name": "validate_token"},
            EdgeType.ASSIGNED_TO,
            NodeLabel.Person, {"name": "Alice"},
        )


class TestKnowledgeIngesterWiki:
    def test_wiki_page(self):
        store = _mock_store()
        k = KnowledgeIngester(store)
        k.wiki_page("Auth Guide", url="https://example.com", source="github", content="# Auth")
        call_args = store.create_node.call_args[0]
        assert call_args[0] == NodeLabel.WikiPage
        assert call_args[1]["name"] == "Auth Guide"

    def test_wiki_documents(self):
        store = _mock_store()
        k = KnowledgeIngester(store)
        k.wiki_documents("Auth Guide", "JWT", {"name": "JWT"}, NodeLabel.Concept)
        store.create_edge.assert_called_once_with(
            NodeLabel.WikiPage, {"name": "Auth Guide"},
            EdgeType.DOCUMENTS,
            NodeLabel.Concept, {"name": "JWT"},
        )


class TestKnowledgeIngesterAnnotate:
    def test_annotate_with_concept(self):
        store = _mock_store()
        k = KnowledgeIngester(store)
        k.annotate_code("validate_token", "Function", concept="JWT")
        store.create_edge.assert_called_once_with(
            NodeLabel.Concept, {"name": "JWT"},
            EdgeType.ANNOTATES,
            NodeLabel.Function, {"name": "validate_token"},
        )

    def test_annotate_with_rule(self):
        store = _mock_store()
        k = KnowledgeIngester(store)
        k.annotate_code("validate_token", "Function", rule="Tokens must expire")
        store.create_edge.assert_called_once_with(
            NodeLabel.Rule, {"name": "Tokens must expire"},
            EdgeType.ANNOTATES,
            NodeLabel.Function, {"name": "validate_token"},
        )

    def test_annotate_with_both(self):
        store = _mock_store()
        k = KnowledgeIngester(store)
        k.annotate_code("validate_token", "Function", concept="JWT", rule="Tokens must expire")
        assert store.create_edge.call_count == 2

    def test_code_implements(self):
        store = _mock_store()
        k = KnowledgeIngester(store)
        k.code_implements("validate_token", "Function", "JWT")
        store.create_edge.assert_called_once_with(
            NodeLabel.Function, {"name": "validate_token"},
            EdgeType.IMPLEMENTS,
            NodeLabel.Concept, {"name": "JWT"},
        )

    def test_annotate_invalid_label_raises(self):
        store = _mock_store()
        k = KnowledgeIngester(store)
        with pytest.raises(ValueError):
            k.annotate_code("foo", "InvalidLabel", concept="Bar")
