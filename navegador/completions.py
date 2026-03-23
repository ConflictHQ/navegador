"""
Shell completions — generate tab-completion scripts for bash, zsh, and fish.

Usage:
  Print the eval line to add to your shell rc file:
    navegador completions bash
    navegador completions zsh
    navegador completions fish

  Install automatically:
    navegador completions bash --install
    navegador completions zsh --install
    navegador completions fish --install
"""

from __future__ import annotations

from pathlib import Path

SUPPORTED_SHELLS = ["bash", "zsh", "fish"]

# The env-var source command for each shell
_SOURCE_COMMANDS: dict[str, str] = {
    "bash": "_NAVEGADOR_COMPLETE=bash_source navegador",
    "zsh": "_NAVEGADOR_COMPLETE=zsh_source navegador",
    "fish": "_NAVEGADOR_COMPLETE=fish_source navegador",
}

# The eval/source wrapper for each shell
_EVAL_LINES: dict[str, str] = {
    "bash": 'eval "$(_NAVEGADOR_COMPLETE=bash_source navegador)"',
    "zsh": 'eval "$(_NAVEGADOR_COMPLETE=zsh_source navegador)"',
    "fish": "_NAVEGADOR_COMPLETE=fish_source navegador | source",
}

# Default rc file paths for each shell (relative to $HOME)
_RC_PATHS: dict[str, str] = {
    "bash": "~/.bashrc",
    "zsh": "~/.zshrc",
    "fish": "~/.config/fish/config.fish",
}


def get_eval_line(shell: str) -> str:
    """Return the eval/source line to add to the shell rc file.

    Raises ValueError for unsupported shells.
    """
    if shell not in SUPPORTED_SHELLS:
        raise ValueError(
            f"Unsupported shell: {shell!r}. Choose from: {', '.join(SUPPORTED_SHELLS)}"
        )
    return _EVAL_LINES[shell]


def get_rc_path(shell: str) -> str:
    """Return the default rc file path (unexpanded) for *shell*.

    Raises ValueError for unsupported shells.
    """
    if shell not in SUPPORTED_SHELLS:
        raise ValueError(
            f"Unsupported shell: {shell!r}. Choose from: {', '.join(SUPPORTED_SHELLS)}"
        )
    return _RC_PATHS[shell]


def get_install_instruction(shell: str) -> str:
    """Return a human-readable instruction for adding completions to *shell*."""
    if shell not in SUPPORTED_SHELLS:
        raise ValueError(
            f"Unsupported shell: {shell!r}. Choose from: {', '.join(SUPPORTED_SHELLS)}"
        )
    rc = _RC_PATHS[shell]
    line = _EVAL_LINES[shell]
    return f"Add the following line to {rc}:\n\n  {line}"


def install_completion(shell: str, rc_path: str | None = None) -> Path:
    """Append the completion eval line to the shell rc file.

    Args:
        shell:   One of 'bash', 'zsh', 'fish'.
        rc_path: Override the default rc file path. Tilde-expansion is applied.

    Returns:
        The Path of the file that was written to.

    Raises:
        ValueError: For unsupported shells.
    """
    if shell not in SUPPORTED_SHELLS:
        raise ValueError(
            f"Unsupported shell: {shell!r}. Choose from: {', '.join(SUPPORTED_SHELLS)}"
        )

    target = Path(rc_path or _RC_PATHS[shell]).expanduser()
    line = _EVAL_LINES[shell]

    # Idempotent: don't append if the line is already present
    if target.exists():
        existing = target.read_text()
        if line in existing:
            return target

    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a") as fh:
        fh.write(f"\n# navegador shell completion\n{line}\n")

    return target
