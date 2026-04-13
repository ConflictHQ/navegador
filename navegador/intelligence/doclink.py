"""
Confidence-ranked doc linking — automatically link markdown, wiki, memory, and
documentation nodes to code and concept nodes using exact + fuzzy matching.

Each inferred link carries a confidence score (0.0–1.0) and a rationale string
so humans and agents can distinguish trusted links from suggested ones.

Match strategies (applied in order, highest confidence first):

  1. EXACT_NAME   — document mentions a name that exactly matches a graph node
  2. ALIAS        — heading/bold term is a known alias (case-insensitive)
  3. FUZZY        — partial name overlap above a configurable threshold
  4. SEMANTIC     — cosine similarity via LLMProvider embeddings (optional)

Usage::

    from navegador.intelligence.doclink import DocLinker, LinkCandidate

    linker = DocLinker(store)
    candidates = linker.suggest_links(min_confidence=0.5)

    for c in candidates:
        print(c.source_name, "→", c.target_name, f"({c.confidence:.2f})")
        # Accept high-confidence links
        if c.confidence >= 0.8:
            linker.accept(c)

    # Or accept all above threshold at once
    linker.accept_all(candidates, min_confidence=0.8)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from navegador.graph import GraphStore
from navegador.graph.schema import EdgeType

if TYPE_CHECKING:
    from navegador.llm import LLMProvider

_BOLD_RE = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")
_BACKTICK_RE = re.compile(r"`([^`]+)`")
_H_RE = re.compile(r"^#{1,6}\s+(.+)", re.MULTILINE)


def _terms_from_content(content: str) -> list[str]:
    """Extract candidate term strings from markdown content."""
    terms: list[str] = []
    terms += [m.group(1).strip() for m in _H_RE.finditer(content)]
    for m in _BOLD_RE.finditer(content):
        t = (m.group(1) or m.group(2) or "").strip()
        if t:
            terms.append(t)
    terms += [m.group(1).strip() for m in _BACKTICK_RE.finditer(content)]
    # Also consider each whole first line
    first = content.strip().splitlines()[0] if content.strip() else ""
    if first:
        terms.append(first.strip("#").strip())
    return list(dict.fromkeys(t for t in terms if t))


def _fuzzy_score(a: str, b: str) -> float:
    """Simple overlap score: longer common substring / len(longer)."""
    a, b = a.lower(), b.lower()
    if a == b:
        return 1.0
    if a in b or b in a:
        return min(len(a), len(b)) / max(len(a), len(b))
    # character bigram overlap
    def bigrams(s: str) -> set[str]:
        return {s[i:i+2] for i in range(len(s) - 1)}
    bg_a, bg_b = bigrams(a), bigrams(b)
    if not bg_a or not bg_b:
        return 0.0
    return len(bg_a & bg_b) / len(bg_a | bg_b)


@dataclass
class LinkCandidate:
    source_label: str        # Document / WikiPage / Rule / Decision
    source_name: str
    target_label: str        # Function / Class / Concept / Rule …
    target_name: str
    target_file: str = ""
    edge_type: str = "DOCUMENTS"   # proposed edge type
    confidence: float = 0.0
    strategy: str = ""       # EXACT_NAME | ALIAS | FUZZY | SEMANTIC
    rationale: str = ""


class DocLinker:
    """
    Suggests confidence-ranked links from documentation nodes (Document,
    WikiPage, Decision, Rule) to code and concept nodes.

    No LLMProvider is required for exact/fuzzy matching. Pass *provider*
    to enable the SEMANTIC strategy.
    """

    EXACT_CONFIDENCE = 0.95
    ALIAS_CONFIDENCE = 0.80
    FUZZY_THRESHOLD = 0.55
    SEMANTIC_THRESHOLD = 0.70

    def __init__(self, store: GraphStore, provider: "LLMProvider | None" = None) -> None:
        self.store = store
        self._provider = provider

    # ── Public API ────────────────────────────────────────────────────────────

    def suggest_links(self, min_confidence: float = 0.5) -> list[LinkCandidate]:
        """
        Scan documentation nodes and return link candidates above *min_confidence*.

        Results are sorted by confidence descending. Already-linked pairs
        (existing DOCUMENTS/ANNOTATES/GOVERNS edges) are excluded.
        """
        candidates: list[LinkCandidate] = []

        doc_nodes = self._fetch_doc_nodes()
        code_nodes = self._fetch_code_nodes()

        # Build lookup indices
        exact_index: dict[str, list[dict[str, Any]]] = {}
        for n in code_nodes:
            exact_index.setdefault(n["name"].lower(), []).append(n)

        existing = self._existing_links()

        for doc in doc_nodes:
            content = doc.get("content", "") or doc.get("description", "") or doc.get("name", "")
            terms = _terms_from_content(content)

            for term in terms:
                term_lower = term.lower()

                # 1. Exact match
                for target in exact_index.get(term_lower, []):
                    key = (doc["name"], target["name"])
                    if key in existing:
                        continue
                    candidates.append(LinkCandidate(
                        source_label=doc["label"],
                        source_name=doc["name"],
                        target_label=target["label"],
                        target_name=target["name"],
                        target_file=target.get("file_path", ""),
                        confidence=self.EXACT_CONFIDENCE,
                        strategy="EXACT_NAME",
                        rationale=f"term '{term}' exactly matches {target['label']} name",
                    ))
                    continue

                # 2. Fuzzy match
                for target in code_nodes:
                    key = (doc["name"], target["name"])
                    if key in existing:
                        continue
                    score = _fuzzy_score(term, target["name"])
                    if score >= self.FUZZY_THRESHOLD:
                        candidates.append(LinkCandidate(
                            source_label=doc["label"],
                            source_name=doc["name"],
                            target_label=target["label"],
                            target_name=target["name"],
                            target_file=target.get("file_path", ""),
                            confidence=round(score * self.ALIAS_CONFIDENCE, 3),
                            strategy="FUZZY",
                            rationale=f"term '{term}' fuzzy-matches '{target['name']}' "
                                      f"(score={score:.2f})",
                        ))

        # 3. Semantic matching (requires provider)
        if self._provider:
            candidates += self._semantic_candidates(doc_nodes, code_nodes, existing)

        # Dedupe: keep highest confidence per (source, target) pair
        best: dict[tuple[str, str], LinkCandidate] = {}
        for c in candidates:
            key = (c.source_name, c.target_name)
            if key not in best or c.confidence > best[key].confidence:
                best[key] = c

        results = [c for c in best.values() if c.confidence >= min_confidence]
        results.sort(key=lambda c: c.confidence, reverse=True)
        return results

    def accept(self, candidate: LinkCandidate) -> None:
        """Write a single link candidate as a graph edge."""
        edge = getattr(EdgeType, candidate.edge_type, EdgeType.DOCUMENTS)
        self.store.create_edge(
            candidate.source_label,
            {"name": candidate.source_name},
            edge,
            candidate.target_label,
            {"name": candidate.target_name},
            props={"confidence": candidate.confidence, "strategy": candidate.strategy},
        )

    def accept_all(self, candidates: list[LinkCandidate], min_confidence: float = 0.8) -> int:
        """Accept all candidates above *min_confidence*. Returns count written."""
        written = 0
        for c in candidates:
            if c.confidence >= min_confidence:
                self.accept(c)
                written += 1
        return written

    # ── Internals ─────────────────────────────────────────────────────────────

    def _fetch_doc_nodes(self) -> list[dict[str, Any]]:
        cypher = (
            "MATCH (n) WHERE (n:Document OR n:WikiPage OR n:Decision OR n:Rule) "
            "RETURN labels(n)[0], n.name, "
            "coalesce(n.content, n.rationale, n.description, '') LIMIT 500"
        )
        rows = self.store.query(cypher).result_set or []
        return [{"label": r[0], "name": r[1], "content": r[2]} for r in rows if r[1]]

    def _fetch_code_nodes(self) -> list[dict[str, Any]]:
        cypher = (
            "MATCH (n) WHERE (n:Function OR n:Class OR n:Method OR n:Concept) "
            "RETURN labels(n)[0], n.name, coalesce(n.file_path, ''), "
            "coalesce(n.docstring, n.description, '') LIMIT 2000"
        )
        rows = self.store.query(cypher).result_set or []
        return [
            {"label": r[0], "name": r[1], "file_path": r[2], "text": r[3]}
            for r in rows if r[1]
        ]

    def _existing_links(self) -> set[tuple[str, str]]:
        cypher = (
            "MATCH (a)-[:DOCUMENTS|ANNOTATES|GOVERNS]->(b) "
            "RETURN a.name, b.name LIMIT 5000"
        )
        rows = self.store.query(cypher).result_set or []
        return {(r[0], r[1]) for r in rows if r[0] and r[1]}

    def _semantic_candidates(
        self,
        doc_nodes: list[dict[str, Any]],
        code_nodes: list[dict[str, Any]],
        existing: set[tuple[str, str]],
    ) -> list[LinkCandidate]:
        import math

        def _cos(a: list[float], b: list[float]) -> float:
            if len(a) != len(b):
                return 0.0
            dot = sum(x * y for x, y in zip(a, b))
            mag = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(x * x for x in b))
            return dot / mag if mag else 0.0

        assert self._provider is not None
        candidates: list[LinkCandidate] = []

        # Embed code nodes (use cached embedding if stored)
        code_vecs: list[tuple[dict[str, Any], list[float]]] = []
        for n in code_nodes[:200]:  # cap to avoid rate-limiting
            text = n.get("text", "") or n["name"]
            try:
                vec = self._provider.embed(text)
                code_vecs.append((n, vec))
            except Exception:
                pass

        for doc in doc_nodes[:100]:
            content = (doc.get("content", "") or doc["name"])[:500]
            try:
                doc_vec = self._provider.embed(content)
            except Exception:
                continue

            for target, code_vec in code_vecs:
                key = (doc["name"], target["name"])
                if key in existing:
                    continue
                score = _cos(doc_vec, code_vec)
                if score >= self.SEMANTIC_THRESHOLD:
                    candidates.append(LinkCandidate(
                        source_label=doc["label"],
                        source_name=doc["name"],
                        target_label=target["label"],
                        target_name=target["name"],
                        target_file=target.get("file_path", ""),
                        confidence=round(score, 3),
                        strategy="SEMANTIC",
                        rationale=f"semantic similarity={score:.2f}",
                    ))

        return candidates
