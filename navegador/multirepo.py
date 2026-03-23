"""
Multi-repo support — index and query across multiple repositories.

Issue: #62 adds WorkspaceMode (UNIFIED / FEDERATED) and WorkspaceManager.

Usage::

    from navegador.multirepo import MultiRepoManager, WorkspaceMode, WorkspaceManager

    # Legacy: single shared graph
    mgr = MultiRepoManager(store)
    mgr.add_repo("backend", "/path/to/backend")
    mgr.add_repo("frontend", "/path/to/frontend")
    stats = mgr.ingest_all()
    results = mgr.cross_repo_search("authenticate")

    # v0.4: workspace with explicit mode
    ws = WorkspaceManager(store, mode=WorkspaceMode.UNIFIED)
    ws.add_repo("backend", "/path/to/backend")
    ws.add_repo("frontend", "/path/to/frontend")
    stats = ws.ingest_all()
    results = ws.search("authenticate")

    # Federated: each repo gets its own graph; cross-repo queries merge results
    ws_fed = WorkspaceManager(store, mode=WorkspaceMode.FEDERATED)
    ws_fed.add_repo("backend", "/path/to/backend")
    results = ws_fed.search("authenticate")
"""

from __future__ import annotations

import logging
from enum import Enum
from pathlib import Path
from typing import Any

from navegador.graph.schema import NodeLabel
from navegador.graph.store import GraphStore

logger = logging.getLogger(__name__)

# Key used to store repo registry as a special node in the graph
_REGISTRY_LABEL = "RepoRegistry"


# ── WorkspaceMode ─────────────────────────────────────────────────────────────


class WorkspaceMode(str, Enum):
    """
    Controls how a multi-repo workspace stores its graph data.

    UNIFIED
        All repositories share one graph.  Cross-repo traversal is trivial
        but repo isolation is not enforced.

    FEDERATED
        Each repository gets its own named graph.  Cross-repo queries are
        executed against each graph in turn and the results are merged.
        Provides namespace isolation — nodes in repo A cannot accidentally
        collide with nodes in repo B.
    """

    UNIFIED = "unified"
    FEDERATED = "federated"


# ── WorkspaceManager ──────────────────────────────────────────────────────────


