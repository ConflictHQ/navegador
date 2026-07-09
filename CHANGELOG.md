# Changelog

## 1.2.0 — 2026-07-08

### Federation

- **Super-graph aggregator** — `navegador aggregate [NAME=]PATH...` rolls repo-local graphs bottom-up into one central FalkorDB meta-graph: per-repo namespacing (`repo` property + path prefixes), synthetic Repository anchors, and Concept/Person/Domain/Rule deduped by name with persisted cross-repo edges; re-aggregation is idempotent
- **MCP multi-graph routing** — new optional `repo` argument on the context, search, blast-radius, and stats tools to scope one namespace or span all repos; new `list_repos` tool (24 tools total); `navegador mcp --federate [NAME=]PATH` rolls shards up at startup
- **Shard load/unload** — `ShardManager` pages repo shards in and out under LRU with count and memory ceilings (`[cluster] max_resident_shards` / `max_shard_memory_mb` in `config.toml`); eviction persists the RDB and reloads transparently

### Interoperability

- **conflict-kg/v1 interchange** — canonical cross-tool KG format with JSON and SQLite encodings, content-derived stable node ids, and id-referencing edges; `navegador export --format conflict-kg`, auto-detecting `import`, and explorer `GET /api/graph?format=conflict-kg`

### Fixes

- **Traversal queries returned empty results** — FalkorDB rejects parameterized `*1..$depth` bounds and the errors were swallowed, so blast radius, callers/callees, task packs, and cross-repo impact silently returned nothing on the embedded backend; depth is now inlined via `queries.inline_depth()`
- **`navegador[languages]` was uninstallable** — the `tree-sitter-swift>=0.23.0` pin doesn't exist on PyPI; relaxed to `>=0.7.3` (verified against tree-sitter 0.25)
- **graph.db documentation** — `.navegador/graph.db` is a FalkorDB (Redis) RDB snapshot, not SQLite; README, generated config comments, and docstrings now say so

## 1.1.0 — 2026-04-13

### Interoperability

- **PlanOpticon format alignment** — auto-detect current batch `manifest.json` outputs separately from single-run manifests and accept `exchange.json` as the interchange format
- **PlanOpticon docs refresh** — updated the integration guide and API reference to match current manifest, knowledge graph, exchange, and batch payload shapes
- **Neutral memory terminology** — replaced public `CONFLICT-format memory` wording with `structured memory`

## 1.0.1 — 2026-04-13

### Release Readiness

- **Credential handling hardening** — moved authenticated wiki and Fossil Git access out of process argv to avoid token exposure
- **Graph correctness fixes** — cleaned up stale import subgraphs, aligned cluster snapshot edge restore with persisted node identities, and closed temporary graph stores reliably
- **Workspace and dependency resolution** — fixed Go `use (...)` parsing, manifest-name resolution for scoped packages, and `bare` workspace dependency mapping
- **Diff accuracy** — included untracked files in working-tree change detection
- **CI reliability** — added `pyyaml>=6.0` to dev extras for Python 3.13 Ansible parser coverage and upgraded GitHub Actions to Node 24-based majors

## 0.7.0 — 2026-03-23

### v0.2 — Foundation

- **Knowledge MCP tools** — `get_rationale`, `find_owners`, `search_knowledge`
- **Incremental ingestion** — content-hash-based change detection, `--incremental` flag, `--watch` mode
- **Schema versioning and migrations** — `:Meta` node versioning, `navegador migrate` CLI
- **Enhanced init** — `config.toml` with storage, LLM, and cluster settings
- **Text-based graph export** — deterministic JSONL format for git-friendly diffs
- **Editor integrations** — MCP config generation for Claude Code, Cursor, Codex, Windsurf
- **CI/CD mode** — `navegador ci ingest/stats/check` with JSON output, exit codes, GitHub Actions annotations
- **Python SDK** — `Navegador` class wrapping all internal modules
- **Sensitive content detection** — API key, password, token redaction before graph storage
- **VCS abstraction** — `GitAdapter` and `FossilAdapter` with auto-detection
- **MCP security hardening** — query validation, complexity limits, `--read-only` mode
- **Shell completions** — bash, zsh, fish tab completion
- **LLM backend abstraction** — unified provider interface for Anthropic, OpenAI, Ollama
- **AST optimizations** — LRU tree cache, incremental re-parsing, graph node diffing, parallel ingestion

