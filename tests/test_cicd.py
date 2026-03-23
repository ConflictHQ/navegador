"""Tests for navegador.cicd — CI/CD mode, CICDReporter, and `navegador ci` commands."""

import json
import os
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from navegador.cicd import (
    EXIT_ERROR,
    EXIT_SUCCESS,
    EXIT_WARN,
    CICDReporter,
    detect_ci,
    is_ci,
    is_github_actions,
)
from navegador.cli.commands import main


# ── Helpers ───────────────────────────────────────────────────────────────────


def _clear_ci_env(monkeypatch):
    """Remove all known CI indicator env vars so each test starts clean."""
    for var in ("GITHUB_ACTIONS", "CI", "GITLAB_CI", "CIRCLECI", "JENKINS_URL"):
        monkeypatch.delenv(var, raising=False)


def _mock_store():
    store = MagicMock()
    store.query.return_value = MagicMock(result_set=[])
    return store


# ── CI detection ──────────────────────────────────────────────────────────────


class TestDetectCI:
    def test_returns_none_outside_ci(self, monkeypatch):
        _clear_ci_env(monkeypatch)
        assert detect_ci() is None

    def test_detects_github_actions(self, monkeypatch):
        _clear_ci_env(monkeypatch)
        monkeypatch.setenv("GITHUB_ACTIONS", "true")
        assert detect_ci() == "github_actions"

    def test_detects_generic_ci(self, monkeypatch):
        _clear_ci_env(monkeypatch)
        monkeypatch.setenv("CI", "true")
        assert detect_ci() == "ci"

    def test_detects_gitlab_ci(self, monkeypatch):
        _clear_ci_env(monkeypatch)
        monkeypatch.setenv("GITLAB_CI", "true")
        assert detect_ci() == "gitlab_ci"

    def test_detects_circleci(self, monkeypatch):
        _clear_ci_env(monkeypatch)
        monkeypatch.setenv("CIRCLECI", "true")
        assert detect_ci() == "circleci"

    def test_detects_jenkins(self, monkeypatch):
        _clear_ci_env(monkeypatch)
        monkeypatch.setenv("JENKINS_URL", "http://jenkins.local/")
        assert detect_ci() == "jenkins"

    def test_github_actions_takes_priority_over_ci(self, monkeypatch):
        _clear_ci_env(monkeypatch)
        monkeypatch.setenv("GITHUB_ACTIONS", "true")
        monkeypatch.setenv("CI", "true")
        assert detect_ci() == "github_actions"


class TestIsCI:
    def test_false_outside_ci(self, monkeypatch):
        _clear_ci_env(monkeypatch)
        assert is_ci() is False

    def test_true_in_ci(self, monkeypatch):
        _clear_ci_env(monkeypatch)
        monkeypatch.setenv("CI", "true")
        assert is_ci() is True


class TestIsGitHubActions:
    def test_false_outside_gha(self, monkeypatch):
        _clear_ci_env(monkeypatch)
        assert is_github_actions() is False

    def test_false_for_generic_ci(self, monkeypatch):
        _clear_ci_env(monkeypatch)
        monkeypatch.setenv("CI", "true")
        assert is_github_actions() is False

    def test_true_for_github_actions(self, monkeypatch):
        _clear_ci_env(monkeypatch)
        monkeypatch.setenv("GITHUB_ACTIONS", "true")
        assert is_github_actions() is True


# ── CICDReporter — exit codes ─────────────────────────────────────────────────


class TestExitCodes:
    def test_success_when_clean(self):
        r = CICDReporter()
        assert r.exit_code() == EXIT_SUCCESS

    def test_error_when_error_added(self):
        r = CICDReporter()
        r.add_error("something broke")
        assert r.exit_code() == EXIT_ERROR

    def test_warn_when_only_warnings(self):
        r = CICDReporter()
        r.add_warning("heads up")
        assert r.exit_code() == EXIT_WARN

    def test_error_takes_priority_over_warning(self):
        r = CICDReporter()
        r.add_warning("minor issue")
        r.add_error("fatal issue")
        assert r.exit_code() == EXIT_ERROR

    def test_exit_code_constants(self):
        assert EXIT_SUCCESS == 0
        assert EXIT_ERROR == 1
        assert EXIT_WARN == 2


# ── CICDReporter — JSON output ────────────────────────────────────────────────


