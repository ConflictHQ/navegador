"""
Content-addressed source storage (#183).

Against a real embedded store — the store is where two of these bugs lived,
so mocking it would test nothing. The binary-connection case in particular
only appears against a real Redis: the graph client decodes responses, and
compressed bytes read back through it die on the first non-UTF-8 byte.
"""

import hashlib

import pytest

from navegador.graph import GraphStore
from navegador.graph.content import ContentStore

SOURCE = '''"""A module — with an em dash, which is two bytes."""


def alpha():
    return "first"


def beta():
    raise ValueError("something went wrong: %s" % name)
'''


def sha_of(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@pytest.fixture
def store(tmp_path):
    return GraphStore.sqlite(str(tmp_path / "g.db"))


@pytest.fixture
def content(store):
    return ContentStore(store, graph="graph-one")


class TestRoundTrip:
    def test_stores_and_returns_exactly(self, content):
        sha = sha_of(SOURCE)
        assert content.put(sha, SOURCE) is True
        assert content.get(sha) == SOURCE

    def test_binary_payload_survives_a_decoding_client(self, content):
        """
        The graph connects with decode_responses=True because Cypher results
        are text. Compressed bytes read back through that connection raise
        UnicodeDecodeError, so the store uses a sibling connection with
        decoding off. Non-ASCII content is what makes it fail.
        """
        text = "# ✂ — ünïcode ✓\nvalue = 1\n" * 40
        sha = sha_of(text)
        content.put(sha, text)
        assert content.get(sha) == text

    def test_missing_blob_returns_none(self, content):
        assert content.get("0" * 64) is None

    def test_lines(self, content):
        sha = sha_of(SOURCE)
        content.put(sha, SOURCE)
        assert content.lines(sha)[3] == "def alpha():"


class TestDedup:
    def test_second_put_of_the_same_content_writes_nothing(self, content):
        sha = sha_of(SOURCE)
        assert content.put(sha, SOURCE) is True
        assert content.put(sha, SOURCE) is False

    def test_two_graphs_share_one_blob(self, store):
        """
        A vendored copy, a fork, and the same file in three workspaces are one
        blob. Several codebases on a shared server are indexed under more than
        one graph name, so this is the common case, not an edge case.
        """
        one = ContentStore(store, graph="g1")
        two = ContentStore(store, graph="g2")
        sha = sha_of(SOURCE)

        assert one.put(sha, SOURCE) is True
        assert two.put(sha, SOURCE) is False
        assert two.get(sha) == SOURCE


class TestLineResolution:
    def test_byte_offset_resolves_to_its_line(self, content):
        sha = sha_of(SOURCE)
        content.put(sha, SOURCE)
        offset = content.byte_offset_of(sha, "def beta")
        assert content.line_at(sha, offset) == (8, "def beta():")

    def test_non_ascii_shifts_byte_offsets_away_from_string_indexes(self, content):
        """
        The trap this API is named against. The em dash in the first line is
        two bytes, so a Python string index is one short of the byte offset
        and resolves to the wrong line on a long enough file.
        """
        sha = sha_of(SOURCE)
        content.put(sha, SOURCE)

        string_index = SOURCE.index("def beta")
        byte_offset = content.byte_offset_of(sha, "def beta")
        assert byte_offset > string_index, "expected the em dash to shift the offset"
        assert content.line_at(sha, byte_offset)[0] == 8

    def test_offset_past_the_end_returns_none(self, content):
        sha = sha_of(SOURCE)
        content.put(sha, SOURCE)
        assert content.line_at(sha, 10**6) is None

    def test_negative_offset_returns_none(self, content):
        sha = sha_of(SOURCE)
        content.put(sha, SOURCE)
        assert content.line_at(sha, -1) is None

    def test_offset_of_absent_needle(self, content):
        sha = sha_of(SOURCE)
        content.put(sha, SOURCE)
        assert content.byte_offset_of(sha, "no_such_symbol") is None


class TestRefcounting:
    def test_releasing_one_holder_keeps_shared_content(self, store):
        """
        Deleting a graph must not take a shared file out from under another
        graph still pointing at it.
        """
        one = ContentStore(store, graph="g1")
        two = ContentStore(store, graph="g2")
        sha = sha_of(SOURCE)
        one.put(sha, SOURCE)
        two.put(sha, SOURCE)

        assert one.release() == 0
        assert two.exists(sha)

    def test_last_holder_releases_the_blob(self, store):
        one = ContentStore(store, graph="g1")
        two = ContentStore(store, graph="g2")
        sha = sha_of(SOURCE)
        one.put(sha, SOURCE)
        two.put(sha, SOURCE)
        one.release()

        assert two.release() == 1
        assert not two.exists(sha)

    def test_release_is_idempotent(self, content):
        sha = sha_of(SOURCE)
        content.put(sha, SOURCE)
        assert content.release() == 1
        assert content.release() == 0

    def test_re_ingesting_does_not_inflate_the_holder_set(self, content):
        """
        Refs are a set, not a counter: re-ingesting the same repository twice
        must not leave a blob that takes two releases to free.
        """
        sha = sha_of(SOURCE)
        content.put(sha, SOURCE)
        content.put(sha, SOURCE)
        content.put(sha, SOURCE)
        assert content.release() == 1
        assert not content.exists(sha)


class TestEncoding:
    def test_short_content_is_stored_uncompressed(self, content):
        """
        zlib adds a header and checksum, so a short file comes back larger
        than it went in. Storing the worse of the two forms would make the
        content store grow the thing it exists to shrink.
        """
        text = "x = 1\n"
        sha = sha_of(text)
        content.put(sha, text)

        stats = content.stats()
        assert stats.compressed_bytes <= stats.original_bytes + 1  # +1 for the tag
        assert content.get(sha) == text

    def test_long_content_is_compressed(self, content):
        text = SOURCE * 50
        sha = sha_of(text)
        content.put(sha, text)

        stats = content.stats()
        assert stats.compressed_bytes < stats.original_bytes / 2

    def test_both_forms_round_trip(self, content):
        for text in ("tiny\n", SOURCE * 40, "ünïcode — ✓\n" * 5):
            sha = sha_of(text)
            content.put(sha, text)
            assert content.get(sha) == text


class TestStats:
    def test_reports_compression(self, content):
        text = SOURCE * 50
        sha = sha_of(text)
        content.put(sha, text)

        stats = content.stats()
        assert stats.blobs == 1
        assert stats.original_bytes == len(text.encode("utf-8"))
        assert stats.compressed_bytes < stats.original_bytes
        assert stats.ratio > 1.0

    def test_empty_store(self, content):
        stats = content.stats()
        assert stats.blobs == 0
        assert stats.ratio == 0.0

    def test_stats_are_per_graph(self, store):
        one = ContentStore(store, graph="g1")
        two = ContentStore(store, graph="g2")
        one.put(sha_of(SOURCE), SOURCE)

        assert one.stats().blobs == 1
        assert two.stats().blobs == 0
