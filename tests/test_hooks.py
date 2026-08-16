# Copyright CONFLICT LLC 2026 (weareconflict.com)
"""
Tests for the shipped agent hooks.

Every hook invoked the CLI as ``navegador --db <path> <subcommand>``. ``--db``
is a per-command option, so click rejected the whole invocation — and because
the hooks read only stdout, the failure surfaced to the agent as an empty
string, indistinguishable from "the graph has no context for this file".

These tests run the real CLI through each hook's own command builder, so a
regression in argument order fails here rather than silently in production.
"""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parent.parent / "hooks"
NAV_CMD = str(Path(sys.executable).parent / "navegador")


def load_hook(name: str, monkeypatch, nav_db: str = ""):
    """Import a hook module by path, with its environment set first."""
    monkeypatch.setenv("NAVEGADOR_CMD", NAV_CMD)
    if nav_db:
        monkeypatch.setenv("NAVEGADOR_DB", nav_db)
    else:
        monkeypatch.delenv("NAVEGADOR_DB", raising=False)

    spec = importlib.util.spec_from_file_location(f"hook_{name}", HOOKS_DIR / f"{name}-hook.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestCliInvocationForm:
    """The CLI contract the hooks depend on."""

    def test_db_after_subcommand_is_accepted(self, tmp_path):
        result = subprocess.run(
            [NAV_CMD, "stats", "--db", str(tmp_path / "graph.db"), "--json"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr

    def test_db_before_subcommand_is_rejected(self, tmp_path):
        """The exact form every hook used to emit."""
        result = subprocess.run(
            [NAV_CMD, "--db", str(tmp_path / "graph.db"), "stats"],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert "No such option" in result.stderr


class TestClaudeHook:
    def test_run_nav_returns_output(self, monkeypatch, tmp_path):
        hook = load_hook("claude", monkeypatch)
        monkeypatch.chdir(tmp_path)
        assert hook.run_nav("stats", "--db", str(tmp_path / "g.db"), "--json").strip()

    def test_no_db_flag_when_unconfigured(self, monkeypatch):
        """An unconditional --db would override the project's own [storage]."""
        hook = load_hook("claude", monkeypatch)
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(hook.subprocess, "run", fake_run)
        hook.run_nav("stats")
        assert "--db" not in captured["cmd"]

    def test_db_flag_follows_the_subcommand_when_configured(self, monkeypatch, tmp_path):
        hook = load_hook("claude", monkeypatch, nav_db=str(tmp_path / "g.db"))
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(hook.subprocess, "run", fake_run)
        hook.run_nav("stats")
        cmd = captured["cmd"]
        assert cmd.index("--db") > cmd.index("stats")

    def test_failure_is_reported_not_swallowed(self, monkeypatch, capsys):
        hook = load_hook("claude", monkeypatch)
        assert hook.run_nav("definitely-not-a-subcommand") == ""
        assert "navegador:" in capsys.readouterr().err


class TestGeminiHook:
    def test_no_db_flag_when_unconfigured(self, monkeypatch):
        hook = load_hook("gemini", monkeypatch)
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(hook.subprocess, "run", fake_run)
        hook.run_nav("stats")
        assert "--db" not in captured["cmd"]

    def test_db_flag_follows_the_subcommand(self, monkeypatch, tmp_path):
        hook = load_hook("gemini", monkeypatch, nav_db=str(tmp_path / "g.db"))
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(hook.subprocess, "run", fake_run)
        hook.run_nav("stats")
        cmd = captured["cmd"]
        assert cmd.index("--db") > cmd.index("stats")


class TestBootstrapScript:
    @pytest.fixture
    def script(self):
        return (HOOKS_DIR / "bootstrap.sh").read_text(encoding="utf-8")

    def test_is_valid_bash(self):
        result = subprocess.run(
            ["bash", "-n", str(HOOKS_DIR / "bootstrap.sh")], capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr

    def test_never_places_db_before_a_subcommand(self, script):
        assert 'navegador --db ' not in script

    def test_db_is_optional(self, script):
        assert 'NAV_DB="${NAVEGADOR_DB:-}"' in script
