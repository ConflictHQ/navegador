# Navegador

**The project knowledge graph for AI coding agents.**

Navegador builds and maintains a queryable graph of your software project — combining static code analysis with human-curated business knowledge — so that AI coding agents always have precise, structured context instead of raw file dumps.

> *navegador* — Spanish for *navigator / sailor*. It helps agents navigate your code.
>
> **Current version: 0.7.0**

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
              stored in FalkorDB  (SQLite local / Redis prod)
```

The **code layer** is populated automatically by ingesting source trees. 13 languages are supported via tree-sitter (Python, TypeScript, JavaScript, Go, Rust, Java, Kotlin, C#, PHP, Ruby, Swift, C, C++). The **knowledge layer** is populated by manual curation (`navegador add`), GitHub wiki ingestion, and [Planopticon](guide/planopticon.md) output (meeting and video knowledge graphs).

---

## Quick start

```bash
pip install navegador              # Python 3.12+ required
navegador ingest ./my-repo         # parse + index the codebase
navegador explain AuthService      # what is this thing?
navegador search "rate limit" --all  # search code + knowledge together
```

Or use the Python SDK:

```python
from navegador import Navegador

nav = Navegador(".navegador/navegador.db")
nav.ingest("./my-repo")
bundle = nav.explain("AuthService")
print(bundle.to_markdown())
```

---

## What goes in the graph

| Layer | Node type | Populated by |
|---|---|---|
| Code | Repository, File, Module | `navegador ingest` |
| Code | Class, Function, Method | `navegador ingest` (tree-sitter AST) |
| Code | Decorator, Import, Variable | `navegador ingest` |
| Code | CALLS / INHERITS edges | `navegador ingest` (call graph analysis) |
| Knowledge | Concept, Domain | `navegador add concept` / `add domain` |
| Knowledge | Rule | `navegador add rule` |
| Knowledge | Decision | `navegador add decision` |
| Knowledge | Person | `navegador add person` |
| Knowledge | WikiPage | `navegador wiki ingest` |
| Knowledge | (any) | `navegador planopticon ingest` |
| Cross-layer | ANNOTATES, GOVERNS, IMPLEMENTS | `navegador annotate` |
| Analysis | TESTS, COUPLED_WITH edges | `navegador testmap`, `navegador cycles` |

---

## Agent integration

=== "CLI"

    The simplest integration: call `navegador explain` or `navegador context` from any shell script or agent tool definition.

    ```bash
    # get context for the file the agent just edited
    navegador context src/auth/service.py --format json

    # look up a function before editing it
    navegador function validate_token --depth 2 --format json

    # find everything annotated with a business concept
    navegador concept PaymentProcessing --format json
    ```

=== "MCP"

    Run navegador as a Model Context Protocol server. Configure it once in your agent settings and all navegador commands become callable tools with structured input/output.

    ```json
    {
      "mcpServers": {
        "navegador": {
          "command": "navegador",
          "args": ["mcp"]
        }
      }
    }
    ```

    11 tools available. See [MCP Integration](guide/mcp-integration.md) for the full tool list and per-agent config snippets. Use `--read-only` mode to restrict agents to query-only access.

=== "Bootstrap"

    One command to install navegador, ingest a repo, and wire the agent hook for your preferred AI coding assistant.

    ```bash
    ./bootstrap.sh --repo owner/repo --wiki --agent claude
    ```

    Supports `--agent claude`, `--agent gemini`, and `--agent openai`. See [Agent Hooks](guide/agent-hooks.md) for what the hook does and how to configure it manually.

=== "Editor integration"

    Wire navegador into your editor with one command:

    ```bash
    navegador editor setup claude-code
    navegador editor setup cursor
    navegador editor setup codex
    navegador editor setup windsurf
    ```

=== "CI/CD"

    Run navegador in CI pipelines for automated context checks:

    ```bash
    navegador ci ingest
    navegador ci stats
    navegador ci check
    ```

---

## What's new in 0.7.0

| Feature | Command / API |
|---|---|
| **13 languages** (added Kotlin, C#, PHP, Ruby, Swift, C, C++) | `pip install "navegador[languages]"` |
| **Python SDK** | `from navegador import Navegador` |
| **Incremental ingestion** | `navegador ingest --incremental`, `--watch` |
| **Schema migrations** | `navegador migrate` |
| **Export / import** | `navegador export`, `navegador import` (JSONL) |
| **Editor integrations** | `navegador editor setup <editor>` |
| **Analysis commands** | `navegador diff`, `navegador churn`, `navegador impact`, `navegador trace`, `navegador deadcode`, `navegador cycles`, `navegador testmap` |
| **Multi-repo** | `navegador repo add/list/ingest-all/search` |
| **Semantic search** | `navegador semantic-search`, `navegador ask` |
| **Framework enrichment** | Django, FastAPI, React, Rails, Spring Boot, Laravel, and more |
| **Monorepo support** | Turborepo, Nx, Yarn, pnpm, Cargo, Go workspaces |
| **Cluster mode** | Shared Redis graph, pub/sub, task queue, sessions |
| **11 MCP tools** (was 7) | `get_rationale`, `find_owners`, `search_knowledge`, `blast_radius` added |
| **Sensitive content redaction** | `navegador ingest --redact` |
| **Shell completions** | `navegador completions bash/zsh/fish` |

---

## License

Navegador is open source under the [MIT License](https://github.com/ConflictHQ/navegador/blob/main/LICENSE). Copyright 2026 CONFLICT LLC.
