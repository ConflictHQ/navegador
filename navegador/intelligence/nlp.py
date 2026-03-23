"""
NLPEngine — natural language queries, community naming, and documentation generation.

Converts plain-English questions into Cypher queries, names communities with
descriptive labels, and generates documentation for individual symbols.

Usage::

    from navegador.graph import GraphStore
    from navegador.llm import get_provider
    from navegador.intelligence.nlp import NLPEngine

    store = GraphStore.sqlite(".navegador/graph.db")
    provider = get_provider("anthropic")
    engine = NLPEngine(store, provider)

    answer = engine.natural_query("Which functions call authenticate_user?")
    print(answer)

    docs = engine.generate_docs("authenticate_user", file_path="auth.py")
    print(docs)
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from navegador.graph.store import GraphStore
    from navegador.intelligence.community import Community
    from navegador.llm import LLMProvider


# ── Prompt templates ──────────────────────────────────────────────────────────

_SCHEMA_SUMMARY = """
The navegador knowledge graph contains these node types:
  Function, Class, Method, File, Decorator  — code layer
  Concept, Rule, Decision, WikiPage, Domain, Person  — knowledge layer

Common relationships:
  CALLS, INHERITS, DECORATES, CONTAINS, REFERENCES, IMPLEMENTS,
  BELONGS_TO, GOVERNS, DOCUMENTS, RELATED_TO, ASSIGNED_TO, DECIDED_BY

Node properties (where present):
  name, file_path, line_start, docstring, description, signature,
  status, domain, rationale, alternatives, date, community
"""

_NL_TO_CYPHER_PROMPT = """\
You are a FalkorDB Cypher expert. Given the schema below and a user question,
write a single Cypher query that answers the question.

Return ONLY the Cypher query — no markdown fences, no explanation.

{schema}

User question: {question}
"""

_FORMAT_RESULT_PROMPT = """\
The user asked: "{question}"

The Cypher query executed was:
{cypher}

The raw result rows are:
{rows}

Please summarise the result in a clear, concise paragraph (2–5 sentences).
"""

_NAME_COMMUNITY_PROMPT = """\
You are naming software communities detected via graph analysis.

Community members (function/class/concept names): {members}

Based on these names, suggest a short, descriptive community name (3–6 words).
Return ONLY the name — no punctuation, no explanation.
"""

_GENERATE_DOCS_PROMPT = """\
Generate concise markdown documentation for the symbol described below.

Symbol name: {name}
File: {file_path}
Type: {type}
Docstring: {docstring}
Signature: {signature}
Callers: {callers}
Callees: {callees}

Write:
1. A one-paragraph description.
2. Parameters (if applicable).
3. Returns (if applicable).
4. Example usage snippet.

Use markdown.
"""


class NLPEngine:
    """
    LLM-powered natural language interface to the navegador graph.

    Args:
        store: A :class:`~navegador.graph.GraphStore` instance.
        provider: An :class:`~navegador.llm.LLMProvider` for completions.
    """

    def __init__(self, store: "GraphStore", provider: "LLMProvider") -> None:
        self._store = store
        self._provider = provider

    # ── Natural language query ─────────────────────────────────────────────

    def natural_query(self, question: str) -> str:
        """
        Convert a natural-language *question* into Cypher, execute it, and
        return an LLM-formatted answer.

        Args:
            question: Plain-English question about the codebase or knowledge
                graph (e.g. ``"Which functions call validate_token?"``).

        Returns:
            A human-readable answer string.
        """
        # Step 1: translate question → Cypher
        cypher_prompt = _NL_TO_CYPHER_PROMPT.format(
            schema=_SCHEMA_SUMMARY, question=question
        )
        cypher = self._provider.complete(cypher_prompt).strip()

        # Strip any accidental markdown fences the model may still produce
        cypher = _strip_fences(cypher)

        # Step 2: execute
        try:
            result = self._store.query(cypher, {})
            rows = result.result_set or []
        except Exception as exc:  # noqa: BLE001
            return (
                f"Failed to execute the generated Cypher query.\n\n"
                f"Query: {cypher}\n\nError: {exc}"
            )

        # Step 3: format result
        rows_text = json.dumps(rows[:50], indent=2, default=str)
        fmt_prompt = _FORMAT_RESULT_PROMPT.format(
            question=question, cypher=cypher, rows=rows_text
        )
        return self._provider.complete(fmt_prompt)

    # ── Community naming ──────────────────────────────────────────────────

    def name_communities(self, communities: list["Community"]) -> list[dict[str, Any]]:
        """
        Use the LLM to generate a meaningful name for each community.

        Args:
            communities: List of :class:`~navegador.intelligence.community.Community`
                objects (as returned by :meth:`~CommunityDetector.detect`).

        Returns:
            List of dicts with keys ``original_name``, ``suggested_name``,
            ``members``, ``size``.
        """
        named: list[dict[str, Any]] = []
        for comm in communities:
            members_str = ", ".join(comm.members[:20])  # cap to avoid huge prompts
            prompt = _NAME_COMMUNITY_PROMPT.format(members=members_str)
            try:
                suggested = self._provider.complete(prompt).strip()
            except Exception:  # noqa: BLE001
                suggested = comm.name
            named.append(
                {
                    "original_name": comm.name,
                    "suggested_name": suggested,
                    "members": comm.members,
                    "size": comm.size,
                }
            )
        return named

    # ── Documentation generation ──────────────────────────────────────────

    def generate_docs(self, name: str, file_path: str = "") -> str:
        """
        Generate markdown documentation for a named symbol.

        Retrieves graph context (type, docstring, signature, callers, callees)
        and asks the LLM to produce structured markdown.

        Args:
            name: Symbol name (function, class, etc.).
            file_path: Optional file path to disambiguate.

        Returns:
            Markdown documentation string.
        """
        # Look up the node
        cypher = """
MATCH (n)
WHERE n.name = $name AND ($file_path = '' OR n.file_path = $file_path)
RETURN labels(n)[0] AS type, n.name AS name,
       coalesce(n.file_path, '') AS file_path,
       coalesce(n.docstring, n.description, '') AS docstring,
       coalesce(n.signature, '') AS signature
LIMIT 1
"""
        result = self._store.query(cypher, {"name": name, "file_path": file_path})
        rows = result.result_set or []

        node_type = "Unknown"
        docstring = ""
        signature = ""
        fp = file_path

        if rows:
            row = rows[0]
            node_type, _, fp, docstring, signature = (
                row[0], row[1], row[2], row[3], row[4]
            )

        # Fetch callers
        callers_result = self._store.query(
            "MATCH (caller)-[:CALLS]->(n {name: $name}) "
            "RETURN caller.name LIMIT 10",
            {"name": name},
        )
        callers = [r[0] for r in (callers_result.result_set or []) if r[0]]

        # Fetch callees
        callees_result = self._store.query(
            "MATCH (n {name: $name})-[:CALLS]->(callee) "
            "RETURN callee.name LIMIT 10",
            {"name": name},
        )
        callees = [r[0] for r in (callees_result.result_set or []) if r[0]]

        prompt = _GENERATE_DOCS_PROMPT.format(
            name=name,
            file_path=fp,
            type=node_type,
            docstring=docstring or "(none)",
            signature=signature or "(none)",
            callers=", ".join(callers) if callers else "(none)",
            callees=", ".join(callees) if callees else "(none)",
        )
        return self._provider.complete(prompt)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _strip_fences(text: str) -> str:
    """Remove markdown code fences (```cypher … ```) from LLM output."""
    text = re.sub(r"```[a-zA-Z]*\n?", "", text)
    text = text.replace("```", "")
    return text.strip()