class TestJSONOutput:
    def _emit_to_str(self, reporter, data=None, monkeypatch=None) -> dict:
        buf = StringIO()
        if monkeypatch:
            _clear_ci_env(monkeypatch)
        reporter.emit(data=data, file=buf)
        return json.loads(buf.getvalue())

    def test_status_success(self):
        r = CICDReporter()
        out = self._emit_to_str(r)
        assert out["status"] == "success"

    def test_status_error(self):
        r = CICDReporter()
        r.add_error("boom")
        out = self._emit_to_str(r)
        assert out["status"] == "error"

    def test_status_warning(self):
        r = CICDReporter()
        r.add_warning("careful")
        out = self._emit_to_str(r)
        assert out["status"] == "warning"

    def test_errors_list_in_payload(self):
        r = CICDReporter()
        r.add_error("err1")
        r.add_error("err2")
        out = self._emit_to_str(r)
        assert out["errors"] == ["err1", "err2"]

    def test_warnings_list_in_payload(self):
        r = CICDReporter()
        r.add_warning("w1")
        out = self._emit_to_str(r)
        assert out["warnings"] == ["w1"]

    def test_data_included_when_provided(self):
        r = CICDReporter()
        out = self._emit_to_str(r, data={"files": 5, "functions": 20})
        assert out["data"] == {"files": 5, "functions": 20}

    def test_data_absent_when_not_provided(self):
        r = CICDReporter()
        out = self._emit_to_str(r)
        assert "data" not in out

    def test_output_is_valid_json(self):
        r = CICDReporter()
        r.add_error("oops")
        r.add_warning("watch out")
        buf = StringIO()
        r.emit(data={"key": "value"}, file=buf)
        parsed = json.loads(buf.getvalue())
        assert isinstance(parsed, dict)


# ── CICDReporter — GitHub Actions annotations ─────────────────────────────────


class TestGitHubActionsAnnotations:
    def test_annotations_emitted_in_gha(self, monkeypatch):
        _clear_ci_env(monkeypatch)
        monkeypatch.setenv("GITHUB_ACTIONS", "true")

        r = CICDReporter()
        r.add_error("bad thing")
        r.add_warning("odd thing")

        buf = StringIO()
        r.emit(file=buf)
        output = buf.getvalue()

        assert "::error::bad thing" in output
        assert "::warning::odd thing" in output

    def test_no_annotations_outside_gha(self, monkeypatch):
        _clear_ci_env(monkeypatch)
        monkeypatch.setenv("CI", "true")

        r = CICDReporter()
        r.add_error("something")

        buf = StringIO()
        r.emit(file=buf)
        output = buf.getvalue()

        assert "::error::" not in output

    def test_multiple_errors_all_annotated(self, monkeypatch):
        _clear_ci_env(monkeypatch)
        monkeypatch.setenv("GITHUB_ACTIONS", "true")

        r = CICDReporter()
        r.add_error("e1")
        r.add_error("e2")

        buf = StringIO()
        r.emit(file=buf)
        output = buf.getvalue()

        assert "::error::e1" in output
        assert "::error::e2" in output


# ── CICDReporter — GitHub Actions step summary ────────────────────────────────


class TestGitHubStepSummary:
    def test_writes_summary_file(self, monkeypatch, tmp_path):
        _clear_ci_env(monkeypatch)
        monkeypatch.setenv("GITHUB_ACTIONS", "true")
        summary = tmp_path / "summary.md"
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))

        r = CICDReporter()
        r.emit(data={"files": 3}, file=StringIO())

        content = summary.read_text()
        assert "Navegador" in content
        assert "files" in content

    def test_summary_includes_errors(self, monkeypatch, tmp_path):
        _clear_ci_env(monkeypatch)
        monkeypatch.setenv("GITHUB_ACTIONS", "true")
        summary = tmp_path / "summary.md"
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))

        r = CICDReporter()
        r.add_error("ingest failed")
        r.emit(file=StringIO())

        content = summary.read_text()
        assert "ingest failed" in content

    def test_summary_includes_warnings(self, monkeypatch, tmp_path):
        _clear_ci_env(monkeypatch)
        monkeypatch.setenv("GITHUB_ACTIONS", "true")
        summary = tmp_path / "summary.md"
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))

        r = CICDReporter()
        r.add_warning("no files found")
        r.emit(file=StringIO())

        content = summary.read_text()
        assert "no files found" in content

    def test_no_summary_without_env_var(self, monkeypatch, tmp_path):
        _clear_ci_env(monkeypatch)
        monkeypatch.setenv("GITHUB_ACTIONS", "true")
        monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)

        r = CICDReporter()
        # Should not raise even when GITHUB_STEP_SUMMARY is absent
        r.emit(file=StringIO())

    def test_summary_appends_not_overwrites(self, monkeypatch, tmp_path):
        _clear_ci_env(monkeypatch)
        monkeypatch.setenv("GITHUB_ACTIONS", "true")
        summary = tmp_path / "summary.md"
        summary.write_text("# Previous content\n")
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))

        r = CICDReporter()
        r.emit(file=StringIO())

        content = summary.read_text()
        assert "# Previous content" in content
        assert "Navegador" in content

    def test_summary_handles_oserror_gracefully(self, monkeypatch, tmp_path):
        _clear_ci_env(monkeypatch)
        monkeypatch.setenv("GITHUB_ACTIONS", "true")
        # Point to a directory instead of a file — open() will raise OSError
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path))

        r = CICDReporter()
        # Should not raise
        r.emit(file=StringIO())

    def test_annotations_default_to_stdout(self, monkeypatch, capsys):
        _clear_ci_env(monkeypatch)
        monkeypatch.setenv("GITHUB_ACTIONS", "true")
        monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)

        r = CICDReporter()
        r.add_error("test error")
        r._emit_github_annotations()
        captured = capsys.readouterr()
        assert "::error::test error" in captured.out


