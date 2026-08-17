# Changelog

## 1.6.0 — 2026-08-16

Two themes: ingest was indexing the wrong files, and navegador could not answer the questions agents actually ask. The work is measurement-gated, and the measurement is reported below including the part that does not yet support the thesis.

### Ingest correctness

- **`.gitignore` is respected** — the walk pruned against a hardcoded skip list and never read `.gitignore`, so any project whose build output sat outside that list was indexed wholesale. On one real graph 54,543 of 54,552 `File` nodes were gitignored build output: 99.98% of a 98 MiB index was generated code being offered to agents as source. Git is the oracle (`ls-files --cached --others --exclude-standard`, exactly what ripgrep walks), which gets nested ignore files, negation, anchoring and `core.excludesFile` right for free. Ignored trees are pruned during the walk, not filtered after. `--no-gitignore` opts out (#180)
- **`navegador storage audit` and `storage prune`** — with one shared server the graph list accumulates and nothing notices when an entry goes bad. Auditing a live 41-graph server found a 45 MB graph with none of its paths resolving, one repository indexed under three names, and nine empty graphs named `1)`–`9)`. Conservative by design: a graph with no checkout to check against is `unknown` rather than assumed stale, stale graphs are held back unless `--include-stale`, and duplicates are reported rather than merged (#181)
- **`navegador storage reindex`** — #180 changed what ingest indexes and nothing rebuilt the graphs it invalidated, so every graph created before it still holds whatever the old skip-list let through. Those graphs are not stale and not duplicates, so `audit` does not flag them; this finds them and rebuilds in place, dry run unless `--yes`. It asks `git check-ignore` rather than checking membership of `git ls-files`, because a submodule's files are absent from the parent's listing without being ignored — the first version reported 100% of a healthy 12-submodule workspace as needing a rebuild (#194)
- **Audit verdicts are repeatable** — `MATCH (f:File) RETURN f.path LIMIT n` with no `ORDER BY` returns an arbitrary subset, so a 15,291-file graph was called stale on one run and healthy on the next depending on which rows came back. `prune --include-stale` deletes on that verdict. Paths are now read in order and in full (#194)
- **Call edges record how they were derived** — tree-sitter is syntax only, so `foo.bar()` cannot be proven to reach `Baz.bar`. Edges carry `resolution: "inferred"`, leaving room for compiler-accurate `resolved`. Two of the eight fidelity bugs in 1.5 came from treating those guesses as certainties (#188, partial)

### Targeting

Navegador now answers "where should I look", not "what is the answer". Telemetry across 151 real sessions found agents already scope 96.5% of their searches; what costs is the turns spent working out where.

- **`navegador locate`** — ranked places to look, each stating why it surfaced. Exact text, symbol names, file prose and vector similarity, fused by reciprocal rank rather than added, since those scores are not on comparable scales (#186)
- **`navegador scope`** — the files reachable from a symbol through calls, references and imports, optionally searched in place. On this repository it returns 2 files out of 264. This is the search-space reduction no flat text index can compute. An unknown symbol yields nothing rather than falling back to the whole repository (#186)
- **`navegador grep`** — exact substring and regex search with file and line. Trigrams narrow, the real pattern decides, so results are exact: verified against ripgrep on this package's own source across eight patterns with zero disagreements. Cost scales with matches rather than corpus size — a search matching nothing takes 0.1 ms where ripgrep pays 13 ms (#184)
- **Four new MCP tools** — `locate`, `scope_for`, `neighbourhood`, `grep_code`, taking the server to 31. `scope_for` accepts a pattern so an agent can narrow and search in one round trip (#186)

### Retrieval

- **Content store** — the graph kept structure and discarded the text, so there was nothing to match a literal against. Content lives in Redis beside the graph, addressed by the `content_hash` ingest already computes, which makes dedup and incremental re-ingest free. stdlib `zlib`, 3.83× on this package's source; short files are stored raw because compression made an 81-byte file 93 (#183)
- **Literals, comments and split identifiers are indexed** — an AST keeps a function's name and throws away the error message it raises. A pasted error message now finds the file that raises it, "rate limiting" finds a file where the phrase exists only in a comment, and "user id" reaches `getUserById` (#185)
- **Semantic search runs inside the database** — every query used to fetch all embedded nodes with full vectors and compute cosine in Python, roughly a gigabyte per query at 100k nodes, growing with the graph and paid again by every agent sharing the server. Now behind FalkorDB's native vector index: 500 nodes 1.18 ms, 5000 nodes 1.37 ms. Also fixes a silent 1000-node truncation, full re-embedding on every call, and a name-based upsert that could write to the wrong node (#182)

### Performance and process

- **CLI startup 137 ms → 37 ms** — `rich.markdown` and `asyncio` were ~95 ms of module import paid by every invocation, including agent hooks, against a 0.45 ms graph query. Deferred to the functions that need them, with tests asserting they stay absent (#190)
- **Coverage is enforced** — it was measured on every run and gated nowhere. Ratcheted at 93 against a measured 94. The more useful rule is the companion: all eight 1.5 fidelity bugs shipped inside covered lines, and #173 survived because its tests mocked the store, where a `MagicMock` write always succeeds. New tests reject any new test file that mocks the graph store (#191)
- **Retrieval telemetry** — `scripts/retrieval_telemetry.py` and a frozen baseline. Median 13 turns to first correct target, 5 orientation turns, 1.17 re-read ratio (#187)
- **Dependency posture documented** — `pip install navegador` is the whole installation. It is why the trigram index was built rather than integrating Zoekt, and why SCIP ingestion will consume an index if present but never require one (#189)

### On the evidence

This release is measured rather than argued, and the measurement is not finished.

`scripts/retrieval_telemetry.py` carries a frozen baseline of what agents actually
do: 206 tool calls in a median session, 13 turns before touching the file that gets
edited, and 32% of file reads re-opening something already read.

It also carries the measurement that can settle this without a control group. When a
targeting call hands an agent a set of places, is the file it goes on to edit among
them? Both the call and its result are in the transcript, and so is the edit that
follows, so the hit rate is directly computable — alongside the size of the scope
offered, because precision on its own is gamed by returning everything.

That reading is not in yet. The tools shipped before any agent had used them, so the
only scored calls so far are this release's own tests.

Two things are already true independent of it. `scope_for` reduces a 264-file
repository to the 2 files reachable from a symbol, a reduction no flat text index can
compute. And `grep` costs 0.1 ms on a miss where a full scan costs 13 ms, because its
cost tracks matches rather than corpus size.

Whether those add up to fewer turns in practice is an open question with an
instrument pointed at it. If the answer is no, the instrument will say so.

## 1.5.0 — 2026-08-16

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
- **`doctor` tells "never ingested" apart from "already migrated"** — it reads the node count of the graph the project actually resolves to. An empty one is a problem (queries return nothing, which is not the same as not-found); a populated one with a leftover local file is a note, not a warning to live with forever
- **`navegador scan <root>`** inventories every project under a tree, flags the ones ingesting where nothing reads, and recommends a shared server with its reasons

### Graph fidelity

- **Export/import dropped every edge and reported success** — the JSONL export wrote node ids derived from array position, so an import re-derived different ids and every edge referenced an endpoint that did not exist. `create_edge` returned falsy, nothing checked it, and the summary counted edges it had described rather than edges it had written. A round-trip of a 4,000-node graph restored 4,000 nodes and 0 edges, which reads as a repo with no call structure rather than a failed import. Ids are now content-derived so they survive the round trip, the created count comes from the store, and a shortfall raises instead of printing a total (#173)
- **Ingest never removed nodes for files deleted from disk** — incremental ingest compared mtimes to decide what to re-parse and had no branch at all for files that had gone. A renamed module left its old symbols in the graph indefinitely, and impact queries cited functions that no longer existed anywhere in the repository. The file set present on disk is now recorded before parse decisions are made, and the difference is pruned (#168)
- **Repository identity survived neither a worktree nor a renamed clone** — the `Repository` node was keyed by the checkout directory's basename, so `git clone <url> myproj-review` produced a second, indistinguishable `Repository`, and files ended up owned by every node they had been ingested under. Identity now comes from the normalized git remote, the only thing constant across all three, with the directory name as the fallback outside a repo (#167)
- **Python call edges never crossed a file boundary** — `_extract_calls` recorded every callee as living in the calling file, so an imported function resolved to a node that does not exist and the edge was dropped. On a multi-package fixture the graph held the right symbols and *zero* `CALLS` edges, so explain, trace, and impact stopped dead at each file and looked like code with no callers. Imports are now resolved to repo files (absolute, relative, and function-local), and a callable passed to a higher-order helper is recorded as `REFERENCES` rather than lost (#163)
- **`from x import y` produced no `Import` nodes at all** — the parser matched a node type (`import_from_member`) that does not exist in the tree-sitter Python grammar; both the module and its members are `dotted_name` (#163)
- **testmap invented confident cross-repository `TESTS` edges** — the heuristic stripped `test_`, tried ever-shorter prefixes, and took the first symbol with that name anywhere in the graph, so `test_request_returns_200` degraded to `request` and linked to an unrelated repository's method at full confidence. A wrong `TESTS` edge is worse than a missing one, because impact and context queries then cite it. Candidates are now scored on repository, path proximity, and name distinctiveness; generic verbs are never candidates at any threshold; test-module helpers are never targets; edges carry their confidence and evidence; and ties are reported as ambiguous rather than resolved by iteration order (#166)

### LLM configuration

- **`[llm]` in `config.toml` was written by `init` and read by nothing** — `resolve_llm()` now layers it the same way storage is resolved, with provenance
- **Provider discovery required only that the SDK import** — Anthropic was selected whenever the package was installed, credentials or not, and the failure surfaced later as a raw auth error from inside the call. Availability now means a usable credential, and the error names what each provider is missing (#164)
- **The default Anthropic model was retired** — `claude-3-5-haiku-20241022` has 404'd since 2026-02-19, so every fallback path was calling a model that no longer exists (#164)
- **Generated Cypher was not executable** — the NLP engine emitted `(n:Class|Function)`, which FalkorDB rejects: a node pattern takes exactly one label. Alternative *relationship* types are valid and are left alone. Node alternatives are rewritten to `WHERE (n:Class OR n:Function)`, and a query that still fails is retried once with the error fed back (#165)

### Supergraph interoperability

- **Contract v1.0 conformance** — navegador owns the `code` realm and is addressable from every other graph in the system as `[<repo>/]code:<path>[#<symbol>]`. Conformance is a mapping layer at the tool surface; internal ids, the store, and the schema are unchanged (#158)
- **`navegador contract resolve <address>`** and the `resolve_address` MCP tool complete the brain-to-code hop: a brain-side `implemented_in` edge carries a code address, and resolving it returns the node plus its callers and callees, each with its own address, so traversal continues without a second round trip
- **`navegador contract propose`** and the `propose_join_edges` MCP tool emit contract-format join-edge proposals with confidence and evidence. Navegador proposes and writes nothing; the brain reviews and commits. Targets that are brain-realm nodes are dropped rather than given a code address we have no authority to mint
- **Responses that emit addresses declare `contract: "1.0"`**, and `search_symbols` results carry the address a brain would record to point back at them

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
- **`bootstrap.sh` was unrunnable after a Windows checkout** — with no `.gitattributes` in the repository, git converted it to CRLF and bash rejected it outright (`syntax error near unexpected token $'in\r'`). That is the documented install path for WSL2 users, who reach it through a Windows checkout. `* text=auto eol=lf` is now committed so the convention travels with the repo

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
