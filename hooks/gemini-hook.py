#!/usr/bin/env python3
"""
Navegador hook for Gemini CLI (gemini-cli).

Gemini CLI supports tool hooks via GEMINI.md + shell scripts executed
after tool calls. This script is designed to be invoked as a post-tool hook.

Install:
  Copy to your project root as .gemini/hooks/navegador.py
  Reference in GEMINI.md:

  ## Hooks
  After editing or creating any source file, run:
    python3 .gemini/hooks/navegador.py <tool_name> <file_path>

  This keeps the navegador knowledge graph in sync with your changes.

Usage (called by gemini-cli hook runner):
  python3 navegador.py edit src/auth.py
  python3 navegador.py write src/new_module.py
"""

import os
import subprocess
import sys

# Only pass --db when one is explicitly configured. Passing it unconditionally
# would pin every lookup to a per-project file and override the project's own
# [storage] configuration — defeating a shared FalkorDB server entirely.
NAV_DB = os.environ.get("NAVEGADOR_DB", "")
NAV_CMD = os.environ.get("NAVEGADOR_CMD", "navegador")
INGESTABLE = {".py", ".ts", ".tsx", ".js", ".jsx"}


def run_nav(*args):
    # --db must follow the subcommand; before it, click rejects the invocation.
    cmd = [NAV_CMD, *args]
    if NAV_DB:
        cmd += ["--db", NAV_DB]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"navegador: {result.stderr.strip()}", file=sys.stderr)


def main():
    args = sys.argv[1:]
    if len(args) < 2:
        sys.exit(0)

    _tool, file_path = args[0], args[1]
    ext = os.path.splitext(file_path)[1]

    if ext in INGESTABLE and os.path.exists(file_path):
        repo_root = _find_repo_root(file_path)
        if repo_root:
            run_nav("ingest", repo_root)


def _find_repo_root(path: str) -> str | None:
    d = os.path.dirname(os.path.abspath(path))
    while d != os.path.dirname(d):
        if os.path.exists(os.path.join(d, ".git")):
            return d
        d = os.path.dirname(d)
    return None


if __name__ == "__main__":
    main()
