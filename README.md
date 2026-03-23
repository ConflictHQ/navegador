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
| `query_graph` | Raw Cypher passthrough |
| `graph_stats` | Node and edge counts |

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
