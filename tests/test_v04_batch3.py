"""
Tests for navegador v0.4 batch 3 — issues #7, #18, #53, #55, #58, #61, #62.

Covers:
  #7  / #18 — PlanopticonPipeline (pipeline, action items, decision timeline, auto-link)
  #53        — TicketIngester (GitHub, Linear stub, Jira stub)
  #55        — FossilAdapter (current_branch, changed_files, file_history, blame)
  #58        — DependencyIngester (npm, pip/requirements.txt, pip/pyproject.toml, cargo)
  #61        — SubmoduleIngester (detect_submodules, ingest_with_submodules)
  #62        — WorkspaceMode enum, WorkspaceManager (unified + federated)
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ── Shared mock store factory ─────────────────────────────────────────────────


def _make_store():
    store = MagicMock()
    store.query.return_value = MagicMock(result_set=[])
    return store


# =============================================================================
# #7 / #18 — PlanopticonPipeline
# =============================================================================


class TestPlanopticonPipelineDetectInput:
    """_detect_input correctly identifies file types from path."""

    from navegador.planopticon_pipeline import PlanopticonPipeline as _Pipeline

    def test_manifest_file(self, tmp_path):
        from navegador.planopticon_pipeline import PlanopticonPipeline

        f = tmp_path / "manifest.json"
        f.write_text("{}")
        itype, resolved = PlanopticonPipeline._detect_input(f)
        assert itype == "manifest"
        assert resolved == f

    def test_interchange_file(self, tmp_path):
        from navegador.planopticon_pipeline import PlanopticonPipeline

        f = tmp_path / "interchange.json"
        f.write_text("{}")
        itype, _ = PlanopticonPipeline._detect_input(f)
        assert itype == "interchange"

    def test_batch_file(self, tmp_path):
        from navegador.planopticon_pipeline import PlanopticonPipeline

        f = tmp_path / "batch_manifest.json"
        f.write_text("{}")
        itype, _ = PlanopticonPipeline._detect_input(f)
        assert itype == "batch"

    def test_kg_file_default(self, tmp_path):
        from navegador.planopticon_pipeline import PlanopticonPipeline

        f = tmp_path / "knowledge_graph.json"
        f.write_text("{}")
        itype, _ = PlanopticonPipeline._detect_input(f)
        assert itype == "kg"

    def test_directory_with_manifest(self, tmp_path):
        from navegador.planopticon_pipeline import PlanopticonPipeline

        (tmp_path / "manifest.json").write_text("{}")
        itype, resolved = PlanopticonPipeline._detect_input(tmp_path)
        assert itype == "manifest"

    def test_directory_without_known_files_raises(self, tmp_path):
        from navegador.planopticon_pipeline import PlanopticonPipeline

        with pytest.raises(FileNotFoundError):
            PlanopticonPipeline._detect_input(tmp_path)


class TestPlanopticonPipelineRun:
    """PlanopticonPipeline.run delegates to PlanopticonIngester and auto-links."""

    def test_run_returns_stats_with_linked_key(self, tmp_path):
        from navegador.planopticon_pipeline import PlanopticonPipeline

        kg_data = {"nodes": [], "relationships": [], "sources": []}
        kg_file = tmp_path / "knowledge_graph.json"
        kg_file.write_text(json.dumps(kg_data))

        store = _make_store()
        pipeline = PlanopticonPipeline(store, source_tag="test")
        stats = pipeline.run(str(kg_file))

        assert "nodes" in stats
        assert "linked" in stats

    def test_run_calls_ingester(self, tmp_path):
        from navegador.planopticon_pipeline import PlanopticonPipeline

        kg_data = {
            "nodes": [{"id": "n1", "type": "concept", "name": "Auth"}],
            "relationships": [],
            "sources": [],
        }
        kg_file = tmp_path / "knowledge_graph.json"
        kg_file.write_text(json.dumps(kg_data))

        store = _make_store()
        pipeline = PlanopticonPipeline(store)
        stats = pipeline.run(str(kg_file), source_tag="Meeting")

        assert isinstance(stats, dict)
        # create_node should have been called at least once for the concept node
        store.create_node.assert_called()


class TestExtractActionItems:
    """extract_action_items pulls action items from various KG data formats."""

    def test_action_items_list(self):
        from navegador.planopticon_pipeline import ActionItem, PlanopticonPipeline

        kg_data = {
            "action_items": [
                {"action": "Write tests", "assignee": "Alice", "priority": "high"},
                {"action": "Deploy service", "assignee": "", "priority": "info"},
            ]
        }
        items = PlanopticonPipeline.extract_action_items(kg_data)
        assert len(items) == 2
        assert all(isinstance(i, ActionItem) for i in items)
        assert items[0].action == "Write tests"
        assert items[0].assignee == "Alice"
        assert items[1].action == "Deploy service"

    def test_blank_actions_skipped(self):
        from navegador.planopticon_pipeline import PlanopticonPipeline

        kg_data = {"action_items": [{"action": "  ", "assignee": "Bob"}]}
        items = PlanopticonPipeline.extract_action_items(kg_data)
        assert items == []

    def test_entities_with_task_type(self):
        from navegador.planopticon_pipeline import PlanopticonPipeline

        kg_data = {
            "entities": [
                {"planning_type": "task", "name": "Refactor auth module"},
                {"planning_type": "decision", "name": "Use PostgreSQL"},
            ]
        }
        items = PlanopticonPipeline.extract_action_items(kg_data)
        assert len(items) == 1
        assert items[0].action == "Refactor auth module"

    def test_nodes_with_action_item_type(self):
        from navegador.planopticon_pipeline import PlanopticonPipeline

        kg_data = {
            "nodes": [
                {"type": "action_item", "name": "Update documentation"},
            ]
        }
        items = PlanopticonPipeline.extract_action_items(kg_data)
        assert len(items) == 1
        assert items[0].action == "Update documentation"

    def test_empty_data_returns_empty_list(self):
        from navegador.planopticon_pipeline import PlanopticonPipeline

        assert PlanopticonPipeline.extract_action_items({}) == []

    def test_action_item_to_dict(self):
        from navegador.planopticon_pipeline import ActionItem

        item = ActionItem(action="Do thing", assignee="Carol", priority="critical")
        d = item.to_dict()
        assert d["action"] == "Do thing"
        assert d["assignee"] == "Carol"
        assert d["priority"] == "critical"


class TestBuildDecisionTimeline:
    """build_decision_timeline queries the store and returns chronological list."""

    def test_returns_list_from_store(self):
        from navegador.planopticon_pipeline import PlanopticonPipeline

        store = _make_store()
        store.query.return_value = MagicMock(
            result_set=[
                ["Use microservices", "Split monolith", "arch", "accepted", "Scalability", "2024-01-10"],
                ["Use PostgreSQL", "Relational DB", "data", "accepted", "ACID", "2024-02-01"],
            ]
        )
        timeline = PlanopticonPipeline.build_decision_timeline(store)
        assert len(timeline) == 2
        assert timeline[0]["name"] == "Use microservices"
        assert timeline[0]["date"] == "2024-01-10"

    def test_returns_empty_on_query_failure(self):
        from navegador.planopticon_pipeline import PlanopticonPipeline

        store = _make_store()
        store.query.side_effect = Exception("DB error")
        timeline = PlanopticonPipeline.build_decision_timeline(store)
        assert timeline == []

    def test_entry_has_required_keys(self):
        from navegador.planopticon_pipeline import PlanopticonPipeline

        store = _make_store()
        store.query.return_value = MagicMock(
            result_set=[["D1", "Desc", "domain", "accepted", "rationale", "2024-01-01"]]
        )
        timeline = PlanopticonPipeline.build_decision_timeline(store)
        required_keys = {"name", "description", "domain", "status", "rationale", "date"}
        assert required_keys.issubset(timeline[0].keys())


class TestAutoLinkToCode:
    """auto_link_to_code matches knowledge nodes to code by name similarity."""

    def test_returns_zero_when_no_nodes(self):
        from navegador.planopticon_pipeline import PlanopticonPipeline

        store = _make_store()
        store.query.return_value = MagicMock(result_set=[])
        assert PlanopticonPipeline.auto_link_to_code(store) == 0

    def test_links_matching_nodes(self):
        from navegador.planopticon_pipeline import PlanopticonPipeline

        store = _make_store()

        # First call: knowledge nodes; second: code nodes; subsequent: merge queries
        call_count = 0
        def _query(cypher, params=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # knowledge nodes — use "authenticate" (12 chars) which IS in "authenticate_user"
                return MagicMock(result_set=[["Concept", "authenticate handler"]])
            elif call_count == 2:
                # code nodes
                return MagicMock(result_set=[["Function", "authenticate_user"]])
            else:
                # MERGE query — no result needed
                return MagicMock(result_set=[])

        store.query.side_effect = _query
        linked = PlanopticonPipeline.auto_link_to_code(store)
        # "authenticate" (12 chars, ≥4) is contained in "authenticate_user"
        assert linked >= 1

    def test_short_tokens_skipped(self):
        from navegador.planopticon_pipeline import PlanopticonPipeline

        store = _make_store()

        call_count = 0
        def _query(cypher, params=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return MagicMock(result_set=[["Concept", "API"]])  # all tokens < 4 chars
            elif call_count == 2:
                return MagicMock(result_set=[["Function", "api_handler"]])
            return MagicMock(result_set=[])

        store.query.side_effect = _query
        linked = PlanopticonPipeline.auto_link_to_code(store)
        # "api" is only 3 chars — should not match
        assert linked == 0

    def test_returns_zero_on_query_failure(self):
        from navegador.planopticon_pipeline import PlanopticonPipeline

        store = _make_store()
        store.query.side_effect = Exception("boom")
        result = PlanopticonPipeline.auto_link_to_code(store)
        assert result == 0


# =============================================================================
# #53 — TicketIngester
# =============================================================================


class TestTicketIngesterGitHub:
    """TicketIngester.ingest_github_issues fetches and ingests GitHub issues."""

    def _make_issue(self, number=1, title="Fix bug", body="Details", labels=None, assignees=None):
        return {
            "number": number,
            "title": title,
            "body": body,
            "html_url": f"https://github.com/owner/repo/issues/{number}",
            "labels": [{"name": l} for l in (labels or [])],
            "assignees": [{"login": a} for a in (assignees or [])],
        }

    def test_ingest_creates_ticket_nodes(self):
        from navegador.pm import TicketIngester

        store = _make_store()
        store.query.return_value = MagicMock(result_set=[])
        ing = TicketIngester(store)

        issues = [self._make_issue(1, "Bug report"), self._make_issue(2, "Feature request")]
        with patch("urllib.request.urlopen") as mock_open:
            cm = MagicMock()
            cm.__enter__ = MagicMock(return_value=cm)
            cm.__exit__ = MagicMock(return_value=False)
            cm.read.return_value = json.dumps(issues).encode()
            mock_open.return_value = cm

            stats = ing.ingest_github_issues("owner/repo", token="test_token")

        assert stats["tickets"] == 2
        assert "linked" in stats

    def test_pull_requests_filtered_out(self):
        from navegador.pm import TicketIngester

        store = _make_store()
        store.query.return_value = MagicMock(result_set=[])
        ing = TicketIngester(store)

        # Mix of issue and PR
        issue = self._make_issue(1, "Real issue")
        pr = {**self._make_issue(2, "A PR"), "pull_request": {"url": "..."}}

        with patch("urllib.request.urlopen") as mock_open:
            cm = MagicMock()
            cm.__enter__ = MagicMock(return_value=cm)
            cm.__exit__ = MagicMock(return_value=False)
            cm.read.return_value = json.dumps([issue, pr]).encode()
            mock_open.return_value = cm

            stats = ing.ingest_github_issues("owner/repo")

        assert stats["tickets"] == 1  # PR filtered out

    def test_assignees_become_person_nodes(self):
        from navegador.pm import TicketIngester

        store = _make_store()
        store.query.return_value = MagicMock(result_set=[])
        ing = TicketIngester(store)

        issue = self._make_issue(1, "Assign me", assignees=["alice"])

        with patch("urllib.request.urlopen") as mock_open:
            cm = MagicMock()
            cm.__enter__ = MagicMock(return_value=cm)
            cm.__exit__ = MagicMock(return_value=False)
            cm.read.return_value = json.dumps([issue]).encode()
            mock_open.return_value = cm

            ing.ingest_github_issues("owner/repo")

        # Person node created for alice
        person_calls = [
            c for c in store.create_node.call_args_list
            if c.args and hasattr(c.args[0], "value") and c.args[0].value == "Person"
        ]
        assert len(person_calls) >= 1

    def test_network_error_raises_runtime_error(self):
        from navegador.pm import TicketIngester

        store = _make_store()
        ing = TicketIngester(store)

        with patch("urllib.request.urlopen", side_effect=Exception("network error")):
            with pytest.raises(RuntimeError, match="Failed to fetch GitHub issues"):
                ing.ingest_github_issues("owner/repo")


class TestTicketIngesterSeverity:
    """_github_severity maps label names to severity levels."""

    def test_critical_label(self):
        from navegador.pm import TicketIngester

        assert TicketIngester._github_severity(["critical"]) == "critical"
        assert TicketIngester._github_severity(["blocker"]) == "critical"

    def test_warning_label(self):
        from navegador.pm import TicketIngester

        assert TicketIngester._github_severity(["bug"]) == "warning"
        assert TicketIngester._github_severity(["high"]) == "warning"

    def test_default_info(self):
        from navegador.pm import TicketIngester

        assert TicketIngester._github_severity([]) == "info"
        assert TicketIngester._github_severity(["enhancement"]) == "info"


class TestTicketIngesterStubs:
    """Linear and Jira raise NotImplementedError with helpful messages."""

    def test_linear_raises_not_implemented(self):
        from navegador.pm import TicketIngester

        ing = TicketIngester(_make_store())
        with pytest.raises(NotImplementedError, match="Linear"):
            ing.ingest_linear("lin_apikey")

    def test_jira_raises_not_implemented(self):
        from navegador.pm import TicketIngester

        ing = TicketIngester(_make_store())
        with pytest.raises(NotImplementedError, match="Jira"):
            ing.ingest_jira("https://company.atlassian.net", token="tok")

    def test_linear_message_contains_guidance(self):
        from navegador.pm import TicketIngester

        ing = TicketIngester(_make_store())
        with pytest.raises(NotImplementedError) as exc_info:
            ing.ingest_linear("lin_key", project="MyProject")
        assert "53" in str(exc_info.value) or "Linear" in str(exc_info.value)

    def test_jira_message_contains_guidance(self):
        from navegador.pm import TicketIngester

        ing = TicketIngester(_make_store())
        with pytest.raises(NotImplementedError) as exc_info:
            ing.ingest_jira("https://x.atlassian.net")
        assert "Jira" in str(exc_info.value) or "jira" in str(exc_info.value).lower()


# =============================================================================
# #55 — FossilAdapter
# =============================================================================


@pytest.fixture()
def fossil_dir(tmp_path):
    d = tmp_path / "fossil_repo"
    d.mkdir()
    (d / ".fslckout").touch()
    return d


class TestFossilAdapterCurrentBranch:
    """current_branch calls 'fossil branch current' and returns stripped output."""

    def test_returns_branch_name(self, fossil_dir):
        from navegador.vcs import FossilAdapter

        adapter = FossilAdapter(fossil_dir)
        mock_result = MagicMock()
        mock_result.stdout = "trunk\n"

        with patch("subprocess.run", return_value=mock_result):
            branch = adapter.current_branch()

        assert branch == "trunk"

    def test_strips_whitespace(self, fossil_dir):
        from navegador.vcs import FossilAdapter

        adapter = FossilAdapter(fossil_dir)
        mock_result = MagicMock()
        mock_result.stdout = "  feature-branch  \n"

        with patch("subprocess.run", return_value=mock_result):
            branch = adapter.current_branch()

        assert branch == "feature-branch"

    def test_calls_fossil_branch_current(self, fossil_dir):
        from navegador.vcs import FossilAdapter

        adapter = FossilAdapter(fossil_dir)
        mock_result = MagicMock()
        mock_result.stdout = "main\n"

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            adapter.current_branch()

        call_args = mock_run.call_args
        assert call_args[0][0] == ["fossil", "branch", "current"]


class TestFossilAdapterChangedFiles:
    """changed_files calls 'fossil changes --differ' and parses output."""

    def test_returns_changed_file_list(self, fossil_dir):
        from navegador.vcs import FossilAdapter

        adapter = FossilAdapter(fossil_dir)
        mock_result = MagicMock()
        mock_result.stdout = "EDITED  src/main.py\nADDED   tests/test_new.py\n"

        with patch("subprocess.run", return_value=mock_result):
            files = adapter.changed_files()

        assert "src/main.py" in files
        assert "tests/test_new.py" in files

    def test_empty_output_returns_empty_list(self, fossil_dir):
        from navegador.vcs import FossilAdapter

        adapter = FossilAdapter(fossil_dir)
        mock_result = MagicMock()
        mock_result.stdout = ""

        with patch("subprocess.run", return_value=mock_result):
            files = adapter.changed_files()

        assert files == []

    def test_calls_fossil_changes_differ(self, fossil_dir):
        from navegador.vcs import FossilAdapter

        adapter = FossilAdapter(fossil_dir)
        mock_result = MagicMock()
        mock_result.stdout = ""

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            adapter.changed_files()

        call_args = mock_run.call_args
        assert call_args[0][0] == ["fossil", "changes", "--differ"]

    def test_returns_list(self, fossil_dir):
        from navegador.vcs import FossilAdapter

        adapter = FossilAdapter(fossil_dir)
        mock_result = MagicMock()
        mock_result.stdout = "EDITED  foo.py\n"

        with patch("subprocess.run", return_value=mock_result):
            result = adapter.changed_files()

        assert isinstance(result, list)


class TestFossilAdapterFileHistory:
    """file_history calls 'fossil timeline' and parses output into entry dicts."""

    SAMPLE_TIMELINE = """\