# ── CLI: navegador ci ingest ──────────────────────────────────────────────────


class TestCIIngestCommand:
    def test_success_outputs_json(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            Path("src").mkdir()
            with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
                 patch("navegador.ingestion.RepoIngester") as MockRI:
                MockRI.return_value.ingest.return_value = {"files": 5, "functions": 20}
                result = runner.invoke(main, ["ci", "ingest", "src"])
                assert result.exit_code == 0
                payload = json.loads(result.output)
                assert payload["status"] == "success"
                assert payload["data"]["files"] == 5

    def test_warning_when_no_files_ingested(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            Path("src").mkdir()
            with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
                 patch("navegador.ingestion.RepoIngester") as MockRI:
                MockRI.return_value.ingest.return_value = {"files": 0, "functions": 0}
                result = runner.invoke(main, ["ci", "ingest", "src"])
                assert result.exit_code == 2
                payload = json.loads(result.output)
                assert payload["status"] == "warning"
                assert payload["warnings"]

    def test_error_on_ingest_exception(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            Path("src").mkdir()
            with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
                 patch("navegador.ingestion.RepoIngester") as MockRI:
                MockRI.return_value.ingest.side_effect = RuntimeError("DB unavailable")
                result = runner.invoke(main, ["ci", "ingest", "src"])
                assert result.exit_code == 1
                payload = json.loads(result.output)
                assert payload["status"] == "error"
                assert "DB unavailable" in payload["errors"][0]

    def test_output_is_valid_json(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            Path("src").mkdir()
            with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
                 patch("navegador.ingestion.RepoIngester") as MockRI:
                MockRI.return_value.ingest.return_value = {"files": 1}
                result = runner.invoke(main, ["ci", "ingest", "src"])
                parsed = json.loads(result.output)
                assert isinstance(parsed, dict)


# ── CLI: navegador ci stats ───────────────────────────────────────────────────


class TestCIStatsCommand:
    def _store_with_counts(self):
        store = MagicMock()

        def _query(cypher, *args, **kwargs):
            result = MagicMock()
            if "NODE" in cypher.upper() or "node" in cypher.lower():
                result.result_set = [["Function", 10], ["Class", 3]]
            else:
                result.result_set = [["CALLS", 25]]
            return result

        store.query.side_effect = _query
        return store

    def test_outputs_json_stats(self):
        runner = CliRunner()
        with patch("navegador.cli.commands._get_store", return_value=self._store_with_counts()):
            result = runner.invoke(main, ["ci", "stats"])
            assert result.exit_code == 0
            payload = json.loads(result.output)
            assert payload["status"] == "success"
            assert "data" in payload
            assert "total_nodes" in payload["data"]
            assert "total_edges" in payload["data"]

    def test_error_on_store_failure(self):
        runner = CliRunner()
        with patch("navegador.cli.commands._get_store", side_effect=RuntimeError("no db")):
            result = runner.invoke(main, ["ci", "stats"])
            assert result.exit_code == 1
            payload = json.loads(result.output)
            assert payload["status"] == "error"


# ── CLI: navegador ci check ───────────────────────────────────────────────────


class TestCICheckCommand:
    def test_success_when_schema_current(self):
        from navegador.graph.migrations import CURRENT_SCHEMA_VERSION

        store = MagicMock()
        store.query.return_value = MagicMock(result_set=[[CURRENT_SCHEMA_VERSION]])

        runner = CliRunner()
        with patch("navegador.cli.commands._get_store", return_value=store):
            result = runner.invoke(main, ["ci", "check"])
            assert result.exit_code == 0
            payload = json.loads(result.output)
            assert payload["status"] == "success"
            assert payload["data"]["schema_version"] == CURRENT_SCHEMA_VERSION

    def test_warning_when_migration_needed(self):
        store = MagicMock()
        # Return version 0 so migration is needed
        store.query.return_value = MagicMock(result_set=[[0]])

        runner = CliRunner()
        with patch("navegador.cli.commands._get_store", return_value=store):
            result = runner.invoke(main, ["ci", "check"])
            assert result.exit_code == 2
            payload = json.loads(result.output)
            assert payload["status"] == "warning"
            assert payload["warnings"]

    def test_error_on_store_failure(self):
        runner = CliRunner()
        with patch("navegador.cli.commands._get_store", side_effect=RuntimeError("no db")):
            result = runner.invoke(main, ["ci", "check"])
            assert result.exit_code == 1
            payload = json.loads(result.output)
            assert payload["status"] == "error"

    def test_payload_includes_version_info(self):
        from navegador.graph.migrations import CURRENT_SCHEMA_VERSION

        store = MagicMock()
        store.query.return_value = MagicMock(result_set=[[CURRENT_SCHEMA_VERSION]])

        runner = CliRunner()
        with patch("navegador.cli.commands._get_store", return_value=store):
            result = runner.invoke(main, ["ci", "check"])
            payload = json.loads(result.output)
            assert "schema_version" in payload["data"]
            assert "current_schema_version" in payload["data"]
