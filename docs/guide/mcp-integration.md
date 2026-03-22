# MCP Integration

Navegador ships a built-in [Model Context Protocol](https://modelcontextprotocol.io) server. When running in MCP mode, all navegador commands become callable tools that agents can invoke with structured input and receive structured output.

---

## CLI vs MCP: when to use which

The primary interface for agents is the **CLI**, not MCP. Here's why:

| | CLI | MCP |
|---|---|---|
| Token cost | Low — agent calls a shell tool, gets back only what it asked for | Higher — MCP tool calls involve protocol overhead |
| Setup | None beyond installing navegador | Requires MCP config in agent settings |
| Best for | Agent hooks, shell scripts, CI | Interactive sessions in Claude / Cursor |
| Output formats | JSON, markdown, rich terminal | Structured JSON always |

Use **MCP** when you want navegador tools available as first-class tool calls in an interactive Claude or Cursor session. Use the **CLI** (via agent hooks) for automated background sync and pre-edit context loading.

---

## Starting the MCP server

```bash
navegador mcp
```

With a custom database path:

```bash
navegador mcp --db .navegador/navegador.db
```

The server speaks MCP over stdio. It does not bind a port.

---

## Agent configuration

=== "Claude Code"

    Add to your project's `.claude/settings.json`:

    ```json
    {
      "mcpServers": {
        "navegador": {
          "command": "navegador",
          "args": ["mcp"],
          "env": {
            "NAVEGADOR_DB": ".navegador/navegador.db"
          }
        }
      }
    }
    ```

=== "Claude Desktop"

    Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

    ```json
    {
      "mcpServers": {
        "navegador": {
          "command": "navegador",
          "args": ["mcp", "--db", "/path/to/project/.navegador/navegador.db"]
        }
      }
    }
    ```

=== "Cursor"

    Add to `.cursor/mcp.json` in your project root:

    ```json
    {
      "mcpServers": {
        "navegador": {
          "command": "navegador",
          "args": ["mcp"],
          "env": {
            "NAVEGADOR_DB": ".navegador/navegador.db"
          }
        }
      }
    }
    ```

---

## Available MCP tools

All tools accept and return JSON.

| Tool | Equivalent CLI | Description |
|---|---|---|
| `ingest` | `navegador ingest` | Ingest a repo into the graph |
| `context` | `navegador context` | File-level context bundle |
| `function` | `navegador function` | Function with call graph |
| `class` | `navegador class` | Class with hierarchy |
| `explain` | `navegador explain` | Universal node lookup |
| `search` | `navegador search` | Text search across graph |
| `query` | `navegador query` | Raw Cypher passthrough |

### Tool input schemas

**ingest**
```json
{ "path": "./repo", "clear": false }
```

**context**
```json
{ "file": "src/auth/service.py", "format": "json" }
```

**function**
```json
{ "name": "validate_token", "file": "src/auth/service.py", "depth": 2 }
```

**class**
```json
{ "name": "PaymentProcessor", "file": "src/payments/processor.py" }
```

**explain**
```json
{ "name": "AuthService", "file": "src/auth/service.py" }
```

**search**
```json
{ "query": "rate limit", "all": true, "docs": false, "limit": 20 }
```

**query**
```json
{ "cypher": "MATCH (f:Function) RETURN f.name LIMIT 10" }
```

---

## When MCP makes sense

- You are in an interactive Claude or Cursor session and want to call `explain`, `search`, or `function` without dropping to a terminal
- You want navegador tools auto-discovered by the agent without writing custom tool definitions
- You are building an agent workflow that dynamically queries the graph mid-task

For automated background tasks (re-ingest on file save, sync on pull), use the CLI via [agent hooks](agent-hooks.md) instead.
