"""Tests for #125 — pm ingest pulls issue comment threads, extracts Decision
nodes from them via LLM, and retrofits decisions into a brain's memory store.

Graph assertions run against real embedded FalkorDB stores; the GitHub API
and LLM provider are mocked at their seams."""

import json
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from navegador.cli.commands import main
from navegador.graph.store import GraphStore
from navegador.ingestion.memory import MemoryIngester
from navegador.pm import TicketIngester, _strip_code_fences, retrofit_decisions

# ── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def store(tmp_path_factory):
    s = GraphStore.sqlite(str(tmp_path_factory.mktemp("pm") / "graph.db"))
    yield s
    s.close()


@pytest.fixture(autouse=True)
def _clean(store):
    store.clear()


_ISSUE = {
    "number": 7,
    "title": "Pick a cache layer",
    "body": "We need caching.",
    "html_url": "https://github.com/o/r/issues/7",
    "labels": [],
    "assignees": [],
    "comments": 2,
}

_COMMENTS = [
    {"user": {"login": "leo"}, "body": "Redis fits the existing infra."},
    {"user": {"login": "ana"}, "body": "Agreed — decision: use Redis over memcached."},
]


def _fake_fetch(url, headers):
    if "/comments" in url:
        return list(_COMMENTS)
    return [dict(_ISSUE)]


def _ticket_row(store):
    result = store.query("MATCH (t:Rule) RETURN t.name, t.discussion, t.comments_count LIMIT 1")
    return result.result_set[0]


# ── Comment ingestion ──────────────────────────────────────────────────────


class TestCommentIngestion:
    def test_comments_attached_to_ticket(self, store):
        ing = TicketIngester(store)
        with patch.object(TicketIngester, "_fetch_json", side_effect=_fake_fetch):
            stats = ing.ingest_github_issues("o/r")

        assert stats["tickets"] == 1
        assert stats["comments"] == 2
        name, discussion, comments_count = _ticket_row(store)
        assert name == "#7: Pick a cache layer"
        assert "**leo**: Redis fits the existing infra." in discussion
        assert "**ana**: Agreed" in discussion
        assert comments_count == 2

    def test_no_comments_flag_skips_fetch(self, store):
        ing = TicketIngester(store)
        with patch.object(TicketIngester, "_fetch_json", side_effect=_fake_fetch) as fetch:
            stats = ing.ingest_github_issues("o/r", include_comments=False)

        assert stats["comments"] == 0
        assert fetch.call_count == 1  # only the issue list, no comment calls
        _, discussion, _ = _ticket_row(store)
        assert discussion == ""

    def test_comment_fetch_failure_degrades_gracefully(self, store):
        def _failing_fetch(url, headers):
            if "/comments" in url:
                raise OSError("rate limited")
            return [dict(_ISSUE)]

        ing = TicketIngester(store)
        with patch.object(TicketIngester, "_fetch_json", side_effect=_failing_fetch):
            stats = ing.ingest_github_issues("o/r")

        assert stats["tickets"] == 1
        assert stats["comments"] == 0


# ── Decision extraction ────────────────────────────────────────────────────


_LLM_RESPONSE = """```json
[{"name": "Use Redis for caching",
  "description": "Redis chosen as the cache layer.",
  "rationale": "Fits the existing infra.",
  "alternatives": "memcached",
  "code_refs": ["get_cached"]}]
```"""


def _provider(response):
    provider = MagicMock()
    provider.complete.return_value = response
    return provider