=== 2024-01-15 ===
14:23:07 [abc123def456] Add feature. (user: alice, tags: trunk)
09:00:00 [deadbeef1234] Fix typo. (user: bob, tags: trunk)
=== 2024-01-14 ===
22:10:00 [cafe0000abcd] Initial commit. (user: alice, tags: initial)
"""

    def test_returns_list_of_dicts(self, fossil_dir):
        from navegador.vcs import FossilAdapter

        adapter = FossilAdapter(fossil_dir)
        mock_result = MagicMock()
        mock_result.stdout = self.SAMPLE_TIMELINE

        with patch("subprocess.run", return_value=mock_result):
            history = adapter.file_history("src/main.py")

        assert isinstance(history, list)
        assert len(history) >= 1

    def test_entry_has_required_keys(self, fossil_dir):
        from navegador.vcs import FossilAdapter

        adapter = FossilAdapter(fossil_dir)
        mock_result = MagicMock()
        mock_result.stdout = self.SAMPLE_TIMELINE

        with patch("subprocess.run", return_value=mock_result):
            history = adapter.file_history("src/main.py")

        for entry in history:
            assert "hash" in entry
            assert "author" in entry
            assert "date" in entry
            assert "message" in entry

    def test_limit_passed_to_fossil(self, fossil_dir):
        from navegador.vcs import FossilAdapter

        adapter = FossilAdapter(fossil_dir)
        mock_result = MagicMock()
        mock_result.stdout = ""

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            adapter.file_history("src/main.py", limit=5)

        args = mock_run.call_args[0][0]
        assert "5" in args

    def test_empty_output_returns_empty_list(self, fossil_dir):
        from navegador.vcs import FossilAdapter

        adapter = FossilAdapter(fossil_dir)
        mock_result = MagicMock()
        mock_result.stdout = ""

        with patch("subprocess.run", return_value=mock_result):
            history = adapter.file_history("nonexistent.py")

        assert history == []


class TestFossilAdapterBlame:
    """blame calls 'fossil annotate --log' and parses per-line output."""

    SAMPLE_ANNOTATE = """\