### v0.3 — Framework Intelligence

- **Language expansion** — Kotlin, C#, PHP, Ruby, Swift, C, C++
- **FrameworkEnricher base class** — auto-discovery, node promotion, semantic edges
- **Framework enrichers** — Django, FastAPI, React/Next.js, Express.js, React Native, Rails, Spring Boot, Laravel
- **Monorepo support** — Turborepo, Nx, Yarn, pnpm, Cargo, Go workspace detection
- **Git diff integration** — map uncommitted changes to affected symbols and knowledge
- **Code churn correlation** — git history analysis for behavioural coupling

### v0.4 — Structural + Knowledge

- **Impact analysis** — blast-radius traversal with MCP tool and CLI
- **Execution flow tracing** — call chain precomputation from entry points
- **Dead code detection** — unreachable functions, classes, and files
- **Test coverage mapping** — link test functions to production code via TESTS edges
- **Circular dependency detection** — DFS-based cycle detection in import and call graphs
- **Multi-repo support** — register, ingest, and search across repositories
- **Coordinated rename** — graph-assisted multi-file symbol refactoring with preview
- **CODEOWNERS integration** — parse ownership files to Person and Domain nodes
- **ADR ingestion** — MADR-format Architecture Decision Records
- **OpenAPI / GraphQL ingestion** — API contract schemas as graph nodes
- **PlanOpticon pipeline** — end-to-end meeting-to-knowledge with auto-linking
- **PM tool integration** — GitHub issues ingestion (Linear/Jira stubs)
- **External dependency nodes** — npm/pip/cargo package tracking
- **Fossil SCM support** — full VCS implementation
- **Submodule traversal** — parent + submodule linked ingestion
- **Multi-repo workspace** — unified and federated knowledge graph modes

### v0.5 — Intelligence Layer

- **Semantic search** — embedding-based similarity search with LLM providers
- **Community detection** — label propagation over heterogeneous graph
- **LLM integration** — natural language queries, community naming, documentation generation
- **Documentation generation** — template and LLM-powered docs from graph context

### v0.6 — Cluster + Swarm

- **Cluster core** — Redis↔SQLite snapshot sync for agent swarms
- **Pub/sub notifications** — real-time graph change events
- **Task queue** — FIFO work assignment for agent swarms
- **Work partitioning** — community-based splitting across agents
- **Session namespacing** — branch-isolated graph namespaces
- **Distributed locking** — Redis SETNX-based mutual exclusion
- **Checkpoint/rollback** — JSONL-based state snapshots
- **Agent messaging** — async agent-to-agent communication
- **Swarm observability** — dashboard metrics
- **Fossil live integration** — ATTACH DATABASE for zero-copy queries

### v0.7 — Human Interface

- **Graph explorer** — HTTP server with browser-based force-directed visualization
- **Test coverage** — 96% coverage across 1902 tests

### Quality

- 96% test coverage (1902 tests)
- CI matrix: Ubuntu + macOS, Python 3.12 / 3.13 / 3.14

---

## 0.1.0 — 2026-03-22

First public release.

### Features

- **7-language AST ingestion** — Python, TypeScript, JavaScript, Go, Rust, Java via tree-sitter
- **Property graph storage** — FalkorDB-lite (SQLite, zero-infra) or Redis-backed FalkorDB
- **Context bundles** — file, function, class, concept, and explain context loading
- **MCP server** — 7 tools for AI agent integration (`ingest_repo`, `load_file_context`, `load_function_context`, `load_class_context`, `search_symbols`, `query_graph`, `graph_stats`)
- **CLI** — `ingest`, `context`, `function`, `class`, `explain`, `search`, `decorated`, `query`, `stats`, `add`, `annotate`, `domain`, `concept`, `wiki ingest`, `planopticon ingest`, `mcp`
- **Knowledge ingestion** — concepts, rules, decisions, persons, domains, wiki pages, PlanOpticon video analysis outputs
- **Wiki ingestion** — local Markdown directories, GitHub repo docs via API or git clone

### Quality

- 100% test coverage (426 tests)
- mypy clean (`--ignore-missing-imports`)
- ruff lint + format passing
- CI matrix: Ubuntu + macOS, Python 3.12 / 3.13 / 3.14
