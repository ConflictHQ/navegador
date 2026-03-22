"""Tests for navegador CLI commands via click CliRunner."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from navegador.cli.commands import main
from navegador.context.loader import ContextBundle, ContextNode

# ── Helpers ───────────────────────────────────────────────────────────────────

def _mock_store():
    store = MagicMock()
    store.query.return_value = MagicMock(result_set=[])
    return store


def _node(name="foo", type_="Function", file_path="app.py"):
    return ContextNode(name=name, type=type_, file_path=file_path)


def _empty_bundle(name="target", type_="Function"):
    """Return a ContextBundle with a minimal target for testing."""
    return ContextBundle(target=_node(name, type_), nodes=[])


# ── init ──────────────────────────────────────────────────────────────────────

class TestInitCommand:
    def test_creates_navegador_dir(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(main, ["init", "."])
            assert result.exit_code == 0
            assert Path(".navegador").exists()

    def test_shows_redis_hint_when_url_provided(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(main, ["init", ".", "--redis", "redis://localhost:6379"])
            assert result.exit_code == 0
            assert "redis://localhost:6379" in result.output

    def test_shows_sqlite_hint_by_default(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(main, ["init", "."])
            assert result.exit_code == 0
            assert "Local SQLite" in result.output


# ── ingest ────────────────────────────────────────────────────────────────────

class TestIngestCommand:
    def test_outputs_table_on_success(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            Path("src").mkdir()
            with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
                 patch("navegador.ingestion.RepoIngester") as MockRI:
                MockRI.return_value.ingest.return_value = {"files": 5, "functions": 20}
                result = runner.invoke(main, ["ingest", "src"])
                assert result.exit_code == 0

    def test_json_flag_outputs_json(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            Path("src").mkdir()
            with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
                 patch("navegador.ingestion.RepoIngester") as MockRI:
                MockRI.return_value.ingest.return_value = {"files": 5}
                result = runner.invoke(main, ["ingest", "src", "--json"])
                assert result.exit_code == 0
                data = json.loads(result.output)
                assert data["files"] == 5


# ── context ───────────────────────────────────────────────────────────────────

class TestContextCommand:
    def test_json_format(self):
        runner = CliRunner()
        bundle = ContextBundle(target=_node("MyClass", "Class"), nodes=[])
        with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
             patch("navegador.context.ContextLoader") as MockCL:
            MockCL.return_value.load_file.return_value = bundle
            result = runner.invoke(main, ["context", "app.py", "--format", "json"])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert isinstance(data, dict)

    def test_markdown_format(self):
        runner = CliRunner()
        bundle = ContextBundle(target=_node(), nodes=[])
        with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
             patch("navegador.context.ContextLoader") as MockCL:
            MockCL.return_value.load_file.return_value = bundle
            result = runner.invoke(main, ["context", "app.py"])
            assert result.exit_code == 0


# ── function ──────────────────────────────────────────────────────────────────

class TestFunctionCommand:
    def test_function_json(self):
        runner = CliRunner()
        bundle = ContextBundle(target=_node("my_func"), nodes=[])
        with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
             patch("navegador.context.ContextLoader") as MockCL:
            MockCL.return_value.load_function.return_value = bundle
            result = runner.invoke(main, ["function", "my_func", "--format", "json"])
            assert result.exit_code == 0
            json.loads(result.output)  # must be valid JSON

    def test_function_with_file_option(self):
        runner = CliRunner()
        bundle = _empty_bundle()
        with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
             patch("navegador.context.ContextLoader") as MockCL:
            MockCL.return_value.load_function.return_value = bundle
            result = runner.invoke(main, ["function", "foo", "--file", "bar.py"])
            MockCL.return_value.load_function.assert_called_with(
                "foo", file_path="bar.py", depth=2)
            assert result.exit_code == 0


# ── class ─────────────────────────────────────────────────────────────────────

class TestClassCommand:
    def test_class_json(self):
        runner = CliRunner()
        bundle = ContextBundle(target=_node("MyClass", "Class"), nodes=[])
        with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
             patch("navegador.context.ContextLoader") as MockCL:
            MockCL.return_value.load_class.return_value = bundle
            result = runner.invoke(main, ["class", "MyClass", "--format", "json"])
            assert result.exit_code == 0
            json.loads(result.output)


# ── explain ───────────────────────────────────────────────────────────────────

class TestExplainCommand:
    def test_explain_json(self):
        runner = CliRunner()
        bundle = _empty_bundle()
        with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
             patch("navegador.context.ContextLoader") as MockCL:
            MockCL.return_value.explain.return_value = bundle
            result = runner.invoke(main, ["explain", "SomeName", "--format", "json"])
            assert result.exit_code == 0
            json.loads(result.output)


# ── search ────────────────────────────────────────────────────────────────────

class TestSearchCommand:
    def test_search_json_no_results(self):
        runner = CliRunner()
        with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
             patch("navegador.context.ContextLoader") as MockCL:
            MockCL.return_value.search.return_value = []
            result = runner.invoke(main, ["search", "foo", "--format", "json"])
            assert result.exit_code == 0
            assert json.loads(result.output) == []

    def test_search_all_flag(self):
        runner = CliRunner()
        with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
             patch("navegador.context.ContextLoader") as MockCL:
            MockCL.return_value.search_all.return_value = []
            result = runner.invoke(main, ["search", "foo", "--all", "--format", "json"])
            assert result.exit_code == 0
            MockCL.return_value.search_all.assert_called_once()

    def test_search_docs_flag(self):
        runner = CliRunner()
        with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
             patch("navegador.context.ContextLoader") as MockCL:
            MockCL.return_value.search_by_docstring.return_value = []
            result = runner.invoke(main, ["search", "foo", "--docs", "--format", "json"])
            assert result.exit_code == 0
            MockCL.return_value.search_by_docstring.assert_called_once()

    def test_search_markdown_no_results(self):
        runner = CliRunner()
        with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
             patch("navegador.context.ContextLoader") as MockCL:
            MockCL.return_value.search.return_value = []
            result = runner.invoke(main, ["search", "nothing"])
            assert result.exit_code == 0

    def test_search_with_results(self):
        runner = CliRunner()
        node = _node("result_fn")
        with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
             patch("navegador.context.ContextLoader") as MockCL:
            MockCL.return_value.search.return_value = [node]
            result = runner.invoke(main, ["search", "result", "--format", "json"])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert len(data) == 1
            assert data[0]["name"] == "result_fn"


# ── decorated ─────────────────────────────────────────────────────────────────

class TestDecoratedCommand:
    def test_decorated_json_no_results(self):
        runner = CliRunner()
        with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
             patch("navegador.context.ContextLoader") as MockCL:
            MockCL.return_value.decorated_by.return_value = []
            result = runner.invoke(main, ["decorated", "login_required", "--format", "json"])
            assert result.exit_code == 0
            assert json.loads(result.output) == []

    def test_decorated_with_results(self):
        runner = CliRunner()
        node = _node("my_view", "Function", "views.py")
        with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
             patch("navegador.context.ContextLoader") as MockCL:
            MockCL.return_value.decorated_by.return_value = [node]
            result = runner.invoke(main, ["decorated", "login_required", "--format", "json"])
            data = json.loads(result.output)
            assert data[0]["name"] == "my_view"


# ── query ─────────────────────────────────────────────────────────────────────

class TestQueryCommand:
    def test_returns_json(self):
        runner = CliRunner()
        store = _mock_store()
        store.query.return_value = MagicMock(result_set=[["Node1", "Node2"]])
        with patch("navegador.cli.commands._get_store", return_value=store):
            result = runner.invoke(main, ["query", "MATCH (n) RETURN n.name"])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data == [["Node1", "Node2"]]

    def test_empty_result(self):
        runner = CliRunner()
        store = _mock_store()
        with patch("navegador.cli.commands._get_store", return_value=store):
            result = runner.invoke(main, ["query", "MATCH (n) RETURN n"])
            assert result.exit_code == 0
            assert json.loads(result.output) == []


# ── add concept / rule / decision / person / domain ───────────────────────────

class TestAddCommands:
    def _run_add(self, *args):
        runner = CliRunner()
        with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
             patch("navegador.ingestion.KnowledgeIngester") as MockKI:
            MockKI.return_value = MagicMock()
            result = runner.invoke(main, list(args))
            return result, MockKI

    def test_add_concept(self):
        result, MockKI = self._run_add("add", "concept", "Payment", "--desc", "Handles money")
        assert result.exit_code == 0
        MockKI.return_value.add_concept.assert_called_once()

    def test_add_rule(self):
        result, MockKI = self._run_add("add", "rule", "NoNullIds", "--severity", "critical")
        assert result.exit_code == 0
        MockKI.return_value.add_rule.assert_called_once()

    def test_add_decision(self):
        result, MockKI = self._run_add("add", "decision", "Use PostgreSQL")
        assert result.exit_code == 0
        MockKI.return_value.add_decision.assert_called_once()

    def test_add_person(self):
        result, MockKI = self._run_add("add", "person", "Alice", "--email", "alice@example.com")
        assert result.exit_code == 0
        MockKI.return_value.add_person.assert_called_once()

    def test_add_domain(self):
        result, MockKI = self._run_add("add", "domain", "Billing", "--desc", "All billing logic")
        assert result.exit_code == 0
        MockKI.return_value.add_domain.assert_called_once()


# ── annotate ──────────────────────────────────────────────────────────────────

class TestAnnotateCommand:
    def test_annotate_with_concept(self):
        runner = CliRunner()
        with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
             patch("navegador.ingestion.KnowledgeIngester") as MockKI:
            MockKI.return_value = MagicMock()
            result = runner.invoke(main, ["annotate", "process_payment", "--concept", "Payment"])
            assert result.exit_code == 0
            MockKI.return_value.annotate_code.assert_called_once()

    def test_annotate_with_rule(self):
        runner = CliRunner()
        with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
             patch("navegador.ingestion.KnowledgeIngester") as MockKI:
            MockKI.return_value = MagicMock()
            result = runner.invoke(main, ["annotate", "validate_card", "--rule", "PCI"])
            assert result.exit_code == 0


# ── domain / concept ──────────────────────────────────────────────────────────

class TestDomainConceptCommands:
    def test_domain_json(self):
        runner = CliRunner()
        bundle = _empty_bundle()
        with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
             patch("navegador.context.ContextLoader") as MockCL:
            MockCL.return_value.load_domain.return_value = bundle
            result = runner.invoke(main, ["domain", "Billing", "--format", "json"])
            assert result.exit_code == 0
            json.loads(result.output)

    def test_concept_json(self):
        runner = CliRunner()
        bundle = _empty_bundle()
        with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
             patch("navegador.context.ContextLoader") as MockCL:
            MockCL.return_value.load_concept.return_value = bundle
            result = runner.invoke(main, ["concept", "Payment", "--format", "json"])
            assert result.exit_code == 0
            json.loads(result.output)


# ── wiki ingest ───────────────────────────────────────────────────────────────

class TestWikiIngestCommand:
    def test_ingest_local_dir(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            Path("wiki").mkdir()
            (Path("wiki") / "home.md").write_text("# Home")
            with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
                 patch("navegador.ingestion.WikiIngester") as MockWI:
                MockWI.return_value.ingest_local.return_value = {"pages": 1, "links": 0}
                result = runner.invoke(main, ["wiki", "ingest", "--dir", "wiki"])
                assert result.exit_code == 0
                assert "1" in result.output

    def test_error_without_repo_or_dir(self):
        runner = CliRunner()
        with patch("navegador.cli.commands._get_store", return_value=_mock_store()):
            result = runner.invoke(main, ["wiki", "ingest"])
            assert result.exit_code != 0

    def test_ingest_github_api(self):
        runner = CliRunner()
        with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
             patch("navegador.ingestion.WikiIngester") as MockWI:
            MockWI.return_value.ingest_github_api.return_value = {"pages": 3, "links": 2}
            result = runner.invoke(main, ["wiki", "ingest", "--repo", "owner/repo", "--api"])
            assert result.exit_code == 0


# ── stats ─────────────────────────────────────────────────────────────────────

class TestStatsCommand:
    def test_json_output(self):
        runner = CliRunner()
        store = _mock_store()
        store.query.side_effect = [
            MagicMock(result_set=[["Function", 10], ["Class", 5]]),
            MagicMock(result_set=[["CALLS", 20]]),
        ]
        with patch("navegador.cli.commands._get_store", return_value=store):
            result = runner.invoke(main, ["stats", "--json"])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data["total_nodes"] == 15
            assert data["total_edges"] == 20

    def test_table_output(self):
        runner = CliRunner()
        store = _mock_store()
        store.query.side_effect = [
            MagicMock(result_set=[["Function", 10]]),
            MagicMock(result_set=[["CALLS", 5]]),
        ]
        with patch("navegador.cli.commands._get_store", return_value=store):
            result = runner.invoke(main, ["stats"])
            assert result.exit_code == 0

    def test_empty_graph(self):
        runner = CliRunner()
        store = _mock_store()
        store.query.side_effect = [
            MagicMock(result_set=[]),
            MagicMock(result_set=[]),
        ]
        with patch("navegador.cli.commands._get_store", return_value=store):
            result = runner.invoke(main, ["stats", "--json"])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data["total_nodes"] == 0


# ── planopticon ingest ────────────────────────────────────────────────────────

class TestPlanopticonIngestCommand:
    def test_auto_detect_kg(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            Path("knowledge_graph.json").write_text('{"nodes":[],"relationships":[],"sources":[]}')
            with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
                 patch("navegador.ingestion.PlanopticonIngester") as MockPI:
                MockPI.return_value.ingest_kg.return_value = {"nodes": 0, "edges": 0}
                result = runner.invoke(main, ["planopticon", "ingest", "knowledge_graph.json"])
                assert result.exit_code == 0

    def test_auto_detect_manifest(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            Path("manifest.json").write_text("{}")
            with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
                 patch("navegador.ingestion.PlanopticonIngester") as MockPI:
                MockPI.return_value.ingest_manifest.return_value = {"nodes": 0, "edges": 0}
                result = runner.invoke(main, ["planopticon", "ingest", "manifest.json"])
                assert result.exit_code == 0
                MockPI.return_value.ingest_manifest.assert_called_once()

    def test_json_output(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            Path("kg.json").write_text('{"nodes":[],"relationships":[],"sources":[]}')
            with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
                 patch("navegador.ingestion.PlanopticonIngester") as MockPI:
                MockPI.return_value.ingest_kg.return_value = {"nodes": 3, "edges": 1}
                result = runner.invoke(main, ["planopticon", "ingest", "kg.json", "--json"])
                assert result.exit_code == 0
                data = json.loads(result.output)
                assert data["nodes"] == 3

    def test_directory_resolves_manifest(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            Path("output").mkdir()
            Path("output/manifest.json").write_text("{}")
            with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
                 patch("navegador.ingestion.PlanopticonIngester") as MockPI:
                MockPI.return_value.ingest_manifest.return_value = {"nodes": 0, "edges": 0}
                result = runner.invoke(main, ["planopticon", "ingest", "output"])
                assert result.exit_code == 0
                MockPI.return_value.ingest_manifest.assert_called_once()


# ── --help smoke tests ─────────────────────────────────────────────────────────

class TestHelp:
    def test_main_help(self):
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "navegador" in result.output.lower() or "knowledge" in result.output.lower()

    def test_add_help(self):
        runner = CliRunner()
        result = runner.invoke(main, ["add", "--help"])
        assert result.exit_code == 0

    def test_wiki_help(self):
        runner = CliRunner()
        result = runner.invoke(main, ["wiki", "--help"])
        assert result.exit_code == 0

    def test_planopticon_help(self):
        runner = CliRunner()
        result = runner.invoke(main, ["planopticon", "--help"])
        assert result.exit_code == 0
