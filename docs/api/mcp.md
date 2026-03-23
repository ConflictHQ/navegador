# MCP Server API

Navegador exposes all context-loading commands as an MCP server. The server is created with `create_mcp_server()` and speaks the [Model Context Protocol](https://modelcontextprotocol.io) over stdio.

```python
from navegador.mcp import create_mcp_server
```

---

## create_mcp_server

```python
def create_mcp_server(
    store: GraphStore | None = None,
    db_path: str = "",
    read_only: bool = False,
    max_query_complexity: int = 0,
) -> MCPServer: ...
```

Creates and returns an MCP server instance. If `store` is not provided, opens a `GraphStore.sqlite()` at `db_path` (or `NAVEGADOR_DB` env var, or `./navegador.db`).

- `read_only`: when `True`, disables the `ingest` tool and restricts the `query` tool to `MATCH`/`RETURN` only. Corresponds to `navegador mcp --read-only`.
- `max_query_complexity`: if non-zero, rejects Cypher queries whose estimated complexity exceeds this value. Prevents runaway traversals in multi-agent environments.

The CLI command `navegador mcp [--db path] [--read-only]` calls this function and runs the server loop.

### Usage

```python
from navegador.graph import GraphStore
from navegador.mcp import create_mcp_server

store = GraphStore.sqlite(".navegador/navegador.db")
server = create_mcp_server(store=store)
server.run()   # blocks; serves over stdio
```

To embed in a larger MCP server:

```python
server = create_mcp_server(db_path=".navegador/navegador.db")
# register additional tools on server before running
server.run()
```

---

## Available MCP tools

All tools accept JSON input and return JSON output. There are 11 tools in total.

---

### `ingest`

Ingest a repository or file into the graph.

**Input schema:**
```json
{
  "type": "object",
  "properties": {
    "path": {
      "type": "string",
      "description": "Path to the repo or file to ingest"
    },
    "clear": {
      "type": "boolean",
      "description": "Wipe the graph before ingesting",
      "default": false
    }
  },
  "required": ["path"]
}
```

**Output:** `IngestionResult` serialized to JSON.

---

### `context`

Return the full context bundle for a source file.

**Input schema:**
```json
{
  "type": "object",
  "properties": {
    "file": {
      "type": "string",
      "description": "Path to the source file"
    },
    "format": {
      "type": "string",
      "enum": ["json", "markdown"],
      "default": "json"
    }
  },
  "required": ["file"]
}
```

**Output:** `ContextBundle` as JSON or markdown string.

---

### `function`

Return a function's context bundle including callers, callees, and decorators.

**Input schema:**
```json
{
  "type": "object",
  "properties": {
    "name": {
      "type": "string",
      "description": "Function name"
    },
    "file": {
      "type": "string",
      "description": "Optional file path to disambiguate"
    },
    "depth": {
      "type": "integer",
      "description": "Call graph traversal depth",
      "default": 1
    }
  },
  "required": ["name"]
}
```

**Output:** `ContextBundle` as JSON.

---

### `class`

Return a class context bundle including hierarchy, methods, and references.

**Input schema:**
```json
{
  "type": "object",
  "properties": {
    "name": {
      "type": "string",
      "description": "Class name"
    },
    "file": {
      "type": "string",
      "description": "Optional file path to disambiguate"
    }
  },
  "required": ["name"]
}
```

**Output:** `ContextBundle` as JSON.

---

### `explain`

Universal lookup: explain any node (function, class, file, concept, rule, decision) by name.

**Input schema:**
```json
{
  "type": "object",
  "properties": {
    "name": {
      "type": "string",
      "description": "Node name to explain"
    },
    "file": {
      "type": "string",
      "description": "Optional file path to disambiguate code nodes"
    }
  },
  "required": ["name"]
}
```

**Output:** `ContextBundle` as JSON.

---

### `search`

Search the graph by text query.

**Input schema:**
```json
{
  "type": "object",
  "properties": {
    "query": {
      "type": "string",
      "description": "Search query"
    },
    "all": {
      "type": "boolean",
      "description": "Search all layers including knowledge layer",
      "default": false
    },
    "docs": {
      "type": "boolean",
      "description": "Search docstrings and wiki content only",
      "default": false
    },
    "limit": {
      "type": "integer",
      "description": "Maximum results to return",
      "default": 20
    }
  },
  "required": ["query"]
}
```

**Output:** Array of `ContextNode` objects as JSON.

---

### `query`

Execute a raw Cypher query against the graph.

**Input schema:**
```json
{
  "type": "object",
  "properties": {
    "cypher": {
      "type": "string",
      "description": "Cypher query string"
    },
    "params": {
      "type": "object",
      "description": "Optional query parameters",
      "default": {}
    }
  },
  "required": ["cypher"]
}
```

**Output:** Array of result rows as JSON.

!!! warning
    This tool executes writes as well as reads. Agents should use read-only queries (`MATCH` / `RETURN`) unless explicitly performing graph updates. Use `--read-only` mode or `read_only=True` in `create_mcp_server()` to enforce this at the server level.

---

### `get_rationale`

Return the rationale, decisions, and rules that govern a named code node.

**Input schema:**
```json
{
  "type": "object",
  "properties": {
    "name": {
      "type": "string",
      "description": "Function, class, or file name"
    },
    "file": {
      "type": "string",
      "description": "Optional file path to disambiguate"
    }
  },
  "required": ["name"]
}
```

**Output:** Array of `Decision` and `Rule` nodes with `rationale` fields, as JSON.

---

### `find_owners`

Return the people and domains that own a code node, based on CODEOWNERS, annotation, and domain membership.

**Input schema:**
```json
{
  "type": "object",
  "properties": {
    "name": {
      "type": "string",
      "description": "Function, class, or file name"
    },
    "file": {
      "type": "string",
      "description": "Optional file path"
    }
  },
  "required": ["name"]
}
```

**Output:** Array of `Person` and `Domain` nodes as JSON.

---

### `search_knowledge`

Search the knowledge layer only (concepts, rules, decisions, wiki pages, domains).

**Input schema:**
```json
{
  "type": "object",
  "properties": {
    "query": {
      "type": "string",
      "description": "Search query"
    },
    "limit": {
      "type": "integer",
      "description": "Maximum results to return",
      "default": 20
    }
  },
  "required": ["query"]
}
```

**Output:** Array of knowledge layer `ContextNode` objects as JSON.

---

### `blast_radius`

Return all code nodes transitively reachable from a given node via CALLS, IMPORTS, and INHERITS edges — the set of things that could break if this node changes.

**Input schema:**
```json
{
  "type": "object",
  "properties": {
    "name": {
      "type": "string",
      "description": "Starting node name"
    },
    "file": {
      "type": "string",
      "description": "Optional file path to disambiguate"
    },
    "depth": {
      "type": "integer",
      "description": "Maximum traversal depth",
      "default": 3
    }
  },
  "required": ["name"]
}
```

**Output:** Array of `ContextNode` objects ordered by distance from the starting node.

---

## Security

### Read-only mode

Start the server in read-only mode to prevent agents from modifying the graph:

```bash
navegador mcp --read-only
```

In read-only mode:
- The `ingest` tool is disabled (returns an error if called)
- The `query` tool validates that queries contain only `MATCH`, `RETURN`, `WITH`, `WHERE`, `ORDER BY`, `LIMIT`, and `SKIP` clauses
- Write Cypher keywords (`CREATE`, `MERGE`, `SET`, `DELETE`, `DETACH`) are rejected

### Query complexity limits

Set a maximum query complexity to prevent runaway traversals:

```bash
navegador mcp --max-query-complexity 100
```

Or in `.navegador/config.toml`:

```toml
[mcp]
max_query_complexity = 100
```

Queries that exceed the complexity threshold return an error rather than executing.
