# Cluster Mode

Cluster mode lets multiple machines share a single navegador graph over Redis. Use it when your team runs large ingestion jobs, want shared context across agents and CI, or need partitioned work processing.

---

## Prerequisites

- Redis 7+ (or a managed Redis service — Upstash, Redis Cloud, etc.)
- `pip install "navegador[redis]"`

---

## Setup

### 1. Initialize cluster mode

Point navegador at your Redis instance and run init:

```bash
navegador init --cluster --redis redis://your-redis-host:6379
```

This writes cluster config to `navegador.toml`:

```toml
[cluster]
enabled = true
redis_url = "redis://your-redis-host:6379"
graph_name = "navegador"
node_id = "worker-1"   # auto-generated; override with --node-id
```

### 2. Verify connectivity

```bash
navegador cluster status
```

Output:

```
Cluster: connected
Redis:   redis://your-redis-host:6379
Graph:   navegador (47,231 nodes, 189,043 edges)
Workers: 3 online (worker-1, worker-2, ci-runner-7)
Queue:   0 tasks pending
```

---

## Shared graph

All cluster members read from and write to the same FalkorDB graph stored in Redis. Any ingestion or annotation from any node is immediately visible to all other nodes.

```bash
# on any machine in the cluster
navegador ingest ./src

# on any other machine — sees the result immediately
navegador explain AuthService
```

### Local snapshots

To work offline or reduce Redis round-trips, snapshot the graph to a local SQLite file:

```bash
# pull a snapshot from the shared graph
navegador cluster snapshot --pull .navegador/local.db

# use the snapshot for queries
navegador --db .navegador/local.db explain AuthService

# push local changes back to the shared graph
navegador cluster snapshot --push .navegador/local.db
```

Snapshots are point-in-time copies. They do not auto-sync. Use `--pull` to refresh and `--push` to merge back.

---

## Task queue

The cluster task queue distributes ingestion and analysis jobs across workers. Instead of running `navegador ingest` directly, submit a task:

```bash
# submit an ingestion task
navegador cluster enqueue ingest ./src --clear

# submit an analysis task
navegador cluster enqueue analyze impact validate_token

# list pending and active tasks
navegador cluster queue
```

Workers pick up tasks from the queue automatically. See [work partitioning](#work-partitioning) for multi-worker ingestion.

### Starting a worker

```bash
navegador cluster worker start
```

The worker polls the queue and processes tasks. Run one worker per machine.

```bash
# run in background
navegador cluster worker start --daemon

# stop the worker
navegador cluster worker stop
```

---

## Work partitioning

For large monorepos, split ingestion across multiple workers:

```bash
# partition a directory across N workers
navegador cluster partition ./src --workers 4

# each worker then runs its assigned slice
navegador cluster worker start --partition 0 --of 4   # worker 0
navegador cluster worker start --partition 1 --of 4   # worker 1
# ...
```

Partitioning splits the file list across workers by file count. All workers write to the same shared graph. The final graph is the union of all partitions.

### In CI

```yaml
# .github/workflows/ingest.yml
jobs:
  ingest:
    strategy:
      matrix:
        partition: [0, 1, 2, 3]
    steps:
      - run: navegador cluster worker start --partition ${{ matrix.partition }} --of 4 --run-once
```

`--run-once` processes the current queue and exits rather than running as a daemon.

---

## Sessions

Sessions let multiple agents coordinate on the same task without interfering with each other.

```bash
# start a session (returns a session ID)
SESSION=$(navegador cluster session start --name "feature/auth-refactor")
echo "Session: $SESSION"

# run commands scoped to the session
navegador --session $SESSION ingest ./src/auth
navegador --session $SESSION explain AuthService

# end the session
navegador cluster session end $SESSION
```

Sessions create a namespaced view of the graph. Writes within a session are visible to other session members but isolated from the main graph until committed.

```bash
# commit session changes to the main graph
navegador cluster session commit $SESSION

# discard session changes
navegador cluster session discard $SESSION
```

---

## Locking

For writes that must not overlap (e.g., `--clear` ingest), navegador acquires a distributed lock:

```bash
navegador ingest ./src --clear
# automatically acquires the graph write lock; other writers block until it releases
```

You can also acquire locks explicitly:

```bash
# acquire a named lock
LOCK=$(navegador cluster lock acquire "ingest-lock" --ttl 300)

# ... run your operations ...

# release the lock
navegador cluster lock release $LOCK
```

Locks have a TTL (seconds) and release automatically if the holder crashes.

---

## Messaging

Workers and agents can exchange messages via the cluster bus:

```bash
# publish a message to a channel
navegador cluster publish "ingest.complete" '{"repo": "myorg/myrepo", "nodes": 12450}'

# subscribe to a channel (blocks; prints messages as they arrive)
navegador cluster subscribe "ingest.complete"
```

Useful for triggering downstream steps (e.g., notify agents that a fresh ingest is ready) without polling.

---

## Observability

### Cluster metrics

```bash
navegador cluster metrics
```

Output:

```
Graph
  Nodes:          47,231
  Edges:         189,043
  Last ingest:   2026-03-23T14:22:11Z (worker-2)

Workers (3 online)
  worker-1        idle         last seen 4s ago
  worker-2        idle         last seen 2s ago
  ci-runner-7     processing   task: ingest ./src/payments

Queue
  Pending:   0
  Active:    1
  Completed: 847 (last 24h)
  Failed:    2 (last 24h)
```

### Logs

Workers emit structured JSON logs. Stream them:

```bash
navegador cluster logs --follow
navegador cluster logs --worker worker-2 --since 1h
```

### Health check

```bash
navegador cluster health
# exits 0 if healthy, 1 if degraded, 2 if unavailable
```

Suitable for use in load balancer health checks and PagerDuty integrations.

---

## Configuration reference

All cluster settings can be set in `navegador.toml` or as environment variables:

| Setting | Env var | Default | Description |
|---|---|---|---|
| `redis_url` | `NAVEGADOR_REDIS_URL` | `redis://localhost:6379` | Redis connection URL |
| `graph_name` | `NAVEGADOR_GRAPH_NAME` | `navegador` | FalkorDB graph name |
| `node_id` | `NAVEGADOR_NODE_ID` | auto | Unique identifier for this worker |
| `lock_ttl` | `NAVEGADOR_LOCK_TTL` | `300` | Default lock TTL in seconds |
| `worker_poll_interval` | `NAVEGADOR_POLL_INTERVAL` | `2` | Queue poll interval in seconds |
| `snapshot_dir` | `NAVEGADOR_SNAPSHOT_DIR` | `.navegador/snapshots` | Local snapshot directory |
