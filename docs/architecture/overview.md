# Architecture Overview

## Design philosophy

Navegador is built around a single observation: **code structure and business knowledge are both graph-shaped, and they belong in the same graph.**

Most context tools for AI agents handle one or the other — either they parse code (AST tools, code search) or they surface docs (RAG over wikis, ADR files). Navegador stores both in the same property graph and connects them with typed edges. An agent asking "what does `process_payment` do?" gets back not just the function signature and call graph, but the business rules that govern it and the architectural decision that shaped its design — in a single structured query.

---

## Two-layer design

```
┌──────────────────────────────────────────────────────────────────┐
│  KNOWLEDGE LAYER                                                 │
│                                                                  │
│   Domain ──BELONGS_TO── Concept ──RELATED_TO── Rule             │
│     │                      │                     │              │
│     └──BELONGS_TO── Decision              GOVERNS │              │
│                      │                           ↓              │
│                   DECIDED_BY── Person         (code nodes)      │
│                                                                  │
│   WikiPage ──DOCUMENTS── Concept                                 │
│                                                                  │
├──────────────── ANNOTATES / GOVERNS / IMPLEMENTS ────────────────┤
│                                                                  │
│  CODE LAYER                                                      │
│                                                                  │
│   Repository ──CONTAINS── File ──CONTAINS── Class                │
│                              │               │                  │
│                           CONTAINS        DEFINES               │
│                              ↓               ↓                  │
│                           Function ──CALLS── Function            │
│                              │                                   │
│                         DECORATES── Decorator                    │
│                              │                                   │
│                           IMPORTS── Import                       │
└──────────────────────────────────────────────────────────────────┘
```

### Code layer

Populated automatically by `navegador ingest`. Contains the structural facts extracted from source code: which functions exist, what they call, which classes inherit from which, what decorators are applied. This layer changes whenever code changes; re-ingest is the refresh mechanism.

### Knowledge layer

Populated by humans (via `navegador add`) or semi-automatically (via wiki and Planopticon ingestion). Contains the *why*: business concepts, architectural rules, recorded decisions, domain ownership, and documentation. This layer changes slowly and deliberately.

### Cross-layer edges

| Edge | Meaning | Direction |
|---|---|---|
| `ANNOTATES` | A knowledge node describes a code node | Concept/Rule → Function/Class/File |
| `GOVERNS` | A rule applies to a code node | Rule → Function/Class |
| `IMPLEMENTS` | A code node implements a concept or interface | Function/Class → Concept/Interface |
| `DOCUMENTS` | A wiki page documents a concept or code node | WikiPage → Concept/Function/Class |

These edges are created explicitly via `navegador annotate` or inferred during wiki/Planopticon ingestion when names match.

---

## FalkorDB as the store

Navegador uses [FalkorDB](https://www.falkordb.com/) — a property graph database with a Cypher query interface.

| Environment | Backend | Install |
|---|---|---|
| Local dev | `falkordblite` (SQLite) | Included in `pip install navegador` |
| Production / team | FalkorDB on Redis | `pip install "navegador[redis]"` |

Both backends implement the same `GraphStore` interface. The query path is identical; only the connection setup differs. The SQLite backend uses an embedded engine — no daemon, no port, just a `.db` file.

---

## Ingestion pipeline

```
Source code (Python · TypeScript · JavaScript · Go · Rust · Java)
          │
          ▼
    tree-sitter parser (per-language grammar)
          │
          ▼
    AST visitor (extract nodes + relationships)
          │
          ▼
    GraphStore.merge_node / create_edge
          │
          ▼
    FalkorDB (SQLite or Redis)
```

```
Human curation (navegador add)
Wiki pages (navegador wiki ingest)
Planopticon output (navegador planopticon ingest)
          │
          ▼
    KnowledgeIngester / WikiIngester / PlanopticonIngester
          │
          ▼
    GraphStore.merge_node / create_edge
          │
          ▼
    FalkorDB (same database)
```

All ingesters write to the same graph. There is no separate code database and knowledge database.

---

## Query path

```
User / agent
     │
     ▼  CLI command or MCP tool call
navegador context / function / explain / search / ...
     │
     ▼
ContextLoader (builds Cypher query)
     │
     ▼
GraphStore.query(cypher)
     │
     ▼
FalkorDB (SQLite or Redis)
     │
     ▼
ContextBundle (structured result)
     │
     ▼
JSON / Markdown / rich terminal output
```

`ContextLoader` is the query abstraction layer. Each command (`function`, `class`, `explain`, etc.) corresponds to a `ContextLoader` method that constructs the appropriate Cypher query, fetches results, and assembles a `ContextBundle`. The CLI formats the bundle for output; the MCP server returns it as JSON.
