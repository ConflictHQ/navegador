# Navegador — Claude Context

## What it is

AST + knowledge graph context engine for AI coding agents. Parses codebases into a FalkorDB property graph. Agents query via MCP or Python API.

## Stack

- **Python 3.10+**, standalone (no Django dependency)
- **tree-sitter** for multi-language AST parsing (`tree-sitter-python`, `tree-sitter-typescript`)
- **FalkorDB** graph DB with **falkordblite** (SQLite via redislite) for local use
- **MCP** (`mcp` Python SDK) for AI agent integration
- **Click + Rich** for CLI
- **Pydantic** for data models
- **Ruff** for linting/formatting

## Package layout

```
navegador/
  cli/         — Click commands (ingest, context, search, stats, mcp)
  graph/       — GraphStore + schema + Cypher query templates
  ingestion/   — RepoIngester + language parsers (python.py, typescript.py)
  context/     — ContextLoader + ContextBundle (JSON/markdown output)
  mcp/         — MCP server with 7 tools
```

## FalkorDB connection

```python
# SQLite (local, zero-infra) — uses falkordblite
from redislite import FalkorDB   # falkordblite provides this
db = FalkorDB("path/to/graph.db")
graph = db.select_graph("navegador")

# Redis (production)
import falkordb
client = falkordb.FalkorDB.from_url("redis://localhost:6379")
```

## Adding a new language parser

1. Create `navegador/ingestion/<lang>.py` subclassing `LanguageParser`
2. Implement `parse_file(path, repo_root, store) -> dict[str, int]`
3. Add the extension + language key to `LANGUAGE_MAP` in `parser.py`
4. Register in `RepoIngester._get_parser()`

## Adding a new MCP tool

1. Add a `Tool(...)` entry in `list_tools()` in `mcp/server.py`
2. Add a handler branch in `call_tool()`

## Running tests

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

## Linting

```bash
ruff check navegador/
ruff format navegador/
```

## Docs

```bash
pip install -e ".[docs]"
mkdocs serve   # local preview at http://localhost:8000
mkdocs gh-deploy --force  # deploy to navegador.dev
```
