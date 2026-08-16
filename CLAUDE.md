# Navegador — Claude Context

Primary conventions doc: [`bootstrap.md`](bootstrap.md) — read it before writing any code.

## What it is

AST + knowledge graph context engine for AI coding agents. Parses codebases into a FalkorDB property graph. Agents query via MCP or Python API.

## Stack

- **Python 3.12+**, standalone (no Django dependency)
- **tree-sitter** for multi-language AST parsing (13 languages)
- **FalkorDB** graph DB with **falkordblite** (embedded via redislite; graph file is a Redis RDB snapshot, not SQLite) for local use
- **MCP** (`mcp` Python SDK) for AI agent integration (25 tools)
- **Click + Rich** for CLI
- **Pydantic** for data models
- **Ruff** for linting/formatting

## Package layout

```
navegador/
  cli/           — Click commands (50+ subcommands)
  graph/         — GraphStore + schema + queries + migrations + export + conflict-kg/v1 interchange + transfer (store-to-store copy)
  ingestion/     — RepoIngester + 13 language parsers + optimization
  context/       — ContextLoader + ContextBundle (JSON/markdown)
  mcp/           — MCP server with 25 tools + security hardening
  enrichment/    — FrameworkEnricher base + 8 framework enrichers
  analysis/      — impact, flow tracing, dead code, cycles, test mapping
  intelligence/  — semantic search, community detection, NLP, doc generation
  cluster/       — Redis pub/sub, task queue, locking, sessions, messaging, shard load/unload
  federation.py  — SuperGraphAggregator (repo graphs → central super-graph)
  sdk.py         — Python SDK (Navegador class)
  llm.py         — LLM provider abstraction (Anthropic, OpenAI, Ollama)
  vcs.py         — VCS abstraction (Git, Fossil)
  diff.py        — Git diff → graph impact mapping
  churn.py       — Behavioural coupling from git history
  monorepo.py    — Workspace detection + ingestion
  security.py    — Sensitive content detection + redaction
  config.py      — Layered storage resolution (flags > env > project > user > default)
  server.py      — Native FalkorDB server lifecycle (no Docker)
  inventory.py   — Project discovery + stranded-graph detection
  manual.py      — Packaged docs, offline + over MCP
  docs/          — mkdocs sources, shipped inside the wheel
  explorer/      — HTTP server + browser-based graph visualization
```

## FalkorDB connection

```python
# Resolve rather than construct — this honours project and user configuration.
from navegador.config import get_store
store = get_store(target="/path/to/repo")
```

Resolution order (first decision wins): `--db`/`--redis-url` → `NAVEGADOR_REDIS_URL`/
`NAVEGADOR_DB` → project `.navegador/config.toml` → `~/.config/navegador/config.toml`
→ embedded `.navegador/graph.db`. CLI commands taking a repo path **must** pass it
as `target`, or they resolve against the wrong project.

`navegador doctor` prints the resolved backend and which config file chose it.

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

Docs live in `navegador/docs/` and ship inside the wheel — `navegador manual`
reads them offline, and the `read_docs` MCP tool serves them to agents. Keep
them accurate: they are shipped output, not a website.

```bash
pip install -e ".[docs]"
mkdocs serve   # local preview at http://localhost:8000 (docs_dir = navegador/docs)
mkdocs gh-deploy --force  # deploy to navegador.dev
```
