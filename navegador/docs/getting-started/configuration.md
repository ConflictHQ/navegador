# Configuration

Navegador has minimal required configuration. The only thing you typically need to set is where the graph database lives.

---

## Where the graph lives

Navegador resolves its storage backend from layered sources. The first one that
makes a decision wins:

| Priority | Source | Set by |
|---|---|---|
| 1 | `--db` / `--redis-url` flags | one command |
| 2 | `NAVEGADOR_REDIS_URL` / `NAVEGADOR_DB` | one shell |
| 3 | Project `.navegador/config.toml` `[storage]` | one project |
| 4 | User `~/.config/navegador/config.toml` `[storage]` | every project on the machine |
| 5 | Default: embedded graph at `.navegador/graph.db` | — |

To see which layer decided, and whether the result actually works:

```bash
navegador doctor
```

It prints the resolved backend and the exact file that chose it.

### Embedded (default)

Zero infrastructure: the graph is a single file inside the project.

```bash
navegador init .                 # writes .navegador/config.toml
navegador ingest .
```

```
my-project/
  .navegador/
    config.toml     ← [storage] backend = "sqlite"
    graph.db        ← the graph
  src/
```

`navegador init` adds `.navegador/` to `.gitignore` — the graph is a build
artifact, rebuilt with `navegador ingest .`. Pass `--commit-graph` to track it
instead.

!!! note "It is not a SQLite file"
    The backend is named `sqlite` for configuration compatibility, but the file
    is a Redis RDB snapshot written by falkordblite. `sqlite3` cannot open it.

### Shared server (multi-repo, multi-agent)

One resident in-memory graph that every project and every agent queries. This is
the right choice once you work across several repos, or run more than one agent:
each agent otherwise re-reads its own copy from disk.

Install a native server — no Docker, nothing compiled locally:

```bash
navegador server install
```

That downloads the official FalkorDB module for your platform, writes a tuned
`redis.conf` under `~/.navegador/`, registers a start-at-login service, and
writes `~/.config/navegador/config.toml` so **every** project uses it by default.

```bash
navegador server status       # version, memory, resident graphs
navegador server stop|start|restart
```

To point one project at a specific server instead, put it in that project's
config:

```toml
[storage]
backend = "redis"
redis_url = "redis://localhost:6379"
```

Or, for a single command or shell:

```bash
export NAVEGADOR_REDIS_URL=redis://localhost:6379
export NAVEGADOR_REDIS_URL=redis://:mypassword@redis.internal:6379   # with auth
```

!!! warning "`NAVEGADOR_DB` is for file paths only"
    A `redis://` URL in `NAVEGADOR_DB` is not a connection string — it is treated
    as a filename. Use `NAVEGADOR_REDIS_URL`.

Install the Redis extra if you have not already:

```bash
pip install "navegador[redis]"
```

---

## Moving an existing graph to a shared server

Existing local graphs do not have to be re-ingested. `storage migrate` copies
them, verifying node and edge counts on both sides and failing rather than
reporting a partial success:

```bash
navegador storage migrate                          # this project
navegador storage migrate --all --root ~/repos     # every project under a tree
navegador storage migrate --all --root ~/repos --dry-run
```

The local file is left in place unless you pass `--prune`, and never removed
when verification fails. To consolidate two servers:

```bash
navegador storage migrate --from redis://localhost:6380 --to redis://localhost:6379 \
    --default-as navegador_otherhost
```

To find out which projects would benefit, and which are ingesting somewhere
nothing reads:

```bash
navegador scan ~/repos
```

---

## Embedded vs shared: when to use which

| | Embedded (falkordblite) | Shared server (FalkorDB) |
|---|---|---|
| Setup | Zero config | `navegador server install` |
| Use case | One repo, one developer | Many repos, CI, agent swarms |
| Residency | Re-read from disk per process | Held in memory, queried by all |
| Staleness | Per-checkout copies drift | One graph, no drift |
| Extra required | None (included) | `navegador[redis]` |

Both implement the same `GraphStore` interface, so nothing above the storage
layer changes when you switch.

---

## GitHub token

