"""
Tests for FossilIngester — wiki and ticket ingestion from Fossil SCM.
"""

from unittest.mock import MagicMock

from navegador.graph.schema import EdgeType, NodeLabel
from navegador.ingestion.fossil import FossilIngester, _extract_terms

# ── Helpers ───────────────────────────────────────────────────────────────────


def _mock_store():
    store = MagicMock()
    store.query.return_value = MagicMock(result_set=[])
    return store


def _mock_adapter(repo_path="/code/myrepo", pages=None, wiki_content="", tickets=None):
    adapter = MagicMock()
    adapter.repo_path = repo_path
    adapter.wiki_pages.return_value = pages or []
    adapter.wiki_export.return_value = wiki_content
    adapter.ticket_list.return_value = tickets or []
    return adapter


# ── _extract_terms ────────────────────────────────────────────────────────────


class TestExtractTerms:
    def test_markdown_headings(self):
        md = "# Auth\n## JWT Tokens\nsome text"
        terms = _extract_terms(md)
        assert "Auth" in terms
        assert "JWT Tokens" in terms

    def test_bold_terms(self):
        md = "The **session_store** handles state. Also **redis** is used."
        terms = _extract_terms(md)
        assert "session_store" in terms
        assert "redis" in terms

    def test_creole_headings(self):
        md = "== Overview ==\n=== Setup ===\ntext"
        terms = _extract_terms(md)
        assert "Overview" in terms
        assert "Setup" in terms

    def test_deduplication(self):
        md = "# Auth\n# Auth\n**Auth**"
        terms = _extract_terms(md)
        assert terms.count("Auth") == 1

    def test_empty_content(self):
        assert _extract_terms("") == []


# ── FossilIngester construction ───────────────────────────────────────────────


class TestFossilIngesterInit:
    def test_repo_name_from_adapter_path(self):
        store = _mock_store()
        adapter = _mock_adapter(repo_path="/repos/my-project")
        ingester = FossilIngester(store, adapter)
        assert ingester.repo_name == "my-project"

    def test_explicit_repo_name(self):
        store = _mock_store()
        adapter = _mock_adapter()
        ingester = FossilIngester(store, adapter, repo_name="custom")
        assert ingester.repo_name == "custom"


# ── ingest_wiki ───────────────────────────────────────────────────────────────


class TestIngestWiki:
    def test_creates_wiki_page_nodes(self):
        store = _mock_store()
        adapter = _mock_adapter(pages=["Home", "Setup"], wiki_content="# Heading\ntext")
        ingester = FossilIngester(store, adapter, repo_name="myrepo")

        stats = ingester.ingest_wiki()

        assert stats["pages"] == 2
        assert store.create_node.call_count == 2
        first_call = store.create_node.call_args_list[0]
        assert first_call[0][0] == NodeLabel.WikiPage
        props = first_call[0][1]
        assert props["source"] == "fossil"
        assert props["name"] == "Home"

    def test_empty_wiki(self):
        store = _mock_store()
        adapter = _mock_adapter(pages=[])
        ingester = FossilIngester(store, adapter, repo_name="myrepo")

        stats = ingester.ingest_wiki()

        assert stats["pages"] == 0
        assert stats["edges"] == 0
        store.create_node.assert_not_called()

    def test_documents_edge_created_when_term_matches(self):
        store = _mock_store()
        # Store returns a matching Function node for the term "validate_token"
        store.query.return_value = MagicMock(result_set=[["validate_token"]])
        adapter = _mock_adapter(pages=["Auth"], wiki_content="# validate_token\nDetails.")
        ingester = FossilIngester(store, adapter, repo_name="myrepo")

        stats = ingester.ingest_wiki()

        assert stats["edges"] >= 1
        edge_calls = store.create_edge.call_args_list
        assert any(
            c[0][2] == EdgeType.DOCUMENTS
            for c in edge_calls
        )

    def test_no_documents_edge_when_no_match(self):
        store = _mock_store()
        store.query.return_value = MagicMock(result_set=[])
        adapter = _mock_adapter(pages=["Home"], wiki_content="# UnknownTerm\ntext")
        ingester = FossilIngester(store, adapter, repo_name="myrepo")

        stats = ingester.ingest_wiki()

        assert stats["edges"] == 0
        store.create_edge.assert_not_called()

    def test_content_stored_on_node(self):
        store = _mock_store()
        adapter = _mock_adapter(pages=["Arch"], wiki_content="# Architecture\nDetails here.")
        ingester = FossilIngester(store, adapter, repo_name="myrepo")
        ingester.ingest_wiki()

        props = store.create_node.call_args[0][1]
        assert "Architecture" in props["content"]


# ── ingest_tickets ────────────────────────────────────────────────────────────


