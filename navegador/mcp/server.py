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

    #: Tools that mutate the graph. In read-only mode these are not advertised
    #: at all, so a caller can route around them instead of discovering the
    #: restriction by calling one and being refused (#171).
    WRITE_TOOLS = frozenset({"ingest_repo"})

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        tools = [
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
                name="read_docs",
                description=(
                    "Read navegador's own documentation, bundled with the package. "
                    "Call with no arguments to list every page, 'page' to read one, "
                    "or 'query' to search. Prefer this over guessing at navegador's "
                    "CLI, SDK, or configuration."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "page": {
                            "type": "string",
                            "default": "",
                            "description": (
                                "Page slug, e.g. 'guide/mcp-integration' or 'quickstart'."
                            ),
                        },
                        "query": {
                            "type": "string",
                            "default": "",
                            "description": "Search all pages for this term instead.",
                        },
                    },
                },
            ),
            Tool(
                name="resolve_address",
                description=(
                    "Resolve a supergraph contract address into the code graph and "
                    "return the node plus its immediate neighbourhood. This is the "
                    "brain-to-code hop: given the target of an `implemented_in` join "
                    "edge, it returns the entry point from which traversal continues "
                    "(callers, callees, containing file). Addresses look like "
                    "`[<repo>/]code:<path>[#<symbol>]`."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "address": {
                            "type": "string",
                            "description": (
                                "Contract address, e.g. `myrepo/code:src/auth.py#validate_token`."
                            ),
                        },
                    },
                    "required": ["address"],
                },
            ),
            Tool(
                name="propose_join_edges",
                description=(
                    "Propose supergraph join edges from inferred documentation-to-code "
                    "affinity, in contract format with confidence and evidence. "
                    "Navegador proposes; the brain reviews and decides what to commit. "
                    "Targets are code-realm addresses."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "repo": {
                            "type": "string",
                            "default": "",
                            "description": "Federation namespace to qualify targets with.",
                        },
                        "min_confidence": {
                            "type": "number",
                            "default": 0.5,
                            "description": "Drop proposals scoring below this.",
                        },
                    },
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
                        "repo": {
                            "type": "string",
                            "default": "",
                            "description": (
                                "Federated repo namespace; scopes file_path to that repo."
                            ),
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
                        "repo": {
                            "type": "string",
                            "default": "",
                            "description": (
                                "Federated repo namespace; scopes file_path to that repo."
                            ),
                        },
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
                        "repo": {
                            "type": "string",
                            "default": "",
                            "description": (
                                "Federated repo namespace; scopes file_path to that repo."
                            ),
                        },
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
                        "repo": {
                            "type": "string",
                            "default": "",
                            "description": (
                                "Federated repo namespace to scope to (see list_repos). "
                                "Omit to span all repos in a super-graph."
                            ),
                        },
                    },
                    "required": ["query"],
                },
            ),
            Tool(
                name="query_graph",
                description=(
                    "Execute a raw Cypher query against the navegador graph. "
                    "On a federated super-graph the query spans all repos; nodes carry a "
                    "`repo` property (knowledge nodes a comma-joined `repos`) for filtering."
                ),
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
                inputSchema={
                    "type": "object",
                    "properties": {
                        "repo": {
                            "type": "string",
                            "default": "",
                            "description": (
                                "Count only nodes/edges of one federated repo namespace."
                            ),
                        },
                    },
                },
            ),
            Tool(
                name="list_repos",
                description=(
                    "List the repo namespaces available in a federated super-graph "
                    "(falls back to all Repository nodes on a single-repo graph)."
                ),
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
                        "repo": {
                            "type": "string",
                            "default": "",
                            "description": (
                                "Federated repo namespace to scope to. Matches shared "
                                "knowledge nodes contributed by that repo. Omit to span all."
                            ),
                        },
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
                        "repo": {
                            "type": "string",
                            "default": "",
                            "description": (
                                "Federated repo namespace; scopes file_path to that repo. "
                                "Omit to span the whole workspace."
                            ),
                        },
                    },
                    "required": ["name"],
                },
            ),
            Tool(
                name="memory_list",
                description=(
                    "List behavioral knowledge nodes ingested from structured memory/ "
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
                                "Filter to a specific repo name (ignored when scope=workspace)."
                            ),
                        },
                        "limit": {"type": "integer", "default": 50},
                    },
                },
            ),
            Tool(
                name="memory_get",
                description=(
                    "Return a single memory node by name. "
                    "Provide repo to disambiguate when multiple repos share the same memory name."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Exact memory node name."},
                        "repo": {
                            "type": "string",
                            "default": "",
                            "description": "Repo name to scope the lookup (optional).",
                        },
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
                    "between base and head. Use for PR review context. "
                    "Set snapshot_mode=true for a true graph diff between two previously "
                    "snapshotted refs (falls back to heuristic if snapshots are missing)."
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
                        "snapshot_mode": {
                            "type": "boolean",
                            "default": False,
                            "description": (
                                "Use snapshot-backed graph diff instead of git-diff heuristic."
                            ),
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
            Tool(
                name="review_diff",
                description=(
                    "Generate rule-aware review comments for a diff. Ties changed "
                    "symbols to governing rules, ADRs, and knowledge nodes with "
                    "severity and confidence scores."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "base": {
                            "type": "string",
                            "default": "main",
                            "description": "Base ref (branch, tag, SHA).",
                        },
                        "head": {
                            "type": "string",
                            "default": "HEAD",
                            "description": "Head ref to compare.",
                        },
                        "repo_path": {
                            "type": "string",
                            "default": ".",
                            "description": "Path to the git repo.",
                        },
                        "min_confidence": {
                            "type": "number",
                            "default": 0.5,
                            "description": "Minimum confidence threshold.",
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
            Tool(
                name="release_check",
                description=(
                    "Run release readiness checks for a git ref range. Summarizes "
                    "changed symbols, missing tests, stale docs, required owner "
                    "sign-offs, and cross-repo impact."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "base": {"type": "string", "default": "main"},
                        "head": {"type": "string", "default": "HEAD"},
                        "repo_path": {"type": "string", "default": "."},
                        "format": {
                            "type": "string",
                            "enum": ["markdown", "json"],
                            "default": "markdown",
                        },
                    },
                    "required": [],
                },
            ),
            Tool(
                name="apply_lens",
                description=(
                    "Apply a named architecture lens to get a focused subgraph. "
                    "Built-in lenses: request_path, ownership_map, domain_boundaries, "
                    "dependency_layers, framework_components."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "lens": {"type": "string", "description": "Lens name."},
                        "symbol": {"type": "string", "default": ""},
                        "domain": {"type": "string", "default": ""},
                        "file_path": {"type": "string", "default": ""},
                        "label": {"type": "string", "default": ""},
                        "format": {
                            "type": "string",
                            "enum": ["markdown", "json"],
                            "default": "markdown",
                        },
                    },
                    "required": ["lens"],
                },
            ),
            Tool(
                name="locate",
                description=(
                    "Find where to look for something, ranked, with the reason each "
                    "place surfaced. Fuses exact text matches, symbol names, "
                    "documents and semantic similarity. Returns places to look, not "
                    "an answer — use it to pick a target in one call instead of "
                    "several rounds of searching, then read or grep precisely."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "intent": {
                            "type": "string",
                            "description": "What you are looking for, in words or as a literal.",
                        },
                        "limit": {"type": "integer", "default": 10},
                    },
                    "required": ["intent"],
                },
            ),
            Tool(
                name="scope_for",
                description=(
                    "The set of files reachable from a symbol, for narrowing a "
                    "search before running it. Pass `pattern` to search only within "
                    "that scope and get back matching lines with file and line "
                    "number. This is the reduction a flat text index cannot compute: "
                    "it follows calls, references and imports through the graph."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "symbol": {"type": "string", "description": "Function, method or class."},
                        "pattern": {
                            "type": "string",
                            "default": "",
                            "description": "Optional; search within the scope instead of "
                            "just listing it.",
                        },
                        "depth": {"type": "integer", "default": 2},
                        "limit": {"type": "integer", "default": 50},
                    },
                    "required": ["symbol"],
                },
            ),
            Tool(
                name="neighbourhood",
                description=(
                    "Callers, callees, tests and defining file for a symbol, in one "
                    "call. Saves the several turns it otherwise takes to assemble "
                    "the same picture."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {"symbol": {"type": "string"}},
                    "required": ["symbol"],
                },
            ),
            Tool(
                name="grep_code",
                description=(
                    "Exact substring or regular-expression search over indexed "
                    "content, returning file, line number and the matching line. "
                    "Results are exact — verified against ripgrep — and cost scales "
                    "with the number of matches rather than the size of the "
                    "codebase, so a search that matches nothing is nearly free."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string"},
                        "regex": {"type": "boolean", "default": False},
                        "ignore_case": {"type": "boolean", "default": False},
                        "limit": {"type": "integer", "default": 50},
                    },
                    "required": ["pattern"],
                },
            ),
        ]
        if read_only:
            return [t for t in tools if t.name not in WRITE_TOOLS]
        return tools

    def _neighbourhood(store, resolved) -> dict:
        """
        One hop out from a resolved node, so a brain-side traversal that just
        crossed a join edge can keep going without a second round trip.
        """
        try:
            callers = (
                store.query(
                    "MATCH (a)-[:CALLS]->(b {name: $name, file_path: $path}) "
                    "RETURN labels(a)[0], a.name, coalesce(a.file_path, '') LIMIT 25",
                    {"name": resolved.name, "path": resolved.path},
                ).result_set
                or []
            )
            callees = (
                store.query(
                    "MATCH (a {name: $name, file_path: $path})-[:CALLS]->(b) "
                    "RETURN labels(b)[0], b.name, coalesce(b.file_path, '') LIMIT 25",
                    {"name": resolved.name, "path": resolved.path},
                ).result_set
                or []
            )
        except Exception:  # noqa: BLE001 — a partial answer beats no answer
            callers, callees = [], []

        from navegador.contract import address_for_node

        def entries(rows):
            out = []
            for row in rows:
                address = address_for_node(row[0] or "", row[1] or "", row[2] or "")
                out.append({"name": row[1], "label": row[0], "address": address})
            return out

        return {"callers": entries(callers), "callees": entries(callees)}

    def _ingest_status(store, repo: str = "") -> dict:
        """
        Tell "empty" apart from "never ingested" (#171).

        A namespace that exists but holds nothing answers every question with a
        valid-looking negative — the most misleading state a caller can be in,
        because it reads as "no such symbol" rather than "this was never
        indexed". Reporting the count alone cannot distinguish the two.
        """
        try:
            if repo:
                result = store.query(
                    "MATCH (n {repo: $repo}) RETURN count(n), max(n.ingested_at)",
                    {"repo": repo},
                )
            else:
                result = store.query("MATCH (n) RETURN count(n), max(n.ingested_at)")
            row = (result.result_set or [[0, None]])[0]
            count, last_ingested = row[0], row[1]
        except Exception:  # noqa: BLE001 — status must never break the caller's tool
            return {"status": "unknown"}

        if count:
            return {"status": "populated", "last_ingested_at": last_ingested}
        return {
            "status": "registered-but-empty",
            "last_ingested_at": None,
            "hint": (
                "This namespace exists but holds no nodes — it was never "
                "successfully ingested, or the ingest wrote to a different "
                "backend. Queries against it return empty, which is not the "
                "same as 'not found'. Re-ingest with: navegador ingest <path>"
            ),
        }

    def _scoped_path(arguments: dict) -> str:
        """file_path, prefixed with the federated repo namespace when given."""
        file_path = arguments.get("file_path", "")
        repo = arguments.get("repo", "")
        if repo and file_path and not file_path.startswith(f"{repo}/"):
            return f"{repo}/{file_path}"
        return file_path

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        # Documentation needs no graph — answer before touching the store, so
        # an agent can read how to use navegador even when the graph is empty
        # or the backend is misconfigured.
        if name == "read_docs":
            from navegador.manual import ManualError, find_page, list_pages
            from navegador.manual import search as search_docs

            try:
                if query := arguments.get("query", ""):
                    hits = search_docs(query)
                    return [TextContent(type="text", text=json.dumps(hits, indent=2))]
                if page := arguments.get("page", ""):
                    doc = find_page(page)
                    return [TextContent(type="text", text=doc.read())]
                index = [p.to_dict() for p in list_pages()]
                return [TextContent(type="text", text=json.dumps(index, indent=2))]
            except ManualError as exc:
                return [TextContent(type="text", text=f"Error: {exc}")]

        loader = _get_loader()

        if name == "locate":
            from navegador.targeting import Targeting

            candidates = Targeting(loader.store).locate(
                arguments["intent"], limit=int(arguments.get("limit", 10))
            )
            payload = {
                "intent": arguments["intent"],
                "candidates": [c.to_dict() for c in candidates],
                "note": (
                    "These are places to look, not an answer. Read or grep the "
                    "top candidates rather than trusting the ranking."
                ),
            }
            return [TextContent(type="text", text=json.dumps(payload, indent=2))]

        if name == "scope_for":
            from navegador.targeting import Targeting

            targeting = Targeting(loader.store)
            symbol = arguments["symbol"]
            depth = int(arguments.get("depth", 2))
            paths = targeting.scope_for(symbol, depth=depth)
            payload: dict = {"symbol": symbol, "depth": depth, "files": paths}

            pattern = arguments.get("pattern") or ""
            if pattern:
                matches = targeting.search_within(
                    symbol, pattern, depth=depth, limit=int(arguments.get("limit", 50))
                )
                payload["pattern"] = pattern
                payload["matches"] = [m.to_dict() for m in matches]
            elif paths:
                payload["hint"] = (
                    "Pass `pattern` to search only these files, or hand them to a "
                    "grep as an explicit file list."
                )
            return [TextContent(type="text", text=json.dumps(payload, indent=2))]

        if name == "neighbourhood":
            from navegador.targeting import Targeting

            payload = Targeting(loader.store).neighbourhood(arguments["symbol"])
            return [TextContent(type="text", text=json.dumps(payload, indent=2))]

        if name == "grep_code":
            from navegador.graph.trigram import TrigramIndex

            matches = TrigramIndex(loader.store).search(
                arguments["pattern"],
                is_regex=bool(arguments.get("regex", False)),
                ignore_case=bool(arguments.get("ignore_case", False)),
                limit=int(arguments.get("limit", 50)),
            )
            payload = {
                "pattern": arguments["pattern"],
                "matches": [m.to_dict() for m in matches],
            }
            if not matches:
                payload["hint"] = (
                    "No matches. If this repository was ingested before content "
                    "storage existed, re-ingest it so there is a corpus to search."
                )
            return [TextContent(type="text", text=json.dumps(payload, indent=2))]

        if name == "resolve_address":
            from navegador.contract import AddressError, resolve

            try:
                resolved = resolve(loader.store, arguments["address"])
            except AddressError as exc:
                return [TextContent(type="text", text=f"Error: {exc}")]

            payload = resolved.to_dict()
            if resolved.found:
                payload["neighbourhood"] = _neighbourhood(loader.store, resolved)
            else:
                payload["hint"] = (
                    "No node at that address. The repo may not be ingested, or the "
                    "path may be recorded relative to a different root — check "
                    "list_repos and graph_stats."
                )
            return [TextContent(type="text", text=json.dumps(payload, indent=2, default=str))]

        elif name == "propose_join_edges":
            from navegador.contract import propose_join_edges

            payload = propose_join_edges(
                loader.store,
                repo=arguments.get("repo", ""),
                min_confidence=arguments.get("min_confidence", 0.5),
            )
            return [TextContent(type="text", text=json.dumps(payload, indent=2, default=str))]

        elif name == "ingest_repo":
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
            bundle = loader.load_file(_scoped_path(arguments))
            fmt = arguments.get("format", "markdown")
            text = bundle.to_markdown() if fmt == "markdown" else bundle.to_json()
            return [TextContent(type="text", text=text)]

        elif name == "load_function_context":
            bundle = loader.load_function(
                arguments["name"],
                _scoped_path(arguments),
                depth=arguments.get("depth", 2),
            )
            fmt = arguments.get("format", "markdown")
            text = bundle.to_markdown() if fmt == "markdown" else bundle.to_json()
            return [TextContent(type="text", text=text)]

        elif name == "load_class_context":
            bundle = loader.load_class(arguments["name"], _scoped_path(arguments))
            fmt = arguments.get("format", "markdown")
            text = bundle.to_markdown() if fmt == "markdown" else bundle.to_json()
            return [TextContent(type="text", text=text)]

        elif name == "search_symbols":
            results = loader.search(
                arguments["query"],
                limit=arguments.get("limit", 20),
                repo=arguments.get("repo", ""),
            )
            # Each hit carries its contract address so a brain can record an
            # `implemented_in` join edge pointing straight back at it (#158).
            from navegador.contract import address_for_node

            repo = arguments.get("repo", "")
            lines = []
            for r in results:
                address = address_for_node(r.type, r.name, r.file_path, repo=repo)
                suffix = f"  `{address}`" if address else ""
                lines.append(f"- **{r.type}** `{r.name}` — `{r.file_path}`:{r.line_start}{suffix}")
            body = "\n".join(lines) or "No results."
            return [TextContent(type="text", text=body)]

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
            repo = arguments.get("repo", "")
            if repo:
                nodes = loader.store.query(
                    "MATCH (n {repo: $repo}) RETURN count(n)", {"repo": repo}
                )
                edges = loader.store.query(
                    "MATCH (a {repo: $repo})-[r]->(b {repo: $repo}) RETURN count(r)",
                    {"repo": repo},
                )
                stats = {
                    "repo": repo,
                    "nodes": (nodes.result_set or [[0]])[0][0],
                    "edges": (edges.result_set or [[0]])[0][0],
                }
            else:
                stats = {
                    "nodes": loader.store.node_count(),
                    "edges": loader.store.edge_count(),
                }
            stats["read_only"] = read_only
            stats.update(_ingest_status(loader.store, repo))
            return [TextContent(type="text", text=json.dumps(stats, indent=2))]

        elif name == "list_repos":
            result = loader.store.query(
                "MATCH (r:Repository {description: 'federated-repo-anchor'}) "
                "RETURN r.name ORDER BY r.name"
            )
            rows = result.result_set or []
            if not rows:
                result = loader.store.query("MATCH (r:Repository) RETURN r.name ORDER BY r.name")
                rows = result.result_set or []
            repos = [{"repo": row[0], **_ingest_status(loader.store, row[0])} for row in rows]
            return [TextContent(type="text", text=json.dumps(repos, indent=2))]

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
            results = loader.search_knowledge(
                arguments["query"],
                limit=arguments.get("limit", 20),
                repo=arguments.get("repo", ""),
            )
            if not results:
                return [TextContent(type="text", text="No results.")]
            lines = [f"- **{r.type}** `{r.name}` — {r.description or ''}" for r in results]
            return [TextContent(type="text", text="\n".join(lines))]

        elif name == "blast_radius":
            from navegador.analysis.impact import ImpactAnalyzer

            result = ImpactAnalyzer(loader.store).blast_radius(
                arguments["name"],
                file_path=_scoped_path(arguments),
                depth=arguments.get("depth", 3),
                repo=arguments.get("repo", ""),
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

            result = loader.store.query(
                queries.MEMORY_GET,
                {"name": arguments["name"], "repo": arguments.get("repo", "")},
            )
            rows = result.result_set or []
            if not rows:
                return [TextContent(type="text", text=f"No memory node found: {arguments['name']}")]
            # Fail closed: if multiple repos contain the same name and no repo was specified,
            # return an error instead of an arbitrary match.
            if len(rows) > 1 and not arguments.get("repo", ""):
                repos = sorted({row[4] for row in rows if row[4]})
                return [
                    TextContent(
                        type="text",
                        text=json.dumps(
                            {
                                "error": "ambiguous",
                                "message": (
                                    f"Memory name {arguments['name']!r} exists in multiple repos. "
                                    "Pass repo= to disambiguate."
                                ),
                                "repos": repos,
                            }
                        ),
                    )
                ]
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
            snapshot_mode = arguments.get("snapshot_mode", False)

            analyzer = DiffGraphAnalyzer(loader.store, repo_path)
            if snapshot_mode:
                report = analyzer.diff_snapshots(base_ref=base, head_ref=head)
            elif base == "HEAD" and head == "working tree":
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
            if "/" in target or any(
                target.endswith(ext)
                for ext in (".py", ".ts", ".tsx", ".js", ".go", ".rb", ".java", ".rs")
            ):
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

        elif name == "review_diff":
            from navegador.analysis.diffgraph import DiffGraphAnalyzer
            from navegador.analysis.review import ReviewGenerator

            base = arguments.get("base", "main")
            head = arguments.get("head", "HEAD")
            repo_path = arguments.get("repo_path", ".")
            min_confidence = float(arguments.get("min_confidence", 0.5))
            fmt = arguments.get("format", "markdown")

            analyzer = DiffGraphAnalyzer(loader.store, repo_path)
            diff_report = analyzer.diff_refs(base=base, head=head)

            changed_symbols = [
                {"name": sc.symbol, "file_path": sc.file_path}
                for sc in diff_report.new_symbols + diff_report.changed_symbols
            ]

            gen = ReviewGenerator(loader.store)
            report = gen.review_diff(
                changed_symbols=changed_symbols,
                changed_files=list(diff_report.affected_files),
            )
            report.comments = [c for c in report.comments if c.confidence >= min_confidence]

            text = report.to_json() if fmt == "json" else report.to_markdown()
            return [TextContent(type="text", text=text)]

        elif name == "release_check":
            from navegador.analysis.release import ReleaseChecker

            base = arguments.get("base", "main")
            head = arguments.get("head", "HEAD")
            repo_path = arguments.get("repo_path", ".")
            fmt = arguments.get("format", "markdown")

            checker = ReleaseChecker(loader.store, repo_path)
            report = checker.check(base=base, head=head)
            text = report.to_json() if fmt == "json" else report.to_markdown()
            return [TextContent(type="text", text=text)]

        elif name == "apply_lens":
            from navegador.lenses import LensEngine

            engine = LensEngine(loader.store)
            lens_name = arguments["lens"]
            try:
                result = engine.apply(
                    lens_name,
                    symbol=arguments.get("symbol", ""),
                    domain=arguments.get("domain", ""),
                    file_path=arguments.get("file_path", ""),
                    label=arguments.get("label", ""),
                )
            except ValueError as exc:
                return [TextContent(type="text", text=f"Error: {exc}")]
            fmt = arguments.get("format", "markdown")
            text = result.to_markdown() if fmt == "markdown" else result.to_json()
            return [TextContent(type="text", text=text)]

        return [TextContent(type="text", text=f"Unknown tool: {name}")]

    return server
