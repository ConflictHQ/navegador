# Navegador — Claude Context

## What it is

AST + knowledge graph context engine for AI coding agents. Parses codebases into a FalkorDB property graph. Agents query via MCP or Python API.

## Stack

- **Python 3.12+**, standalone (no Django dependency)
- **tree-sitter** for multi-language AST parsing (13 languages)
- **FalkorDB** graph DB with **falkordblite** (SQLite via redislite) for local use
- **MCP** (`mcp` Python SDK) for AI agent integration (11 tools)
- **Click + Rich** for CLI
- **Pydantic** for data models
- **Ruff** for linting/formatting

## Package layout

```
navegador/
  cli/           — Click commands (50+ subcommands)
  graph/         — GraphStore + schema + queries + migrations + export
  ingestion/     — RepoIngester + 13 language parsers + optimization
  context/       — ContextLoader + ContextBundle (JSON/markdown)
  mcp/           — MCP server with 11 tools + security hardening
  enrichment/    — FrameworkEnricher base + 8 framework enrichers
  analysis/      — impact, flow tracing, dead code, cycles, test mapping
  intelligence/  — semantic search, community detection, NLP, doc generation
  cluster/       — Redis pub/sub, task queue, locking, sessions, messaging
  sdk.py         — Python SDK (Navegador class)
  llm.py         — LLM provider abstraction (Anthropic, OpenAI, Ollama)
  vcs.py         — VCS abstraction (Git, Fossil)
  diff.py        — Git diff → graph impact mapping
  churn.py       — Behavioural coupling from git history
  monorepo.py    — Workspace detection + ingestion
  security.py    — Sensitive content detection + redaction
  explorer/      — HTTP server + browser-based graph visualization
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

## Adding a new framework enricher

1. Create `navegador/enrichment/<framework>.py` subclassing `FrameworkEnricher`
2. Implement `framework_name`, `detection_patterns`, `enrich()`
3. The CLI auto-discovers enrichers via `pkgutil` — no registration needed

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
