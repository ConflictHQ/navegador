"""
Trigram index for substring and regex search (#184).

The fatal bug for a search index is a **false negative** — telling a caller a
string does not appear when it does. Trigrams are allowed to over-select and
then be filtered by a real match; they are never allowed to under-select. Most
of these tests exist to pin that boundary, particularly in
``required_trigrams`` where a run that looks mandatory but is not (inside a
group, behind a quantifier, one branch of an alternation) would silently drop
real results.

Where ripgrep is installed, a differential test uses it as the oracle against
this package's own source. That is worth more than any hand-written fixture:
the two implementations share nothing.
"""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from navegador.graph import GraphStore
from navegador.graph.trigram import TrigramIndex, required_trigrams, trigrams
from navegador.ingestion import RepoIngester

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def indexed(tmp_path):
    """A small repo, ingested, with its content indexed."""
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "src" / "auth.py").write_text(
        "def check_token():\n    raise ValueError('token expired')\n    # TODO: refresh the token\n"
    )
    (root / "src" / "views.py").write_text("def render():\n    return 'token expired'\n")
    (root / "src" / "quiet.py").write_text("VALUE = 1\n")

    store = GraphStore.sqlite(str(tmp_path / "g.db"))
    RepoIngester(store).ingest(root)
    index = TrigramIndex(store)
    index.index_graph()
    return index


class TestTrigrams:
    def test_windows(self):
        assert trigrams("abcd") == {"abc", "bcd"}

    def test_lowercased(self):
        assert trigrams("ABC") == {"abc"}

    def test_too_short(self):
        assert trigrams("ab") == set()


class TestRequiredTrigrams:
    """
    Every case here is a chance to produce a false negative. A pattern that
    yields trigrams the text need not contain silently loses real matches.
    """

    def test_literal(self):
        assert required_trigrams("hello", False) == {"hel", "ell", "llo"}

    def test_short_literal_cannot_narrow(self):
        assert required_trigrams("ab", False) == set()

    def test_alternation_proves_nothing(self):
        """`foo|bar` requires neither "foo" nor "bar" specifically."""
        assert required_trigrams("foo|bar", True) == set()

    def test_quantified_character_is_not_required(self):
        """`ab?cdef` may not contain "b", so "abc" is not a required trigram."""
        required = required_trigrams("ab?cdef", True)
        assert "abc" not in required
        assert {"cde", "def"} <= required

    def test_group_contents_are_not_required(self):
        """A group may be quantified later; nothing inside it is guaranteed."""
        assert required_trigrams("(abc)def", True) == {"def"}

    def test_character_class_is_not_required(self):
        assert "a-z" not in required_trigrams("[a-z]+ghijk", True)
        assert {"ghi", "hij", "ijk"} <= required_trigrams("[a-z]+ghijk", True)

    def test_escape_breaks_the_run(self):
        """`\\d` is a class, not the letter d."""
        assert "abd" not in required_trigrams(r"ab\def", True)

    def test_pure_metacharacters_yield_nothing(self):
        assert required_trigrams(r"\w+\s*", True) == set()


class TestSearch:
    def test_literal_match_returns_path_and_line(self, indexed):
        hits = indexed.search("token expired")
        located = {(h.path, h.line) for h in hits}
        assert ("src/auth.py", 2) in located
        assert ("src/views.py", 2) in located

    def test_line_text_is_returned(self, indexed):
        hit = next(h for h in indexed.search("ValueError"))
        assert "raise ValueError" in hit.text

    def test_absent_string_returns_nothing(self, indexed):
        assert indexed.search("no_such_string_anywhere") == []

    def test_regex(self, indexed):
        hits = indexed.search(r"TODO:?\s*\w+", is_regex=True)
        assert [(h.path, h.line) for h in hits] == [("src/auth.py", 3)]

    def test_short_pattern_still_works(self, indexed):
        """
        Under three characters there is no trigram to narrow with, so the
        scope is scanned. Slower, still exact — it must not return nothing.
        """
        assert any(h.line == 1 for h in indexed.search("=", limit=10))

    def test_invalid_regex_returns_empty_rather_than_raising(self, indexed):
        assert indexed.search("(unclosed", is_regex=True) == []

    def test_case_insensitive(self, indexed):
        assert indexed.search("VALUEERROR", ignore_case=True)
        assert not indexed.search("VALUEERROR")

    def test_limit(self, indexed):
        assert len(indexed.search("e", limit=2)) <= 2

    def test_results_are_ordered(self, indexed):
        hits = indexed.search("token")
        assert hits == sorted(hits, key=lambda h: (h.path, h.line))