1.1          alice 2024-01-15:  def main():
1.1          alice 2024-01-15:      pass
1.2          bob   2024-01-20:      # added comment
"""

    def test_returns_list(self, fossil_dir):
        from navegador.vcs import FossilAdapter

        adapter = FossilAdapter(fossil_dir)
        mock_result = MagicMock()
        mock_result.stdout = self.SAMPLE_ANNOTATE

        with patch("subprocess.run", return_value=mock_result):
            result = adapter.blame("src/main.py")

        assert isinstance(result, list)
        assert len(result) >= 1

    def test_entry_has_required_keys(self, fossil_dir):
        from navegador.vcs import FossilAdapter

        adapter = FossilAdapter(fossil_dir)
        mock_result = MagicMock()
        mock_result.stdout = self.SAMPLE_ANNOTATE

        with patch("subprocess.run", return_value=mock_result):
            result = adapter.blame("src/main.py")

        for entry in result:
            assert "line" in entry
            assert "hash" in entry
            assert "author" in entry
            assert "content" in entry

    def test_line_numbers_sequential(self, fossil_dir):
        from navegador.vcs import FossilAdapter

        adapter = FossilAdapter(fossil_dir)
        mock_result = MagicMock()
        mock_result.stdout = self.SAMPLE_ANNOTATE

        with patch("subprocess.run", return_value=mock_result):
            result = adapter.blame("src/main.py")

        if len(result) >= 2:
            assert result[1]["line"] > result[0]["line"]

    def test_calls_fossil_annotate(self, fossil_dir):
        from navegador.vcs import FossilAdapter

        adapter = FossilAdapter(fossil_dir)
        mock_result = MagicMock()
        mock_result.stdout = ""

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            adapter.blame("src/main.py")

        args = mock_run.call_args[0][0]
        assert "fossil" in args
        assert "annotate" in args


# =============================================================================
# #58 — DependencyIngester
# =============================================================================


class TestDependencyIngesterNPM:
    """ingest_npm parses package.json and creates dependency nodes."""

    def test_ingests_dependencies(self, tmp_path):
        from navegador.dependencies import DependencyIngester

        pkg = {
            "name": "myapp",
            "dependencies": {"react": "^18.0.0", "lodash": "4.17.21"},
            "devDependencies": {"jest": "^29.0.0"},
        }
        pkg_file = tmp_path / "package.json"
        pkg_file.write_text(json.dumps(pkg))

        store = _make_store()
        ing = DependencyIngester(store)
        stats = ing.ingest_npm(str(pkg_file))

        assert stats["packages"] == 3  # 2 deps + 1 devDep
        assert store.create_node.call_count >= 3

    def test_empty_dependencies(self, tmp_path):
        from navegador.dependencies import DependencyIngester

        pkg = {"name": "empty", "dependencies": {}}
        pkg_file = tmp_path / "package.json"
        pkg_file.write_text(json.dumps(pkg))

        store = _make_store()
        stats = DependencyIngester(store).ingest_npm(str(pkg_file))
        assert stats["packages"] == 0

    def test_peer_dependencies_included(self, tmp_path):
        from navegador.dependencies import DependencyIngester

        pkg = {
            "peerDependencies": {"react": ">=17"},
        }
        pkg_file = tmp_path / "package.json"
        pkg_file.write_text(json.dumps(pkg))

        store = _make_store()
        stats = DependencyIngester(store).ingest_npm(str(pkg_file))
        assert stats["packages"] == 1

    def test_creates_depends_on_edge(self, tmp_path):
        from navegador.dependencies import DependencyIngester

        pkg = {"dependencies": {"axios": "^1.0.0"}}
        pkg_file = tmp_path / "package.json"
        pkg_file.write_text(json.dumps(pkg))

        store = _make_store()
        DependencyIngester(store).ingest_npm(str(pkg_file))
        store.create_edge.assert_called()


class TestDependencyIngesterPip:
    """ingest_pip parses requirements.txt and creates dependency nodes."""

    def test_requirements_txt(self, tmp_path):
        from navegador.dependencies import DependencyIngester

        req_file = tmp_path / "requirements.txt"
        req_file.write_text(
            "requests>=2.28.0\n"
            "flask[async]==2.3.0\n"
            "# a comment\n"
            "\n"
            "pytest>=7.0  # dev\n"
        )

        store = _make_store()
        stats = DependencyIngester(store).ingest_pip(str(req_file))
        assert stats["packages"] == 3

    def test_skips_comments_and_blanks(self, tmp_path):
        from navegador.dependencies import DependencyIngester

        req_file = tmp_path / "requirements.txt"
        req_file.write_text("# comment\n\n-r other.txt\n")

        store = _make_store()
        stats = DependencyIngester(store).ingest_pip(str(req_file))
        assert stats["packages"] == 0

    def test_pyproject_toml(self, tmp_path):
        from navegador.dependencies import DependencyIngester

        toml_content = """\
