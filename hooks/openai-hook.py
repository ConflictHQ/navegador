#!/usr/bin/env python3
"""
Navegador hook for OpenAI Codex / ChatGPT with function calling.

OpenAI agents can call navegador directly as a function tool. Register the
functions below in your assistant's tool list, then call this script to
dispatch them.

Install:
  Use the function schemas in openai-tools.json alongside this dispatcher.
  Your agent calls: python3 navegador-openai.py <json_args>

Example tool call your agent would make:
  {"name": "nav_search", "arguments": {"query": "authentication", "all": true}}

This script reads JSON from stdin or argv[1] and dispatches to navegador CLI.
"""

import json
import os
import subprocess
import sys

NAV_DB = os.environ.get("NAVEGADOR_DB", ".navegador/graph.db")
NAV_CMD = os.environ.get("NAVEGADOR_CMD", "navegador")

DISPATCH = {
    "nav_ingest":   lambda a: [NAV_CMD, "ingest", a["path"], "--json"],
    "nav_context":  lambda a: [NAV_CMD, "context", a["file_path"], "--format", "json"],
    "nav_function": lambda a: [NAV_CMD, "function", a["name"],
                                "--file", a.get("file_path", ""), "--format", "json"],
    "nav_class":    lambda a: [NAV_CMD, "class", a["name"],
                                "--file", a.get("file_path", ""), "--format", "json"],
    "nav_explain":  lambda a: [NAV_CMD, "explain", a["name"], "--format", "json"],
    "nav_search":   lambda a: [NAV_CMD, "search", a["query"],
                                "--format", "json",
                                *( ["--all"] if a.get("all") else [] ),
                                *( ["--docs"] if a.get("by_docstring") else [] )],
    "nav_concept":  lambda a: [NAV_CMD, "concept", a["name"], "--format", "json"],
    "nav_domain":   lambda a: [NAV_CMD, "domain", a["name"], "--format", "json"],
    "nav_stats":    lambda a: [NAV_CMD, "stats", "--json"],
    "nav_query":    lambda a: [NAV_CMD, "query", a["cypher"]],
    "nav_decorated": lambda a: [NAV_CMD, "decorated", a["decorator"], "--format", "json"],
}


def main():
    raw = sys.argv[1] if len(sys.argv) > 1 else sys.stdin.read()
    try:
        call = json.loads(raw)
    except json.JSONDecodeError as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)

    name = call.get("name") or call.get("function", {}).get("name", "")
    arguments = call.get("arguments") or call.get("function", {}).get("arguments", {})
    if isinstance(arguments, str):
        arguments = json.loads(arguments)

    if name not in DISPATCH:
        print(json.dumps({"error": f"Unknown tool: {name}"}))
        sys.exit(1)

    cmd = DISPATCH[name](arguments)
    result = subprocess.run(
        ["--db", NAV_DB] and ([cmd[0]] + ["--db", NAV_DB] + cmd[1:]),
        capture_output=True, text=True,
    )
    sys.stdout.write(result.stdout or result.stderr)


if __name__ == "__main__":
    main()