class TestScoping:
    def test_scope_restricts_the_search(self, indexed, tmp_path):
        """
        The reduction no flat index can compute: search only these blobs.
        The scope joins the intersection rather than filtering after it.
        """
        everything = indexed.search("token expired")
        assert len({h.path for h in everything}) == 2

        one_file = {h.sha for h in everything if h.path == "src/auth.py"}
        scoped = indexed.search("token expired", scope=one_file)
        assert {h.path for h in scoped} == {"src/auth.py"}

    def test_empty_scope_finds_nothing(self, indexed):
        assert indexed.search("token expired", scope=set()) == []


class TestIndexing:
    def test_reindexing_is_a_no_op(self, indexed):
        assert indexed.index_graph() == 0

    def test_identical_content_is_indexed_once(self, tmp_path):
        """Content-addressed: a vendored copy costs one set of postings."""
        root = tmp_path / "proj"
        root.mkdir()
        body = "def duplicated_helper():\n    return 'shared'\n"
        (root / "one.py").write_text(body)
        (root / "two.py").write_text(body)

        store = GraphStore.sqlite(str(tmp_path / "g.db"))
        RepoIngester(store).ingest(root)
        index = TrigramIndex(store)
        assert index.index_graph() == 1

    def test_both_paths_are_still_reported(self, tmp_path):
        """
        One blob, two paths. Dedup must not cost a result — both files really
        do contain the line.
        """
        root = tmp_path / "proj"
        root.mkdir()
        body = "def duplicated_helper():\n    return 'shared'\n"
        (root / "one.py").write_text(body)
        (root / "two.py").write_text(body)

        store = GraphStore.sqlite(str(tmp_path / "g.db"))
        RepoIngester(store).ingest(root)
        index = TrigramIndex(store)
        index.index_graph()

        assert {h.path for h in index.search("duplicated_helper")} == {"one.py", "two.py"}


@pytest.mark.skipif(shutil.which("rg") is None, reason="ripgrep not installed")
class TestAgainstRipgrep:
    """
    Differential test on this package's own source, with ripgrep as oracle.

    Two implementations sharing no code agreeing on real input is stronger
    evidence than any fixture. A disagreement in the `missing` direction is a
    false negative and the bug that matters.
    """

    @pytest.fixture(scope="class")
    @classmethod
    def index(cls):
        """Ingesting the whole package takes seconds, so do it once per class."""
        store = GraphStore.sqlite(os.path.join(tempfile.mkdtemp(), "g.db"))
        RepoIngester(store).ingest(REPO)
        index = TrigramIndex(store)
        index.index_graph()
        return index

    @staticmethod
    def ripgrep(pattern: str, regex: bool) -> set[tuple[str, int]]:
        args = ["rg", "--no-heading", "-n", "--no-messages"]
        if not regex:
            args.append("--fixed-strings")
        args += [pattern, "--glob", "*.py", "."]
        out = subprocess.run(args, cwd=REPO, capture_output=True, text=True).stdout
        hits = set()
        for line in out.splitlines():
            parts = line.split(":", 2)
            if len(parts) == 3:
                hits.add((parts[0].lstrip("./"), int(parts[1])))
        return hits

    @pytest.mark.parametrize(
        "pattern,regex",
        [
            ("content_hash", False),
            ("def resolve", False),
            ("GRAPH.LIST", False),
            ("raise ValueError", False),
            ("# noqa", False),
            (r"def _\w+_store", True),
            (r"self\._conn\.\w+", True),
        ],
    )
    def test_matches_ripgrep_exactly(self, index, pattern, regex):
        theirs = self.ripgrep(pattern, regex)
        ours = {
            (h.path, h.line)
            for h in index.search(pattern, is_regex=regex, limit=1_000_000)
            if h.path.endswith(".py")
        }
        assert not (theirs - ours), f"false negatives: {sorted(theirs - ours)[:5]}"
        assert not (ours - theirs), f"false positives: {sorted(ours - theirs)[:5]}"