[project]
name = "myproject"
dependencies = [
    "click>=8.0",
    "rich>=12.0",
    "pydantic>=2.0",
]
"""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(toml_content)

        store = _make_store()
        stats = DependencyIngester(store).ingest_pip(str(pyproject))
        assert stats["packages"] >= 3


class TestDependencyIngesterCargo:
    """ingest_cargo parses Cargo.toml and creates dependency nodes."""

    def test_basic_cargo_toml(self, tmp_path):
        from navegador.dependencies import DependencyIngester

        cargo_content = """\
[package]
name = "myapp"

[dependencies]
serde = "1.0"
tokio = { version = "1.0", features = ["full"] }

[dev-dependencies]
criterion = "0.4"
"""
        cargo_file = tmp_path / "Cargo.toml"
        cargo_file.write_text(cargo_content)

        store = _make_store()
        stats = DependencyIngester(store).ingest_cargo(str(cargo_file))
        assert stats["packages"] == 3  # serde, tokio, criterion

    def test_empty_cargo_toml(self, tmp_path):
        from navegador.dependencies import DependencyIngester

        cargo_file = tmp_path / "Cargo.toml"
        cargo_file.write_text("[package]\nname = \"empty\"\n")

        store = _make_store()
        stats = DependencyIngester(store).ingest_cargo(str(cargo_file))
        assert stats["packages"] == 0

    def test_build_dependencies_included(self, tmp_path):
        from navegador.dependencies import DependencyIngester

        cargo_content = "[build-dependencies]\nbuild-helper = \"0.3\"\n"
        cargo_file = tmp_path / "Cargo.toml"
        cargo_file.write_text(cargo_content)

        store = _make_store()
        stats = DependencyIngester(store).ingest_cargo(str(cargo_file))
        assert stats["packages"] == 1


# =============================================================================
# #61 — SubmoduleIngester
# =============================================================================


class TestDetectSubmodules:
    """detect_submodules parses .gitmodules into structured dicts."""

    def test_no_gitmodules_returns_empty(self, tmp_path):
        from navegador.submodules import SubmoduleIngester

        result = SubmoduleIngester(_make_store()).detect_submodules(tmp_path)
        assert result == []

    def test_single_submodule(self, tmp_path):
        from navegador.submodules import SubmoduleIngester

        gitmodules = tmp_path / ".gitmodules"
        gitmodules.write_text(
            '[submodule "vendor/lib"]\n'
            "    path = vendor/lib\n"
            "    url = https://github.com/org/lib.git\n"
        )

        result = SubmoduleIngester(_make_store()).detect_submodules(tmp_path)
        assert len(result) == 1
        assert result[0]["name"] == "vendor/lib"
        assert result[0]["path"] == "vendor/lib"
        assert result[0]["url"] == "https://github.com/org/lib.git"
        assert result[0]["abs_path"] == str(tmp_path / "vendor/lib")

    def test_multiple_submodules(self, tmp_path):
        from navegador.submodules import SubmoduleIngester

        gitmodules = tmp_path / ".gitmodules"
        gitmodules.write_text(
            '[submodule "a"]\n    path = sub/a\n    url = https://example.com/a.git\n'
            '[submodule "b"]\n    path = sub/b\n    url = https://example.com/b.git\n'
        )

        result = SubmoduleIngester(_make_store()).detect_submodules(tmp_path)
        assert len(result) == 2
        names = {r["name"] for r in result}
        assert names == {"a", "b"}

    def test_missing_url_returns_empty_string(self, tmp_path):
        from navegador.submodules import SubmoduleIngester

        gitmodules = tmp_path / ".gitmodules"
        gitmodules.write_text('[submodule "x"]\n    path = sub/x\n')

        result = SubmoduleIngester(_make_store()).detect_submodules(tmp_path)
        assert result[0]["url"] == ""


class TestIngestWithSubmodules:
    """ingest_with_submodules ingests parent + submodules, creates DEPENDS_ON edges."""

    def test_no_gitmodules_ingests_parent_only(self, tmp_path):
        from navegador.submodules import SubmoduleIngester

        store = _make_store()
        ing = SubmoduleIngester(store)

        with patch("navegador.ingestion.parser.RepoIngester") as MockIngester:
            mock_inst = MagicMock()
            mock_inst.ingest.return_value = {"files": 5, "nodes": 10}
            MockIngester.return_value = mock_inst

            stats = ing.ingest_with_submodules(str(tmp_path))

        assert stats["parent"]["files"] == 5
        assert stats["submodules"] == {}
        assert stats["total_files"] == 5

    def test_missing_submodule_path_recorded_as_error(self, tmp_path):
        from navegador.submodules import SubmoduleIngester

        gitmodules = tmp_path / ".gitmodules"
        gitmodules.write_text(
            '[submodule "missing"]\n    path = does/not/exist\n    url = https://x.com/r.git\n'
        )

        store = _make_store()
        ing = SubmoduleIngester(store)

        with patch("navegador.ingestion.parser.RepoIngester") as MockIngester:
            mock_inst = MagicMock()
            mock_inst.ingest.return_value = {"files": 3, "nodes": 6}
            MockIngester.return_value = mock_inst

            stats = ing.ingest_with_submodules(str(tmp_path))

        assert "missing" in stats["submodules"]
        assert "error" in stats["submodules"]["missing"]

    def test_existing_submodule_ingested(self, tmp_path):
        from navegador.submodules import SubmoduleIngester

        sub_dir = tmp_path / "libs" / "core"
        sub_dir.mkdir(parents=True)

        gitmodules = tmp_path / ".gitmodules"
        gitmodules.write_text(
            '[submodule "core"]\n    path = libs/core\n    url = https://x.com/core.git\n'
        )

        store = _make_store()
        ing = SubmoduleIngester(store)

        with patch("navegador.ingestion.parser.RepoIngester") as MockIngester:
            mock_inst = MagicMock()
            mock_inst.ingest.return_value = {"files": 4, "nodes": 8}
            MockIngester.return_value = mock_inst

            stats = ing.ingest_with_submodules(str(tmp_path))

        assert "core" in stats["submodules"]
        assert stats["submodules"]["core"]["files"] == 4
        assert stats["total_files"] == 8  # parent 4 + submodule 4

        # DEPENDS_ON edge from parent → submodule
        store.create_edge.assert_called()


# =============================================================================
# #62 — WorkspaceMode + WorkspaceManager
# =============================================================================


class TestWorkspaceMode:
    """WorkspaceMode enum has UNIFIED and FEDERATED values."""

    def test_has_unified(self):
        from navegador.multirepo import WorkspaceMode

        assert WorkspaceMode.UNIFIED == "unified"

    def test_has_federated(self):
        from navegador.multirepo import WorkspaceMode

        assert WorkspaceMode.FEDERATED == "federated"

    def test_is_str_enum(self):
        from navegador.multirepo import WorkspaceMode

        assert isinstance(WorkspaceMode.UNIFIED, str)
        assert isinstance(WorkspaceMode.FEDERATED, str)

    def test_from_string(self):
        from navegador.multirepo import WorkspaceMode

        assert WorkspaceMode("unified") == WorkspaceMode.UNIFIED
        assert WorkspaceMode("federated") == WorkspaceMode.FEDERATED


class TestWorkspaceManagerUnified:
    """WorkspaceManager in UNIFIED mode uses a single shared graph."""

    def test_add_repo_creates_repository_node(self, tmp_path):
        from navegador.multirepo import WorkspaceManager, WorkspaceMode

        store = _make_store()
        wm = WorkspaceManager(store, mode=WorkspaceMode.UNIFIED)
        wm.add_repo("backend", str(tmp_path))

        store.create_node.assert_called()

    def test_list_repos(self, tmp_path):
        from navegador.multirepo import WorkspaceManager, WorkspaceMode

        store = _make_store()
        wm = WorkspaceManager(store, mode=WorkspaceMode.UNIFIED)
        wm.add_repo("backend", str(tmp_path))
        wm.add_repo("frontend", str(tmp_path))

        repos = wm.list_repos()
        names = {r["name"] for r in repos}
        assert names == {"backend", "frontend"}

    def test_ingest_all_calls_repo_ingester(self, tmp_path):
        from navegador.multirepo import WorkspaceManager, WorkspaceMode

        store = _make_store()
        wm = WorkspaceManager(store, mode=WorkspaceMode.UNIFIED)
        wm.add_repo("repo1", str(tmp_path))

        with patch("navegador.ingestion.parser.RepoIngester") as MockIngester:
            mock_inst = MagicMock()
            mock_inst.ingest.return_value = {"files": 2, "nodes": 5}
            MockIngester.return_value = mock_inst

            summary = wm.ingest_all()

        assert "repo1" in summary
        assert summary["repo1"]["files"] == 2

    def test_ingest_all_no_repos_returns_empty(self):
        from navegador.multirepo import WorkspaceManager, WorkspaceMode

        wm = WorkspaceManager(_make_store(), mode=WorkspaceMode.UNIFIED)
        assert wm.ingest_all() == {}

    def test_search_unified_queries_single_store(self):
        from navegador.multirepo import WorkspaceManager, WorkspaceMode

        store = _make_store()
        store.query.return_value = MagicMock(
            result_set=[["Function", "authenticate", "/src/auth.py"]]
        )
        wm = WorkspaceManager(store, mode=WorkspaceMode.UNIFIED)
        wm.add_repo("repo", "/tmp/repo")

        results = wm.search("authenticate")
        assert len(results) >= 1
        assert results[0]["name"] == "authenticate"

    def test_ingest_error_recorded_in_summary(self, tmp_path):
        from navegador.multirepo import WorkspaceManager, WorkspaceMode

        store = _make_store()
        wm = WorkspaceManager(store, mode=WorkspaceMode.UNIFIED)
        wm.add_repo("broken", str(tmp_path))

        with patch("navegador.ingestion.parser.RepoIngester") as MockIngester:
            MockIngester.return_value.ingest.side_effect = RuntimeError("parse error")
            summary = wm.ingest_all()

        assert "broken" in summary
        assert "error" in summary["broken"]


class TestWorkspaceManagerFederated:
    """WorkspaceManager in FEDERATED mode creates per-repo graphs."""

    def test_add_repo_sets_federated_graph_name(self, tmp_path):
        from navegador.multirepo import WorkspaceManager, WorkspaceMode

        store = _make_store()
        wm = WorkspaceManager(store, mode=WorkspaceMode.FEDERATED)
        wm.add_repo("api", str(tmp_path))

        repos = wm.list_repos()
        assert repos[0]["graph_name"] == "navegador_api"

    def test_unified_graph_name_is_navegador(self, tmp_path):
        from navegador.multirepo import WorkspaceManager, WorkspaceMode

        store = _make_store()
        wm = WorkspaceManager(store, mode=WorkspaceMode.UNIFIED)
        wm.add_repo("api", str(tmp_path))

        repos = wm.list_repos()
        assert repos[0]["graph_name"] == "navegador"

    def test_federated_ingest_uses_per_repo_store(self, tmp_path):
        from navegador.multirepo import WorkspaceManager, WorkspaceMode

        store = _make_store()
        # select_graph returns a different mock each time
        store._client.select_graph.return_value = MagicMock()

        wm = WorkspaceManager(store, mode=WorkspaceMode.FEDERATED)
        wm.add_repo("svc", str(tmp_path))

        with patch("navegador.ingestion.parser.RepoIngester") as MockIngester:
            mock_inst = MagicMock()
            mock_inst.ingest.return_value = {"files": 1, "nodes": 3}
            MockIngester.return_value = mock_inst

            summary = wm.ingest_all()

        assert "svc" in summary
        # select_graph should have been called with "navegador_svc"
        called_graphs = [
            c.args[0] for c in store._client.select_graph.call_args_list
        ]
        assert any("navegador_svc" in g for g in called_graphs)

    def test_federated_search_merges_results(self):
        from navegador.multirepo import WorkspaceManager, WorkspaceMode

        store = _make_store()

        # Each per-repo graph returns a result
        per_repo_store_mock = MagicMock()
        per_repo_store_mock.query.return_value = MagicMock(
            result_set=[["Function", "auth_check", "/src/auth.py"]]
        )
        store._client.select_graph.return_value = per_repo_store_mock

        wm = WorkspaceManager(store, mode=WorkspaceMode.FEDERATED)
        wm._repos = {
            "backend": {"path": "/tmp/backend", "graph_name": "navegador_backend"},
            "frontend": {"path": "/tmp/frontend", "graph_name": "navegador_frontend"},
        }

        results = wm.search("auth")
        # Two repos each return one result → 2 total (deduplicated to 1 because same name)
        assert len(results) >= 1


# =============================================================================
# CLI smoke tests
# =============================================================================


class TestCLIPMGroup:
    """pm group is registered on the main CLI."""

    def test_pm_group_exists(self):
        from click.testing import CliRunner

        from navegador.cli.commands import main

        runner = CliRunner()
        result = runner.invoke(main, ["pm", "--help"])
        assert result.exit_code == 0
        assert "ingest" in result.output

    def test_pm_ingest_requires_github(self):
        from click.testing import CliRunner

        from navegador.cli.commands import main

        runner = CliRunner()
        result = runner.invoke(main, ["pm", "ingest"])
        assert result.exit_code != 0


class TestCLIDepsGroup:
    """deps group is registered on the main CLI."""

    def test_deps_group_exists(self):
        from click.testing import CliRunner

        from navegador.cli.commands import main

        runner = CliRunner()
        result = runner.invoke(main, ["deps", "--help"])
        assert result.exit_code == 0
        assert "ingest" in result.output


class TestCLISubmodulesGroup:
    """submodules group is registered on the main CLI."""

    def test_submodules_group_exists(self):
        from click.testing import CliRunner

        from navegador.cli.commands import main

        runner = CliRunner()
        result = runner.invoke(main, ["submodules", "--help"])
        assert result.exit_code == 0

    def test_submodules_list_empty(self, tmp_path):
        from click.testing import CliRunner

        from navegador.cli.commands import main

        runner = CliRunner()
        result = runner.invoke(main, ["submodules", "list", str(tmp_path)])
        assert result.exit_code == 0
        assert "No submodules" in result.output


class TestCLIWorkspaceGroup:
    """workspace group is registered on the main CLI."""

    def test_workspace_group_exists(self):
        from click.testing import CliRunner

        from navegador.cli.commands import main

        runner = CliRunner()
        result = runner.invoke(main, ["workspace", "--help"])
        assert result.exit_code == 0
        assert "ingest" in result.output

    def test_workspace_ingest_requires_repos(self, tmp_path):
        from click.testing import CliRunner

        from navegador.cli.commands import main

        runner = CliRunner()
        result = runner.invoke(
            main,
            ["workspace", "ingest", "--db", str(tmp_path / "g.db")],
        )
        assert result.exit_code != 0
