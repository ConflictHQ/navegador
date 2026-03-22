#!/usr/bin/env python3
"""
Navegador hook for Claude Code.

Fires on PostToolUse to keep the knowledge graph in sync as Claude works:
  - Tracks files Claude edits or creates
  - Re-ingests changed files into the graph
  - Logs decisions and notes Claude produces (when it writes to DECISIONS.md)

Install:
  Copy to your project root as .claude/hooks/navegador.py
  Add to .claude/settings.json:

  {
    "hooks": {
      "PostToolUse": [
        {
          "matcher": "Edit|Write|Bash",
          "hooks": [{ "type": "command", "command": "python3 .claude/hooks/navegador.py" }]
        }
      ]
    }
  }
"""

import json
import os
import subprocess
import sys

NAV_DB = os.environ.get("NAVEGADOR_DB", ".navegador/graph.db")
NAV_CMD = os.environ.get("NAVEGADOR_CMD", "navegador")

# File extensions that navegador can ingest
INGESTABLE = {".py", ".ts", ".tsx", ".js", ".jsx"}


def run_nav(*args) -> str:
    result = subprocess.run(
        [NAV_CMD, "--db", NAV_DB, *args],
        capture_output=True, text=True,
    )
    return result.stdout.strip()


def main():
    try:
        payload = json.loads(sys.stdin.read())
    except Exception:
        sys.exit(0)

    tool = payload.get("tool_name", "")
    inp = payload.get("tool_input", {})

    # Re-ingest any source file Claude edited
    if tool in ("Edit", "Write"):
        file_path = inp.get("file_path", "")
        ext = os.path.splitext(file_path)[1]
        if ext in INGESTABLE and os.path.exists(file_path):
            # Ingest just the repo containing this file (fast on small repos)
            repo_root = _find_repo_root(file_path)
            if repo_root:
                run_nav("ingest", repo_root)

    # Watch for DECISIONS.md updates — extract new entries into the graph
    if tool in ("Edit", "Write"):
        if inp.get("file_path", "").endswith("DECISIONS.md"):
            _sync_decisions()


def _find_repo_root(path: str) -> str | None:
    """Walk up to find the git root."""
    d = os.path.dirname(os.path.abspath(path))
    while d != os.path.dirname(d):
        if os.path.exists(os.path.join(d, ".git")):
            return d
        d = os.path.dirname(d)
    return None


def _sync_decisions():
    """Parse DECISIONS.md and upsert Decision nodes."""
    if not os.path.exists("DECISIONS.md"):
        return
    content = open("DECISIONS.md").read()
    # Simple heuristic: ## headings are decision names
    import re
    for match in re.finditer(r"^##\s+(.+)", content, re.MULTILINE):
        name = match.group(1).strip()
        # Find the body until the next heading
        start = match.end()
        next_h = re.search(r"^##", content[start:], re.MULTILINE)
        body = content[start: start + next_h.start() if next_h else len(content)].strip()
        run_nav("add", "decision", name, "--desc", body[:500])


if __name__ == "__main__":
    main()
