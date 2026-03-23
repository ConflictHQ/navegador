# CI/CD Integration

Navegador's `ci` subcommand is designed for non-interactive use in pipelines. All CI commands emit structured output and use exit codes that CI systems understand.

---

## CI commands

### `navegador ci ingest`

Ingest the repo and output a machine-readable summary. Exits non-zero on errors.

```bash
navegador ci ingest ./src
```

JSON output (always on in CI mode):

```json
{
  "status": "ok",
  "nodes_created": 1240,
  "nodes_updated": 38,
  "edges_created": 4821,
  "files_processed": 87,
  "errors": [],
  "duration_seconds": 4.2
}
```

### `navegador ci stats`

Print graph statistics as JSON. Use to track graph growth over time or assert a minimum coverage threshold.

```bash
navegador ci stats
```

```json
{
  "repositories": 1,
  "files": 87,
  "classes": 143,
  "functions": 891,
  "methods": 412,
  "concepts": 14,
  "rules": 9,
  "decisions": 6,
  "total_edges": 4821
}
```

### `navegador ci check`

Run assertion checks against the graph. Exits non-zero if any check fails.

```bash
navegador ci check
```

Checks run by default:

| Check | Condition for failure |
|---|---|
| `no-cycles` | Circular import chains detected |
| `min-coverage` | Functions with no tests below threshold |
| `critical-rules` | Code violates a `critical`-severity rule |
| `dead-code` | High-confidence dead code above threshold |

Configure checks in `navegador.toml`:

```toml
[ci.checks]
no-cycles = true
min-coverage = 60          # percent of functions with tests
critical-rules = true
dead-code = false          # disable dead-code check

[ci.thresholds]
dead_code_max = 10         # fail if more than 10 dead-code candidates
uncovered_max_percent = 40 # fail if more than 40% of functions lack tests
```

### Running specific checks

```bash
navegador ci check --only no-cycles
navegador ci check --only critical-rules,min-coverage
navegador ci check --skip dead-code
```

---

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Success — all checks passed |
| `1` | Check failure — one or more assertions failed |
| `2` | Ingest error — files could not be parsed (partial result) |
| `3` | Configuration error — bad flags or missing config |
| `4` | Connection error — cannot reach database or Redis |

---

## GitHub Actions

### Basic: ingest on push

```yaml
# .github/workflows/navegador.yml
name: navegador

on:
  push:
    branches: [main]
  pull_request:

jobs:
  graph:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Install navegador
        run: pip install navegador

      - name: Ingest
        run: navegador ci ingest ./src

      - name: Check
        run: navegador ci check
```

### With graph caching

Cache the SQLite database between runs to speed up incremental ingestion:

```yaml
      - name: Cache navegador graph
        uses: actions/cache@v4
        with:
          path: .navegador/navegador.db
          key: navegador-${{ runner.os }}-${{ hashFiles('src/**') }}
          restore-keys: navegador-${{ runner.os }}-

      - name: Ingest
        run: navegador ci ingest ./src

      - name: Check
        run: navegador ci check
```

### Shared graph via Redis (cluster mode)

Use a shared Redis instance for team-wide graph persistence across branches and PRs:

```yaml
      - name: Ingest to shared graph
        env:
          NAVEGADOR_REDIS_URL: ${{ secrets.NAVEGADOR_REDIS_URL }}
        run: |
          navegador ci ingest ./src --cluster
          navegador ci check --cluster
```

### PR impact report

Post an impact analysis comment on pull requests:

```yaml
      - name: Impact analysis
        if: github.event_name == 'pull_request'
        run: |
          CHANGED=$(git diff --name-only origin/main...HEAD | grep '\.py$' | head -20)
          for f in $CHANGED; do
            navegador ci ingest "$f"
          done
          navegador impact --changed-since origin/main --format json > impact.json

      - name: Comment impact
        if: github.event_name == 'pull_request'
        uses: actions/github-script@v7
        with:
          script: |
            const impact = require('./impact.json')
            const body = `## Navegador impact analysis\n\n${impact.summary}`
            github.rest.issues.createComment({
              issue_number: context.issue.number,
              owner: context.repo.owner,
              repo: context.repo.repo,
              body
            })
```

---

## Editor integration

### VS Code

Install the [Navegador VS Code extension](https://marketplace.visualstudio.com/items?itemName=ConflictHQ.navegador) for inline context overlays and on-save re-ingest.

Or configure a task in `.vscode/tasks.json` to run on save:

```json
{
  "version": "2.0.0",
  "tasks": [
    {
      "label": "Navegador: re-ingest on save",
      "type": "shell",
      "command": "navegador ingest ${file}",
      "group": "build",
      "presentation": {
        "reveal": "silent",
        "panel": "shared"
      },
      "runOptions": {
        "runOn": "folderOpen"
      }
    }
  ]
}
```

### Neovim

Add a post-write autocmd to trigger incremental ingest:

```lua
-- in your init.lua or a plugin config
vim.api.nvim_create_autocmd("BufWritePost", {
  pattern = { "*.py", "*.ts", "*.tsx", "*.js" },
  callback = function(ev)
    local file = ev.file
    vim.fn.jobstart({ "navegador", "ingest", file }, { detach = true })
  end,
})
```

### Pre-commit hook

Run checks before committing:

```yaml
# .pre-commit-config.yaml
repos:
  - repo: local
    hooks:
      - id: navegador-check
        name: Navegador graph checks
        entry: navegador ci check --only no-cycles,critical-rules
        language: system
        pass_filenames: false
        stages: [commit]
```

---

## Secrets and auth

The graph database path and Redis URL should come from environment variables in CI, not from committed config:

```bash
# CI environment variables
NAVEGADOR_DB=.navegador/navegador.db      # SQLite path
NAVEGADOR_REDIS_URL=redis://...            # Redis URL (cluster mode)
GITHUB_TOKEN=ghp_...                       # for wiki ingestion
```

Set these as [GitHub Actions secrets](https://docs.github.com/en/actions/security-guides/using-secrets-in-github-actions) and reference them in your workflow with `${{ secrets.NAME }}`.
