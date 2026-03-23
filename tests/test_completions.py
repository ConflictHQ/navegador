"""Tests for shell completions — module API and CLI command."""

from pathlib import Path

import pytest
from click.testing import CliRunner

from navegador.cli.commands import main
from navegador.completions import (
    SUPPORTED_SHELLS,
    get_eval_line,
    get_rc_path,
    install_completion,
)

# ── get_eval_line ─────────────────────────────────────────────────────────────


class TestGetEvalLine:
    def test_bash_contains_bash_source(self):
        line = get_eval_line("bash")
        assert "bash_source" in line
        assert "navegador" in line

    def test_zsh_contains_zsh_source(self):
        line = get_eval_line("zsh")
        assert "zsh_source" in line
        assert "navegador" in line

    def test_fish_contains_fish_source(self):
        line = get_eval_line("fish")
        assert "fish_source" in line
        assert "navegador" in line

    def test_bash_uses_eval(self):
        line = get_eval_line("bash")
        assert line.startswith("eval ")

    def test_zsh_uses_eval(self):
        line = get_eval_line("zsh")
        assert line.startswith("eval ")

    def test_fish_uses_pipe_source(self):
        line = get_eval_line("fish")
        assert "| source" in line

    def test_invalid_shell_raises_value_error(self):
        with pytest.raises(ValueError, match="Unsupported shell"):
            get_eval_line("powershell")

    def test_all_supported_shells_return_string(self):
        for shell in SUPPORTED_SHELLS:
            assert isinstance(get_eval_line(shell), str)


# ── get_rc_path ───────────────────────────────────────────────────────────────


class TestGetRcPath:
    def test_bash_returns_bashrc(self):
        assert get_rc_path("bash") == "~/.bashrc"

    def test_zsh_returns_zshrc(self):
        assert get_rc_path("zsh") == "~/.zshrc"

    def test_fish_returns_config_fish(self):
        assert "config.fish" in get_rc_path("fish")

    def test_invalid_shell_raises_value_error(self):
        with pytest.raises(ValueError, match="Unsupported shell"):
            get_rc_path("ksh")

    def test_all_supported_shells_return_string(self):
        for shell in SUPPORTED_SHELLS:
            assert isinstance(get_rc_path(shell), str)


# ── install_completion ────────────────────────────────────────────────────────


class TestInstallCompletion:
    def test_creates_file_with_eval_line(self, tmp_path):
        rc = tmp_path / ".bashrc"
        result = install_completion("bash", rc_path=str(rc))
        assert result.exists()
        content = rc.read_text()
        assert "bash_source" in content
        assert "navegador" in content

    def test_creates_parent_dirs_for_fish(self, tmp_path):
        rc = tmp_path / ".config" / "fish" / "config.fish"
        result = install_completion("fish", rc_path=str(rc))
        assert result.exists()
        assert rc.parent.is_dir()

    def test_returns_path_object(self, tmp_path):
        rc = tmp_path / ".zshrc"
        result = install_completion("zsh", rc_path=str(rc))
        assert isinstance(result, Path)

    def test_idempotent_does_not_duplicate(self, tmp_path):
        rc = tmp_path / ".bashrc"
        install_completion("bash", rc_path=str(rc))
        install_completion("bash", rc_path=str(rc))
        content = rc.read_text()
        # The eval line should appear exactly once
        line = "eval \"$(_NAVEGADOR_COMPLETE=bash_source navegador)\""
        assert content.count(line) == 1

    def test_appends_to_existing_file(self, tmp_path):
        rc = tmp_path / ".zshrc"
        rc.write_text("# existing content\nexport FOO=bar\n")
        install_completion("zsh", rc_path=str(rc))
        content = rc.read_text()
        assert "existing content" in content
        assert "zsh_source" in content

    def test_invalid_shell_raises_value_error(self, tmp_path):
        with pytest.raises(ValueError, match="Unsupported shell"):
            install_completion("tcsh", rc_path=str(tmp_path / ".tcshrc"))

    def test_zsh_eval_line_in_file(self, tmp_path):
        rc = tmp_path / ".zshrc"
        install_completion("zsh", rc_path=str(rc))
        assert "zsh_source" in rc.read_text()

    def test_fish_source_line_in_file(self, tmp_path):
        rc = tmp_path / "config.fish"
        install_completion("fish", rc_path=str(rc))
        assert "fish_source" in rc.read_text()
        assert "| source" in rc.read_text()


