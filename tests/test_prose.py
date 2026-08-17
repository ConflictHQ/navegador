"""
Indexing the text parsing throws away (#185).

An AST keeps the name of a function and discards the error message it raises,
the comment explaining why it exists, and the SQL in its literals. Those are
what an agent has in hand when it goes looking — a user pastes an error, not
a symbol name.

The tests worth having here are about extraction quality rather than plumbing.
An extractor that returns everything is as useless as one that returns
nothing: literals full of one-word dict keys bury the error messages that
matter, so noise rejection is asserted as carefully as recall.
"""

import pytest

from navegador.graph import GraphStore
from navegador.graph.prose import ProseIndex, extract, split_identifier
from navegador.ingestion import RepoIngester


class TestSplitIdentifier:
    def test_camel_case(self):
        assert set(split_identifier("getUserById")) >= {"get", "user", "by", "id"}

    def test_snake_case(self):
        assert set(split_identifier("parse_import_statement")) >= {
            "parse",
            "import",
            "statement",
        }

    def test_original_is_kept(self):
        """Searching the exact symbol name must still find it."""
        assert "getuserbyid" in split_identifier("getUserById")

    def test_pascal_case(self):
        assert set(split_identifier("TokenValidator")) >= {"token", "validator"}

    def test_single_letters_are_dropped(self):
        """`x` and `i` are noise in a full-text index."""
        assert "x" not in split_identifier("parse_x_value")


class TestExtract:
    def test_finds_error_messages(self):
        extracted = extract("raise ValueError('token signature invalid')")
        assert "token signature invalid" in extracted.literals

    def test_finds_comments(self):
        extracted = extract("# rate limiting guards the endpoint\nx = 1\n")
        assert "rate limiting guards the endpoint" in extracted.comments

    def test_comment_styles(self):
        assert "slashes" in extract("// slashes here\n").comments
        assert "dashes" in extract("-- dashes here\n").comments

    def test_single_word_literals_are_noise(self):
        """
        A one-word string is usually a dict key or a flag. Keeping them buries
        the error messages under thousands of tokens of nothing.
        """
        extracted = extract("d = {'name': 'id', 'kind': 'x'}")
        assert "name" not in extracted.literals.split()

    def test_triple_quoted_strings(self):
        assert "module docstring here" in extract('"""module docstring here"""').literals

    def test_identifiers_are_split(self):
        extracted = extract("def getUserById(): pass")
        assert "user" in extracted.identifiers.split()

    def test_empty_source(self):
        assert extract("").empty

    def test_truncation_bounds_a_huge_file(self):
        """A minified bundle must not put a megabyte into one property."""
        extracted = extract("x = 'a long sentence of prose here' \n" * 20000)
        assert len(extracted.literals) <= 20_000


@pytest.fixture
def indexed(tmp_path):
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "src" / "auth.py").write_text(
        "# rate limiting guards the login endpoint\n"
        "def getUserById(uid):\n"
        "    raise ValueError('token signature invalid for user')\n"
    )
    (root / "src" / "views.py").write_text(
        "def render():\n    return 'html template output here'\n"
    )
    store = GraphStore.sqlite(str(tmp_path / "g.db"))
    RepoIngester(store).ingest(root)
    return ProseIndex(store)


class TestSearch:
    def test_pasted_error_message_finds_its_file(self, indexed):
        """The motivating case: a user pastes an error, not a symbol name."""
        assert indexed.search("token signature")[0]["path"] == "src/auth.py"

    def test_comment_text_is_searchable(self, indexed):
        """ "rate limiting" appears in no symbol name, only in a comment."""
        assert indexed.search("rate limiting")[0]["path"] == "src/auth.py"

    def test_split_identifiers_close_the_gap(self, indexed):
        """ "user id" should reach getUserById."""
        assert any(h["path"] == "src/auth.py" for h in indexed.search("user id"))

    def test_ranking_separates_files(self, indexed):
        assert indexed.search("html template")[0]["path"] == "src/views.py"

    def test_nothing_matching_returns_empty(self, indexed):
        assert indexed.search("quantum chromodynamics") == []

    def test_search_without_an_index_returns_empty(self, tmp_path):
        """No index is an absence of answer, not an error."""
        store = GraphStore.sqlite(str(tmp_path / "empty.db"))
        assert ProseIndex(store).search("anything") == []

    def test_stats(self, indexed):
        assert indexed.stats()["indexed_files"] == 2
