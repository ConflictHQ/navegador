"""Tests for editor integration — EditorIntegration class and CLI command."""

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from navegador.cli.commands import main
from navegador.editor import SUPPORTED_EDITORS, EditorIntegration

# ── EditorIntegration unit tests ──────────────────────────────────────────────


class TestEditorIntegration:
    def setup_method(self):
        self.integration = EditorIntegration(db=".navegador/graph.db")

    # config_for

    def test_config_for_claude_code(self):
        cfg = self.integration.config_for("claude-code")
        assert cfg["mcpServers"]["navegador"]["command"] == "navegador"
        assert cfg["mcpServers"]["navegador"]["args"] == ["mcp", "--db", ".navegador/graph.db"]

    def test_config_for_cursor(self):
        cfg = self.integration.config_for("cursor")
        assert cfg["mcpServers"]["navegador"]["command"] == "navegador"
        assert cfg["mcpServers"]["navegador"]["args"] == ["mcp", "--db", ".navegador/graph.db"]

    def test_config_for_codex(self):
        cfg = self.integration.config_for("codex")
        assert cfg["mcpServers"]["navegador"]["command"] == "navegador"
        assert cfg["mcpServers"]["navegador"]["args"] == ["mcp", "--db", ".navegador/graph.db"]

    def test_config_for_windsurf(self):
        cfg = self.integration.config_for("windsurf")
        assert cfg["mcpServers"]["navegador"]["command"] == "navegador"
        assert cfg["mcpServers"]["navegador"]["args"] == ["mcp", "--db", ".navegador/graph.db"]

    def test_config_for_invalid_editor_raises(self):
        with pytest.raises(ValueError, match="Unsupported editor"):
            self.integration.config_for("vscode")

    # custom db path

    def test_custom_db_path_reflected_in_config(self):
        integration = EditorIntegration(db="/custom/path/graph.db")
        cfg = integration.config_for("cursor")
        assert cfg["mcpServers"]["navegador"]["args"][2] == "/custom/path/graph.db"

    # config_json

    def test_config_json_is_valid_json(self):
        raw = self.integration.config_json("claude-code")
        parsed = json.loads(raw)
        assert "mcpServers" in parsed

    def test_config_json_is_pretty_printed(self):
        raw = self.integration.config_json("cursor")
        assert "\n" in raw  # indented

    # config_path

    def test_config_path_claude_code(self):
        assert self.integration.config_path("claude-code") == ".claude/mcp.json"

    def test_config_path_cursor(self):
        assert self.integration.config_path("cursor") == ".cursor/mcp.json"

    def test_config_path_codex(self):
        assert self.integration.config_path("codex") == ".codex/config.json"

    def test_config_path_windsurf(self):
        assert self.integration.config_path("windsurf") == ".windsurf/mcp.json"

    def test_config_path_invalid_editor_raises(self):
        with pytest.raises(ValueError, match="Unsupported editor"):
            self.integration.config_path("sublime")

    # write_config

    def test_write_config_creates_file(self, tmp_path):
        written = self.integration.write_config("claude-code", base_dir=str(tmp_path))
        assert written.exists()
        parsed = json.loads(written.read_text())
        assert "mcpServers" in parsed

    def test_write_config_creates_parent_dirs(self, tmp_path):
        written = self.integration.write_config("windsurf", base_dir=str(tmp_path))
        assert (tmp_path / ".windsurf").is_dir()
        assert written.name == "mcp.json"

    def test_write_config_returns_path_object(self, tmp_path):
        result = self.integration.write_config("cursor", base_dir=str(tmp_path))
        assert isinstance(result, Path)

    def test_write_config_content_matches_config_json(self, tmp_path):
        written = self.integration.write_config("codex", base_dir=str(tmp_path))
        assert written.read_text() == self.integration.config_json("codex")

    # all editors covered

    def test_all_editors_supported(self):
        for ed in SUPPORTED_EDITORS:
            cfg = self.integration.config_for(ed)
            assert "mcpServers" in cfg


# ── CLI tests ─────────────────────────────────────────────────────────────────


class TestEditorSetupCommand:
    # Basic output

    def test_claude_code_outputs_json(self):
        runner = CliRunner()
        result = runner.invoke(main, ["editor", "setup", "claude-code"])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert "mcpServers" in parsed
        assert parsed["mcpServers"]["navegador"]["command"] == "navegador"

    def test_cursor_outputs_json(self):
        runner = CliRunner()
        result = runner.invoke(main, ["editor", "setup", "cursor"])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert "mcpServers" in parsed

    def test_codex_outputs_json(self):
        runner = CliRunner()
        result = runner.invoke(main, ["editor", "setup", "codex"])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert "mcpServers" in parsed

    def test_windsurf_outputs_json(self):
        runner = CliRunner()
        result = runner.invoke(main, ["editor", "setup", "windsurf"])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert "mcpServers" in parsed

    # --db option

    def test_custom_db_reflected_in_output(self):
        runner = CliRunner()
        result = runner.invoke(main, ["editor", "setup", "cursor", "--db", "/custom/graph.db"])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["mcpServers"]["navegador"]["args"][2] == "/custom/graph.db"

    # 'all' generates for all editors

    def test_all_generates_for_all_editors(self):
        runner = CliRunner()
        result = runner.invoke(main, ["editor", "setup", "all"])
        assert result.exit_code == 0
        # Each editor name should appear in the output header
        for ed in SUPPORTED_EDITORS:
            assert ed in result.output

    def test_all_output_contains_multiple_json_blocks(self):
        runner = CliRunner()
        result = runner.invoke(main, ["editor", "setup", "all"])
        assert result.exit_code == 0
        # Count occurrences of "mcpServers" — one per editor
        assert result.output.count("mcpServers") == len(SUPPORTED_EDITORS)

    # Invalid editor name

    def test_invalid_editor_exits_nonzero(self):
        runner = CliRunner()
        result = runner.invoke(main, ["editor", "setup", "vscode"])
        assert result.exit_code != 0

    def test_invalid_editor_shows_error(self):
        runner = CliRunner()
        result = runner.invoke(main, ["editor", "setup", "vim"])
        assert "vim" in result.output or "vim" in (result.exception or "")

    # --write flag

    def test_write_flag_creates_file(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(main, ["editor", "setup", "claude-code", "--write"])
            assert result.exit_code == 0
            written = Path(".claude/mcp.json")
            assert written.exists()
            parsed = json.loads(written.read_text())
            assert "mcpServers" in parsed

    def test_write_flag_cursor_creates_correct_file(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(main, ["editor", "setup", "cursor", "--write"])
            assert result.exit_code == 0
            assert Path(".cursor/mcp.json").exists()

    def test_write_flag_all_creates_all_files(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(main, ["editor", "setup", "all", "--write"])
            assert result.exit_code == 0
            assert Path(".claude/mcp.json").exists()
            assert Path(".cursor/mcp.json").exists()
            assert Path(".codex/config.json").exists()
            assert Path(".windsurf/mcp.json").exists()

    def test_write_flag_shows_written_path(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(main, ["editor", "setup", "windsurf", "--write"])
            assert result.exit_code == 0
            assert "Written" in result.output or ".windsurf" in result.output
