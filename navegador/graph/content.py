"""
Content-addressed source storage beside the graph (#183).

The graph keeps structure and throws the text away: ``File`` nodes carry a
path, a language, a line count and a ``content_hash``, and symbol nodes carry
line ranges. Nothing carries the source. There is therefore nothing to match a
literal against and nothing to return a line from, which is what lexical
search needs (#184, #185).

Content lives in Redis beside the graph rather than inside it. A graph
database is the wrong shape for a blob store, and keeping it out means graph
size, ``DUMP`` output and traversal cost are unchanged by this.

Two properties come free from addressing by ``content_hash``, which ingest
already computes:

**Dedup.** A vendored copy, a fork and the same file in three workspaces are
one blob. On this machine several codebases are indexed two and three times
under different graph names; identical files across them cost storage once.

**Incremental.** An unchanged file has an unchanged hash, so re-ingesting a
repository writes nothing.

Compression is stdlib ``zlib``. Source compresses well and a compression
dependency is not worth a supply-chain entry (#189).
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass

# Compressed source, keyed by content hash.
BLOB = "nav:blob:{sha}"
# Graphs referencing a blob. A set, not a counter: re-ingesting the same
# repository must not inflate the count, and a counter cannot survive a graph
# being deleted without also being decremented, which nothing guarantees.
BLOB_REFS = "nav:blob:{sha}:refs"
# Reverse index, so releasing a graph does not mean scanning every blob.
GRAPH_BLOBS = "nav:graph:{graph}:blobs"

# zlib level 6. Level 9 buys a few percent for substantially more CPU on a
# path that runs once per changed file during ingest.
LEVEL = 6


ZLIB = b"\x01"  # payload is zlib-compressed
RAW = b"\x00"  # payload is stored as-is


def _encode(text: str) -> bytes:
    """
    Compress, unless compressing makes it bigger.

    zlib adds a header and a checksum, so on a short file the "compressed"
    form is larger than the original — a 81-byte fixture came back as 93.
    A one-byte tag says which form the payload is in, which costs less than
    ever storing the worse of the two.
    """
    encoded = text.encode("utf-8")
    squeezed = zlib.compress(encoded, LEVEL)
    if len(squeezed) < len(encoded):
        return ZLIB + squeezed
    return RAW + encoded


def _decode(blob: bytes) -> str:
    tag, payload = blob[:1], blob[1:]
    if tag == ZLIB:
        payload = zlib.decompress(payload)
    return payload.decode("utf-8", errors="replace")


def _binary_connection(connection):
    """
    A sibling connection that does not decode responses.

    The graph client connects with ``decode_responses=True`` because Cypher
    results are text. Compressed bytes read back through it raise
    UnicodeDecodeError on the first byte that is not valid UTF-8, so this
    reuses the same socket settings with decoding turned off rather than
    base64-ing the payload and paying a third more storage to avoid it.
    """
    import redis as redis_lib

    kwargs = dict(getattr(connection.connection_pool, "connection_kwargs", {}))
    if not kwargs.get("decode_responses"):
        return connection
    kwargs["decode_responses"] = False
    # A unix socket pool keys on `path`; a TCP pool on host/port.
    if "path" in kwargs:
        return redis_lib.Redis(unix_socket_path=kwargs.pop("path"), **kwargs)
    return redis_lib.Redis(**kwargs)


@dataclass
class ContentStats:
    blobs: int = 0
    compressed_bytes: int = 0
    original_bytes: int = 0

    @property
    def ratio(self) -> float:
        if not self.compressed_bytes:
            return 0.0
        return self.original_bytes / self.compressed_bytes

    def to_dict(self) -> dict:
        return {
            "blobs": self.blobs,
            "compressed_bytes": self.compressed_bytes,
            "original_bytes": self.original_bytes,
            "ratio": round(self.ratio, 2),
        }


class ContentStore:
    """
    Source text addressed by content hash, reference-counted per graph.

    Args:
        store: A :class:`~navegador.graph.GraphStore`; its Redis connection is
            reused so content lands on the same server as the graph.
        graph: Owning graph name. Defaults to the store's own.
    """

    def __init__(self, store, graph: str | None = None) -> None:
        self._conn = _binary_connection(store._client.connection)
        self._graph = graph or store.graph_name

    # ── Write ─────────────────────────────────────────────────────────────

    def put(self, sha: str, text: str) -> bool:
        """
        Store *text* under *sha* and record this graph as a referrer.

        Returns True when the blob was newly written, False when it already
        existed — the dedup and incremental cases, which are the common ones.
        """
        key = BLOB.format(sha=sha)
        self._conn.sadd(BLOB_REFS.format(sha=sha), self._graph)
        self._conn.sadd(GRAPH_BLOBS.format(graph=self._graph), sha)
        if self._conn.exists(key):
            return False
        self._conn.set(key, _encode(text))
        return True

    # ── Read ──────────────────────────────────────────────────────────────

    def get(self, sha: str) -> str | None:
        blob = self._conn.get(BLOB.format(sha=sha))
        if blob is None:
            return None
        return _decode(blob)

    def lines(self, sha: str) -> list[str]:
        text = self.get(sha)
        return text.splitlines() if text is not None else []

    def line_at(self, sha: str, byte_offset: int) -> tuple[int, str] | None:
        """
        The 1-indexed line containing *byte_offset*, and its text.

        The offset is a **byte** offset into the UTF-8 encoding, because that
        is what a byte-oriented matcher produces. Passing a Python string
        index instead is silently wrong on any file containing non-ASCII: on
        this module's own source, 187 non-ASCII characters put the two 374
        bytes apart and the answer nine lines off. It is the obvious mistake,
        so the parameter is named for the unit.

        Line offsets are computed here rather than stored alongside the blob:
        resolving an offset already requires the content, and the scan that
        decompresses it counts newlines in the same pass. A stored offset
        table would be a second thing to keep in sync for no saved work.
        """
        text = self.get(sha)
        if text is None or byte_offset < 0:
            return None
        encoded = text.encode("utf-8")
        if byte_offset >= len(encoded):
            return None
        number = encoded[:byte_offset].count(b"\n") + 1
        lines = text.splitlines()
        if number > len(lines):
            return None
        return number, lines[number - 1]

    def byte_offset_of(self, sha: str, needle: str) -> int | None:
        """
        Byte offset of the first occurrence of *needle*, for callers holding
        a string rather than a byte position. Saves them the encode dance
        that :meth:`line_at` documents.
        """
        text = self.get(sha)
        if text is None:
            return None
        index = text.find(needle)
        if index < 0:
            return None
        return len(text[:index].encode("utf-8"))

    def exists(self, sha: str) -> bool:
        return bool(self._conn.exists(BLOB.format(sha=sha)))

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def release(self, graph: str | None = None) -> int:
        """
        Drop *graph*'s claim on its blobs, deleting those nobody else wants.

        Called when a graph is pruned. Without it a deleted graph would leave
        its content resident forever; with naive deletion instead, pruning one
        of two graphs sharing a vendored file would take the file out from
        under the other.

        Returns the number of blobs actually deleted.
        """
        name = graph or self._graph
        index = GRAPH_BLOBS.format(graph=name)
        shas = [s.decode() if isinstance(s, bytes) else s for s in self._conn.smembers(index)]
        deleted = 0
        for sha in shas:
            refs = BLOB_REFS.format(sha=sha)
            self._conn.srem(refs, name)
            if self._conn.scard(refs) == 0:
                self._conn.delete(BLOB.format(sha=sha), refs)
                deleted += 1
        self._conn.delete(index)
        return deleted

    def stats(self) -> ContentStats:
        """
        Size of this graph's content, reported separately from the graph.

        Original size is derived by decompressing, so this is a reporting
        call rather than something to put on a hot path.
        """
        index = GRAPH_BLOBS.format(graph=self._graph)
        shas = [s.decode() if isinstance(s, bytes) else s for s in self._conn.smembers(index)]
        stats = ContentStats()
        for sha in shas:
            blob = self._conn.get(BLOB.format(sha=sha))
            if blob is None:
                continue
            stats.blobs += 1
            stats.compressed_bytes += len(blob)
            stats.original_bytes += len(_decode(blob).encode("utf-8"))
        return stats
