"""
Navegador MCP server — exposes graph context tools to AI coding agents.

Run:
    navegador mcp --db .navegador/graph.db
"""

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def create_mcp_server(store_factory, read_only: bool = False):
    """
    Build and return an MCP server instance wired to a GraphStore factory.

    Args:
        store_factory: Callable[[], GraphStore] — called lazily on first request.
        read_only: When True, the ingest_repo tool is disabled and all
                   query_graph queries are validated for write operations and
                   injection patterns.  Complexity checks apply to all modes.
    """
    try:
        from mcp.server import Server  # type: ignore[import]
        from mcp.types import TextContent, Tool  # type: ignore[import]
    except ImportError as e:
        raise ImportError("Install mcp: pip install mcp") from e

    from navegador.context import ContextLoader
    from navegador.mcp.security import check_complexity, validate_cypher

    server = Server("navegador")
    _store: Any = None
    _loader: ContextLoader | None = None

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
                        "path": {"type": "string", "description": "Absolute path to the repo."},
                        "clear": {
                            "type": "boolean",
                            "description": "Clear existing graph before ingesting.",
                            "default": False,
                        },
                    },
                    "required": ["path"],
                },
            ),
            Tool(
                name="load_file_context",
                description="Return all symbols (functions, classes, imports) in a file.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": "Relative file path within the ingested repo.",
                        },
                        "format": {
                            "type": "string",
                            "enum": ["json", "markdown"],
                            "default": "markdown",
                        },
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
                        "format": {
                            "type": "string",
                            "enum": ["json", "markdown"],
                            "default": "markdown",
                        },
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
                        "format": {
                            "type": "string",
                            "enum": ["json", "markdown"],
                            "default": "markdown",
                        },
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
            Tool(
                name="get_rationale",
                description="Return the rationale, alternatives, and status of an architectural decision.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Decision name."},
                        "format": {
                            "type": "string",
                            "enum": ["json", "markdown"],
                            "default": "markdown",
                        },
                    },
                    "required": ["name"],
                },
            ),
            Tool(
                name="find_owners",
                description="Find people (owners, stakeholders) assigned to a node.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Node name."},
                        "file_path": {
                            "type": "string",
                            "description": "Narrow to a specific file.",
                            "default": "",
                        },
                    },
                    "required": ["name"],
                },
            ),
            Tool(
                name="search_knowledge",
                description="Search concepts, rules, decisions, and wiki pages by name or description.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query."},
                        "limit": {"type": "integer", "default": 20},
                    },
                    "required": ["query"],
                },
            ),
            Tool(
                name="blast_radius",
                description=(
                    "Impact analysis: find all nodes and files affected by changing a symbol. "
                    "Traverses CALLS, REFERENCES, INHERITS, IMPLEMENTS, ANNOTATES edges outward."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Symbol name to analyse."},
                        "file_path": {
                            "type": "string",
                            "description": "Narrow to a specific file (optional).",
                            "default": "",
                        },
                        "depth": {
                            "type": "integer",
                            "description": "Maximum traversal depth.",
                            "default": 3,
                        },
                    },
                    "required": ["name"],
                },
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        loader = _get_loader()

        if name == "ingest_repo":
            if read_only:
                return [TextContent(
                    type="text",
                    text="Error: ingest_repo is disabled in read-only mode.",
                )]
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
            cypher = arguments["cypher"]
            if read_only:
                try:
                    validate_cypher(cypher)
                except Exception as exc:
                    return [TextContent(type="text", text=f"Error: {exc}")]
            try:
                check_complexity(cypher)
            except Exception as exc:
                return [TextContent(type="text", text=f"Error: {exc}")]
            result = loader.store.query(cypher)
            rows = result.result_set or []
            text = json.dumps(rows, default=str, indent=2)
            return [TextContent(type="text", text=text)]

        elif name == "graph_stats":
            stats = {
                "nodes": loader.store.node_count(),
                "edges": loader.store.edge_count(),
            }
            return [TextContent(type="text", text=json.dumps(stats, indent=2))]

        elif name == "get_rationale":
            bundle = loader.load_decision(arguments["name"])
            fmt = arguments.get("format", "markdown")
            text = bundle.to_markdown() if fmt == "markdown" else bundle.to_json()
            return [TextContent(type="text", text=text)]

        elif name == "find_owners":
            results = loader.find_owners(
                arguments["name"], file_path=arguments.get("file_path", "")
            )
            if not results:
                return [TextContent(type="text", text="No owners found.")]
            lines = [
                f"- **{r.name}** ({r.description})" for r in results
            ]
            return [TextContent(type="text", text="\n".join(lines))]

        elif name == "search_knowledge":
            results = loader.search_knowledge(
                arguments["query"], limit=arguments.get("limit", 20)
            )
            if not results:
                return [TextContent(type="text", text="No results.")]
            lines = [
                f"- **{r.type}** `{r.name}` — {r.description or ''}"
                for r in results
            ]
            return [TextContent(type="text", text="\n".join(lines))]

        elif name == "blast_radius":
            from navegador.analysis.impact import ImpactAnalyzer

            result = ImpactAnalyzer(loader.store).blast_radius(
                arguments["name"],
                file_path=arguments.get("file_path", ""),
                depth=arguments.get("depth", 3),
            )
            return [TextContent(type="text", text=json.dumps(result.to_dict(), indent=2))]

        return [TextContent(type="text", text=f"Unknown tool: {name}")]

    return server
