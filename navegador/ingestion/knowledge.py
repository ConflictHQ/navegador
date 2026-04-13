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
        memory: str | None = None,
        file_path: str = "",
        repo: str = "",
    ) -> None:
        """
        Link a code node to a concept, rule, or memory node.

        - concept/rule   → ANNOTATES edge (knowledge → code)
        - memory         → GOVERNS edge from the memory node (Rule/Decision/WikiPage/Person)
                           resolved by name + optional repo. Falls back gracefully if not found.

        code_label should be a string matching a NodeLabel value.
        file_path scopes the code symbol so same-named symbols in different files
        do not both receive the edge.
        """
        label = NodeLabel(code_label)
        code_key = {"name": code_name, "file_path": file_path} if file_path else {"name": code_name}
        if concept:
            self.store.create_edge(
                NodeLabel.Concept,
                {"name": concept},
                EdgeType.ANNOTATES,
                label,
                code_key,
            )
        if rule:
            self.store.create_edge(
                NodeLabel.Rule,
                {"name": rule},
                EdgeType.ANNOTATES,
                label,
                code_key,
            )
        if memory:
            self._memory_governs(memory, label, code_name, file_path=file_path, repo=repo)

    def _memory_governs(
        self,
        memory_name: str,
        code_label: NodeLabel,
        code_name: str,
        file_path: str = "",
        repo: str = "",
    ) -> None:
        """
        Create a GOVERNS edge from a memory node to a code symbol.

        Searches across all node types that MemoryIngester can produce
        (Rule, Decision, WikiPage, Person) for a node with memory_type set.
        Scopes the lookup by repo when provided, and targets only the specific
        code symbol identified by (code_name, file_path) when file_path is given.
        """
        result = self.store.query(
            "MATCH (n {name: $name}) WHERE n.memory_type IS NOT NULL "
            "AND ($repo = '' OR n.repo = $repo) "
            "RETURN labels(n)[0] AS label LIMIT 1",
            {"name": memory_name, "repo": repo},
        )
        rows = result.result_set or []
        if not rows:
            logger.warning("Memory node not found: %r — skipping GOVERNS edge", memory_name)
            return

        mem_label = NodeLabel(rows[0][0])
        code_key = {"name": code_name, "file_path": file_path} if file_path else {"name": code_name}
        self.store.create_edge(
            mem_label,
            {"name": memory_name},
            EdgeType.GOVERNS,
            code_label,
            code_key,
        )
        logger.info("Memory GOVERNS: %s → %s %s", memory_name, code_label, code_name)

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
