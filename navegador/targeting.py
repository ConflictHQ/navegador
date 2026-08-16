"""
Tell an agent where to look. Do not tell it what the answer is (#186).

Grep is not the problem. Once an agent knows where to point it, grep is fast
and precise, and telemetry from 151 real sessions says agents already scope
96.5% of their searches to a named file, file list or subdirectory. Only 3.4%
sweep a whole tree.

What costs is the turns spent working out *where* — 206 tool calls in a median
session, 32% of file reads re-opening something already read. Each of those
turns is inference latency and context window, not disk. So the unit of waste
is a turn, and the number to move is turns-to-first-correct-target, which the
frozen baseline in ``scripts/baselines/`` puts at 13.

Hence three tools that return a **scope**, not an answer:

``locate``
    Ranked places to look, each with the reason it surfaced. Fuses exact
    matches from the trigram index, vector similarity, and graph structure.

``scope_for``
    The file set reachable from a symbol, shaped to hand straight to ripgrep.
    This is the search-space reduction no flat index can compute, and it is
    the whole argument for doing this inside a graph.

``neighbourhood``
    What the agent would otherwise spend three more turns learning: callers,
    callees, tests, and the file a symbol lives in.

None of them write, and none of them synthesise an answer. Being handed six
files and a reason beats being handed a paragraph the agent has to trust.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# How much a hit from each source contributes before rank fusion. Exact text
# is weighted hardest: if a literal appears somewhere, that is a fact, whereas
# vector similarity is a guess and structure is context.
WEIGHTS = {"exact": 1.0, "symbol": 0.9, "vector": 0.6, "structure": 0.4}

# Reciprocal rank fusion constant. 60 is the value from the original TREC work
# and is not sensitive enough to be worth tuning here.
RRF_K = 60


@dataclass
class Candidate:
    """One place worth looking, and why."""

    path: str
    line: int = 0
    symbol: str = ""
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)

    def key(self) -> tuple[str, int]:
        return (self.path, self.line)

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "line": self.line,
            "symbol": self.symbol,
            "score": round(self.score, 4),
            "reasons": self.reasons,
        }


def _fuse(ranked: dict[str, list[Candidate]]) -> list[Candidate]:
    """
    Reciprocal rank fusion across sources.

    Scores from a trigram index, a vector index and a graph traversal are not
    on comparable scales, so they cannot simply be added. Fusing on *rank*
    sidesteps that: each source votes by position, weighted by how much its
    kind of evidence is worth.
    """
    merged: dict[tuple[str, int], Candidate] = {}
    for source, candidates in ranked.items():
        weight = WEIGHTS.get(source, 0.5)
        for position, candidate in enumerate(candidates):
            existing = merged.get(candidate.key())
            if existing is None:
                existing = Candidate(
                    path=candidate.path, line=candidate.line, symbol=candidate.symbol
                )
                merged[candidate.key()] = existing
            existing.score += weight / (RRF_K + position + 1)
            for reason in candidate.reasons:
                if reason not in existing.reasons:
                    existing.reasons.append(reason)
            if candidate.symbol and not existing.symbol:
                existing.symbol = candidate.symbol
    return sorted(merged.values(), key=lambda c: (-c.score, c.path, c.line))


class Targeting:
    """
    Where-to-look queries over one graph.

    Args:
        store: A :class:`~navegador.graph.GraphStore`.
        provider: Optional LLM provider for vector search. Without one the
            other sources still answer — semantic similarity is an
            improvement, not a requirement.
    """

    def __init__(self, store, provider=None) -> None:
        self._store = store
        self._provider = provider

    # ── locate ────────────────────────────────────────────────────────────

    def locate(self, intent: str, limit: int = 10) -> list[Candidate]:
        """
        Ranked places to look for *intent*, each with a stated reason.

        Sources are queried independently and fused on rank. A source that
        fails or is unavailable contributes nothing and the rest still answer,
        because a targeting tool that returns nothing is worse than one that
        returns a partial list.
        """
        ranked = {
            "exact": self._exact(intent, limit),
            "symbol": self._symbols(intent, limit),
            "structure": self._documents(intent, limit),
        }
        if self._provider is not None:
            ranked["vector"] = self._vector(intent, limit)
        return _fuse(ranked)[:limit]

    def _exact(self, intent: str, limit: int) -> list[Candidate]:
        """Literal occurrences. The strongest evidence: it is either there or not."""
        try:
            from navegador.graph.trigram import TrigramIndex

            matches = TrigramIndex(self._store).search(intent, limit=limit)
        except Exception:
            return []
        return [
            Candidate(
                path=m.path,
                line=m.line,
                score=1.0,
                reasons=[f"text appears here: {m.text.strip()[:80]}"],
            )
            for m in matches
        ]

    def _symbols(self, intent: str, limit: int) -> list[Candidate]:
        """
        Symbols whose name resembles the intent.

        Matching on the whole intent rarely hits, so each word is tried; an
        agent asking about "rate limiting" is looking for `rate_limit`.
        """
        words = [w for w in intent.replace("_", " ").split() if len(w) > 2]
        if not words:
            return []
        found: list[Candidate] = []
        seen: set[tuple[str, int]] = set()
        for word in words[:4]:
            rows = self._store.query(
                "MATCH (n) WHERE (n:Function OR n:Method OR n:Class) "
                "AND toLower(n.name) CONTAINS toLower($word) "
                "RETURN n.name, coalesce(n.file_path,''), coalesce(n.line_start,0), labels(n)[0] "
                "LIMIT $limit",
                {"word": word, "limit": limit},
            ).result_set
            for name, path, line, label in rows or []:
                candidate = Candidate(
                    path=path,
                    line=int(line or 0),
                    symbol=name,
                    reasons=[f"{label.lower()} named {name}"],
                )
                if candidate.key() not in seen:
                    seen.add(candidate.key())
                    found.append(candidate)
        return found[:limit]

    def _documents(self, intent: str, limit: int) -> list[Candidate]:
        """Documents mentioning the intent — decisions and prose are context."""
        rows = self._store.query(
            "MATCH (d:Document) WHERE toLower(coalesce(d.title,'')) CONTAINS toLower($intent) "
            "RETURN coalesce(d.path,''), coalesce(d.title,'') LIMIT $limit",
            {"intent": intent, "limit": limit},
        ).result_set
        return [Candidate(path=path, reasons=[f"document: {title}"]) for path, title in rows or []]

    def _vector(self, intent: str, limit: int) -> list[Candidate]:
        try:
            from navegador.intelligence.search import SemanticSearch

            hits = SemanticSearch(self._store, self._provider).search(intent, limit=limit)
        except Exception:
            return []
        return [
            Candidate(
                path=h.get("file_path", ""),
                symbol=h.get("name", ""),
                reasons=[f"semantically similar ({h.get('score', 0):.2f})"],
            )
            for h in hits
            if h.get("file_path")
        ]

    # ── scope_for ─────────────────────────────────────────────────────────

    def scope_for(self, symbol: str, depth: int = 2) -> list[str]:
        """
        Files reachable from *symbol*, for handing straight to a grep.

        Depth is inlined rather than parameterised because FalkorDB rejects a
        parameterised variable-length bound — the bug behind traversals
        silently returning nothing in 1.2.0.

        Returns paths, sorted, with the symbol's own file first if present.
        """
        depth = max(1, min(int(depth), 5))
        rows = self._store.query(
            "MATCH (n) WHERE (n:Function OR n:Method OR n:Class) AND n.name = $symbol "
            f"MATCH (n)-[:CALLS|REFERENCES|IMPORTS*1..{depth}]-(m) "
            "RETURN DISTINCT coalesce(m.file_path, '') ",
            {"symbol": symbol},
        ).result_set
        paths = {row[0] for row in rows or [] if row and row[0]}

        own = self._store.query(
            "MATCH (n) WHERE (n:Function OR n:Method OR n:Class) AND n.name = $symbol "
            "RETURN DISTINCT coalesce(n.file_path,'')",
            {"symbol": symbol},
        ).result_set
        home = {row[0] for row in own or [] if row and row[0]}
        return sorted(home) + sorted(paths - home)

    def hashes_for_paths(self, paths: list[str]) -> set[str]:
        """
        Content hashes for *paths*, so a scope can be handed to the trigram
        index — which addresses blobs by hash, not path.
        """
        if not paths:
            return set()
        rows = self._store.query(
            "MATCH (f:File) WHERE f.path IN $paths RETURN f.content_hash",
            {"paths": list(paths)},
        ).result_set
        return {row[0] for row in rows or [] if row and row[0]}

    def search_within(self, symbol: str, pattern: str, depth: int = 2, limit: int = 50):
        """
        Search only what *symbol* reaches. The reduction, end to end.

        An empty scope returns nothing rather than falling back to the whole
        repository: "no results in this scope" is a real answer, and silently
        widening it would make the scope meaningless.
        """
        from navegador.graph.trigram import TrigramIndex

        scope = self.hashes_for_paths(self.scope_for(symbol, depth=depth))
        return TrigramIndex(self._store).search(pattern, scope=scope, limit=limit)

    # ── neighbourhood ─────────────────────────────────────────────────────

    def neighbourhood(self, symbol: str) -> dict:
        """
        Callers, callees, tests and home file for *symbol*.

        Everything an agent would otherwise spend several turns rediscovering,
        in one answer.
        """

        def names(cypher: str) -> list[dict]:
            rows = self._store.query(cypher, {"symbol": symbol}).result_set
            return [{"name": row[0], "path": row[1] or ""} for row in rows or [] if row and row[0]]

        base = "MATCH (n) WHERE (n:Function OR n:Method OR n:Class) AND n.name = $symbol "
        return {
            "symbol": symbol,
            "defined_in": names(base + "RETURN n.name, coalesce(n.file_path,'') LIMIT 5"),
            "callers": names(
                base + "MATCH (c)-[:CALLS]->(n) RETURN c.name, coalesce(c.file_path,'') LIMIT 25"
            ),
            "callees": names(
                base + "MATCH (n)-[:CALLS]->(c) RETURN c.name, coalesce(c.file_path,'') LIMIT 25"
            ),
            "tests": names(
                base + "MATCH (t)-[:TESTS]->(n) RETURN t.name, coalesce(t.file_path,'') LIMIT 25"
            ),
        }