class TestDecisionExtraction:
    def _seed(self, store):
        ing = TicketIngester(store)
        with patch.object(TicketIngester, "_fetch_json", side_effect=_fake_fetch):
            ing.ingest_github_issues("o/r")
        store.create_node("Function", {"name": "get_cached", "file_path": "cache.py"})
        return ing

    def test_decisions_extracted_and_linked(self, store):
        ing = self._seed(store)
        with patch("navegador.llm.get_provider", return_value=_provider(_LLM_RESPONSE)):
            stats = ing.extract_decisions(domain="r")

        assert stats == {"tickets_scanned": 1, "decisions": 1, "code_links": 1}
        result = store.query("MATCH (d:Decision) RETURN d.name, d.rationale, d.status").result_set
        assert result == [["Use Redis for caching", "Fits the existing infra.", "extracted"]]

        ticket_edges = store.query(
            "MATCH (d:Decision)-[:DOCUMENTS]->(t:Rule) RETURN t.name"
        ).result_set
        assert ticket_edges == [["#7: Pick a cache layer"]]
        code_edges = store.query(
            "MATCH (d:Decision)-[:DOCUMENTS]->(f:Function) RETURN f.name"
        ).result_set
        assert code_edges == [["get_cached"]]

    def test_invalid_llm_json_is_skipped(self, store):
        ing = self._seed(store)
        with patch("navegador.llm.get_provider", return_value=_provider("not json at all")):
            stats = ing.extract_decisions(domain="r")

        assert stats["decisions"] == 0
        assert (store.query("MATCH (d:Decision) RETURN count(d)").result_set)[0][0] == 0

    def test_strip_code_fences(self):
        assert _strip_code_fences('```json\n[{"a": 1}]\n```') == '[{"a": 1}]'
        assert _strip_code_fences('[{"a": 1}]') == '[{"a": 1}]'


# ── Retrofit into brain memory ─────────────────────────────────────────────


class TestRetrofit:
    def _seed_decision(self, store):
        store.create_node(
            "Decision",
            {
                "name": "Use Redis for caching",
                "description": "Redis chosen as the cache layer.",
                "domain": "r",
                "rationale": "Fits the existing infra.",
                "alternatives": "memcached",
                "date": "",
                "status": "extracted",
            },
        )

    def test_markdown_round_trips_through_memory_ingester(self, store, tmp_path):
        self._seed_decision(store)
        memory_dir = tmp_path / "memory"
        stats = retrofit_decisions(store, memory_dir=str(memory_dir))

        assert stats["decisions"] == 1
        assert stats["markdown_files"] == 1
        md = (memory_dir / "project_use-redis-for-caching.md").read_text(encoding="utf-8")
        assert "name: Use Redis for caching" in md
        assert "type: project" in md
        assert "**Rationale:** Fits the existing infra." in md

        # Round-trip: the brain's MemoryIngester reads it back as a Decision
        brain = GraphStore.sqlite(str(tmp_path / "brain" / "graph.db"))
        try:
            MemoryIngester(brain).ingest(memory_dir, repo_name="brain")
            rows = brain.query("MATCH (d:Decision) RETURN d.name, d.memory_type").result_set
            assert rows == [["Use Redis for caching", "project"]]
        finally:
            brain.close()

    def test_json_export(self, store, tmp_path):
        self._seed_decision(store)
        json_path = tmp_path / "app" / "decisions.json"
        stats = retrofit_decisions(store, json_path=str(json_path))

        assert stats["decisions"] == 1
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        assert payload[0]["name"] == "Use Redis for caching"
        assert payload[0]["alternatives"] == "memcached"

    def test_domain_filter(self, store, tmp_path):
        self._seed_decision(store)
        stats = retrofit_decisions(store, json_path=str(tmp_path / "d.json"), domain="other")
        assert stats["decisions"] == 0


# ── CLI ────────────────────────────────────────────────────────────────────


class TestPmCli:
    def test_decisions_requires_an_output(self):
        runner = CliRunner()
        result = runner.invoke(main, ["pm", "decisions"])
        assert result.exit_code != 0
        assert "--to-markdown" in result.output

    def test_decisions_writes_markdown(self, store, tmp_path):
        store.create_node(
            "Decision",
            {"name": "CLI decision", "description": "d", "domain": "", "rationale": ""},
        )
        runner = CliRunner()
        with patch("navegador.cli.commands._get_store", return_value=store):
            result = runner.invoke(
                main, ["pm", "decisions", "--to-markdown", str(tmp_path / "mem")]
            )
        assert result.exit_code == 0, result.output
        assert (tmp_path / "mem" / "project_cli-decision.md").exists()

    def test_pm_ingest_no_comments_flag(self, store):
        runner = CliRunner()
        with (
            patch("navegador.cli.commands._get_store", return_value=store),
            patch.object(TicketIngester, "_fetch_json", side_effect=_fake_fetch) as fetch,
        ):
            result = runner.invoke(
                main, ["pm", "ingest", "--github", "o/r", "--no-comments", "--json"]
            )
        assert result.exit_code == 0, result.output
        assert fetch.call_count == 1
        assert json.loads(result.output)["comments"] == 0
