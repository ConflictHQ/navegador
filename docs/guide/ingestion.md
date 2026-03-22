# Ingesting a Repo

Navegador builds the graph from four sources: code, manual knowledge curation, GitHub wikis, and Planopticon knowledge graph output.

---

## Code ingestion

```bash
navegador ingest ./repo
```

### What gets extracted

Navegador walks every `.py` and `.ts` / `.tsx` file and uses tree-sitter to extract:

| What | Graph nodes / edges created |
|---|---|
| Files and modules | `File`, `Module` nodes; `CONTAINS` edges from `Repository` |
| Classes | `Class` node with `name`, `file`, `line`, `docstring` |
| Functions and methods | `Function` / `Method` nodes with `name`, `signature`, `docstring`, `line` |
| Decorators | `Decorator` node; `DECORATES` edge to the decorated function/class |
| Imports | `Import` node; `IMPORTS` edge from the importing file |
| Call relationships | `CALLS` edges between functions based on static call analysis |
| Inheritance | `INHERITS` edges from subclass to parent; `IMPLEMENTS` for interfaces |
| Variables (module-level) | `Variable` nodes |

### Options

| Flag | Effect |
|---|---|
| `--clear` | Wipe the graph before ingesting (full rebuild) |
| `--json` | Output a JSON summary of nodes and edges created |
| `--db <path>` | Use a specific database file |

### Re-ingesting

Re-run `navegador ingest` anytime to pick up changes. Nodes are upserted by identity (file path + name), so repeated ingestion is idempotent for unchanged nodes. Use `--clear` when you need a clean slate (e.g., after a large rename refactor).

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