class WorkspaceManager:
    """
    Multi-repo workspace with explicit UNIFIED or FEDERATED mode.

    In UNIFIED mode this is a thin wrapper around :class:`MultiRepoManager`
    backed by a single shared :class:`~navegador.graph.store.GraphStore`.

    In FEDERATED mode each repo is tracked with its own graph name.  Queries
    fan out across all per-repo graphs and merge the result lists.
    """

    def __init__(self, store: GraphStore, mode: WorkspaceMode = WorkspaceMode.UNIFIED) -> None:
        self.store = store
        self.mode = mode
        # repo name → {"path": str, "graph_name": str}
        self._repos: dict[str, dict[str, str]] = {}

    # ── Registration ──────────────────────────────────────────────────────────

    def add_repo(self, name: str, path: str | Path) -> None:
        """Register a repository by name and filesystem path."""
        resolved = str(Path(path).resolve())
        graph_name = f"navegador_{name}" if self.mode == WorkspaceMode.FEDERATED else "navegador"
        self._repos[name] = {"path": resolved, "graph_name": graph_name}

        # Persist registration as a Repository node in the shared store
        self.store.create_node(
            NodeLabel.Repository,
            {
                "name": name,
                "path": resolved,
                "description": f"workspace:{self.mode.value}",
                "language": "",
                "file_path": resolved,
            },
        )
        logger.info("WorkspaceManager (%s): registered %s → %s", self.mode.value, name, resolved)

    def list_repos(self) -> list[dict[str, str]]:
        """Return all registered repositories."""
        return [
            {"name": name, "path": info["path"], "graph_name": info["graph_name"]}
            for name, info in self._repos.items()
        ]

    # ── Ingestion ─────────────────────────────────────────────────────────────

    def ingest_all(self, clear: bool = False) -> dict[str, Any]:
        """
        Ingest every registered repository according to the workspace mode.

        In UNIFIED mode all repos are ingested into the shared store.
        In FEDERATED mode each repo is ingested into its own named graph.

        Returns
        -------
        dict keyed by repo name → ingestion stats
        """
        from navegador.ingestion.parser import RepoIngester

        if not self._repos:
            logger.warning("WorkspaceManager: no repositories registered")
            return {}

        if clear:
            self.store.clear()

        summary: dict[str, Any] = {}

        for name, info in self._repos.items():
            path = info["path"]
            logger.info("WorkspaceManager (%s): ingesting %s", self.mode.value, name)

            if self.mode == WorkspaceMode.FEDERATED:
                # Each repo uses its own graph — create a per-repo store
                target_store = self._federated_store(info["graph_name"])
            else:
                target_store = self.store

            try:
                ingester = RepoIngester(target_store)
                stats = ingester.ingest(path, clear=False)
                summary[name] = stats
            except Exception as exc:  # noqa: BLE001
                logger.error("WorkspaceManager: failed to ingest %s: %s", name, exc)
                summary[name] = {"error": str(exc)}

        return summary

    # ── Search ────────────────────────────────────────────────────────────────

    def search(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        """
        Search across all repositories.

        In UNIFIED mode queries the single shared graph.
        In FEDERATED mode fans out across each per-repo graph and merges.

        Returns
        -------
        list of dicts with keys: label, name, file_path, repo
        """
        if self.mode == WorkspaceMode.UNIFIED:
            return self._search_store(self.store, query, limit)

        # Federated: merge results from each repo's graph
        all_results: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()

        for name, info in self._repos.items():
            try:
                target_store = self._federated_store(info["graph_name"])
                results = self._search_store(target_store, query, limit)
                for r in results:
                    key = (r.get("label", ""), r.get("name", ""))
                    if key not in seen:
                        seen.add(key)
                        r["repo"] = name
                        all_results.append(r)
            except Exception:
                logger.debug("WorkspaceManager: search failed for repo %s", name, exc_info=True)

        return all_results[:limit]

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _federated_store(self, graph_name: str) -> GraphStore:
        """
        Return a GraphStore that uses the per-repo graph name.

        Shares the underlying DB client from self.store but selects a
        different named graph.
        """
        store = GraphStore.__new__(GraphStore)
        store._client = self.store._client
        store._graph = self.store._client.select_graph(graph_name)
        return store

    @staticmethod
    def _search_store(store: GraphStore, query: str, limit: int) -> list[dict[str, Any]]:
        cypher = (
            "MATCH (n) "
            "WHERE toLower(n.name) CONTAINS toLower($q) "
            "RETURN labels(n)[0] AS label, n.name AS name, "
            "       coalesce(n.file_path, n.path, '') AS file_path "
            f"LIMIT {int(limit)}"
        )
        try:
            result = store.query(cypher, {"q": query})
            rows = result.result_set or []
        except Exception:
            return []
        return [
            {"label": row[0] or "", "name": row[1] or "", "file_path": row[2] or "", "repo": ""}
            for row in rows
        ]


class MultiRepoManager:
    """
    Register, ingest, and query across multiple repositories.

    Repos are persisted as Repository nodes in the graph so they survive
    process restarts.  A lightweight in-memory cache is layered on top for
    the current session.
    """

    def __init__(self, store: GraphStore) -> None:
        self.store = store

    # ── Registration ──────────────────────────────────────────────────────────

    def add_repo(self, name: str, path: str | Path) -> None:
        """Register a repository by name and filesystem path."""
        resolved = str(Path(path).resolve())
        self.store.create_node(
            NodeLabel.Repository,
            {
                "name": name,
                "path": resolved,
                "description": "",
                "file_path": resolved,
            },
        )
        logger.info("MultiRepo: registered %s → %s", name, resolved)

    # ── Query ─────────────────────────────────────────────────────────────────

    def list_repos(self) -> list[dict[str, Any]]:
        """Return all registered repositories."""
        result = self.store.query("MATCH (r:Repository) RETURN r.name, r.path ORDER BY r.name")
        rows = result.result_set or []
        return [{"name": row[0], "path": row[1]} for row in rows]

    # ── Ingestion ─────────────────────────────────────────────────────────────

    def ingest_all(self, clear: bool = False) -> dict[str, Any]:
        """
        Ingest every registered repository.

        Returns a summary dict keyed by repo name, each value being the
        ingestion stats returned by RepoIngester.
        """
        from navegador.ingestion.parser import RepoIngester

        repos = self.list_repos()
        if not repos:
            logger.warning("MultiRepo: no repositories registered")
            return {}

        if clear:
            self.store.clear()

        summary: dict[str, Any] = {}
        for repo in repos:
            name = repo["name"]
            path = repo["path"]
            logger.info("MultiRepo: ingesting %s from %s", name, path)
            try:
                ingester = RepoIngester(self.store)
                stats = ingester.ingest(path, clear=False)
                summary[name] = stats
            except Exception as exc:  # noqa: BLE001
                logger.error("MultiRepo: failed to ingest %s: %s", name, exc)
                summary[name] = {"error": str(exc)}

        return summary

    # ── Search ────────────────────────────────────────────────────────────────

    def cross_repo_search(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        """
        Full-text name search across all node types in all registered repos.

        Returns a list of dicts with keys: label, name, file_path.
        """
        cypher = (
            "MATCH (n) "
            "WHERE toLower(n.name) CONTAINS toLower($q) "
            "RETURN labels(n)[0] AS label, n.name AS name, "
            "       coalesce(n.file_path, n.path, '') AS file_path "
            f"LIMIT {int(limit)}"
        )
        result = self.store.query(cypher, {"q": query})
        rows = result.result_set or []
        return [{"label": row[0], "name": row[1], "file_path": row[2]} for row in rows]