Required for `navegador wiki ingest --repo owner/repo` to access private wikis or to avoid rate limits on public repos.

```bash
export GITHUB_TOKEN=ghp_...
navegador wiki ingest --repo myorg/myrepo
```

For public repos, wiki ingestion works without a token but will hit GitHub's unauthenticated rate limit (60 req/hr).

---

## Project-local config

Drop a `.navegador/config.toml` in your project root for project-specific defaults:

```toml
[database]
path = ".navegador/navegador.db"

[ingest]
exclude = ["node_modules", "dist", ".venv", "migrations"]
incremental = true        # use content hashing by default
redact = false            # strip secrets from ingested content

[mcp]
read_only = false         # set true to prevent agents from writing to the graph
max_query_complexity = 100  # Cypher query complexity limit
```

---

## LLM provider and model

LLM-backed commands (`ask`, `docs --provider`, `generate-docs`,
`semantic-search`, `pm decisions`) resolve their provider the same way storage
does — first decision wins:

| Priority | Source |
|---|---|
| 1 | `--provider` / `--model` flags |
| 2 | `NAVEGADOR_LLM_PROVIDER` / `NAVEGADOR_LLM_MODEL` |
| 3 | Project `.navegador/config.toml` `[llm]` |
| 4 | User `~/.config/navegador/config.toml` `[llm]` |
| 5 | Auto-discovery across the usable providers |

```toml
[llm]
provider = "anthropic"
model = "claude-opus-5"
```

### Credentials

**Credentials are read from the environment only.** Navegador does not read
`.env` files, keychains, or credential helpers — export the variable, or set it
in your shell profile or CI secrets:

| Provider | Credential | Notes |
|---|---|---|
| `anthropic` | `ANTHROPIC_API_KEY` or `ANTHROPIC_AUTH_TOKEN` | |
| `openai` | `OPENAI_API_KEY` | |
| `ollama` | *(none)* | Must be reachable at `OLLAMA_HOST`, default `http://localhost:11434` |

Auto-discovery considers whether a provider can actually serve a request, not
merely whether its SDK imports. A provider whose package is installed but whose
credential is missing is skipped rather than selected, and the resulting error
names every provider and why each was unusable:

```
No usable LLM provider.
  anthropic: no credential in ANTHROPIC_API_KEY or ANTHROPIC_AUTH_TOKEN
  openai: no credential in OPENAI_API_KEY
  ollama: no Ollama server is reachable at http://localhost:11434
```

Model defaults track current aliases. Pin one in `[llm] model` if you need a
specific model rather than the provider's default.

---

## Cluster config

For team deployments using a shared Redis graph with pub/sub, task queue, and session coordination:

```toml
[cluster]
enabled = true
redis_url = "redis://redis.internal:6379"
graph_name = "navegador-team"

[cluster.pubsub]
channel = "navegador:events"

[cluster.queue]
name = "navegador:tasks"

[cluster.sessions]
ttl_seconds = 3600
```

See the [Cluster mode](../guide/cluster.md) guide for full setup instructions.

---

## Environment variable reference

| Variable | Default | Description |
|---|---|---|
| `NAVEGADOR_REDIS_URL` | — | Shared FalkorDB server URL. Takes precedence over `NAVEGADOR_DB` |
| `NAVEGADOR_DB` | `.navegador/graph.db` | Path to an embedded graph file. **Not** a `redis://` URL |
| `NAVEGADOR_HOME` | `~/.navegador` | Where `navegador server` keeps its module, config, and data |
| `NAVEGADOR_GRAPH` | — | Named graph within the store, overriding `[storage] graph` |
| `NAVEGADOR_LLM_PROVIDER` | — | `anthropic`, `openai`, or `ollama` |
| `NAVEGADOR_LLM_MODEL` | — | Model ID passed to the provider |
| `GITHUB_TOKEN` | — | GitHub personal access token for wiki ingestion |
| `ANTHROPIC_API_KEY` | — | Anthropic API key for LLM features |
| `OPENAI_API_KEY` | — | OpenAI API key for LLM features |
| `NAVEGADOR_CONFIG` | `~/.config/navegador/config.toml` | Override the **user-level** config path |
