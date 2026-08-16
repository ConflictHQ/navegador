# Copyright CONFLICT LLC 2026 (weareconflict.com)
"""
Tests for the storage-facing CLI: doctor, scan, server, and storage migrate.

These commands exist to make storage misconfiguration visible, so the tests
care most about the diagnoses: that a project ingesting somewhere no reader
looks is reported, and that an unreachable server fails loudly rather than
silently producing empty answers.
"""

import json

import pytest
from click.testing import CliRunner

from navegador.cli.commands import main


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch, tmp_path):
    """Keep the developer's real machine configuration and server out of these."""
    monkeypatch.delenv("NAVEGADOR_REDIS_URL", raising=False)
    monkeypatch.delenv("NAVEGADOR_DB", raising=False)
    monkeypatch.setenv("NAVEGADOR_CONFIG", str(tmp_path / "absent.toml"))
    monkeypatch.setenv("NAVEGADOR_HOME", str(tmp_path / "server-home"))


def make_project(root, backend="sqlite", db_bytes=0, redis_url="redis://127.0.0.1:1"):
    nav = root / ".navegador"
    nav.mkdir(parents=True, exist_ok=True)
    lines = ["[storage]", f'backend = "{backend}"']
    if backend == "redis":
        lines.append(f'redis_url = "{redis_url}"')
    (nav / "config.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    if db_bytes:
        (nav / "graph.db").write_bytes(b"\0" * db_bytes)
    return root


# ── doctor ─────────────────────────────────────────────────────────────────


class TestDoctor:
    def test_reports_the_deciding_config_file(self, tmp_path):
        make_project(tmp_path)
        result = CliRunner().invoke(main, ["doctor", "--target", str(tmp_path)])
        assert result.exit_code == 0
        assert "project config" in result.output

    def test_clean_project_has_no_problems(self, tmp_path):
        make_project(tmp_path)
        result = CliRunner().invoke(main, ["doctor", "--target", str(tmp_path)])
        assert "No problems found" in result.output

    def test_flags_a_stranded_project(self, tmp_path):
        """Declares Redis, holds local data — the #169 signature."""
        make_project(tmp_path, backend="redis", db_bytes=50_000)
        result = CliRunner().invoke(main, ["doctor", "--target", str(tmp_path)])
        assert result.exit_code != 0
        assert "not visible" in result.output

    def test_flags_an_unreachable_server(self, tmp_path):
        make_project(tmp_path, backend="redis")
        result = CliRunner().invoke(main, ["doctor", "--target", str(tmp_path)])
        assert result.exit_code != 0
        assert "not reachable" in result.output

    def test_json_output_is_machine_readable(self, tmp_path):
        make_project(tmp_path, backend="redis", db_bytes=50_000)
        result = CliRunner().invoke(main, ["doctor", "--target", str(tmp_path), "--json"])
        payload = json.loads(result.output)
        assert payload["stranded"] is True
        assert payload["problems"]

    def test_json_exit_code_signals_problems(self, tmp_path):
        make_project(tmp_path)
        ok = CliRunner().invoke(main, ["doctor", "--target", str(tmp_path), "--json"])
        assert ok.exit_code == 0


# ── scan ───────────────────────────────────────────────────────────────────


class TestScan:
    def test_reports_nothing_for_an_empty_tree(self, tmp_path):
        result = CliRunner().invoke(main, ["scan", str(tmp_path)])
        assert result.exit_code == 0
        assert "No navegador projects" in result.output

    def test_lists_projects(self, tmp_path):
        make_project(tmp_path / "alpha")
        make_project(tmp_path / "beta")
        result = CliRunner().invoke(main, ["scan", str(tmp_path)])
        assert result.exit_code == 0
        assert "alpha" in result.output
        assert "beta" in result.output

    def test_highlights_stranded_projects(self, tmp_path):
        make_project(tmp_path / "stray", backend="redis", db_bytes=50_000)
        result = CliRunner().invoke(main, ["scan", str(tmp_path)])
        assert "stranded" in result.output

    def test_json_output(self, tmp_path):
        make_project(tmp_path / "alpha", backend="redis", db_bytes=50_000)
        result = CliRunner().invoke(main, ["scan", str(tmp_path), "--json"])
        payload = json.loads(result.output)
        assert payload["central_server_recommended"] is True
        assert payload["projects"][0]["stranded"] is True

    def test_recommends_a_server_with_reasons(self, tmp_path):
        for i in range(6):
            make_project(tmp_path / f"p{i}")
        result = CliRunner().invoke(main, ["scan", str(tmp_path)])
        assert "navegador server install" in result.output


# ── server ─────────────────────────────────────────────────────────────────


class TestServerStatus:
    def test_reports_no_managed_server(self, tmp_path):
        # Pinned to a dead port: with no manifest, status falls back to the
        # default URL, and whatever happens to be on :6379 must not decide this.
        result = CliRunner().invoke(main, ["server", "status", "--url", "redis://127.0.0.1:1"])
        assert result.exit_code != 0
        assert "No managed server installed" in result.output

    def test_unreachable_server_exits_nonzero(self):
        result = CliRunner().invoke(main, ["server", "status", "--url", "redis://127.0.0.1:1"])
        assert result.exit_code != 0
        assert "Not reachable" in result.output

    def test_json_status_is_machine_readable(self):
        result = CliRunner().invoke(
            main, ["server", "status", "--url", "redis://127.0.0.1:1", "--json"]
        )
        payload = json.loads(result.output)
        assert payload["reachable"] is False


# ── storage migrate ────────────────────────────────────────────────────────


class TestStorageMigrate:
    def test_requires_a_destination(self, tmp_path):
        make_project(tmp_path)
        result = CliRunner().invoke(main, ["storage", "migrate", "--target", str(tmp_path)])
        assert result.exit_code != 0
        assert "No destination server" in result.output

    def test_dry_run_writes_nothing_and_reports_a_plan(self, tmp_path):
        from navegador.graph.store import GraphStore

        project = make_project(tmp_path / "repo", backend="redis")
        store = GraphStore.sqlite(str(project / ".navegador" / "graph.db"))
        store.query("CREATE (:Function {name: 'seeded', file_path: 'a.py'})")
        store.close()

        result = CliRunner().invoke(
            main,
            [
                "storage",
                "migrate",
                "--target",
                str(project),
                "--to",
                "redis://127.0.0.1:1",
                "--dry-run",
            ],
        )
        assert result.exit_code == 0
        assert "planned" in result.output

    def test_missing_local_graph_is_skipped_not_failed(self, tmp_path):
        project = make_project(tmp_path / "repo", backend="redis")
        result = CliRunner().invoke(
            main,
            ["storage", "migrate", "--target", str(project), "--to", "redis://127.0.0.1:1"],
        )
        assert result.exit_code == 0
        assert "skipped" in result.output

    def test_unreachable_destination_fails_loudly(self, tmp_path):
        from navegador.graph.store import GraphStore

        project = make_project(tmp_path / "repo", backend="redis")
        store = GraphStore.sqlite(str(project / ".navegador" / "graph.db"))
        store.query("CREATE (:Function {name: 'seeded', file_path: 'a.py'})")
        store.close()

        result = CliRunner().invoke(
            main,
            ["storage", "migrate", "--target", str(project), "--to", "redis://127.0.0.1:1"],
        )
        assert result.exit_code != 0
        assert "failed" in result.output

    def test_overwrite_reaches_the_bulk_path(self, tmp_path):
        """
        --overwrite must be threaded into every project, not just single ones.

        It was silently dropped from the --all branch, so a bulk migration kept
        refusing the exact clashes the flag was passed to resolve.
        """
        from unittest.mock import patch

        from navegador.graph.store import GraphStore

        project = make_project(tmp_path / "repo", backend="redis")
        store = GraphStore.sqlite(str(project / ".navegador" / "graph.db"))
        store.query("CREATE (:Function {name: 'seeded', file_path: 'a.py'})")
        store.close()

        with patch(
            "navegador.cli.commands._migrate_project", return_value={"status": "ok"}
        ) as migrate:
            CliRunner().invoke(
                main,
                [
                    "storage",
                    "migrate",
                    "--all",
                    "--root",
                    str(tmp_path),
                    "--to",
                    "redis://127.0.0.1:1",
                    "--overwrite",
                ],
            )
        assert migrate.call_args.kwargs["overwrite"] is True

    def test_json_output_is_parseable_despite_progress(self, tmp_path):
        """
        Per-graph progress must not land on stdout alongside --json.

        It did, so piping the result into a parser failed on the narration —
        the output is meant to be machine-readable, and a caller should not
        have to strip human-facing lines out of it first.
        """
        from navegador.graph.store import GraphStore

        project = make_project(tmp_path / "repo", backend="redis")
        store = GraphStore.sqlite(str(project / ".navegador" / "graph.db"))
        store.query("CREATE (:Function {name: 'seeded', file_path: 'a.py'})")
        store.close()

        result = CliRunner().invoke(
            main,
            [
                "storage",
                "migrate",
                "--target",
                str(project),
                "--to",
                "redis://127.0.0.1:1",
                "--dry-run",
                "--json",
            ],
        )
        payload = json.loads(result.stdout)
        assert payload["results"][0]["status"] == "planned"

    def test_all_reports_when_nothing_to_do(self, tmp_path):
        make_project(tmp_path / "empty", backend="redis")  # config only, no graph file
        result = CliRunner().invoke(
            main,
            ["storage", "migrate", "--all", "--root", str(tmp_path), "--to", "redis://127.0.0.1:1"],
        )
        assert result.exit_code == 0
        assert "No projects with a local graph" in result.output
