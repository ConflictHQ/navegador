# Navegador

**Your codebase + everything your team knows about it — in one queryable graph.**

Navegador parses your source code into a property graph and layers your team's knowledge on top: decisions, concepts, rules, people, wiki pages, and meeting outputs. AI coding agents get structured, precise context instead of raw file dumps.

> *navegador* — Spanish for *navigator / sailor*

[![CI](https://github.com/ConflictHQ/navegador/actions/workflows/ci.yml/badge.svg)](https://github.com/ConflictHQ/navegador/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/navegador)](https://pypi.org/project/navegador/)
[![Python](https://img.shields.io/pypi/pyversions/navegador)](https://pypi.org/project/navegador/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Docs](https://img.shields.io/badge/docs-navegador.dev-blue)](https://navegador.dev)

---

## Two layers, one graph

```
┌─────────────────────────────────────────────────────────────────┐
│  KNOWLEDGE LAYER                                                │
│  Concepts · Rules · Decisions · WikiPages · People · Domains   │
│                                                                 │
│         ↕  GOVERNS / IMPLEMENTS / DOCUMENTS / ANNOTATES        │
│                                                                 │
│  CODE LAYER                                                     │
│  Repository · File · Module · Class · Function · Method        │
│  Variable · Import · Decorator · (call graphs, hierarchies)    │
└─────────────────────────────────────────────────────────────────┘
              stored in FalkorDB  (SQLite local · Redis prod)
```

The **code layer** is built automatically by ingesting source trees. The **knowledge layer** is populated by your team — manually, via wiki ingestion, or from [PlanOpticon](https://github.com/ConflictHQ/PlanOpticon) meeting analysis output.

---

## Quick start

```bash
pip install navegador

# Ingest your repo
navegador ingest ./myrepo

# Load context for a file
navegador context src/auth.py

# Search across code + knowledge
navegador search "rate limit" --all

# Explain a symbol
navegador explain AuthService

# Check graph stats
navegador stats
```

---

## MCP integration

Add to your Claude / Cursor / Gemini MCP config:

```json
{
  "mcpServers": {
    "navegador": {
      "command": "navegador",
      "args": ["mcp", "--db", ".navegador/graph.db"]
    }
  }
}
```

Available MCP tools:

| Tool | Description |
|------|-------------|
| `ingest_repo` | Parse and load a repo into the graph |
| `load_file_context` | All symbols in a file + their relationships |
| `load_function_context` | What a function calls and what calls it |
| `load_class_context` | Class methods, inheritance, subclasses |
| `search_symbols` | Fuzzy search for functions/classes by name |
| `query_graph` | Raw Cypher passthrough (with security hardening) |
| `graph_stats` | Node and edge counts |
| `get_rationale` | Decision rationale, alternatives, and status |
| `find_owners` | People assigned to any node |
| `search_knowledge` | Search concepts, rules, decisions, wiki |
| `blast_radius` | Impact analysis — what's affected by a change |

---

## Knowledge layer

Beyond code structure, navegador stores what your team knows:

```bash
# Record an architectural decision
navegador add decision "Use FalkorDB for graph storage" \
  --rationale "Cypher queries, SQLite-backed zero-infra mode"

# Define a business concept and link it to code
navegador add concept PaymentProcessing
navegador annotate PaymentProcessing --function process_charge

# Add a rule
navegador add rule "All writes must go through the service layer"

# Ingest your GitHub wiki
navegador wiki ingest --repo myorg/myrepo

# Import PlanOpticon meeting analysis
navegador planopticon ingest ./meeting-output/
```

---

## Graph schema

**Code nodes:** `Repository` · `File` · `Module` · `Class` · `Function` · `Method` · `Variable` · `Import` · `Decorator`

**Knowledge nodes:** `Concept` · `Rule` · `Decision` · `Person` · `Domain` · `WikiPage`

**Edges:** `CONTAINS` · `DEFINES` · `IMPORTS` · `CALLS` · `INHERITS` · `REFERENCES` · `DEPENDS_ON` · `GOVERNS` · `IMPLEMENTS` · `DOCUMENTS` · `ANNOTATES`

---

## Storage

| Mode | Backend | When to use |
|------|---------|-------------|
| Default | `falkordblite` (SQLite) | Local dev, zero infrastructure |
| Production | Redis + FalkorDB module | Shared deployments, agent swarms |

```python
from navegador.graph import GraphStore

store = GraphStore.sqlite(".navegador/graph.db")   # default
store = GraphStore.redis("redis://localhost:6379")  # production
```

---

## Language support

| Language | Status |
|----------|--------|
| Python | ✅ |
| TypeScript / JavaScript | ✅ |
| Go | ✅ |
| Rust | ✅ |
| Java | ✅ |
| Kotlin | ✅ |
| C# | ✅ |
| PHP | ✅ |
| Ruby | ✅ |
| Swift | ✅ |
| C / C++ | ✅ |

---

## Framework enrichment

After ingesting code, navegador can promote generic AST nodes to framework-specific semantic types:

```bash
navegador enrich                          # auto-detect frameworks
navegador enrich --framework django       # target a specific framework
```

Supported frameworks: **Django**, **FastAPI**, **React / Next.js**, **Express.js**, **React Native**, **Rails**, **Spring Boot**, **Laravel**

---

## Structural analysis

```bash
navegador impact AuthService --depth 3    # blast radius
navegador trace handle_request            # execution flow from entry point
navegador deadcode                        # unreachable functions/classes
navegador cycles                          # circular dependencies
navegador testmap                         # link tests to production code
navegador diff                            # map uncommitted changes to graph
navegador churn .                         # behavioural coupling from git history
```

---

## Intelligence layer

```bash
navegador semantic-search "authentication flow"   # embedding-based search
navegador communities                              # detect code communities
navegador ask "what calls the payment service?"    # natural language queries
navegador docs src/auth.py                         # generate documentation
```

Requires an LLM provider: `pip install navegador[llm]`

---

## Python SDK

```python
from navegador import Navegador

nav = Navegador.sqlite(".navegador/graph.db")
nav.ingest("./myrepo")
nav.add_concept("Payment", description="Payment processing", domain="billing")

results = nav.search("auth")
bundle = nav.explain("AuthService")
owners = nav.find_owners("AuthService")
```

---

## Cluster mode (agent swarms)

For multi-agent setups sharing a Redis-backed graph:

```bash
navegador init --redis redis://host:6379 --cluster
```

Features: shared graph with local snapshots, pub/sub notifications, task queues, distributed locking, session namespacing, checkpoints, agent messaging, observability dashboard.

---

## Additional integrations

```bash
navegador codeowners ./myrepo             # parse CODEOWNERS → ownership graph
navegador adr ingest docs/decisions/      # Architecture Decision Records
navegador api ingest openapi.yaml         # OpenAPI / GraphQL schemas
navegador deps ingest package.json        # external dependency tracking
navegador pm ingest --github org/repo     # GitHub issues → knowledge graph
navegador editor setup claude-code        # generate MCP config for editors
navegador explore                         # browser-based graph visualization
```

---

## Installation

### PyPI

```bash
pip install navegador
```

### Standalone binaries

No Python required — download prebuilt binaries from [GitHub Releases](https://github.com/ConflictHQ/navegador/releases):

| Platform | Binary |
|----------|--------|
| macOS (Apple Silicon) | `navegador-macos-arm64` |
| macOS (Intel) | `navegador-macos-x86_64` |
| Linux | `navegador-linux-x86_64` |
| Windows | `navegador-windows-x86_64.exe` |

### From source

```bash
git clone https://github.com/ConflictHQ/navegador.git
cd navegador
pip install -e ".[dev]"
pytest
```

---

## Contributing

See [CONTRIBUTING.md](.github/CONTRIBUTING.md). Bug reports and feature requests welcome via [GitHub Issues](https://github.com/ConflictHQ/navegador/issues).

---

## License

MIT — [CONFLICT](https://weareconflict.com)
