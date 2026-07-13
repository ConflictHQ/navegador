"""
GraphStore — thin wrapper over FalkorDB (SQLite or Redis backend).

Usage:
    # SQLite (local, zero-infra)
    store = GraphStore.sqlite(".navegador/graph.db")

    # Redis-backed FalkorDB (production)
    store = GraphStore.redis("redis://localhost:6379")
"""

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class GraphStore:
    """
    Wraps a FalkorDB graph, providing helpers for navegador node/edge operations.

    The underlying graph is named "navegador" within the database by default;
    pass ``graph_name`` (or use :meth:`with_graph`) to target another named
    graph in the same database — e.g. the per-repo ``navegador_<name>`` graphs
    written by federated workspace ingest.
    """

    GRAPH_NAME = "navegador"

    def __init__(self, client: Any, graph_name: str | None = None) -> None:
        self._client = client
        self.graph_name = graph_name or self.GRAPH_NAME
        self._graph = client.select_graph(self.graph_name)

    # ── Constructors ──────────────────────────────────────────────────────────

    @classmethod
    def sqlite(cls, db_path: str | Path = ".navegador/graph.db") -> "GraphStore":
        """
        Open an embedded FalkorDB graph via falkordblite (zero-infra).

        The on-disk file is a Redis RDB snapshot, not a SQLite database —
        the method name is kept for API compatibility.

        Requires: pip install FalkorDB falkordblite
        """
        try:
            from redislite import FalkorDB  # type: ignore[import]  # provided by falkordblite
        except ImportError as e:
            raise ImportError(
                "Install graph dependencies: pip install FalkorDB falkordblite"
            ) from e

        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        client = FalkorDB(str(db_path))
        logger.info("GraphStore opened (SQLite/falkordblite): %s", db_path)
        return cls(client)

    @classmethod
    def redis(cls, url: str = "redis://localhost:6379") -> "GraphStore":
        """
        Open a Redis-backed FalkorDB graph (production use).

        Requires: pip install FalkorDB redis
        """
        try:
            import falkordb  # type: ignore[import]
        except ImportError as e:
            raise ImportError("Install falkordb: pip install FalkorDB redis") from e

        client = falkordb.FalkorDB.from_url(url)
        logger.info("GraphStore opened (Redis): %s", url)
        return cls(client)

    # ── Core operations ───────────────────────────────────────────────────────

    def query(self, cypher: str, params: dict[str, Any] | None = None) -> Any:
        """Execute a raw Cypher query and return the result."""
        return self._graph.query(cypher, params or {})

    def with_graph(self, graph_name: str) -> "GraphStore":
        """
        Return a GraphStore for another named graph in the same database.

        Shares the underlying client — closing either store closes both.
        """
        return GraphStore(self._client, graph_name)

    def list_graphs(self) -> list[str]:
        """Names of all graphs resident in the connected database."""
        lister = getattr(self._client, "list_graphs", None)
        if callable(lister):
            return [str(g) for g in lister()]
        conn = getattr(self._client, "connection", None)
        if conn is not None:
            return [str(g) for g in conn.execute_command("GRAPH.LIST")]
        return []

    def close(self) -> None:
        """Close the underlying client if it exposes a close method."""
        close = getattr(self._client, "close", None)
        if callable(close):
            close()

    def __enter__(self) -> "GraphStore":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # Labels that are uniquely identified by their path rather than (name, file_path).
    # These nodes represent filesystem artifacts where two files CAN share a basename
    # but never share a path.
    _PATH_KEYED_LABELS = frozenset({"File", "Document", "Repository"})

    def create_node(self, label: str, props: dict[str, Any]) -> None:
        """
        Upsert a node using a label-appropriate merge key.

        - File / Document / Repository  → keyed by ``path`` (unique per filesystem entry)
        - Memory nodes (have ``memory_type``) → keyed by ``(name, repo)`` to prevent
          same-named memories from different repos colliding
        - Code symbols (Function, Class, …) → keyed by ``(name, file_path)``
        - Knowledge nodes (Rule, Concept, …) → keyed by ``name``
        """
        # Filter out None values — FalkorDB rejects them as params
        props = {k: ("" if v is None else v) for k, v in props.items()}
        prop_str = ", ".join(f"n.{k} = ${k}" for k in props)

        if label in self._PATH_KEYED_LABELS:
            props.setdefault("path", "")
            cypher = f"MERGE (n:{label} {{path: $path}}) SET {prop_str}"
        elif props.get("memory_type", "") and props.get("repo", ""):
            # Memory node — scope to (name, repo) so same-named nodes across repos are distinct
            props.setdefault("name", "")
            cypher = f"MERGE (n:{label} {{name: $name, repo: $repo}}) SET {prop_str}"
        elif props.get("file_path", ""):
            # Code symbol with a known file — disambiguate by (name, file_path)
            props.setdefault("name", "")
            cypher = f"MERGE (n:{label} {{name: $name, file_path: $file_path}}) SET {prop_str}"
        else:
            # Knowledge node or symbol without a file — key by name only
            props.setdefault("name", "")
            cypher = f"MERGE (n:{label} {{name: $name}}) SET {prop_str}"

        self.query(cypher, props)

    def create_edge(
        self,
        from_label: str,
        from_key: dict[str, Any],
        edge_type: str,
        to_label: str,
        to_key: dict[str, Any],
        props: dict[str, Any] | None = None,
    ) -> bool:
        """
        Create a directed edge between two nodes, merging if it already exists.

        Returns True when both endpoints matched (edge created or already
        present), False when an endpoint node does not exist — the MERGE
        silently no-ops in that case, so callers that can retry later (e.g.
        the ingest resolution sweep, #143) need the distinction.
        """
        from_match = ", ".join(f"{k}: $from_{k}" for k in from_key)
        to_match = ", ".join(f"{k}: $to_{k}" for k in to_key)
        prop_set = ""
        if props:
            prop_set = " SET " + ", ".join(f"r.{k} = $p_{k}" for k in props)

        cypher = (
            f"MATCH (a:{from_label} {{{from_match}}}), (b:{to_label} {{{to_match}}}) "
            f"MERGE (a)-[r:{edge_type}]->(b){prop_set} "
            f"RETURN count(r) AS matched"
        )
        params = {f"from_{k}": v for k, v in from_key.items()}
        params.update({f"to_{k}": v for k, v in to_key.items()})
        if props:
            params.update({f"p_{k}": v for k, v in props.items()})

        result = self.query(cypher, params)
        rows = result.result_set or []
        return bool(rows and rows[0][0])

    def clear(self) -> None:
        """Delete all nodes and edges in the graph."""
        self.query("MATCH (n) DETACH DELETE n")
        logger.info("Graph cleared")

    def node_count(self) -> int:
        result = self.query("MATCH (n) RETURN count(n) AS c")
        return result.result_set[0][0] if result.result_set else 0

    def edge_count(self) -> int:
        result = self.query("MATCH ()-[r]->() RETURN count(r) AS c")
        return result.result_set[0][0] if result.result_set else 0


# FalkorDB's default RESULTSET_SIZE silently caps a single query at 10,000
# rows; readers that need the full graph must page below that ceiling.
_DEFAULT_PAGE_SIZE = 5000


def paged_query(store: GraphStore, cypher: str, page_size: int | None = None) -> list:
    """
    Run *cypher* in SKIP/LIMIT pages and return all rows.

    The query must have a deterministic order (ORDER BY) so pages don't
    overlap or miss rows.
    """
    size = page_size or _DEFAULT_PAGE_SIZE
    rows: list = []
    offset = 0
    while True:
        result = store.query(f"{cypher} SKIP {offset} LIMIT {size}")
        page = result.result_set or []
        rows.extend(page)
        if len(page) < size:
            return rows
        offset += size
