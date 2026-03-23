"""
SemanticSearch — embedding-based similarity search over the navegador graph.

Embeds function/class docstrings via an LLMProvider, stores the embedding
vectors as JSON in a node property (``embedding``), and retrieves the top-k
most similar nodes to a natural-language query using cosine similarity.

Usage::

    from navegador.graph import GraphStore
    from navegador.llm import get_provider
    from navegador.intelligence.search import SemanticSearch

    store = GraphStore.sqlite(".navegador/graph.db")
    provider = get_provider("openai")
    ss = SemanticSearch(store, provider)

    # Build / refresh the index (idempotent — re-embeds all nodes)
    ss.index()

    # Query
    results = ss.search("function that validates JWT tokens", limit=5)
    for r in results:
        print(r["name"], r["score"])
"""

from __future__ import annotations

import json
import math
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from navegador.graph.store import GraphStore
    from navegador.llm import LLMProvider


# Cypher to fetch all embeddable nodes (those with a docstring or description)
_EMBEDDABLE_NODES = """
MATCH (n)
WHERE (n:Function OR n:Class OR n:Method OR n:Concept OR n:Rule OR n:Decision)
  AND (n.docstring IS NOT NULL OR n.description IS NOT NULL)
RETURN
    labels(n)[0] AS type,
    n.name AS name,
    coalesce(n.file_path, '') AS file_path,
    coalesce(n.docstring, n.description, '') AS text
LIMIT $limit
"""

# Cypher to fetch nodes that already have a stored embedding
_NODES_WITH_EMBEDDINGS = """
MATCH (n)
WHERE n.embedding IS NOT NULL
RETURN
    labels(n)[0] AS type,
    n.name AS name,
    coalesce(n.file_path, '') AS file_path,
    coalesce(n.docstring, n.description, '') AS text,
    n.embedding AS embedding
"""

# Upsert the embedding property on a matched node
_SET_EMBEDDING = """
MATCH (n)
WHERE n.name = $name AND ($file_path = '' OR n.file_path = $file_path)
SET n.embedding = $embedding
"""


class SemanticSearch:
    """
    Embedding-based semantic search over the navegador graph.

    Embeddings are stored as a JSON string (serialised ``list[float]``) in the
    ``embedding`` property of each node so they survive graph restarts without
    any external vector store.

    Args:
        store: A :class:`~navegador.graph.GraphStore` instance.
        provider: An :class:`~navegador.llm.LLMProvider` that implements
            :meth:`embed`.
    """

    def __init__(self, store: "GraphStore", provider: "LLMProvider") -> None:
        self._store = store
        self._provider = provider

    # ── Index ─────────────────────────────────────────────────────────────────

    def index(self, limit: int = 1000) -> int:
        """
        Embed all function/class/concept docstrings and store on the nodes.

        Args:
            limit: Maximum number of nodes to index in one pass.

        Returns:
            The number of nodes that were (re-)embedded.
        """
        result = self._store.query(_EMBEDDABLE_NODES, {"limit": limit})
        rows = result.result_set or []
        indexed = 0
        for row in rows:
            node_type, name, file_path, text = row[0], row[1], row[2], row[3]
            if not text:
                continue
            label = f"[{node_type}] {name}: {text}"
            vector = self._provider.embed(label)
            self._store.query(
                _SET_EMBEDDING,
                {
                    "name": name,
                    "file_path": file_path,
                    "embedding": json.dumps(vector),
                },
            )
            indexed += 1
        return indexed

    # ── Search ────────────────────────────────────────────────────────────────

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        """
        Embed *query* and return the *limit* most similar indexed nodes.

        Each result dict has keys: ``type``, ``name``, ``file_path``,
        ``text``, ``score`` (cosine similarity, 0–1).

        Args:
            query: Natural-language search query.
            limit: Maximum number of results to return.

        Returns:
            List of result dicts sorted by descending similarity score.
        """
        query_vec = self._provider.embed(query)

        result = self._store.query(_NODES_WITH_EMBEDDINGS, {})
        rows = result.result_set or []

        scored: list[dict[str, Any]] = []
        for row in rows:
            node_type, name, file_path, text, emb_json = (row[0], row[1], row[2], row[3], row[4])
            if not emb_json:
                continue
            try:
                node_vec: list[float] = json.loads(emb_json)
            except (json.JSONDecodeError, TypeError):
                continue
            score = self._cosine_similarity(query_vec, node_vec)
            scored.append(
                {
                    "type": node_type,
                    "name": name,
                    "file_path": file_path,
                    "text": text,
                    "score": score,
                }
            )

        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:limit]

    # ── Internals ─────────────────────────────────────────────────────────────

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        """
        Compute the cosine similarity between two vectors.

        Args:
            a: First embedding vector.
            b: Second embedding vector.

        Returns:
            Cosine similarity in the range ``[-1, 1]``.  Returns ``0.0`` if
            either vector is the zero vector or the lengths differ.
        """
        if len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        mag_a = math.sqrt(sum(x * x for x in a))
        mag_b = math.sqrt(sum(x * x for x in b))
        if mag_a == 0.0 or mag_b == 0.0:
            return 0.0
        return dot / (mag_a * mag_b)
