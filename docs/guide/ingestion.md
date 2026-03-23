# Ingesting a Repo

Navegador builds the graph from four sources: code, manual knowledge curation, GitHub wikis, and Planopticon knowledge graph output.

---

## Code ingestion

```bash
navegador ingest ./repo
```

### What gets extracted

Navegador walks all source files in the repo and uses tree-sitter to extract structure. Supported languages:

| Extension(s) | Language | Extra |
|---|---|---|
| `.py` | Python | — |
| `.ts`, `.tsx` | TypeScript | — |
| `.js`, `.jsx` | JavaScript | — |
| `.go` | Go | — |
| `.rs` | Rust | — |
| `.java` | Java | — |
| `.kt`, `.kts` | Kotlin | `[languages]` |
| `.cs` | C# | `[languages]` |
| `.php` | PHP | `[languages]` |
| `.rb` | Ruby | `[languages]` |
| `.swift` | Swift | `[languages]` |
| `.c`, `.h` | C | `[languages]` |
| `.cpp`, `.cc`, `.cxx`, `.hpp` | C++ | `[languages]` |

Install extended language support:

```bash
pip install "navegador[languages]"
```

The following directories are always skipped: `.git`, `.venv`, `venv`, `node_modules`, `__pycache__`, `dist`, `build`, `.next`, `target` (Rust/Java builds), `vendor` (Go modules), `.gradle`.

### What gets extracted

| What | Graph nodes / edges created |
|---|---|
| Files | `File` node; `CONTAINS` edge from `Repository` |
| Classes, structs, interfaces | `Class` node with `name`, `file`, `line`, `docstring` |
| Functions and methods | `Function` / `Method` nodes with `name`, `docstring`, `line` |
| Imports / use declarations | `Import` node; `IMPORTS` edge from the importing file |
| Call relationships | `CALLS` edges between functions based on static call analysis |
| Inheritance | `INHERITS` edges from subclass to parent |

Doc comment formats supported per language: Python docstrings, JSDoc (`/** */`), Rust `///`, Java Javadoc.

### Options

| Flag | Effect |
|---|---|
| `--clear` | Wipe the graph before ingesting (full rebuild) |
| `--incremental` | Only reprocess files whose content hash has changed |
| `--watch` | Keep running and re-ingest on file changes |
| `--redact` | Strip secrets (tokens, passwords, keys) from string literals |
| `--monorepo` | Traverse workspace sub-packages (Turborepo, Nx, Yarn, pnpm, Cargo, Go) |
| `--json` | Output a JSON summary of nodes and edges created |
| `--db <path>` | Use a specific database file |

### Re-ingesting

Re-run `navegador ingest` anytime to pick up changes. Nodes are upserted by identity (file path + name), so repeated ingestion is idempotent for unchanged nodes. Use `--incremental` for large repos to skip unchanged files. Use `--clear` when you need a clean slate (e.g., after a large rename refactor).

### Incremental ingestion

`--incremental` uses SHA-256 content hashing to skip files that haven't changed since the last ingest. The hash is stored on each `File` node. On large repos this can reduce ingest time by 90%+ after the initial run.

```bash
navegador ingest ./repo --incremental
```

### Watch mode

`--watch` starts a file-system watcher and automatically re-ingests any file that changes:

```bash
navegador ingest ./repo --watch
```

Press `Ctrl-C` to stop. Watch mode uses `--incremental` automatically.

### Sensitive content redaction

`--redact` scans string literals for patterns that look like API keys, tokens, and passwords, and replaces their values with `[REDACTED]` in the graph. Source files are never modified.

```bash
navegador ingest ./repo --redact
```

### Monorepo support

`--monorepo` detects the workspace type and traverses all sub-packages:

```bash
navegador ingest ./monorepo --monorepo
```

Supported workspace formats: Turborepo, Nx, Yarn workspaces, pnpm workspaces, Cargo workspaces, Go workspaces.

---

## Knowledge curation

Manual knowledge is added with `navegador add` commands and linked to code with `navegador annotate`.

### Concepts

A concept is a named idea or design pattern relevant to the codebase.

```bash
navegador add concept "Idempotency" \
  --desc "Operations safe to retry without side effects" \
  --domain Payments
```

### Rules

A rule is an enforceable constraint on code behaviour.

```bash
navegador add rule "RequireIdempotencyKey" \
  --desc "All write endpoints must accept an idempotency key header" \
  --domain Payments \
  --severity critical \
  --rationale "Prevents double-processing on client retries"
```

Severity values: `info`, `warning`, `critical`.

### Decisions

An architectural decision record (ADR) stored in the graph.

```bash
navegador add decision "UsePostgresForTransactions" \
  --desc "PostgreSQL is the primary datastore for transactional data" \
  --domain Infrastructure \
  --rationale "ACID guarantees required for financial data" \
  --alternatives "MySQL, CockroachDB" \
  --date 2025-03-01 \
  --status accepted
```

Status values: `proposed`, `accepted`, `deprecated`, `superseded`.

### People

```bash
navegador add person "Alice Chen" \
  --email alice@example.com \
  --role "Lead Engineer" \
  --team Payments
```

### Domains

Domains are top-level groupings for concepts, rules, and decisions.

```bash
navegador add domain "Payments" \
  --desc "Everything related to payment processing and billing"
```

### Annotating code

Link a code node to a concept or rule:

```bash
navegador annotate process_payment \
  --type Function \
  --concept Idempotency \
  --rule RequireIdempotencyKey
```

`--type` accepts: `Function`, `Class`, `Method`, `File`, `Module`.

This creates `ANNOTATES` edges between the knowledge nodes and the code node. The code node then appears in results for `navegador concept Idempotency` and `navegador explain process_payment`.

---

## Wiki ingestion

Pull a GitHub wiki into the graph as `WikiPage` nodes.

```bash
# ingest from GitHub API
navegador wiki ingest --repo myorg/myrepo --token $GITHUB_TOKEN

# ingest from a locally cloned wiki directory
navegador wiki ingest --dir ./myrepo.wiki

# force API mode (bypass auto-detection)
navegador wiki ingest --repo myorg/myrepo --api
```

Each wiki page becomes a `WikiPage` node with `title`, `content`, `url`, and `updated_at` properties. Pages are linked to relevant `Concept`, `Domain`, or `Function` nodes with `DOCUMENTS` edges where names match.

Set `GITHUB_TOKEN` in your environment to avoid rate limits and to access private wikis.

---

## Planopticon ingestion

[Planopticon](planopticon.md) is a video/meeting knowledge extraction tool. It produces structured knowledge graph output that navegador can ingest directly.

```bash
navegador planopticon ingest ./meeting-output/ --type auto
```

See the [Planopticon guide](planopticon.md) for the full input format reference and entity mapping details.
