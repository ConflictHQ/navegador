# Architecture Overview

## Design philosophy

Navegador is built around a single observation: **code structure and business knowledge are both graph-shaped, and they belong in the same graph.**

Most context tools for AI agents handle one or the other — either they parse code (AST tools, code search) or they surface docs (RAG over wikis, ADR files). Navegador stores both in the same property graph and connects them with typed edges. An agent asking "what does `process_payment` do?" gets back not just the function signature and call graph, but the business rules that govern it and the architectural decision that shaped its design — in a single structured query.

---

## Architecture layers

```
┌──────────────────────────────────────────────────────────────────┐
│  INTELLIGENCE LAYER                                              │
│  LLM providers (Anthropic · OpenAI · Ollama)                    │
│  Semantic search · NLP queries · Doc generation                  │
├──────────────────────────────────────────────────────────────────┤
│  ANALYSIS LAYER                                                  │
│  Impact · Trace · Churn · Deadcode · Cycles · Testmap            │
│  Diff · Rename · Communities · Blast radius                      │
├──────────────────────────────────────────────────────────────────┤
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
│                              │               │                  │
│                         DECORATES─Decorator  TESTS──TestFn       │
│                              │                                   │
│                           IMPORTS── Import                       │
├──────────────────────────────────────────────────────────────────┤
│  ENRICHMENT LAYER                                                │
│  Framework metadata (Django · FastAPI · React · Rails · Spring)  │
│  VCS (Git · Fossil) · CODEOWNERS · ADRs · OpenAPI · GraphQL      │
├──────────────────────────────────────────────────────────────────┤
│  STORE LAYER                                                     │
│  FalkorDB (falkordblite SQLite local / Redis cluster)           │
└──────────────────────────────────────────────────────────────────┘
```

### Code layer

Populated automatically by `navegador ingest`. Contains the structural facts extracted from source code across 13 languages: which functions exist, what they call, which classes inherit from which, what decorators are applied. Supports incremental ingestion (content hashing), watch mode, and parallel processing. This layer changes whenever code changes; re-ingest is the refresh mechanism.

### Knowledge layer

Populated by humans (via `navegador add`) or semi-automatically (via wiki, Planopticon, ADR, OpenAPI, and PM ingestion). Contains the *why*: business concepts, architectural rules, recorded decisions, domain ownership, and documentation. This layer changes slowly and deliberately.

### Enrichment layer

Framework enrichers run after AST parsing to add framework-specific metadata — Django model field types, FastAPI route paths, React component display names, etc. VCS adapters (Git, Fossil) provide blame, history, and churn data. CODEOWNERS files are parsed to populate `Person`→`File` ownership edges.

### Analysis layer

Graph analysis commands operate over the populated code and knowledge layers without additional ingestion. They run Cypher traversals for impact analysis, cycle detection, dead code detection, test mapping, and community detection.

### Intelligence layer

NLP and LLM commands (`navegador ask`, `navegador semantic-search`, `navegador docs`) use configurable LLM providers (Anthropic, OpenAI, Ollama) to answer natural language queries grounded in graph data.

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
Source code (13 languages via tree-sitter)
          │
          ▼
    tree-sitter parser (per-language grammar)
    + incremental parsing (LRU cache, content hashing)
    + parallel ingestion (worker pool)
          │
          ▼
    AST visitor (extract nodes + relationships)
          │
          ▼
    Framework enrichers (Django · FastAPI · React · Rails · Spring · Laravel · …)
          │
          ▼
    Graph diffing (only write changed nodes/edges)
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
ADRs (navegador adr ingest)
OpenAPI / GraphQL schemas (navegador api ingest)
PM issues (navegador pm ingest --github)
External deps (navegador deps ingest)
Submodules (navegador submodules ingest)
          │
          ▼
    KnowledgeIngester / WikiIngester / PlanopticonIngester / …
          │
          ▼
    GraphStore.merge_node / create_edge
          │
          ▼
    FalkorDB (same database)
```

All ingesters write to the same graph. There is no separate code database and knowledge database.

---

## Query and analysis path

```
User / agent
     │
     ▼  CLI command, MCP tool call, or Python SDK
navegador context / function / explain / search / impact / trace / ...
     │
     ▼
ContextLoader / AnalysisEngine (builds Cypher query)
     │
     ▼
GraphStore.query(cypher)          ← MCP: query validation + complexity check
     │
     ▼
FalkorDB (SQLite or Redis)
     │
     ▼
ContextBundle / AnalysisResult (structured result)
     │
     ▼
JSON / Markdown / rich terminal output
```

`ContextLoader` handles context retrieval commands (`function`, `class`, `explain`, etc.). `AnalysisEngine` handles graph analysis commands (`impact`, `trace`, `deadcode`, `cycles`, `churn`, `testmap`). Both construct Cypher queries, fetch results from `GraphStore`, and return structured output. The CLI formats output for the terminal; the MCP server returns JSON; the Python SDK returns typed Python objects.

---

## Cluster architecture

For team and CI environments, navegador supports a cluster mode backed by a shared Redis graph:

```
Multiple agents / CI runners
          │
          ▼
    navegador (each instance)
          │
          ▼
    ┌────────────────────────────────────────┐
    │  Shared Redis                          │
    │  ├── FalkorDB graph (shared state)     │
    │  ├── Pub/sub (event broadcast)         │
    │  ├── Task queue (ingest jobs)          │
    │  ├── Sessions (agent state)            │
    │  ├── Checkpoints (long-running tasks)  │
    │  └── Observability (metrics, traces)  │
    └────────────────────────────────────────┘
```

Configure with `[cluster]` in `.navegador/config.toml`. See [Configuration](../getting-started/configuration.md) for details.
