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
                description="Return rationale, alternatives, and status of a decision.",
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
                description="Search concepts, rules, decisions, and wiki pages.",
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
            Tool(
                name="memory_list",
                description=(
                    "List behavioral knowledge nodes ingested from CONFLICT-format memory/ "
                    "directories. Returns rules, project context, references, and user profiles."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "type": {
                            "type": "string",
                            "enum": ["feedback", "project", "reference", "user"],
                            "description": "Filter by memory type.",
                        },
                        "scope": {
                            "type": "string",
                            "enum": ["local", "workspace"],
                            "default": "local",
                            "description": (
                                "'local' returns nodes for the current repo; "
                                "'workspace' returns all repos."
                            ),
                        },
                        "repo": {
                            "type": "string",
                            "default": "",
                            "description": (
                                "Filter to a specific repo name "
                                "(ignored when scope=workspace)."
                            ),
                        },
                        "limit": {"type": "integer", "default": 50},
                    },
                },
            ),
            Tool(
                name="memory_get",
                description="Return a single memory node by name.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Exact memory node name."},
                    },
                    "required": ["name"],
                },
            ),
            Tool(
                name="memory_for_file",
                description=(
                    "Return all memory/knowledge nodes linked to symbols in a given file. "
                    "Useful for loading relevant rules and context before editing a file."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Relative file path within the ingested repo.",
                        },
                    },
                    "required": ["path"],
                },
            ),
            Tool(
                name="diff_graph",
                description=(
                    "Structural diff between two git refs. Reports new/changed symbols, "
                    "blast-radius summary, and affected knowledge nodes for lines changed "
                    "between base and head. Use for PR review context."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "base": {
                            "type": "string",
                            "default": "HEAD",
                            "description": "Base git ref (branch, tag, SHA).",
                        },
                        "head": {
                            "type": "string",
                            "default": "working tree",
                            "description": "Head ref to compare against base.",
                        },
                        "repo_path": {
                            "type": "string",
                            "default": ".",
                            "description": "Absolute path to the git repository.",
                        },
                        "format": {
                            "type": "string",
                            "enum": ["markdown", "json"],
                            "default": "markdown",
                        },
                    },
                },
            ),
            Tool(
                name="drift_check",
                description=(
                    "Run architecture drift checks: compare rules, ADRs, and memory nodes "
                    "against the live code graph. Returns violations (stale refs, undocumented "
                    "domain symbols, missing owners) with evidence."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "format": {
                            "type": "string",
                            "enum": ["markdown", "json"],
                            "default": "markdown",
                        },
                    },
                },
            ),
            Tool(
                name="blast_radius_cross_repo",
                description=(
                    "Cross-repo blast-radius analysis. Given a symbol, traverses the unified "
                    "workspace graph across repository boundaries to find all affected symbols, "
                    "files, and repos. Requires a unified workspace graph."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Symbol name to analyse."},
                        "file_path": {"type": "string", "default": ""},
                        "repo": {
                            "type": "string",
                            "default": "",
                            "description": "Source repository name for attribution.",
                        },
                        "depth": {"type": "integer", "default": 3},
                        "format": {
                            "type": "string",
                            "enum": ["markdown", "json"],
                            "default": "markdown",
                        },
                    },
                    "required": ["name"],
                },
            ),
            Tool(
                name="build_task_pack",
                description=(
                    "Build a compact, high-signal task pack for a symbol or file. "
                    "Assembles code structure, callers/callees, governing rules, memory nodes, "
                    "docs, owners, and related tests into one artifact — ready for agent prompt "
                    "injection without requiring multiple separate tool calls."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "target": {
                            "type": "string",
                            "description": "Symbol name or relative file path.",
                        },
                        "file_path": {
                            "type": "string",
                            "default": "",
                            "description": "Narrow symbol lookup to a specific file.",
                        },
                        "mode": {
                            "type": "string",
                            "enum": ["implement", "review", "debug", "refactor"],
                            "default": "implement",
                        },
                        "depth": {"type": "integer", "default": 2},
                        "format": {
                            "type": "string",
                            "enum": ["markdown", "json"],
                            "default": "markdown",
                        },
                    },
                    "required": ["target"],
                },
            ),
            Tool(
                name="symbol_history",
                description=(
                    "Query the historical timeline of a symbol across graph snapshots. "
                    "Returns first-seen, moved, renamed, and removed events. "
                    "Use snapshot() first to record states, then query here."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Symbol name (function, class, or method).",
                        },
                        "file_path": {
                            "type": "string",
                            "default": "",
                            "description": "Narrow to a specific file path.",
                        },
                        "mode": {
                            "type": "string",
                            "enum": ["history", "lineage", "symbols_at"],
                            "default": "history",
                            "description": (
                                "history=timeline events, lineage=rename/move chain, "
                                "symbols_at=all symbols at a ref (use ref param)."
                            ),
                        },
                        "ref": {
                            "type": "string",
                            "default": "",
                            "description": "Git ref for symbols_at mode.",
                        },
                        "format": {
                            "type": "string",
                            "enum": ["markdown", "json"],
                            "default": "markdown",
                        },
                    },
                    "required": ["name"],
                },
            ),
            Tool(
                name="suggest_doc_links",
                description=(
                    "Suggest confidence-ranked links from documentation nodes to code "
                    "symbols. Returns candidates with source, target, confidence, "
                    "strategy, and rationale."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "min_confidence": {
                            "type": "number",
                            "default": 0.5,
                            "description": "Minimum confidence threshold.",
                        },
                        "strategy": {
                            "type": "string",
                            "enum": ["EXACT_NAME", "FUZZY", "SEMANTIC", ""],
                            "default": "",
                            "description": "Filter by match strategy.",
                        },
                        "format": {
                            "type": "string",
                            "enum": ["markdown", "json"],
                            "default": "markdown",
                        },
                    },
                    "required": [],
                },
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        loader = _get_loader()

        if name == "ingest_repo":
            if read_only:
                return [
                    TextContent(
                        type="text",
                        text="Error: ingest_repo is disabled in read-only mode.",
                    )
                ]
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
            lines = [f"- **{r.name}** ({r.description})" for r in results]
            return [TextContent(type="text", text="\n".join(lines))]

        elif name == "search_knowledge":
            results = loader.search_knowledge(arguments["query"], limit=arguments.get("limit", 20))
            if not results:
                return [TextContent(type="text", text="No results.")]
            lines = [f"- **{r.type}** `{r.name}` — {r.description or ''}" for r in results]
            return [TextContent(type="text", text="\n".join(lines))]

        elif name == "blast_radius":
            from navegador.analysis.impact import ImpactAnalyzer

            result = ImpactAnalyzer(loader.store).blast_radius(
                arguments["name"],
                file_path=arguments.get("file_path", ""),
                depth=arguments.get("depth", 3),
            )
            return [TextContent(type="text", text=json.dumps(result.to_dict(), indent=2))]

        elif name == "memory_list":
            from navegador.graph import queries

            scope = arguments.get("scope", "local")
            repo = arguments.get("repo", "")
            mem_type = arguments.get("type", "")
            limit = arguments.get("limit", 50)
            result = loader.store.query(
                queries.MEMORY_LIST,
                {"type": mem_type, "scope": scope, "repo": repo, "limit": limit},
            )
            rows = result.result_set or []
            if not rows:
                return [TextContent(type="text", text="No memory nodes found.")]
            items = [
                {
                    "name": row[1],
                    "description": row[2],
                    "memory_type": row[3],
                    "repo": row[4],
                    "content": row[5],
                }
                for row in rows
            ]
            return [TextContent(type="text", text=json.dumps(items, indent=2))]

        elif name == "memory_get":
            from navegador.graph import queries

            result = loader.store.query(queries.MEMORY_GET, {"name": arguments["name"]})
            rows = result.result_set or []
            if not rows:
                return [TextContent(type="text", text=f"No memory node found: {arguments['name']}")]
            row = rows[0]
            item = {
                "label": row[0],
                "name": row[1],
                "description": row[2],
                "memory_type": row[3],
                "repo": row[4],
                "content": row[5],
            }
            return [TextContent(type="text", text=json.dumps(item, indent=2))]

        elif name == "memory_for_file":
            from navegador.graph import queries

            result = loader.store.query(queries.MEMORY_FOR_FILE, {"path": arguments["path"]})
            rows = result.result_set or []
            if not rows:
                return [TextContent(type="text", text="No memory nodes linked to this file.")]
            items = [
                {
                    "label": row[0],
                    "name": row[1],
                    "description": row[2],
                    "memory_type": row[3],
                    "repo": row[4],
                    "content": row[5],
                }
                for row in rows
            ]
            return [TextContent(type="text", text=json.dumps(items, indent=2))]

        elif name == "diff_graph":
            from navegador.analysis.diffgraph import DiffGraphAnalyzer

            base = arguments.get("base", "HEAD")
            head = arguments.get("head", "working tree")
            repo_path = arguments.get("repo_path", ".")
            fmt = arguments.get("format", "markdown")

            analyzer = DiffGraphAnalyzer(loader.store, repo_path)
            if base == "HEAD" and head == "working tree":
                report = analyzer.diff_working_tree()
            else:
                report = analyzer.diff_refs(base=base, head=head)

            text = report.to_json() if fmt == "json" else report.to_markdown()
            return [TextContent(type="text", text=text)]

        elif name == "drift_check":
            from navegador.analysis.drift import DriftChecker

            report = DriftChecker(loader.store).check()
            fmt = arguments.get("format", "markdown")
            text = report.to_json() if fmt == "json" else report.to_markdown()
            return [TextContent(type="text", text=text)]

        elif name == "blast_radius_cross_repo":
            from navegador.analysis.crossrepo import CrossRepoImpactAnalyzer

            result = CrossRepoImpactAnalyzer(loader.store).blast_radius(
                arguments["name"],
                file_path=arguments.get("file_path", ""),
                repo=arguments.get("repo", ""),
                depth=arguments.get("depth", 3),
            )
            fmt = arguments.get("format", "markdown")
            text = result.to_json() if fmt == "json" else result.to_markdown()
            return [TextContent(type="text", text=text)]

        elif name == "build_task_pack":
            from navegador.taskpack import TaskPackBuilder

            target = arguments["target"]
            file_path = arguments.get("file_path", "")
            mode = arguments.get("mode", "implement")
            depth = arguments.get("depth", 2)
            fmt = arguments.get("format", "markdown")

            builder = TaskPackBuilder(loader.store)
            if "/" in target or any(target.endswith(ext) for ext in (
                ".py", ".ts", ".tsx", ".js", ".go", ".rb", ".java", ".rs"
            )):
                pack = builder.for_file(target, mode=mode)
            else:
                pack = builder.for_symbol(target, file_path=file_path, depth=depth, mode=mode)

            text = pack.to_json() if fmt == "json" else pack.to_markdown()
            return [TextContent(type="text", text=text)]

        elif name == "symbol_history":
            from navegador.history import HistoryStore

            sym_name = arguments["name"]
            file_path = arguments.get("file_path", "")
            mode = arguments.get("mode", "history")
            ref = arguments.get("ref", "")
            fmt = arguments.get("format", "markdown")

            h = HistoryStore(loader.store)
            if mode == "symbols_at":
                symbols = h.symbols_at(ref or "HEAD")
                if fmt == "json":
                    text = json.dumps([s.__dict__ for s in symbols], indent=2)
                else:
                    lines = [f"## Symbols at `{ref or 'HEAD'}`\n"]
                    for s in symbols:
                        lines.append(f"- [{s.label}] `{s.name}` `{s.file_path}`")
                    text = "\n".join(lines)
            elif mode == "lineage":
                report = h.lineage(sym_name, file_path=file_path)
                text = report.to_json() if fmt == "json" else report.to_markdown()
            else:
                report = h.history(sym_name, file_path=file_path)
                text = report.to_json() if fmt == "json" else report.to_markdown()
            return [TextContent(type="text", text=text)]

        elif name == "suggest_doc_links":
            from navegador.intelligence.doclink import DocLinker

            min_conf = float(arguments.get("min_confidence", 0.5))
            strategy = arguments.get("strategy", "")
            fmt = arguments.get("format", "markdown")
            linker = DocLinker(loader.store)
            candidates = linker.suggest_links(min_confidence=min_conf)
            if strategy:
                candidates = [c for c in candidates if c.strategy == strategy]
            if fmt == "json":
                import json as _json

                text = _json.dumps([c.__dict__ for c in candidates], indent=2)
            else:
                if not candidates:
                    text = "No link candidates found."
                else:
                    lines = [f"## Doc Link Suggestions ({len(candidates)})\n"]
                    for c in candidates:
                        lines.append(
                            f"- **{c.source_name}** -> `{c.target_name}` "
                            f"`{c.target_file}` [{c.strategy}] conf={c.confidence:.2f}  \n"
                            f"  _{c.rationale}_"
                        )
                    text = "\n".join(lines)
            return [TextContent(type="text", text=text)]

        return [TextContent(type="text", text=f"Unknown tool: {name}")]

    return server
