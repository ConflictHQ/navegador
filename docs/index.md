# Navegador

**AST + knowledge graph context engine for AI coding agents.**

Navegador parses your codebase into a property graph — functions, classes, files, imports, call relationships — and makes that structure queryable by AI coding agents via MCP or a Python API.

> *navegador* — Spanish for *navigator / sailor*. It helps agents navigate your code.

---

## Why

AI coding agents (Claude, Cursor, Copilot) load context by reading raw files. They don't know what calls what, what depends on what, or how to find the relevant 5 functions out of 500. Navegador gives agents a structured map.

```
agent: "find everything that depends on auth1/sessions.py"

→ MATCH (f:File {path: "auth1/sessions.py"})<-[:IMPORTS|CALLS*1..3]-(n)
→ returns: 4 files, 12 functions, full source + docstrings
```

---

## Features

- **Multi-language AST parsing** — Python and TypeScript/JavaScript via tree-sitter
- **Property graph storage** — FalkorDB-lite (SQLite, zero infra) or Redis FalkorDB for production
- **MCP server** — native integration with Claude, Cursor, and any MCP-compatible agent
- **Context bundles** — structured JSON or markdown export of a file/function/class and its relationships
- **CLI** — `navegador ingest ./myrepo`, `navegador context src/auth.py`

---

## Quick start

```bash
pip install navegador
navegador ingest ./myrepo
navegador context src/auth.py
```

---

## License

MIT — [CONFLICT LLC](https://github.com/ConflictHQ)