class TestIngestTickets:
    def _make_ticket(self, **overrides):
        base = {
            "tkt_uuid": "abc123",
            "title": "Fix login bug",
            "status": "open",
            "type": "bug",
            "priority": "high",
            "severity": "critical",
            "assignee": "alice",
            "resolution": "",
            "comment": "Login fails after JWT expiry.",
            "tkt_mtime": "2024-01-15",
        }
        base.update(overrides)
        return base

    def test_creates_ticket_node(self):
        store = _mock_store()
        adapter = _mock_adapter(tickets=[self._make_ticket()])
        ingester = FossilIngester(store, adapter, repo_name="myrepo")

        stats = ingester.ingest_tickets()

        assert stats["tickets"] == 1
        store.create_node.assert_called_once()
        props = store.create_node.call_args[0][1]
        assert props["ticket_id"] == "abc123"
        assert props["title"] == "Fix login bug"
        assert props["source"] == "fossil"
        assert props["repo"] == "myrepo"

    def test_empty_tickets(self):
        store = _mock_store()
        adapter = _mock_adapter(tickets=[])
        ingester = FossilIngester(store, adapter, repo_name="myrepo")

        stats = ingester.ingest_tickets()

        assert stats["tickets"] == 0
        store.create_node.assert_not_called()

    def test_belongs_to_edge_when_repo_exists(self):
        store = _mock_store()
        store.query.return_value = MagicMock(result_set=[["myrepo"]])
        adapter = _mock_adapter(tickets=[self._make_ticket()])
        ingester = FossilIngester(store, adapter, repo_name="myrepo")

        stats = ingester.ingest_tickets()

        assert stats["edges"] == 1
        edge_calls = store.create_edge.call_args_list
        assert any(c[0][2] == EdgeType.BELONGS_TO for c in edge_calls)

    def test_no_belongs_to_edge_when_repo_missing(self):
        store = _mock_store()
        store.query.return_value = MagicMock(result_set=[])
        adapter = _mock_adapter(tickets=[self._make_ticket()])
        ingester = FossilIngester(store, adapter, repo_name="myrepo")

        stats = ingester.ingest_tickets()

        assert stats["edges"] == 0
        store.create_edge.assert_not_called()

    def test_skips_empty_tickets(self):
        store = _mock_store()
        adapter = _mock_adapter(tickets=[{"tkt_uuid": "", "title": ""}])
        ingester = FossilIngester(store, adapter, repo_name="myrepo")

        stats = ingester.ingest_tickets()

        assert stats["tickets"] == 0
        store.create_node.assert_not_called()

    def test_multiple_tickets(self):
        store = _mock_store()
        adapter = _mock_adapter(tickets=[
            self._make_ticket(tkt_uuid="t1", title="Bug A"),
            self._make_ticket(tkt_uuid="t2", title="Bug B"),
            self._make_ticket(tkt_uuid="t3", title="Feature C"),
        ])
        ingester = FossilIngester(store, adapter, repo_name="myrepo")

        stats = ingester.ingest_tickets()

        assert stats["tickets"] == 3
        assert store.create_node.call_count == 3

    def test_alternative_column_names(self):
        """Fossil may use different column names depending on schema."""
        store = _mock_store()
        ticket = {
            "uuid": "xyz789",
            "summary": "Alt column names ticket",
            "tkt_status": "closed",
            "tkt_type": "feature",
            "tkt_priority": "low",
            "tkt_severity": "minor",
            "assigned_to": "bob",
            "tkt_resolution": "fixed",
            "description": "Resolved via PR #42.",
            "mtime": "2024-03-01",
        }
        adapter = _mock_adapter(tickets=[ticket])
        ingester = FossilIngester(store, adapter, repo_name="myrepo")

        stats = ingester.ingest_tickets()

        assert stats["tickets"] == 1
        props = store.create_node.call_args[0][1]
        assert props["ticket_id"] == "xyz789"
        assert props["title"] == "Alt column names ticket"
        assert props["status"] == "closed"


# ── vcs._parse_fossil_tickets ─────────────────────────────────────────────────


class TestParseFossilTickets:
    def test_parses_tab_separated_output(self):
        from navegador.vcs import _parse_fossil_tickets

        output = "tkt_uuid\ttitle\tstatus\nabc123\tFix bug\topen\ndef456\tAdd feature\tclosed\n"
        tickets = _parse_fossil_tickets(output)
        assert len(tickets) == 2
        assert tickets[0]["tkt_uuid"] == "abc123"
        assert tickets[0]["title"] == "Fix bug"
        assert tickets[1]["status"] == "closed"

    def test_empty_output(self):
        from navegador.vcs import _parse_fossil_tickets

        assert _parse_fossil_tickets("") == []

    def test_header_only(self):
        from navegador.vcs import _parse_fossil_tickets

        assert _parse_fossil_tickets("tkt_uuid\ttitle\n") == []

    def test_respects_limit(self):
        from navegador.vcs import _parse_fossil_tickets

        rows = ["id\ttitle"] + [f"t{i}\tTicket {i}" for i in range(10)]
        output = "\n".join(rows)
        tickets = _parse_fossil_tickets(output, limit=3)
        assert len(tickets) == 3

    def test_short_rows_get_empty_string(self):
        from navegador.vcs import _parse_fossil_tickets

        output = "tkt_uuid\ttitle\tstatus\nabc\tOnly two cols\n"
        tickets = _parse_fossil_tickets(output)
        assert tickets[0]["status"] == ""
