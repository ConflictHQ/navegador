# Navegador Bootstrap

This is the primary conventions document. All agent shims (`CLAUDE.md`, `AGENTS.md`, `calliope.md`) point here. `AGENTS.md` is also used by OpenAI Codex.

An agent given this document and a business requirement should be able to generate correct, idiomatic code without exploring the codebase.

---

## What's Already Built

| Layer | What's there |
|-------|-------------|
| Graph store | `navegador/graph/` — GraphStore + schema + queries + migrations + export + conflict-kg/v1 interchange, backed by FalkorDB |
| Federation | `navegador/federation.py` — SuperGraphAggregator: roll repo-local graphs into a central super-graph (namespacing, knowledge dedup) |
| Ingestion | `navegador/ingestion/` — RepoIngester + 13 tree-sitter language parsers + optimization |
| Context | `navegador/context/` — ContextLoader + ContextBundle (JSON/markdown output) |
| MCP server | `navegador/mcp/` — 24 tools + security hardening, via the `mcp` Python SDK |
| CLI | `navegador/cli/` — Click + Rich, 50+ subcommands, entry point `navegador` |
| Enrichment | `navegador/enrichment/` — FrameworkEnricher base + 8 framework enrichers, auto-discovered via `pkgutil` |
| Analysis | `navegador/analysis/` — impact, flow tracing, dead code, cycles, test mapping |
| Intelligence | `navegador/intelligence/` — semantic search, community detection, NLP, doc generation |
| Cluster | `navegador/cluster/` — Redis pub/sub, task queue, locking, sessions, messaging, LRU shard load/unload (`shards.py`) |
| SDK | `navegador/sdk.py` — Python SDK (`Navegador` class) |
| LLM | `navegador/llm.py` — provider abstraction (Anthropic, OpenAI, Ollama) |
| VCS | `navegador/vcs.py` — Git + Fossil abstraction; `diff.py` (diff → graph impact), `churn.py` (behavioural coupling) |
| Monorepo | `navegador/monorepo.py` — workspace detection + ingestion |
| Security | `navegador/security.py` — sensitive content detection + redaction |
| Explorer | `navegador/explorer/` — HTTP server + browser-based graph visualization |
| Docs | `docs/` + `mkdocs.yml` — mkdocs-material, deployed to navegador.dev |

Stack: **Python 3.12+**, standalone (no Django). tree-sitter for AST parsing, FalkorDB property graph, Pydantic models, Click + Rich CLI, Ruff for lint/format.

---

## FalkorDB Connection

```python
# Embedded (local, zero-infra) — uses falkordblite; graph file is an RDB snapshot, not SQLite
from redislite import FalkorDB   # falkordblite provides this
db = FalkorDB("path/to/graph.db")
graph = db.select_graph("navegador")

# Redis (production)
import falkordb
client = falkordb.FalkorDB.from_url("redis://localhost:6379")
```

---

## Conventions

### Data Models

Pydantic (v2) models throughout. Graph node/edge shapes are defined in `navegador/graph/schema.py` — extend the schema there, never ad-hoc.

### CLI

Click commands under `navegador/cli/`, output via Rich. New subcommands follow the existing command-group structure in `cli/commands.py`.

### MCP Server

Tools are declared in `list_tools()` and dispatched in `call_tool()` in `mcp/server.py`. Security hardening (path validation, redaction) applies to every tool — new tools must go through the same guards.

### Extension Points

**New language parser:**
1. Create `navegador/ingestion/<lang>.py` subclassing `LanguageParser`
2. Implement `parse_file(path, repo_root, store) -> dict[str, int]`
3. Add the extension + language key to `LANGUAGE_MAP` in `parser.py`
4. Register in `RepoIngester._get_parser()`

**New framework enricher:**
1. Create `navegador/enrichment/<framework>.py` subclassing `FrameworkEnricher`
2. Implement `framework_name`, `detection_patterns`, `enrich()`
3. Auto-discovered via `pkgutil` — no registration needed

**New MCP tool:**
1. Add a `Tool(...)` entry in `list_tools()` in `mcp/server.py`
2. Add a handler branch in `call_tool()`

### Tests

- pytest, tests in `tests/`, files named `test_*.py`
- Coverage is on by default (`addopts = "--cov=navegador"`)
- Tests run against a **real graph store** (falkordblite embedded) — never mock the graph database
- Parser tests: real source-file fixtures in, assert node/edge counts and graph state out
- Cover happy path AND error/edge cases (unparseable files, empty repos, missing grammars)

### Code Style

- Ruff for both lint and format: line length **100**, target **py312**, rules `E, F, W, I`
- Per-file ignores: `explorer/templates.py` and `tests/*` are exempt from E501
- mypy configured (`warn_return_any`, py312)
- Package manager: pip (editable installs); lockfile-free library — version bounds live in `pyproject.toml`
- venv mandatory — never install into system Python

---

## Ports (local)

| Service | URL |
|---------|-----|
| mkdocs preview | http://localhost:8000 |

The explorer server and MCP server are started via the `navegador` CLI; ports are configured per-invocation.

---

## Common Commands

```bash
pip install -e ".[dev]"          # dev install (add .[all] for every extra)
pytest tests/ -v                 # run tests (coverage included)
ruff check navegador/            # lint
ruff format navegador/           # format
mkdocs serve                     # docs preview at :8000 (needs .[docs])
mkdocs gh-deploy --force         # deploy docs to navegador.dev
navegador --help                 # CLI entry point
```
