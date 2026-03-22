# Navegador

**The project knowledge graph for AI coding agents.**

Navegador builds and maintains a queryable graph of your software project — combining static code analysis with human-curated business knowledge — so that AI coding agents always have precise, structured context instead of raw file dumps.

> *navegador* — Spanish for *navigator / sailor*. It helps agents navigate your code.

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

The **code layer** is populated automatically by ingesting source trees. Python and TypeScript are supported out of the box via tree-sitter. The **knowledge layer** is populated by manual curation (`navegador add`), GitHub wiki ingestion, and [Planopticon](guide/planopticon.md) output (meeting and video knowledge graphs).

---

## Quick start

```bash
pip install navegador              # Python 3.12+ required
navegador ingest ./my-repo         # parse + index the codebase
navegador explain AuthService      # what is this thing?
navegador search "rate limit" --all  # search code + knowledge together
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

    See [MCP Integration](guide/mcp-integration.md) for the full tool list and per-agent config snippets.

=== "Bootstrap"

    One command to install navegador, ingest a repo, and wire the agent hook for your preferred AI coding assistant.

    ```bash
    ./bootstrap.sh --repo owner/repo --wiki --agent claude
    ```

    Supports `--agent claude`, `--agent gemini`, and `--agent openai`. See [Agent Hooks](guide/agent-hooks.md) for what the hook does and how to configure it manually.

---

## License

Navegador is open source under the [MIT License](https://github.com/ConflictHQ/navegador/blob/main/LICENSE). Copyright 2026 CONFLICT LLC.
