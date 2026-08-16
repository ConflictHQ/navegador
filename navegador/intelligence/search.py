"""
SemanticSearch — vector similarity over the navegador graph.

Embeds the text attached to a node and retrieves the nearest matches for a
natural-language query using FalkorDB's native vector index.

The previous implementation stored embeddings as JSON strings and, on every
query, fetched **every** embedded node together with its full vector, parsed
each one, and computed cosine similarity in a Python loop. At 100k nodes and
1536 dimensions that is on the order of a gigabyte crossing the wire per
query, and the cost grew with the graph — reintroducing exactly the full-scan
problem this project exists to remove, and multiplying it by every agent
sharing the server (#182).

Now the k-nearest search happens inside the database against an index, so
query cost is independent of graph size and no vector is transferred except
the query's own.

Usage::

    from navegador.config import get_store
    from navegador.llm import get_provider
    from navegador.intelligence.search import SemanticSearch

    ss = SemanticSearch(get_store(target="."), get_provider("openai"))
    ss.index()                       # incremental; unchanged text is skipped
    ss.search("validates JWT tokens", limit=5)
"""

from __future__ import annotations

import hashlib
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from navegador.graph.store import GraphStore
    from navegador.llm import LLMProvider

logger = logging.getLogger(__name__)

# A FalkorDB node pattern takes exactly one label, and a vector index is
# created per label, so both the index and the query fan out across these.
EMBEDDABLE_LABELS = ("Function", "Method", "Class", "Concept", "Rule", "Decision")

# Nodes whose embedded text is unchanged are skipped on re-index. The hash is
# of the text actually embedded rather than File.content_hash, because symbol
# nodes carry no content hash and a file can change without changing a given
# function's signature or docstring.
_EMBED_HASH = "embedding_hash"


def _text_for(row: dict) -> str:
    """
    The text embedded for a node.

    Previously only nodes with a docstring or description were embeddable,
    which made most real functions invisible to semantic search. The name and
    signature carry real meaning on their own — `parse_import_statement` is
    findable from "handle imports" without a word of prose.
    """
    parts = [row.get("name") or ""]
    if row.get("class_name"):
        parts.append(f"in {row['class_name']}")
    if row.get("file_path"):
        parts.append(row["file_path"].replace("/", " ").replace("_", " "))
    if row.get("text"):
        parts.append(row["text"])
    return " — ".join(p for p in parts if p).strip()


class SemanticSearch:
    """
    Vector search over the graph, backed by FalkorDB's native vector index.

    Args:
        store: A :class:`~navegador.graph.GraphStore`.
        provider: An :class:`~navegador.llm.LLMProvider` implementing ``embed``.
    """

    def __init__(self, store: "GraphStore", provider: "LLMProvider") -> None:
        self._store = store
        self._provider = provider

    # ── Index ─────────────────────────────────────────────────────────────

    def _ensure_index(self, label: str, dimension: int) -> None:
        """
        Create the vector index for *label*, ignoring "already indexed".

        FalkorDB has no CREATE VECTOR INDEX IF NOT EXISTS, so the second call
        raises and there is nothing to do about it but carry on.
        """
        try:
            self._store.query(
                f"CREATE VECTOR INDEX FOR (n:{label}) ON (n.embedding) "
                f"OPTIONS {{dimension:{int(dimension)}, similarityFunction:'cosine'}}"
            )
        except Exception as exc:  # noqa: BLE001 — the only failure worth acting on is a new one
            if "already" not in str(exc).lower():
                logger.debug("vector index for %s: %s", label, exc)

    def index(self, limit: int | None = None, batch: int = 256) -> int:
        """
        Embed node text and store it as a native vector.

        Incremental: a node whose embedded text has not changed since last
        time is skipped, so re-indexing an unchanged repository costs nothing
        and does not re-pay the embedding bill.

        Args:
            limit: Stop after this many nodes. None means all of them — the
                old default silently truncated at 1000, leaving a partially
                indexed graph that looked complete.
            batch: Nodes per write round trip.

        Returns:
            Number of nodes newly embedded.
        """
        embedded = 0
        dimension: int | None = None

        for label in EMBEDDABLE_LABELS:
            rows = self._store.query(
                f"MATCH (n:{label}) "
                "RETURN id(n), n.name, coalesce(n.file_path,''), "
                "coalesce(n.docstring, n.description, ''), "
                f"coalesce(n.class_name,''), n.{_EMBED_HASH}"
            ).result_set
            pending: list[tuple[int, list[float], str]] = []

            for row in rows or []:
                if limit is not None and embedded >= limit:
                    break
                node_id, name, file_path, text, class_name, stored_hash = row
                if not name:
                    continue
                content = _text_for(
                    {
                        "name": name,
                        "file_path": file_path,
                        "text": text,
                        "class_name": class_name,
                    }
                )
                if not content:
                    continue
                digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
                if stored_hash == digest:
                    continue  # unchanged since last index

                vector = self._provider.embed(content)
                if not vector:
                    continue
                if dimension is None:
                    dimension = len(vector)
                    for lbl in EMBEDDABLE_LABELS:
                        self._ensure_index(lbl, dimension)
                pending.append((int(node_id), vector, digest))
                embedded += 1
                if len(pending) >= batch:
                    self._write(pending)
                    pending = []

            if pending:
                self._write(pending)

        return embedded

    def _write(self, pending: list[tuple[int, list[float], str]]) -> None:
        """
        Attach vectors to nodes by internal id.

        The previous version matched on name plus optional file_path, which is
        ambiguous for an overloaded method or a module-level function sharing
        a name with one — it could write the embedding to the wrong node, or
        to several.
        """
        for node_id, vector, digest in pending:
            literal = ",".join(f"{v:.7g}" for v in vector)
            self._store.query(
                f"MATCH (n) WHERE id(n) = {node_id} "
                f"SET n.embedding = vecf32([{literal}]), n.{_EMBED_HASH} = $digest",
                {"digest": digest},
            )

    # ── Search ────────────────────────────────────────────────────────────

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        """
        Return the *limit* nodes nearest to *query*.

        Each result has ``type``, ``name``, ``file_path``, ``text`` and
        ``score`` — cosine similarity in 0..1, converted from the distance the
        index returns so that higher remains better for callers.
        """
        vector = self._provider.embed(query)
        if not vector:
            return []
        literal = ",".join(f"{v:.7g}" for v in vector)

        results: list[dict[str, Any]] = []
        for label in EMBEDDABLE_LABELS:
            try:
                rows = self._store.query(
                    f"CALL db.idx.vector.queryNodes('{label}', 'embedding', {int(limit)}, "
                    f"vecf32([{literal}])) YIELD node, score "
                    "RETURN labels(node)[0], node.name, coalesce(node.file_path,''), "
                    "coalesce(node.docstring, node.description, ''), score"
                ).result_set
            except Exception as exc:  # noqa: BLE001
                # No index for this label yet — nothing has been embedded for
                # it. Not an error; the other labels still answer.
                logger.debug("vector query on %s: %s", label, exc)
                continue

            for row in rows or []:
                node_type, name, file_path, text, distance = row
                results.append(
                    {
                        "type": node_type,
                        "name": name,
                        "file_path": file_path,
                        "text": text,
                        "score": max(0.0, 1.0 - float(distance)),
                    }
                )

        results.sort(key=lambda r: r["score"], reverse=True)
        return results[:limit]
