"""
KnowledgeIngester — manual curation of business concepts, rules, decisions,
people, and domains into the navegador graph.

These are the things that don't live in code but belong in the knowledge graph:
business rules, architectural decisions, domain groupings, ownership, wiki refs.
"""

import logging
from typing import Any

from navegador.graph.schema import EdgeType, NodeLabel
from navegador.graph.store import GraphStore

logger = logging.getLogger(__name__)


class KnowledgeIngester:
    """
    Writes business knowledge nodes and relationships into the graph.

    Usage:
        store = GraphStore.sqlite(".navegador/graph.db")
        k = KnowledgeIngester(store)

        k.add_domain("auth", description="Authentication and authorisation")
        k.add_concept("JWT", description="Stateless token auth", domain="auth")
        k.add_rule("tokens must expire", domain="auth", severity="critical",
                   rationale="Security requirement per OWASP")
        k.annotate_code("validate_token", "Function", concept="JWT")
        k.wiki_page("JWT Auth", url="...", content="...")
    """

    def __init__(self, store: GraphStore) -> None:
        self.store = store

    # ── Domains ───────────────────────────────────────────────────────────────

    def add_domain(self, name: str, description: str = "") -> None:
        self.store.create_node(
            NodeLabel.Domain,
            {
                "name": name,
                "description": description,
            },
        )
        logger.info("Domain: %s", name)

    # ── Concepts ──────────────────────────────────────────────────────────────

    def add_concept(
        self,
        name: str,
        description: str = "",
        domain: str = "",
        status: str = "",
        rules: str = "",
        examples: str = "",
        wiki_refs: str = "",
    ) -> None:
        self.store.create_node(
            NodeLabel.Concept,
            {
                "name": name,
                "description": description,
                "domain": domain,
                "status": status,
                "rules": rules,
                "examples": examples,
                "wiki_refs": wiki_refs,
            },
        )
        if domain:
            self._link_to_domain(name, NodeLabel.Concept, domain)
        logger.info("Concept: %s", name)

    def relate_concepts(self, a: str, b: str) -> None:
        """Mark two concepts as related (bidirectional intent)."""
        self.store.create_edge(
            NodeLabel.Concept,
            {"name": a},
            EdgeType.RELATED_TO,
            NodeLabel.Concept,
            {"name": b},
        )

    # ── Rules ─────────────────────────────────────────────────────────────────

    def add_rule(
        self,
        name: str,
        description: str = "",
        domain: str = "",
        severity: str = "info",
        rationale: str = "",
        examples: str = "",
    ) -> None:
        self.store.create_node(
            NodeLabel.Rule,
            {
                "name": name,
                "description": description,
                "domain": domain,
                "severity": severity,
                "rationale": rationale,
                "examples": examples,
            },
        )
        if domain:
            self._link_to_domain(name, NodeLabel.Rule, domain)
        logger.info("Rule: %s", name)

    def rule_governs(self, rule_name: str, target_name: str, target_label: NodeLabel) -> None:
        self.store.create_edge(
            NodeLabel.Rule,
            {"name": rule_name},
            EdgeType.GOVERNS,
            target_label,
            {"name": target_name},
        )

    # ── Decisions ─────────────────────────────────────────────────────────────

    def add_decision(
        self,
        name: str,
        description: str = "",
        domain: str = "",
        status: str = "accepted",
        rationale: str = "",
        alternatives: str = "",
        date: str = "",
    ) -> None:
        self.store.create_node(
            NodeLabel.Decision,
            {
                "name": name,
                "description": description,
                "domain": domain,
                "status": status,
                "rationale": rationale,
                "alternatives": alternatives,
                "date": date,
            },
        )
        if domain:
            self._link_to_domain(name, NodeLabel.Decision, domain)
        logger.info("Decision: %s", name)

    # ── People ────────────────────────────────────────────────────────────────

    def add_person(
        self,
        name: str,
        email: str = "",
        role: str = "",
        team: str = "",
    ) -> None:
        self.store.create_node(
            NodeLabel.Person,
            {
                "name": name,
                "email": email,
                "role": role,
                "team": team,
            },
        )
        logger.info("Person: %s", name)

    def assign(self, target_name: str, target_label: NodeLabel, person_name: str) -> None:
        """Assign a person as owner of any node."""
        self.store.create_edge(
            target_label,
            {"name": target_name},
            EdgeType.ASSIGNED_TO,
            NodeLabel.Person,
            {"name": person_name},
        )

    # ── Wiki pages ────────────────────────────────────────────────────────────

    def wiki_page(
        self,
        name: str,
        url: str = "",
        source: str = "github",
        content: str = "",
        updated_at: str = "",
    ) -> None:
        self.store.create_node(
            NodeLabel.WikiPage,
            {
                "name": name,
                "url": url,
                "source": source,
                "content": content,
                "updated_at": updated_at,
            },
        )
        logger.info("WikiPage: %s", name)

    def wiki_documents(
        self,
        wiki_page_name: str,
        target_name: str,
        target_props: dict[str, Any],
        target_label: NodeLabel,
    ) -> None:
        self.store.create_edge(
            NodeLabel.WikiPage,
            {"name": wiki_page_name},
            EdgeType.DOCUMENTS,
            target_label,
            target_props,
        )

    # ── Code ↔ Knowledge bridges ──────────────────────────────────────────────

    def annotate_code(
        self,
        code_name: str,
        code_label: str,
        concept: str | None = None,
        rule: str | None = None,
    ) -> None:
        """
        Link a code node to a concept or rule via ANNOTATES.
        code_label should be a string matching a NodeLabel value.
        """
        label = NodeLabel(code_label)
        if concept:
            self.store.create_edge(
                NodeLabel.Concept,
                {"name": concept},
                EdgeType.ANNOTATES,
                label,
                {"name": code_name},
            )
        if rule:
            self.store.create_edge(
                NodeLabel.Rule,
                {"name": rule},
                EdgeType.ANNOTATES,
                label,
                {"name": code_name},
            )

    def code_implements(self, code_name: str, code_label: str, concept_name: str) -> None:
        """Mark a function/class as implementing a concept."""
        label = NodeLabel(code_label)
        self.store.create_edge(
            label,
            {"name": code_name},
            EdgeType.IMPLEMENTS,
            NodeLabel.Concept,
            {"name": concept_name},
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _link_to_domain(self, name: str, label: NodeLabel, domain: str) -> None:
        # Ensure domain node exists
        self.store.create_node(NodeLabel.Domain, {"name": domain, "description": ""})
        self.store.create_edge(
            label,
            {"name": name},
            EdgeType.BELONGS_TO,
            NodeLabel.Domain,
            {"name": domain},
        )
