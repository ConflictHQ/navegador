# Configuration

Navegador has minimal required configuration. The only thing you typically need to set is where the graph database lives.

---

## Database path

### SQLite (default)

By default navegador writes a `navegador.db` file in the current working directory. Override with the `--db` flag or the `NAVEGADOR_DB` environment variable:

```bash
# flag (takes precedence)
navegador ingest ./repo --db ~/.navegador/myproject.db

# environment variable
export NAVEGADOR_DB=~/.navegador/myproject.db
navegador ingest ./repo
```

The `.navegador/` directory convention keeps the database alongside your project:

```
my-project/
  .navegador/
    navegador.db    ← graph database
  src/
  ...
```

Add `.navegador/` to `.gitignore` — the database is a build artifact, not source.

### Redis (production)

For team or CI environments, point `NAVEGADOR_DB` at a Redis instance running FalkorDB:

```bash
export NAVEGADOR_DB=redis://localhost:6379
```

With authentication:

```bash
export NAVEGADOR_DB=redis://:mypassword@redis.internal:6379
```

Install the Redis extra if you haven't already:

```bash
pip install "navegador[redis]"
```

---

## SQLite vs Redis: when to use which

| | SQLite (falkordblite) | Redis (FalkorDB) |
|---|---|---|
| Setup | Zero config | Requires a Redis server |
| Use case | Local dev, single developer | Team, CI, shared context |
| Persistence | Local file | Redis persistence config |
| Performance | Fast for single-user workloads | Scales to large codebases |
| Extra required | None (included) | `navegador[redis]` |

Both backends implement the same `GraphStore` interface. You can migrate by re-ingesting against the new backend.

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

## LLM provider config

Configure LLM providers used by `navegador ask`, `navegador docs`, and `navegador semantic-search`. Requires `pip install "navegador[llm]"`.

```toml
[llm]
provider = "anthropic"    # "anthropic", "openai", or "ollama"
model = "claude-3-5-haiku-20241022"

[llm.anthropic]
api_key_env = "ANTHROPIC_API_KEY"   # env var name (not the key itself)

[llm.openai]
api_key_env = "OPENAI_API_KEY"
model = "gpt-4o-mini"

[llm.ollama]
base_url = "http://localhost:11434"
model = "llama3"
```

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
| `NAVEGADOR_DB` | `./navegador.db` | Path to SQLite file or `redis://` URL |
| `GITHUB_TOKEN` | — | GitHub personal access token for wiki ingestion |
| `ANTHROPIC_API_KEY` | — | Anthropic API key for LLM features |
| `OPENAI_API_KEY` | — | OpenAI API key for LLM features |
| `NAVEGADOR_CONFIG` | `.navegador/config.toml` | Override config file path |
