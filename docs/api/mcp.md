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
) -> MCPServer: ...
```

Creates and returns an MCP server instance. If `store` is not provided, opens a `GraphStore.sqlite()` at `db_path` (or `NAVEGADOR_DB` env var, or `./navegador.db`).

The CLI command `navegador mcp [--db path]` calls this function and runs the server loop.

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

All tools accept JSON input and return JSON output.

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
    This tool executes writes as well as reads. Agents should use read-only queries (`MATCH` / `RETURN`) unless explicitly performing graph updates.
