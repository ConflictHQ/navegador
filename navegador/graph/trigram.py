"""
Trigram index for substring and regex search (#184).

The graph answers structural questions well and exact-text questions not at
all, yet agents search for literals constantly — an error message, a feature
flag, a magic constant, an env var, a TODO. A graph cannot answer those, and
embeddings answer them badly because vectors are weak on exact tokens.

This is the Zoekt design adapted to Redis, so it lives on the same shared
server as the graph and adds no external dependency (#189).

**Trigrams narrow; they never decide.** A query is reduced to the trigrams it
must contain, those postings are intersected to a candidate set, and each
candidate is then matched with the real pattern against its stored content.
The index is therefore allowed to over-select but never to under-select, and
results are exact rather than approximate.

**Scoping is free.** Postings hold blob ids and so does the per-graph blob
index, so restricting a search to one repository — or to the files reachable
from a symbol (#186) — is another set in the same intersection rather than a
filter applied afterwards. That is the reduction no flat index can compute,
and the reason this belongs inside navegador rather than shelling out to
ripgrep.

Postings are keyed by content hash, so the dedup and incremental properties
of the content store carry over: a vendored copy indexed twice costs one set
of postings, and re-indexing unchanged content is a no-op.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from navegador.graph.content import GRAPH_BLOBS, ContentStore, _binary_connection

# trigram -> set of blob ids. Ids rather than hashes: a 64-character hex digest
# in every posting would dwarf the content it points at, while small integers
# let Redis hold the set in its compact intset encoding.
POSTING = "nav:tri:{tri}"
# Two-way mapping between content hash and blob id.
BLOB_ID = "nav:blobid:{sha}"
BLOB_SHA = "nav:blobsha:{bid}"
BLOB_SEQ = "nav:blobseq"
# Blobs already indexed, so re-indexing is a cheap no-op.
INDEXED = "nav:tri:indexed"
# Blob *ids* this graph holds, maintained at index time. Postings hold ids and
# the content store's index holds hashes, so without this every search rebuilt
# the mapping with one round trip per blob — a fixed 22ms floor on a 262-file
# corpus, paid even by a search matching nothing.
GRAPH_IDS = "nav:tri:ids:{graph}"

# Below this a pattern has no trigram to narrow with and the scope is scanned.
MIN_TRIGRAM = 3

# Characters that make a run of text something other than a literal.
_META = set(".^$*+?{}[]()|\\")
# A literal run followed by one of these is optional or repeated, so it is not
# guaranteed to appear: `ab?` does not require "ab".
_QUANTIFIERS = set("?*{")


@dataclass
class Match:
    """One matching line, with enough provenance to act on."""

    path: str
    line: int
    text: str
    sha: str

    def to_dict(self) -> dict:
        return {"path": self.path, "line": self.line, "text": self.text, "sha": self.sha}


def trigrams(text: str) -> set[str]:
    """Distinct lowercased 3-character windows."""
    lowered = text.lower()
    return {lowered[i : i + 3] for i in range(len(lowered) - 2)}


def required_trigrams(pattern: str, is_regex: bool) -> set[str]:
    """
    Trigrams every match must contain, or an empty set when none can be proven.

    Over-requiring here causes false negatives, which is the one failure a
    search index may not have: the caller would be told a string does not
    appear when it does. So a run only counts when it is unconditionally
    present — not inside a group or character class, and not followed by a
    quantifier that could make it vanish.

    An empty result is not failure. It means "no narrowing available", and the
    caller scans the scope instead — slower, still exact.
    """
    if not is_regex:
        return trigrams(pattern) if len(pattern) >= MIN_TRIGRAM else set()

    # Alternation anywhere means no single run is guaranteed: `foo|bar` must
    # contain neither "foo" nor "bar" specifically.
    if "|" in pattern.replace("\\|", ""):
        return set()

    runs: list[str] = []
    current: list[str] = []
    depth = 0
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if char == "\\":
            current = []  # an escape may be a class like \d; stop the run
            index += 2
            continue
        if char in "([":
            depth += 1
            current = []
        elif char in ")]":
            depth = max(0, depth - 1)
            current = []
        elif char in _META:
            # A quantifier makes the character before it optional, so that
            # character cannot be part of a required run.
            if char in _QUANTIFIERS and current:
                current.pop()
            if len(current) >= MIN_TRIGRAM:
                runs.append("".join(current))
            current = []
        elif depth == 0:
            current.append(char)
        index += 1

    if len(current) >= MIN_TRIGRAM:
        runs.append("".join(current))

    required: set[str] = set()
    for run in runs:
        required |= trigrams(run)
    return required


class TrigramIndex:
    """
    Substring and regex search over stored content, narrowed by trigrams.

    Args:
        store: A :class:`~navegador.graph.GraphStore`.
        graph: Owning graph name. Defaults to the store's own.
    """

    def __init__(self, store, graph: str | None = None) -> None:
        self._store = store
        self._conn = _binary_connection(store._client.connection)
        self._graph = graph or store.graph_name
        self._content = ContentStore(store, graph=self._graph)

    # ── Blob identity ─────────────────────────────────────────────────────

    def _blob_id(self, sha: str, create: bool = False) -> int | None:
        existing = self._conn.get(BLOB_ID.format(sha=sha))
        if existing is not None:
            return int(existing)
        if not create:
            return None
        new_id = int(self._conn.incr(BLOB_SEQ))
        self._conn.set(BLOB_ID.format(sha=sha), new_id)
        self._conn.set(BLOB_SHA.format(bid=new_id), sha)
        return new_id

    def _sha_for(self, blob_id: int) -> str | None:
        raw = self._conn.get(BLOB_SHA.format(bid=int(blob_id)))
        return raw.decode() if isinstance(raw, bytes) else raw

    # ── Index ─────────────────────────────────────────────────────────────

    def index_blob(self, sha: str, text: str) -> bool:
        """
        Add one blob's trigrams. Returns False when it was already indexed.

        Content-addressed, so the same file appearing in three repositories is
        indexed once and re-ingesting an unchanged repository does nothing.
        """
        blob_id = self._blob_id(sha, create=True)
        # Recorded even when the blob was indexed by another graph: postings
        # are shared, but which graph holds the blob is per graph.
        self._conn.sadd(GRAPH_IDS.format(graph=self._graph), blob_id)
        if self._conn.sismember(INDEXED, sha):
            return False
        grams = trigrams(text)
        if grams:
            pipe = self._conn.pipeline(transaction=False)
            for gram in grams:
                pipe.sadd(POSTING.format(tri=gram), blob_id)
            pipe.sadd(INDEXED, sha)
            pipe.execute()
        else:
            self._conn.sadd(INDEXED, sha)
        return True

    def index_graph(self, limit: int | None = None) -> int:
        """Index every blob this graph holds. Returns the number newly indexed."""
        shas = [
            s.decode() if isinstance(s, bytes) else s
            for s in self._conn.smembers(GRAPH_BLOBS.format(graph=self._graph))
        ]
        indexed = 0
        for sha in shas:
            if limit is not None and indexed >= limit:
                break
            text = self._content.get(sha)
            if text is None:
                continue
            if self.index_blob(sha, text):
                indexed += 1
        return indexed

    # ── Search ────────────────────────────────────────────────────────────

    def _candidate_ids(self, required: set[str], scope_key: str) -> list[int]:
        """
        Blob ids that hold every required trigram and are in scope.

        The scope set joins the intersection rather than filtering after it,
        so a search restricted to one repository never materialises the other
        repositories' candidates at all.
        """
        keys = [POSTING.format(tri=g) for g in sorted(required)]
        raw = self._conn.sinter([scope_key, *keys]) if keys else self._conn.smembers(scope_key)
        return [int(v) for v in raw]

    def _scope_key(self, scope: set[str] | None) -> tuple[str, bool]:
        """
        A Redis key holding the blob ids to search, and whether it is temporary.

        The unscoped case is the common one and must cost nothing: it returns
        the persistent per-graph id set maintained at index time. Building it
        per search instead meant one round trip per blob before any matching
        began, which dominated every query regardless of how few matched.

        A caller-supplied scope still materialises a short-lived key, since
        the set is different every time.
        """
        if scope is None:
            return GRAPH_IDS.format(graph=self._graph), False

        ids = [i for i in (self._blob_id(s) for s in scope) if i is not None]
        key = f"nav:tri:scope:{self._graph}:{id(self)}"
        self._conn.delete(key)
        if ids:
            self._conn.sadd(key, *ids)
            self._conn.expire(key, 60)
        return key, True

    def search(
        self,
        pattern: str,
        is_regex: bool = False,
        scope: set[str] | None = None,
        limit: int = 100,
        ignore_case: bool = False,
    ) -> list[Match]:
        """
        Lines matching *pattern*, with file and line number.

        Args:
            pattern: Literal substring, or a regular expression when
                *is_regex* is set.
            scope: Content hashes to search within. None means this graph.
            limit: Maximum matches returned.
            ignore_case: Case-insensitive matching.

        Results are exact: trigrams only choose which blobs to look at, and
        every returned line was matched with the real pattern.
        """
        try:
            flags = re.IGNORECASE if ignore_case else 0
            matcher = re.compile(pattern if is_regex else re.escape(pattern), flags)
        except re.error:
            return []

        scope_key, temporary = self._scope_key(scope)
        try:
            candidates = self._candidate_ids(required_trigrams(pattern, is_regex), scope_key)
            shas = [s for s in (self._sha_for(i) for i in candidates) if s]
            paths = self._paths_for(shas)

            matches: list[Match] = []
            for sha in shas:
                text = self._content.get(sha)
                if text is None:
                    continue
                hit_lines = [
                    (number, line)
                    for number, line in enumerate(text.splitlines(), start=1)
                    if matcher.search(line)
                ]
                if not hit_lines:
                    continue
                for path in paths.get(sha, [""]):
                    for number, line in hit_lines:
                        matches.append(Match(path=path, line=number, text=line, sha=sha))
                        if len(matches) >= limit:
                            return sorted(matches, key=lambda m: (m.path, m.line))
            return sorted(matches, key=lambda m: (m.path, m.line))
        finally:
            if temporary:
                self._conn.delete(scope_key)

    def _paths_for(self, shas: list[str]) -> dict[str, list[str]]:
        """
        Map content hashes back to the paths holding them, via the graph.

        One hash can map to several paths — that is dedup working, not an
        error, and every one of them is a real place the line appears.
        """
        if not shas:
            return {}
        result = self._store.query(
            "MATCH (f:File) WHERE f.content_hash IN $shas RETURN f.content_hash, f.path",
            {"shas": list(shas)},
        ).result_set
        paths: dict[str, list[str]] = {}
        for row in result or []:
            paths.setdefault(row[0], []).append(row[1])
        return paths

    def stats(self) -> dict:
        indexed = self._conn.scard(INDEXED)
        return {"indexed_blobs": int(indexed)}
