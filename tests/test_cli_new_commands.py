"""Tests for uncovered CLI commands in navegador.

Covers: init --commit-graph, memory ingest (recursive/workspace/default),
impact (knowledge output), drift, diff-graph, review, cross-impact,
repo list/ingest-all/search (JSON paths), rename, codeowners, adr ingest,
deps ingest, submodules list, workspace ingest, pack, doclink group,
lens group, snapshot, history, graph-at, lineage.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from navegador.cli.commands import main

# ── Helpers ──────────────────────────────────────────────────────────────────


def _mock_store():
    store = MagicMock()
    store.query.return_value = MagicMock(result_set=[])
    return store


# ── init --commit-graph (line 132) ───────────────────────────────────────────


class TestInitCommitGraph:
    def test_commit_graph_flag_shows_committed_hint(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(main, ["init", ".", "--commit-graph"])
            assert result.exit_code == 0
            assert "committed to git" in result.output
            assert "navegador ingest" in result.output


# ── memory ingest (lines 589-607) ────────────────────────────────────────────


class TestMemoryIngest:
    def test_recursive_flag(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            Path("repo").mkdir()
            with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
                 patch("navegador.ingestion.MemoryIngester") as MockMI:
                MockMI.return_value.ingest_recursive.return_value = {
                    "ingested": 12, "skipped": 2, "repos": ["svc-a", "svc-b"],
                }
                result = runner.invoke(main, ["memory", "ingest", "repo", "--recursive"])
                assert result.exit_code == 0
                assert "recursive" in result.output
                assert "12" in result.output
                assert "2 scopes" in result.output
                MockMI.return_value.ingest_recursive.assert_called_once()

    def test_workspace_flag(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            Path("ws").mkdir()
            with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
                 patch("navegador.ingestion.MemoryIngester") as MockMI:
                MockMI.return_value.ingest_workspace.return_value = {
                    "ingested": 8, "skipped": 1, "repos": ["root", "sub1"],
                }
                result = runner.invoke(main, ["memory", "ingest", "ws", "--workspace"])
                assert result.exit_code == 0
                assert "workspace" in result.output
                assert "8" in result.output
                MockMI.return_value.ingest_workspace.assert_called_once()

    def test_default_ingest(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            Path("mem").mkdir()
            with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
                 patch("navegador.ingestion.MemoryIngester") as MockMI:
                MockMI.return_value.ingest.return_value = {
                    "ingested": 5, "repo": "my-repo",
                    "by_type": {"Concept": 3, "Rule": 2},
                }
                result = runner.invoke(main, ["memory", "ingest", "mem", "--repo", "my-repo"])
                assert result.exit_code == 0
                assert "Memory ingested" in result.output
                assert "my-repo" in result.output
                MockMI.return_value.ingest.assert_called_once()


# ── impact with affected_knowledge (lines 1449-1452) ────────────────────────


class TestImpactKnowledge:
    def test_impact_shows_knowledge_nodes(self):
        runner = CliRunner()
        with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
             patch("navegador.analysis.impact.ImpactAnalyzer") as MockIA:
            mock_result = MagicMock()
            mock_result.affected_nodes = [
                {"type": "Function", "name": "helper", "file_path": "a.py", "line_start": 10},
            ]
            mock_result.affected_files = ["a.py"]
            mock_result.affected_knowledge = [
                {"type": "Rule", "name": "NoNullIds"},
                {"type": "Concept", "name": "Payment"},
            ]
            MockIA.return_value.blast_radius.return_value = mock_result
            result = runner.invoke(main, ["impact", "process"])
            assert result.exit_code == 0
            assert "Affected knowledge" in result.output
            assert "NoNullIds" in result.output
            assert "Payment" in result.output


# ── drift (lines 1472-1482) ──────────────────────────────────────────────────


class TestDriftCommand:
    def test_drift_markdown(self):
        runner = CliRunner()
        with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
             patch("navegador.analysis.drift.DriftChecker") as MockDC:
            mock_report = MagicMock()
            mock_report.to_markdown.return_value = "# Drift Report\nAll good"
            mock_report.has_violations = False
            MockDC.return_value.check.return_value = mock_report
            result = runner.invoke(main, ["drift"])
            assert result.exit_code == 0
            assert "Drift Report" in result.output

    def test_drift_json(self):
        runner = CliRunner()
        with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
             patch("navegador.analysis.drift.DriftChecker") as MockDC:
            mock_report = MagicMock()
            mock_report.to_json.return_value = '{"violations": []}'
            mock_report.has_violations = False
            MockDC.return_value.check.return_value = mock_report
            result = runner.invoke(main, ["drift", "--json"])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data["violations"] == []

    def test_drift_fail_on_violations(self):
        runner = CliRunner()
        with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
             patch("navegador.analysis.drift.DriftChecker") as MockDC:
            mock_report = MagicMock()
            mock_report.to_markdown.return_value = "# Violations found"
            mock_report.has_violations = True
            MockDC.return_value.check.return_value = mock_report
            result = runner.invoke(main, ["drift", "--fail-on-violations"])
            assert result.exit_code == 1


# ── diff-graph (lines 1521-1534) ────────────────────────────────────────────


class TestDiffGraphCommand:
    def test_diff_working_tree_default(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
                 patch("navegador.analysis.diffgraph.DiffGraphAnalyzer") as MockDGA:
                mock_report = MagicMock()
                mock_report.to_markdown.return_value = "# Diff Report"
                MockDGA.return_value.diff_working_tree.return_value = mock_report
                result = runner.invoke(main, ["diff-graph", "--repo-path", "."])
                assert result.exit_code == 0
                assert "Diff Report" in result.output
                MockDGA.return_value.diff_working_tree.assert_called_once()

    def test_diff_refs(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
                 patch("navegador.analysis.diffgraph.DiffGraphAnalyzer") as MockDGA:
                mock_report = MagicMock()
                mock_report.to_json.return_value = '{"changes": []}'
                MockDGA.return_value.diff_refs.return_value = mock_report
                result = runner.invoke(main, [
                    "diff-graph", "--base", "main", "--head", "HEAD",
                    "--json", "--repo-path", ".",
                ])
                assert result.exit_code == 0
                data = json.loads(result.output)
                assert "changes" in data
                MockDGA.return_value.diff_refs.assert_called_once_with(
                    base="main", head="HEAD",
                )

    def test_diff_snapshots(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
                 patch("navegador.analysis.diffgraph.DiffGraphAnalyzer") as MockDGA:
                mock_report = MagicMock()
                mock_report.to_markdown.return_value = "# Snapshot Diff"
                MockDGA.return_value.diff_snapshots.return_value = mock_report
                result = runner.invoke(main, [
                    "diff-graph", "--base", "v1.0", "--head", "v2.0",
                    "--snapshot", "--repo-path", ".",
                ])
                assert result.exit_code == 0
                assert "Snapshot Diff" in result.output
                MockDGA.return_value.diff_snapshots.assert_called_once_with(
                    base_ref="v1.0", head_ref="v2.0",
                )


# ── review (lines 1571-1595) ────────────────────────────────────────────────


class TestReviewCommand:
    def _mock_diff_report(self):
        report = MagicMock()
        symbol = MagicMock()
        symbol.symbol = "process_payment"
        symbol.file_path = "payments.py"
        report.new_symbols = [symbol]
        report.changed_symbols = []
        report.affected_files = {"payments.py"}
        return report

    def test_review_markdown(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
                 patch("navegador.analysis.diffgraph.DiffGraphAnalyzer") as MockDGA, \
                 patch("navegador.analysis.review.ReviewGenerator") as MockRG:
                MockDGA.return_value.diff_refs.return_value = self._mock_diff_report()
                mock_review = MagicMock()
                comment = MagicMock()
                comment.confidence = 0.8
                mock_review.comments = [comment]
                mock_review.to_markdown.return_value = "# Review\n- Check payment validation"
                MockRG.return_value.review_diff.return_value = mock_review
                result = runner.invoke(main, ["review", "--repo-path", "."])
                assert result.exit_code == 0
                assert "Review" in result.output

    def test_review_json(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
                 patch("navegador.analysis.diffgraph.DiffGraphAnalyzer") as MockDGA, \
                 patch("navegador.analysis.review.ReviewGenerator") as MockRG:
                MockDGA.return_value.diff_refs.return_value = self._mock_diff_report()
                mock_review = MagicMock()
                comment = MagicMock()
                comment.confidence = 0.9
                mock_review.comments = [comment]
                mock_review.to_json.return_value = '{"comments": [{"text": "ok"}]}'
                MockRG.return_value.review_diff.return_value = mock_review
                result = runner.invoke(main, ["review", "--json", "--repo-path", "."])
                assert result.exit_code == 0
                data = json.loads(result.output)
                assert "comments" in data


# ── cross-impact (lines 1616-1626) ──────────────────────────────────────────


class TestCrossImpactCommand:
    def test_cross_impact_markdown(self):
        runner = CliRunner()
        with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
             patch("navegador.analysis.crossrepo.CrossRepoImpactAnalyzer") as MockCR:
            mock_result = MagicMock()
            mock_result.to_markdown.return_value = "# Cross-repo impact\n- 3 repos affected"
            MockCR.return_value.blast_radius.return_value = mock_result
            result = runner.invoke(main, ["cross-impact", "AuthService"])
            assert result.exit_code == 0
            assert "Cross-repo impact" in result.output

    def test_cross_impact_json(self):
        runner = CliRunner()
        with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
             patch("navegador.analysis.crossrepo.CrossRepoImpactAnalyzer") as MockCR:
            mock_result = MagicMock()
            mock_result.to_json.return_value = '{"repos": ["api", "worker"]}'
            MockCR.return_value.blast_radius.return_value = mock_result
            result = runner.invoke(main, ["cross-impact", "AuthService", "--json"])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data["repos"] == ["api", "worker"]


# ── repo list/ingest-all/search JSON paths (lines 1847, 1872, 1894) ────────


class TestRepoGroupJsonPaths:
    def test_repo_list_json(self):
        runner = CliRunner()
        with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
             patch("navegador.multirepo.MultiRepoManager") as MockMRM:
            MockMRM.return_value.list_repos.return_value = [
                {"name": "api", "path": "/code/api"},
            ]
            result = runner.invoke(main, ["repo", "list", "--json"])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data[0]["name"] == "api"

    def test_repo_ingest_all_json(self):
        runner = CliRunner()
        with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
             patch("navegador.multirepo.MultiRepoManager") as MockMRM:
            MockMRM.return_value.ingest_all.return_value = {
                "api": {"files": 10, "nodes": 50},
            }
            result = runner.invoke(main, ["repo", "ingest-all", "--json"])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data["api"]["files"] == 10

    def test_repo_search_json(self):
        runner = CliRunner()
        with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
             patch("navegador.multirepo.MultiRepoManager") as MockMRM:
            MockMRM.return_value.cross_repo_search.return_value = [
                {"label": "Function", "name": "auth", "file_path": "auth.py"},
            ]
            result = runner.invoke(main, ["repo", "search", "auth", "--json"])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data[0]["name"] == "auth"


# ── rename (lines 1948-1949) ────────────────────────────────────────────────


class TestRenameCommand:
    def test_rename_json(self):
        runner = CliRunner()
        with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
             patch("navegador.refactor.SymbolRenamer") as MockSR:
            mock_result = MagicMock()
            mock_result.old_name = "old_fn"
            mock_result.new_name = "new_fn"
            mock_result.affected_files = ["app.py"]
            mock_result.affected_nodes = [MagicMock()]
            mock_result.edges_updated = 3
            MockSR.return_value.apply_rename.return_value = mock_result
            result = runner.invoke(main, ["rename", "old_fn", "new_fn", "--json"])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data["old_name"] == "old_fn"
            assert data["new_name"] == "new_fn"
            assert data["edges_updated"] == 3


# ── codeowners (lines 1974-1975) ────────────────────────────────────────────


class TestCodeownersCommand:
    def test_codeowners_json(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            Path("repo").mkdir()
            with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
                 patch("navegador.codeowners.CodeownersIngester") as MockCI:
                MockCI.return_value.ingest.return_value = {
                    "owners": 3, "patterns": 5, "edges": 8,
                }
                result = runner.invoke(main, ["codeowners", "repo", "--json"])
                assert result.exit_code == 0
                data = json.loads(result.output)
                assert data["owners"] == 3
                assert data["patterns"] == 5


# ── adr ingest (lines 2000-2001) ────────────────────────────────────────────


class TestADRIngestCommand:
    def test_adr_ingest_json(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            Path("adrs").mkdir()
            with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
                 patch("navegador.adr.ADRIngester") as MockAI:
                MockAI.return_value.ingest.return_value = {
                    "decisions": 4, "skipped": 1,
                }
                result = runner.invoke(main, ["adr", "ingest", "adrs", "--json"])
                assert result.exit_code == 0
                data = json.loads(result.output)
                assert data["decisions"] == 4
                assert data["skipped"] == 1


# ── api ingest auto-detect openapi fallback (line 2047) ─────────────────────


class TestAPIIngestAutoDetect:
    def test_auto_detect_openapi_from_json_file(self):
        """Non-.graphql files default to openapi auto-detection (line 2047)."""
        runner = CliRunner()
        with runner.isolated_filesystem():
            Path("swagger.json").write_text("{}")
            with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
                 patch("navegador.api_schema.APISchemaIngester") as MockASI:
                MockASI.return_value.ingest_openapi.return_value = {
                    "endpoints": 5, "schemas": 3,
                }
                result = runner.invoke(main, ["api", "ingest", "swagger.json"])
                assert result.exit_code == 0
                MockASI.return_value.ingest_openapi.assert_called_once()
                assert "OpenAPI" in result.output


# ── deps ingest (lines 2161-2191) ───────────────────────────────────────────


class TestDepsIngestCommand:
    def test_deps_ingest_npm_auto(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            Path("package.json").write_text("{}")
            with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
                 patch("navegador.dependencies.DependencyIngester") as MockDI:
                MockDI.return_value.ingest_npm.return_value = {"packages": 12}
                result = runner.invoke(main, ["deps", "ingest", "package.json"])
                assert result.exit_code == 0
                assert "12 packages" in result.output
                MockDI.return_value.ingest_npm.assert_called_once()

    def test_deps_ingest_json_output(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            Path("requirements.txt").write_text("")
            with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
                 patch("navegador.dependencies.DependencyIngester") as MockDI:
                MockDI.return_value.ingest_pip.return_value = {"packages": 7}
                result = runner.invoke(main, ["deps", "ingest", "requirements.txt", "--json"])
                assert result.exit_code == 0
                data = json.loads(result.output)
                assert data["packages"] == 7

    def test_deps_ingest_cargo_auto(self):
        """Auto-detect cargo from Cargo.toml (line 2175)."""
        runner = CliRunner()
        with runner.isolated_filesystem():
            Path("Cargo.toml").write_text("")
            with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
                 patch("navegador.dependencies.DependencyIngester") as MockDI:
                MockDI.return_value.ingest_cargo.return_value = {"packages": 4}
                result = runner.invoke(main, ["deps", "ingest", "Cargo.toml"])
                assert result.exit_code == 0
                assert "4 packages" in result.output
                MockDI.return_value.ingest_cargo.assert_called_once()

    def test_deps_ingest_unknown_type_errors(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            Path("unknown.lock").write_text("")
            with patch("navegador.cli.commands._get_store", return_value=_mock_store()):
                result = runner.invoke(main, ["deps", "ingest", "unknown.lock"])
                assert result.exit_code != 0
                assert "Cannot auto-detect" in result.output


# ── submodules list (lines 2249-2255) ────────────────────────────────────────


class TestSubmodulesListCommand:
    def test_submodules_list_with_results(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            Path("repo").mkdir()
            # The command calls SubmoduleIngester.__new__(SubmoduleIngester)
            # then subs.detect_submodules(repo_path). Patch detect_submodules
            # on the SubmoduleIngester class so __new__ produces a real
            # instance but the method returns controlled data.
            with patch(
                "navegador.submodules.SubmoduleIngester.detect_submodules",
                return_value=[
                    {"name": "lib-core", "path": "libs/core", "url": "git@host:core"},
                ],
            ):
                result = runner.invoke(main, ["submodules", "list", "repo"])
                assert result.exit_code == 0
                assert "lib-core" in result.output


# ── workspace ingest (lines 2295-2311) ──────────────────────────────────────


class TestWorkspaceIngestCommand:
    def test_workspace_ingest_json(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            Path("api").mkdir()
            Path("web").mkdir()
            with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
                 patch("navegador.multirepo.WorkspaceManager") as MockWM, \
                 patch("navegador.multirepo.WorkspaceMode"):
                MockWM.return_value.ingest_all.return_value = {
                    "api": {"files": 5, "nodes": 20},
                    "web": {"files": 3, "nodes": 10},
                }
                result = runner.invoke(
                    main, ["workspace", "ingest", "api=api", "web=web", "--json"]
                )
                assert result.exit_code == 0
                data = json.loads(result.output)
                assert data["api"]["files"] == 5
                assert data["web"]["nodes"] == 10

    def test_workspace_ingest_table_output(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            Path("svc").mkdir()
            with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
                 patch("navegador.multirepo.WorkspaceManager") as MockWM, \
                 patch("navegador.multirepo.WorkspaceMode"):
                MockWM.return_value.ingest_all.return_value = {
                    "svc": {"files": 2, "nodes": 8},
                }
                result = runner.invoke(main, ["workspace", "ingest", "svc=svc"])
                assert result.exit_code == 0
                assert "svc" in result.output
                assert "2 files" in result.output

    def test_workspace_ingest_error_repo(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            Path("bad").mkdir()
            with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
                 patch("navegador.multirepo.WorkspaceManager") as MockWM, \
                 patch("navegador.multirepo.WorkspaceMode"):
                MockWM.return_value.ingest_all.return_value = {
                    "bad": {"error": "not a git repo"},
                }
                result = runner.invoke(main, ["workspace", "ingest", "bad=bad"])
                assert result.exit_code == 0
                assert "Error" in result.output
                assert "not a git repo" in result.output

    def test_workspace_ingest_invalid_spec(self):
        runner = CliRunner()
        result = runner.invoke(main, ["workspace", "ingest", "no-equals-sign"])
        assert result.exit_code != 0
        assert "NAME=PATH" in result.output


# ── pack (lines 2345-2359) ──────────────────────────────────────────────────


class TestPackCommand:
    def test_pack_symbol(self):
        runner = CliRunner()
        with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
             patch("navegador.taskpack.TaskPackBuilder") as MockTPB:
            mock_pack = MagicMock()
            mock_pack.to_markdown.return_value = "# Task Pack\nImplement AuthService"
            MockTPB.return_value.for_symbol.return_value = mock_pack
            result = runner.invoke(main, ["pack", "AuthService"])
            assert result.exit_code == 0
            assert "Task Pack" in result.output
            MockTPB.return_value.for_symbol.assert_called_once()

    def test_pack_file_path(self):
        runner = CliRunner()
        with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
             patch("navegador.taskpack.TaskPackBuilder") as MockTPB:
            mock_pack = MagicMock()
            mock_pack.to_json.return_value = '{"target": "app/auth.py"}'
            MockTPB.return_value.for_file.return_value = mock_pack
            result = runner.invoke(main, ["pack", "app/auth.py", "--json"])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data["target"] == "app/auth.py"
            MockTPB.return_value.for_file.assert_called_once()


# ── doclink suggest (lines 2789-2820) ───────────────────────────────────────


class TestDocLinkSuggestCommand:
    def _mock_candidate(self, source="API Guide", target="AuthService",
                        strategy="EXACT_NAME", confidence=0.9):
        c = MagicMock()
        c.source_name = source
        c.target_name = target
        c.target_file = "auth.py"
        c.strategy = strategy
        c.confidence = confidence
        c.__dict__ = {
            "source_name": source, "target_name": target,
            "target_file": "auth.py", "strategy": strategy,
            "confidence": confidence,
        }
        return c

    def test_suggest_table_output(self):
        runner = CliRunner()
        with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
             patch("navegador.intelligence.doclink.DocLinker") as MockDL:
            MockDL.return_value.suggest_links.return_value = [self._mock_candidate()]
            result = runner.invoke(main, ["doclink", "suggest"])
            assert result.exit_code == 0
            assert "API Guide" in result.output
            assert "AuthService" in result.output

    def test_suggest_json_output(self):
        runner = CliRunner()
        with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
             patch("navegador.intelligence.doclink.DocLinker") as MockDL:
            MockDL.return_value.suggest_links.return_value = [self._mock_candidate()]
            result = runner.invoke(main, ["doclink", "suggest", "--json"])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert len(data) == 1
            assert data[0]["source_name"] == "API Guide"

    def test_suggest_no_candidates(self):
        runner = CliRunner()
        with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
             patch("navegador.intelligence.doclink.DocLinker") as MockDL:
            MockDL.return_value.suggest_links.return_value = []
            result = runner.invoke(main, ["doclink", "suggest"])
            assert result.exit_code == 0
            assert "No link candidates" in result.output

    def test_suggest_strategy_filter(self):
        runner = CliRunner()
        with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
             patch("navegador.intelligence.doclink.DocLinker") as MockDL:
            exact = self._mock_candidate(strategy="EXACT_NAME")
            fuzzy = self._mock_candidate(source="Readme", target="parse", strategy="FUZZY")
            MockDL.return_value.suggest_links.return_value = [exact, fuzzy]
            result = runner.invoke(main, [
                "doclink", "suggest", "--strategy", "EXACT_NAME", "--json",
            ])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert len(data) == 1
            assert data[0]["strategy"] == "EXACT_NAME"


# ── doclink accept (lines 2846-2874) ────────────────────────────────────────


class TestDocLinkAcceptCommand:
    def test_accept_existing_candidate(self):
        runner = CliRunner()
        with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
             patch("navegador.intelligence.doclink.DocLinker") as MockDL, \
             patch("navegador.intelligence.doclink.LinkCandidate"):
            candidate = MagicMock()
            candidate.source_name = "API Guide"
            candidate.target_name = "AuthService"
            MockDL.return_value.suggest_links.return_value = [candidate]
            result = runner.invoke(main, ["doclink", "accept", "API Guide", "AuthService"])
            assert result.exit_code == 0
            assert "Accepted" in result.output
            assert "API Guide" in result.output
            assert "AuthService" in result.output
            MockDL.return_value.accept.assert_called_once_with(candidate)

    def test_accept_manual_fallback(self):
        runner = CliRunner()
        with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
             patch("navegador.intelligence.doclink.DocLinker") as MockDL, \
             patch("navegador.intelligence.doclink.LinkCandidate") as MockLC:
            MockDL.return_value.suggest_links.return_value = []
            result = runner.invoke(main, ["doclink", "accept", "README", "parse_token"])
            assert result.exit_code == 0
            assert "Accepted" in result.output
            MockLC.assert_called_once()
            MockDL.return_value.accept.assert_called_once()


# ── doclink accept-all (lines 2896-2910) ────────────────────────────────────


class TestDocLinkAcceptAllCommand:
    def test_accept_all_dry_run(self):
        runner = CliRunner()
        with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
             patch("navegador.intelligence.doclink.DocLinker") as MockDL:
            MockDL.return_value.suggest_links.return_value = [MagicMock(), MagicMock()]
            result = runner.invoke(main, ["doclink", "accept-all", "--dry-run"])
            assert result.exit_code == 0
            assert "Dry run" in result.output
            assert "2" in result.output
            MockDL.return_value.accept_all.assert_not_called()

    def test_accept_all_writes_edges(self):
        runner = CliRunner()
        with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
             patch("navegador.intelligence.doclink.DocLinker") as MockDL:
            candidates = [MagicMock(), MagicMock(), MagicMock()]
            MockDL.return_value.suggest_links.return_value = candidates
            MockDL.return_value.accept_all.return_value = 3
            result = runner.invoke(main, ["doclink", "accept-all", "--min-confidence", "0.9"])
            assert result.exit_code == 0
            assert "Accepted" in result.output
            assert "3" in result.output


# ── lens list (lines 2932-2952) ─────────────────────────────────────────────


class TestLensListCommand:
    def test_lens_list_table(self):
        runner = CliRunner()
        with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
             patch("navegador.lenses.LensEngine") as MockLE:
            MockLE.return_value.list_lenses.return_value = [
                {"name": "request_path", "builtin": True, "description": "HTTP request flow"},
                {"name": "ownership_map", "builtin": True, "description": "Code owners"},
            ]
            result = runner.invoke(main, ["lens", "list"])
            assert result.exit_code == 0
            assert "request_path" in result.output
            assert "ownership_map" in result.output

    def test_lens_list_json(self):
        runner = CliRunner()
        with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
             patch("navegador.lenses.LensEngine") as MockLE:
            MockLE.return_value.list_lenses.return_value = [
                {"name": "request_path", "builtin": True, "description": "flow"},
            ]
            result = runner.invoke(main, ["lens", "list", "--json"])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data[0]["name"] == "request_path"


# ── lens apply (lines 2984-2996) ────────────────────────────────────────────


class TestLensApplyCommand:
    def test_lens_apply_markdown(self):
        runner = CliRunner()
        with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
             patch("navegador.lenses.LensEngine") as MockLE:
            mock_result = MagicMock()
            mock_result.to_markdown.return_value = "# Request Path\nauth -> handler -> db"
            MockLE.return_value.apply.return_value = mock_result
            result = runner.invoke(main, ["lens", "apply", "request_path"])
            assert result.exit_code == 0
            assert "Request Path" in result.output

    def test_lens_apply_json(self):
        runner = CliRunner()
        with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
             patch("navegador.lenses.LensEngine") as MockLE:
            mock_result = MagicMock()
            mock_result.to_json.return_value = '{"lens": "ownership_map", "nodes": []}'
            MockLE.return_value.apply.return_value = mock_result
            result = runner.invoke(main, [
                "lens", "apply", "ownership_map",
                "--domain", "auth", "--json",
            ])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data["lens"] == "ownership_map"
            MockLE.return_value.apply.assert_called_once_with(
                "ownership_map", symbol="", domain="auth", file_path="", label=""
            )

    def test_lens_apply_unknown_lens_error(self):
        runner = CliRunner()
        with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
             patch("navegador.lenses.LensEngine") as MockLE:
            MockLE.return_value.apply.side_effect = ValueError("Unknown lens: bogus")
            result = runner.invoke(main, ["lens", "apply", "bogus"])
            assert result.exit_code != 0
            assert "bogus" in result.output


# ── snapshot (lines 2660-2665) ──────────────────────────────────────────────


class TestSnapshotCommand:
    def test_snapshot_head(self):
        runner = CliRunner()
        with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
             patch("navegador.history.HistoryStore") as MockHS:
            info = MagicMock()
            info.ref = "HEAD"
            info.commit_sha = "abc1234"
            info.symbol_count = 42
            MockHS.return_value.snapshot.return_value = info
            result = runner.invoke(main, ["snapshot", "HEAD"])
            assert result.exit_code == 0
            assert "HEAD" in result.output
            assert "abc1234" in result.output
            assert "42" in result.output

    def test_snapshot_tag(self):
        runner = CliRunner()
        with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
             patch("navegador.history.HistoryStore") as MockHS:
            info = MagicMock()
            info.ref = "v2.0.0"
            info.commit_sha = "def5678"
            info.symbol_count = 100
            MockHS.return_value.snapshot.return_value = info
            result = runner.invoke(main, ["snapshot", "v2.0.0"])
            assert result.exit_code == 0
            assert "v2.0.0" in result.output


# ── history (lines 2688-2695) ───────────────────────────────────────────────


class TestHistoryCommand:
    def test_history_markdown(self):
        runner = CliRunner()
        with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
             patch("navegador.history.HistoryStore") as MockHS:
            mock_report = MagicMock()
            mock_report.to_markdown.return_value = "# History of AuthService\nFirst seen in v1.0"
            MockHS.return_value.history.return_value = mock_report
            result = runner.invoke(main, ["history", "AuthService"])
            assert result.exit_code == 0
            assert "History of AuthService" in result.output

    def test_history_json(self):
        runner = CliRunner()
        with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
             patch("navegador.history.HistoryStore") as MockHS:
            mock_report = MagicMock()
            mock_report.to_json.return_value = '{"name": "AuthService", "events": []}'
            MockHS.return_value.history.return_value = mock_report
            result = runner.invoke(main, ["history", "AuthService", "--json"])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data["name"] == "AuthService"

    def test_history_with_file_filter(self):
        runner = CliRunner()
        with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
             patch("navegador.history.HistoryStore") as MockHS:
            mock_report = MagicMock()
            mock_report.to_markdown.return_value = "# History"
            MockHS.return_value.history.return_value = mock_report
            result = runner.invoke(main, ["history", "parse_token", "--file", "auth.py"])
            assert result.exit_code == 0
            MockHS.return_value.history.assert_called_once_with("parse_token", file_path="auth.py")


# ── graph-at (lines 2711-2723) ──────────────────────────────────────────────


class TestGraphAtCommand:
    def test_graph_at_table_output(self):
        runner = CliRunner()
        with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
             patch("navegador.history.HistoryStore") as MockHS:
            sym1 = MagicMock()
            sym1.label = "Function"
            sym1.name = "parse_token"
            sym1.file_path = "auth.py"
            sym1.__dict__ = {"label": "Function", "name": "parse_token", "file_path": "auth.py"}
            sym2 = MagicMock()
            sym2.label = "Class"
            sym2.name = "AuthService"
            sym2.file_path = "auth.py"
            sym2.__dict__ = {"label": "Class", "name": "AuthService", "file_path": "auth.py"}
            MockHS.return_value.symbols_at.return_value = [sym1, sym2]
            result = runner.invoke(main, ["graph-at", "v1.0.0"])
            assert result.exit_code == 0
            assert "2" in result.output
            assert "parse_token" in result.output
            assert "AuthService" in result.output

    def test_graph_at_json(self):
        runner = CliRunner()
        with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
             patch("navegador.history.HistoryStore") as MockHS:
            sym = MagicMock()
            sym.__dict__ = {"label": "Function", "name": "foo", "file_path": "bar.py"}
            MockHS.return_value.symbols_at.return_value = [sym]
            result = runner.invoke(main, ["graph-at", "main", "--json"])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data[0]["name"] == "foo"

    def test_graph_at_no_snapshot(self):
        runner = CliRunner()
        with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
             patch("navegador.history.HistoryStore") as MockHS:
            MockHS.return_value.symbols_at.return_value = []
            result = runner.invoke(main, ["graph-at", "nonexistent"])
            assert result.exit_code == 0
            assert "No snapshot found" in result.output


# ── lineage (lines 2742-2749) ───────────────────────────────────────────────


class TestLineageCommand:
    def test_lineage_markdown(self):
        runner = CliRunner()
        with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
             patch("navegador.history.HistoryStore") as MockHS:
            mock_report = MagicMock()
            mock_report.to_markdown.return_value = "# Lineage: AuthService\nRenamed from Auth"
            MockHS.return_value.lineage.return_value = mock_report
            result = runner.invoke(main, ["lineage", "AuthService"])
            assert result.exit_code == 0
            assert "Lineage" in result.output

    def test_lineage_json(self):
        runner = CliRunner()
        with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
             patch("navegador.history.HistoryStore") as MockHS:
            mock_report = MagicMock()
            mock_report.to_json.return_value = '{"name": "AuthService", "chain": []}'
            MockHS.return_value.lineage.return_value = mock_report
            result = runner.invoke(main, ["lineage", "AuthService", "--json"])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data["name"] == "AuthService"
