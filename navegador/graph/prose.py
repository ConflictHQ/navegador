"""
Index the text parsing throws away (#185).

An AST keeps structure and discards most of what people actually search for.
The name of a function survives; the error message it raises does not, nor the
comment explaining why it exists, nor the SQL, URLs and feature flags sitting
in its string literals. Those are the things an agent has in hand when it goes
looking — a user pastes an error, not a symbol name.

This extracts three kinds of text per file and indexes them with FalkorDB's
full-text index, which is already on the server and was unused:

**Literals** — error messages, log lines, SQL, URLs. The single biggest search
target and previously invisible.

**Comments** — kept apart from docstrings, because a comment usually records
why something is the way it is, which is the question docstrings answer least.

**Identifiers, split on camelCase and snake_case** — so ``getUserById`` is
reachable from "user id". Cheap, and it closes most of the distance between
how code is written and how people ask about it.

Why not fold this into the trigram index: different jobs. Trigrams answer
"this exact string appears here" — precise, unranked, no notion of aboutness.
Full text answers "these files are about this" — tokenised and ranked, and
useful when the agent does not know the exact spelling. Targeting fuses both.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# One node per file holding its extracted prose. A separate label keeps the
# full-text index off File, whose properties are queried structurally on hot
# paths and should not carry a large text blob.
LABEL = "FileText"

# Quoted strings, including triple-quoted. Non-greedy, and escapes are skipped
# so an embedded quote does not end the match early.
_LITERAL = re.compile(
    r'"""(.*?)"""' r"|'''(.*?)'''" r'|"((?:[^"\\]|\\.)*)"' r"|'((?:[^'\\]|\\.)*)'",
    re.DOTALL,
)
# Line comments across the languages parsed here: #, //, --.
_COMMENT = re.compile(r"(?:^|\s)(?:#|//|--)\s?(.*)$", re.MULTILINE)
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
# camelCase and PascalCase boundaries.
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")

# Extracted text is truncated per file. A minified bundle or a vendored blob
# would otherwise put a megabyte of noise into one property and swamp ranking.
MAX_CHARS = 20_000


def split_identifier(name: str) -> list[str]:
    """
    ``getUserById`` -> ["get", "user", "by", "id"]; ``parse_import`` -> both parts.

    The original is kept as well: an agent searching the exact symbol name
    should still find it.
    """
    parts: list[str] = []
    for chunk in name.split("_"):
        if not chunk:
            continue
        parts.extend(p for p in _CAMEL.split(chunk) if p)
    lowered = [p.lower() for p in parts if len(p) > 1]
    return sorted(set(lowered + [name.lower()]))


@dataclass
class Extracted:
    literals: str = ""
    comments: str = ""
    identifiers: str = ""

    @property
    def empty(self) -> bool:
        return not (self.literals or self.comments or self.identifiers)


def extract(source: str) -> Extracted:
    """Pull literals, comments and split identifiers out of *source*."""
    literals: list[str] = []
    for match in _LITERAL.finditer(source):
        value = next((g for g in match.groups() if g), "")
        value = value.strip()
        # A one-word string is usually a dict key or a flag, not prose worth
        # ranking; keeping them buries the error messages under noise.
        if len(value) > 3 and " " in value:
            literals.append(value)

    comments = [m.group(1).strip() for m in _COMMENT.finditer(source) if m.group(1).strip()]

    words: set[str] = set()
    for match in _IDENTIFIER.finditer(source):
        words.update(split_identifier(match.group(0)))

    return Extracted(
        literals=" ".join(literals)[:MAX_CHARS],
        comments=" ".join(comments)[:MAX_CHARS],
        identifiers=" ".join(sorted(words))[:MAX_CHARS],
    )


class ProseIndex:
    """Full-text search over the literals, comments and identifiers of a graph."""

    def __init__(self, store) -> None:
        self._store = store

    def ensure_index(self) -> None:
        """
        Create the full-text index, ignoring "already indexed".

        There is no IF NOT EXISTS form, so the second call raises and there is
        nothing useful to do but continue.
        """
        try:
            self._store.query(
                f"CALL db.idx.fulltext.createNodeIndex('{LABEL}', "
                "'literals', 'comments', 'identifiers')"
            )
        except Exception:
            pass

    def index_file(self, path: str, source: str) -> bool:
        """Attach extracted text to *path*. Returns False when there was none."""
        extracted = extract(source)
        if extracted.empty:
            return False
        self.ensure_index()
        self._store.query(
            f"MERGE (t:{LABEL} {{path: $path}}) "
            "SET t.literals = $literals, t.comments = $comments, "
            "t.identifiers = $identifiers",
            {
                "path": path,
                "literals": extracted.literals,
                "comments": extracted.comments,
                "identifiers": extracted.identifiers,
            },
        )
        return True

    def search(self, query: str, limit: int = 20) -> list[dict]:
        """
        Files whose prose matches *query*, ranked.

        Returns an empty list rather than raising when nothing has been
        indexed yet — no index is an absence of answer, not an error.
        """
        try:
            rows = self._store.query(
                f"CALL db.idx.fulltext.queryNodes('{LABEL}', $query) YIELD node, score "
                "RETURN node.path, score ORDER BY score DESC LIMIT $limit",
                {"query": query, "limit": int(limit)},
            ).result_set
        except Exception:
            return []
        return [{"path": row[0], "score": float(row[1])} for row in rows or [] if row and row[0]]

    def stats(self) -> dict:
        rows = self._store.query(f"MATCH (t:{LABEL}) RETURN count(t)").result_set
        return {"indexed_files": int(rows[0][0]) if rows else 0}
