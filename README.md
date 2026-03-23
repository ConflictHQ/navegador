# Navegador

**AST + knowledge graph context engine for AI coding agents.**

Navegador parses your codebase into a property graph and makes it queryable. AI coding agents can ask "what calls this function?", "what does this file depend on?", or "show me everything related to auth" — and get structured, precise answers instead of raw file dumps.

> *navegador* — Spanish for *navigator / sailor*

[![CI](https://github.com/ConflictHQ/navegador/actions/workflows/ci.yml/badge.svg)](https://github.com/ConflictHQ/navegador/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/navegador)](https://pypi.org/project/navegador/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Docs](https://img.shields.io/badge/docs-navegador.dev-blue)](https://navegador.dev)

---

## Why

AI coding agents load context by reading raw files. They don't know what calls what, what depends on what, or which 5 functions out of 500 are actually relevant. Navegador builds a structured map — then exposes it via MCP so any agent can navigate your code with precision.

---

## Quick start

```bash
pip install navegador

# Ingest your repo
navegador ingest ./myrepo

# Load context for a file
navegador context src/auth.py

# Search for a symbol
navegador search "get_user"

# Check graph stats
navegador stats
```

---

## MCP integration

Add to your Claude / Cursor MCP config:

```json
{
  "mcpServers": {
    "navegador": {
      "command": "navegador",
      "args": ["mcp", "--db", ".navegador/graph.db"]
    }
  }
}
```

Available MCP tools:

| Tool | Description |
|------|-------------|
| `ingest_repo` | Parse and load a repo into the graph |
| `load_file_context` | All symbols in a file + their relationships |
| `load_function_context` | What a function calls and what calls it |
| `load_class_context` | Class methods, inheritance, subclasses |
| `search_symbols` | Fuzzy search for functions/classes by name |
| `query_graph` | Raw Cypher passthrough |
| `graph_stats` | Node and edge counts |

---

## Graph schema

**Nodes:** `Repository` · `File` · `Module` · `Class` · `Function` · `Method` · `Variable` · `Import` · `Decorator`

**Edges:** `CONTAINS` · `DEFINES` · `IMPORTS` · `CALLS` · `INHERITS` · `REFERENCES` · `DEPENDS_ON`

---

## Storage

Navegador uses **FalkorDB** (property graph, Cypher queries).

| Mode | Backend | When to use |
|------|---------|-------------|
| Default | `falkordblite` (SQLite) | Local dev, zero infrastructure |
| Production | Redis + FalkorDB module | Shared / persistent deployments |

```python
from navegador.graph import GraphStore

# SQLite (default)
store = GraphStore.sqlite(".navegador/graph.db")

# Redis
store = GraphStore.redis("redis://localhost:6379")
```

---

## Language support

| Language | Status |
|----------|--------|
| Python | ✅ |
| TypeScript / JavaScript | ✅ |
| Go | ✅ |
| Rust | ✅ |
| Java | ✅ |

---

## License

MIT — [CONFLICT LLC](https://github.com/ConflictHQ)