# ── CLI: navegador completions <shell> ────────────────────────────────────────


class TestCompletionsCommand:
    # Basic output

    def test_bash_outputs_eval_line(self):
        runner = CliRunner()
        result = runner.invoke(main, ["completions", "bash"])
        assert result.exit_code == 0
        assert "bash_source" in result.output

    def test_zsh_outputs_eval_line(self):
        runner = CliRunner()
        result = runner.invoke(main, ["completions", "zsh"])
        assert result.exit_code == 0
        assert "zsh_source" in result.output

    def test_fish_outputs_source_line(self):
        runner = CliRunner()
        result = runner.invoke(main, ["completions", "fish"])
        assert result.exit_code == 0
        assert "fish_source" in result.output

    def test_output_mentions_rc_file(self):
        runner = CliRunner()
        result = runner.invoke(main, ["completions", "bash"])
        assert result.exit_code == 0
        assert ".bashrc" in result.output

    def test_output_mentions_install_hint(self):
        runner = CliRunner()
        result = runner.invoke(main, ["completions", "zsh"])
        assert result.exit_code == 0
        assert "--install" in result.output

    # Invalid shell

    def test_invalid_shell_exits_nonzero(self):
        runner = CliRunner()
        result = runner.invoke(main, ["completions", "powershell"])
        assert result.exit_code != 0

    def test_invalid_shell_shows_error(self):
        runner = CliRunner()
        result = runner.invoke(main, ["completions", "powershell"])
        assert result.exit_code != 0
        # Click's Choice type reports the invalid value
        assert "powershell" in result.output or "invalid" in result.output.lower()

    # --install flag

    def test_install_bash_creates_file(self):
        runner = CliRunner()
        with runner.isolated_filesystem() as tmp:
            rc = str(Path(tmp) / ".bashrc")
            result = runner.invoke(main, ["completions", "bash", "--install", "--rc-path", rc])
            assert result.exit_code == 0
            assert Path(rc).exists()
            assert "bash_source" in Path(rc).read_text()

    def test_install_zsh_creates_file(self):
        runner = CliRunner()
        with runner.isolated_filesystem() as tmp:
            rc = str(Path(tmp) / ".zshrc")
            result = runner.invoke(main, ["completions", "zsh", "--install", "--rc-path", rc])
            assert result.exit_code == 0
            assert Path(rc).exists()

    def test_install_fish_creates_file(self):
        runner = CliRunner()
        with runner.isolated_filesystem() as tmp:
            rc = str(Path(tmp) / "config.fish")
            result = runner.invoke(main, ["completions", "fish", "--install", "--rc-path", rc])
            assert result.exit_code == 0
            assert Path(rc).exists()

    def test_install_shows_success_message(self):
        runner = CliRunner()
        with runner.isolated_filesystem() as tmp:
            rc = str(Path(tmp) / ".bashrc")
            result = runner.invoke(main, ["completions", "bash", "--install", "--rc-path", rc])
            assert result.exit_code == 0
            assert "installed" in result.output.lower() or "Completion" in result.output

    def test_install_shows_restart_hint(self):
        runner = CliRunner()
        with runner.isolated_filesystem() as tmp:
            rc = str(Path(tmp) / ".zshrc")
            result = runner.invoke(main, ["completions", "zsh", "--install", "--rc-path", rc])
            assert result.exit_code == 0
            assert "source" in result.output or "restart" in result.output.lower()

    def test_install_idempotent_via_cli(self):
        runner = CliRunner()
        with runner.isolated_filesystem() as tmp:
            rc = str(Path(tmp) / ".zshrc")
            runner.invoke(main, ["completions", "zsh", "--install", "--rc-path", rc])
            runner.invoke(main, ["completions", "zsh", "--install", "--rc-path", rc])
            content = Path(rc).read_text()
            line = 'eval "$(_NAVEGADOR_COMPLETE=zsh_source navegador)"'
            assert content.count(line) == 1
