"""
Editor integrations — generate MCP config snippets for AI coding editors.

Supported editors:
  claude-code   — .claude/mcp.json
  cursor        — .cursor/mcp.json
  codex         — .codex/config.json
  windsurf      — .windsurf/mcp.json
"""

from __future__ import annotations

import json
from pathlib import Path

SUPPORTED_EDITORS = ["claude-code", "cursor", "codex", "windsurf"]

# Config file path relative to the project root for each editor
_CONFIG_PATHS: dict[str, str] = {
    "claude-code": ".claude/mcp.json",
    "cursor": ".cursor/mcp.json",
    "codex": ".codex/config.json",
    "windsurf": ".windsurf/mcp.json",
}


def _mcp_block(db: str) -> dict:
    """Return the shared mcpServers block used by all editors."""
    return {
        "mcpServers": {
            "navegador": {
                "command": "navegador",
                "args": ["mcp", "--db", db],
            }
        }
    }


class EditorIntegration:
    """Generate MCP config snippets for AI coding editors."""

    def __init__(self, db: str = ".navegador/graph.db") -> None:
        self.db = db

    # ── public API ────────────────────────────────────────────────────────────

    def config_for(self, editor: str) -> dict:
        """Return the config dict for *editor*.

        Raises ValueError for unsupported editors.
        """
        if editor not in SUPPORTED_EDITORS:
            raise ValueError(
                f"Unsupported editor {editor!r}. "
                f"Choose from: {', '.join(SUPPORTED_EDITORS)}"
            )
        return _mcp_block(self.db)

    def config_json(self, editor: str) -> str:
        """Return the JSON string for *editor*'s config file."""
        return json.dumps(self.config_for(editor), indent=2)

    def config_path(self, editor: str) -> str:
        """Return the relative config file path for *editor*."""
        if editor not in SUPPORTED_EDITORS:
            raise ValueError(
                f"Unsupported editor {editor!r}. "
                f"Choose from: {', '.join(SUPPORTED_EDITORS)}"
            )
        return _CONFIG_PATHS[editor]

    def write_config(self, editor: str, base_dir: str = ".") -> Path:
        """Write the config file to the expected path under *base_dir*.

        Creates parent directories as needed. Returns the written Path.
        """
        rel = self.config_path(editor)
        dest = Path(base_dir) / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(self.config_json(editor))
        return dest
