"""
Navegador MCP server — exposes graph context tools to AI coding agents.

Run:
    navegador mcp --db .navegador/graph.db
"""

import json
import logging

logger = logging.getLogger(__name__)


def create_mcp_server(store_factory):
    """
    Build and return an MCP server instance wired to a GraphStore factory.

    Args:
        store_factory: Callable[[], GraphStore] — called lazily on first request.
    """
    try:
        from mcp.server import Server  # type: ignore[import]
        from mcp.server.stdio import stdio_server  # type: ignore[import]
        from mcp.types import TextContent, Tool  # type: ignore[import]
    except ImportError as e:
        raise ImportError("Install mcp: pip install mcp") from e

    from navegador.context import ContextLoader

    server = Server("navegador")
    _store = None
    _loader = None

    def _get_loader() -> ContextLoader:
        nonlocal _store, _loader
        if _loader is None:
            _store = store_factory()
            _loader = ContextLoader(_store)
        return _loader

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name="ingest_repo",
                description="Parse and ingest a local code repository into the navegador graph.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Absolute path to the repository."},
                        "clear": {"type": "boolean", "description": "Clear existing graph before ingesting.", "default": False},
                    },
                    "required": ["path"],
                },
            ),
            Tool(
                name="load_file_context",
                description="Return all symbols (functions, classes, imports) in a file and their relationships.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string", "description": "Relative file path within the ingested repo."},
                        "format": {"type": "string", "enum": ["json", "markdown"], "default": "markdown"},
                    },
                    "required": ["file_path"],
                },
            ),
            Tool(
                name="load_function_context",
                description="Return context for a function — what it calls and what calls it.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Function name."},
                        "file_path": {"type": "string", "description": "Relative file path."},
                        "depth": {"type": "integer", "default": 2},
                        "format": {"type": "string", "enum": ["json", "markdown"], "default": "markdown"},
                    },
                    "required": ["name", "file_path"],
                },
            ),
            Tool(
                name="load_class_context",
                description="Return context for a class — methods, inheritance, subclasses.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Class name."},
                        "file_path": {"type": "string", "description": "Relative file path."},
                        "format": {"type": "string", "enum": ["json", "markdown"], "default": "markdown"},
                    },
                    "required": ["name", "file_path"],
                },
            ),
            Tool(
                name="search_symbols",
                description="Fuzzy search for functions, classes, or methods by name.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Partial name to search."},
                        "limit": {"type": "integer", "default": 20},
                    },
                    "required": ["query"],
                },
            ),
            Tool(
                name="query_graph",
                description="Execute a raw Cypher query against the navegador graph.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "cypher": {"type": "string", "description": "Cypher query string."},
                    },
                    "required": ["cypher"],
                },
            ),
            Tool(
                name="graph_stats",
                description="Return node and edge counts for the current graph.",
                inputSchema={"type": "object", "properties": {}},
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        loader = _get_loader()

        if name == "ingest_repo":
            from navegador.ingestion import RepoIngester
            ingester = RepoIngester(loader.store)
            stats = ingester.ingest(arguments["path"], clear=arguments.get("clear", False))
            return [TextContent(type="text", text=json.dumps(stats, indent=2))]

        elif name == "load_file_context":
            bundle = loader.load_file(arguments["file_path"])
            fmt = arguments.get("format", "markdown")
            text = bundle.to_markdown() if fmt == "markdown" else bundle.to_json()
            return [TextContent(type="text", text=text)]

        elif name == "load_function_context":
            bundle = loader.load_function(
                arguments["name"],
                arguments["file_path"],
                depth=arguments.get("depth", 2),
            )
            fmt = arguments.get("format", "markdown")
            text = bundle.to_markdown() if fmt == "markdown" else bundle.to_json()
            return [TextContent(type="text", text=text)]

        elif name == "load_class_context":
            bundle = loader.load_class(arguments["name"], arguments["file_path"])
            fmt = arguments.get("format", "markdown")
            text = bundle.to_markdown() if fmt == "markdown" else bundle.to_json()
            return [TextContent(type="text", text=text)]

        elif name == "search_symbols":
            results = loader.search(arguments["query"], limit=arguments.get("limit", 20))
            lines = [f"- **{r.type}** `{r.name}` — `{r.file_path}`:{r.line_start}" for r in results]
            return [TextContent(type="text", text="\n".join(lines) or "No results.")]

        elif name == "query_graph":
            result = loader.store.query(arguments["cypher"])
            rows = result.result_set or []
            text = json.dumps(rows, default=str, indent=2)
            return [TextContent(type="text", text=text)]

        elif name == "graph_stats":
            stats = {
                "nodes": loader.store.node_count(),
                "edges": loader.store.edge_count(),
            }
            return [TextContent(type="text", text=json.dumps(stats, indent=2))]

        return [TextContent(type="text", text=f"Unknown tool: {name}")]

    return server
