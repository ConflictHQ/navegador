# Changelog

## Unreleased

### Storage configuration

- **`[storage]` in `config.toml` is finally read** — `navegador init` wrote `backend = "redis"` and nothing anywhere consumed it. Storage resolved only from `--db`, `NAVEGADOR_REDIS_URL` and `NAVEGADOR_DB`, so a project configured for a shared server silently ingested into its own local file: the command reported success, the data was real, and every reader disagreed (#169)
- **Layered resolution with provenance** — `resolve_storage()` orders explicit flags → environment → project `.navegador/config.toml` → user `~/.config/navegador/config.toml` → embedded default, and every result carries the layer that produced it. `StorageConfig.describe()` renders it, so commands can name their backend instead of leaving the user to infer it
- **Configuration is found from the target, not the working directory** — `ingest`, `submodules ingest` and `workspace ingest` resolve against the repo they were given. Naming a repo from a directory with no config previously fell through to an embedded store on a temp socket and exited with a `redis-py` stack trace (#170)
- **A relative `db_path` is anchored to the project root**, so running from a subdirectory opens the same graph rather than creating a second one
- **Unreachable backends explain themselves** — `StorageResolutionError` names the backend, the config file that selected it, and the next command to run
- **Machine-wide default** via `~/.config/navegador/config.toml`, for developers who want one shared graph across every project

### Native FalkorDB server

- **`navegador server install|start|stop|restart|status|uninstall`** — installs the official prebuilt FalkorDB module for the platform, writes a tuned `redis.conf`, and registers a launchd (macOS) or systemd user (Linux) service. No Docker, nothing compiled locally
- **`noeviction` is enforced** — under any eviction policy Redis discards part of a graph rather than shedding cache, so a memory-pressured server would silently start answering incomplete queries
- **`status` distinguishes a plain Redis from a FalkorDB** — the former answers `PING` and then fails every graph query, which is otherwise a very confusing state to debug

### Migration

- **`navegador storage migrate`** copies embedded graphs into a shared server, one project or a whole tree, with `--dry-run` and `--prune`. Node and edge counts are compared on both sides and a mismatch raises rather than reporting success
- **Endpoint identity is exact** — each node is stamped with its source-internal id and edges are reconnected by it, instead of the ambiguous `(name, path)` merge keys used by the JSONL export path (#173)
- **A stale transfer index silently swallowed edges** — indexes survive `MATCH (n) DETACH DELETE n`, and one left over from an earlier copy into the same graph returned no rows for nodes that were present, so edges with those endpoints were never created. Observed as a repeat migration landing all 83 nodes and 96 of 171 edges — exactly the groups pointing at one label. The index is now rebuilt rather than reused, torn down afterwards, and waited on until FalkorDB reports it operational, since index construction is asynchronous and a lookup against one still building returns nothing rather than waiting
- **Every named graph in a store is copied**, not only the default one. A store holds more than one whenever a federated or workspace ingest has run against it
- **Consolidating servers refuses to clobber** — two servers each have an unnamespaced `navegador` graph; copying both would leave only the last. Destination graphs holding data are refused unless renamed with `--default-as` or permitted with `--overwrite`
- **`GraphStore.query` accepts a per-query `timeout`** — servers ship a short interactive default (the official FalkorDB image sets `TIMEOUT 1000`) that a bulk read over a large graph exceeds partway through
- **Bulk reads page by internal id, not `SKIP`/`LIMIT`** — a deep `SKIP` re-scans and re-sorts everything it skips, so page cost grew with offset. On a 738k-node graph, one page at offset 600k took 1074 ms by `SKIP` and 202 ms by id

### Per-project graphs on a shared server

- **`[storage] graph`** addresses a project's own namespace. Pointing several projects at one server made them all read the same default `navegador` graph, leaving the per-repo namespaces written by federated ingest and migration unreachable — querying astrolift returned the 68-node default rather than its own 81,903-node graph
- `--graph` and `NAVEGADOR_GRAPH` override it for one command or one shell; `init --redis` derives `navegador_<directory>` to match the convention federated ingest already uses
- **`storage migrate --write-config`** records the destination in the project's config after a verified copy, closing the loop between migrating a graph and being able to query it. Configs tracked by git are left alone — a committed `[storage]` is a decision the repository makes for everyone who clones it

### Diagnostics

- **`navegador doctor`** reports the resolved backend, which config file chose it, whether the server is reachable and usable, and whether the project holds local data while declaring a shared backend
- **`navegador scan <root>`** inventories every project under a tree, flags the ones ingesting where nothing reads, and recommends a shared server with its reasons

### Output and lifecycle correctness

- **`--json` output is parseable** — per-graph progress was printed to stdout alongside the JSON payload, so piping the result into a parser failed on the narration. Progress now goes to stderr
- **`server restart` no longer reports success without starting anything** — `launchctl bootout` returns before the job is released, so the subsequent start saw the still-listed label, concluded the service was already running, and did nothing. Stop now waits for release, start retries and raises with the launchctl error and log path, and restart waits for the server to actually serve graphs before reporting success
- **`server install` names a failed start** instead of softening it into "the graph module did not report in yet"

### Documentation

- **Docs ship inside the wheel** — `docs/` moved to `navegador/docs/` and is declared as package data; mkdocs builds from there and the published site is unchanged
- **`navegador manual`** lists, reads, and searches the documentation offline, with no network and no mkdocs install
- **`read_docs` MCP tool** serves the same pages to agents, answered before the graph store is opened so it works even when the backend is misconfigured
- **Corrected `NAVEGADOR_DB=redis://…`** — documented since the beginning and never functional; that variable is a filesystem path, and a URL in it was treated as a filename. The configuration guide now documents the real resolution order

### MCP

- **Read-only mode is discoverable before the call** — write tools are omitted from the advertised schema instead of being advertised and then refused, and `graph_stats` reports `read_only` (#171)
- **Empty is distinguishable from never-ingested** — `graph_stats` and `list_repos` report an ingest `status`, so a registered-but-empty namespace is labelled rather than answering like a repo with no matching code. `list_repos` now returns objects rather than bare names (#171)

### Agent hooks

- **Every shipped hook was broken** — all four built `navegador --db <path> <subcommand>`. `--db` is a per-command option, so click rejected the invocation outright, and because the hooks read only stdout the failure reached the agent as an empty string — indistinguishable from "the graph has no context for this file". As shipped, none of them had ever returned anything (#174)
- `--db` now follows the subcommand and is omitted unless `NAVEGADOR_DB` is set, so project configuration and shared servers are honoured rather than overridden; non-zero exits go to stderr

## 1.4.1 — 2026-07-27

Distribution fixes. No functional changes to the library, CLI or graph engine.

### Standalone binaries

- **Binaries now actually run** — every published binary was inert: it started, did nothing and exited 0. `navegador/cli/commands.py` had no `if __name__ == "__main__"` guard and PyInstaller targets that file, so the binary defined the command group and all 50+ subcommands, reached the end of the module and exited without dispatching. Affected `linux-x86_64`, `macos-arm64` and `macos-x86_64` in every release from 1.0.1 onward (#154)
- **Release smoke test asserts behaviour, not exit status** — an inert binary exits 0, so an exit-code-only check passed it. The release job now verifies `--help` contains usage text, subcommands resolve, `--version` is non-empty, and an unknown flag exits non-zero (#154)

### Platform support

- **Windows binary withdrawn** — `falkordblite` publishes no Windows wheel and its sdist refuses to build on `win32`, so `pip install` cannot succeed there. The `windows-x86_64` target shipped anyway because the release job's install step ran under a shell that does not abort on an intermediate command's failure, producing a binary with no dependencies bundled. Target removed and the install step pinned to bash (#151)
- **Honest platform metadata** — the `Operating System :: OS Independent` classifier is replaced with `POSIX`, `POSIX :: Linux` and `MacOS :: MacOS X`. Windows users run navegador under WSL2 (#151)
- **Actionable error on Windows** — `GraphStore.sqlite()` told a failing user to `pip install falkordblite`, which can never succeed on Windows. It now points at WSL2 or a Redis-backed FalkorDB (#151)

## 1.4.0 — 2026-07-12

### Ingestion

- **Incremental ingest keeps cross-document REFERENCES edges** — re-parsing a changed markdown document no longer detach-deletes its Document node (which destroyed incoming REFERENCES edges nothing rebuilt); only its outgoing REFERENCES are cleared and rebuilt from current content, so incremental ingest now converges to the same edge set as a full ingest (#142)
- **Call-graph fixpoint in a single pass** — `create_edge` reports whether both endpoints matched, and ingest queues forward references (a caller parsed before its callee's node exists) and replays them once after the walk; repeat ingest passes no longer keep adding CALLS/DEPENDS_ON edges, so clean `--clear` rebuilds equal maintained graphs; sweep count surfaced as `edges_resolved` (#143)

### Repo Identity & Attribution

- **Portable Repository ids** — Repository nodes are keyed by repo name (plain ingest), parent-relative path (submodules), or workspace-relative path (monorepo packages) instead of the machine-local absolute checkout path; conflict-kg exports no longer leak filesystem layout and are byte-identical across checkout locations; new `repo_key` parameter on `RepoIngester.ingest` (#145)
- **Repo attribution for every node** — every parsed File/Document gets a `BELONGS_TO` edge to its Repository node, and `submodules ingest`/monorepo record node paths relative to the workspace root (`libs/core/main.tf`), so same-named files in different repos no longer collide on id and repo membership is a one-hop query; new `rel_root` parameter on `RepoIngester.ingest` (#144)

## 1.3.0 — 2026-07-12

### Ingestion

- **Pruned ingest walks** — the file walk uses `os.walk` with in-place pruning so skip dirs and nested git clones are never entered (previously `rglob` physically descended into `.git`, `node_modules`, and vendored clones before filtering, hanging metarepo ingests for 90+ minutes); also fixes repos under skip-dir-named parents (e.g. `~/build/myrepo`) being skipped entirely
- **Missing grammars no longer abort ingest** — files whose optional tree-sitter grammar isn't installed are skipped with a one-time install hint and counted under a new `grammar_skipped` stat instead of raising `ImportError` mid-ingest
- **Native exclusion + metarepo modes** — `--exclude` glob patterns (merged with a repo-root `.navignore`) prune directories before descent; `workspace ingest --recursive` discovers nested git clones under each root, and `--mode authored`/`--mode full` control whether vendored cores are boundary-stopped or indexed into their wrapper's graph; bare `PATH` specs default the repo name to the directory basename

### Federation & Export

- **Full-graph exports** — both exporters (conflict-kg and legacy JSONL) page past FalkorDB's 10k `RESULTSET_SIZE` ceiling with `ORDER BY id() SKIP/LIMIT` batches, so exports are no longer silently truncated; `navegador export --graph NAME` targets per-repo graphs resident in central Redis
- **Aggregate from central graphs** — `aggregate` source resolution falls back to graphs resident in the connected FalkorDB (exact name, then `navegador_<name>`), so federated workspace shards with no local files roll up; `--graph NAME` targets a distinct super-graph, with a guard against aggregating a source into itself

### Project Management

- **Issue thread ingestion + decision extraction** — `pm` ingest pulls each issue's comment thread onto the Ticket node (`--no-comments` to opt out); an optional `--extract-decisions` LLM pass surfaces Decision nodes linked to tickets and referenced code symbols; `pm decisions --to-markdown`/`--to-json` export decisions in brain-memory-ingestable form

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
