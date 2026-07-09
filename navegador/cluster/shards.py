"""
Brain-scale shard load/unload — bounded resident memory for federated graphs.

At company scale the meta-graph doesn't fit in memory as one resident set.
ShardManager pages repo/namespace shards in and out: a shard's embedded
store loads on first access, sits in an LRU order while resident, and is
evicted (closed) when a configurable ceiling is exceeded. Closing a
redislite-backed store persists its RDB, so eviction never loses state —
the next access transparently reloads it.

Ceilings (either or both):
- ``max_resident`` — number of simultaneously loaded shards
- ``max_memory_mb`` — summed ``INFO memory used_memory`` of resident shards

Configured via ``.navegador/config.toml``::

    [cluster]
    max_resident_shards = 4
    max_shard_memory_mb = 512

Usage::

    from navegador.cluster.shards import ShardManager

    with ShardManager({"backend": "/path/a", "frontend": "/path/b"},
                      max_resident=2) as shards:
        result = shards.query("backend", "MATCH (n) RETURN count(n)")
"""

from __future__ import annotations

import logging
import tomllib
from collections import OrderedDict
from pathlib import Path
from typing import Any

from navegador.graph.store import GraphStore

logger = logging.getLogger(__name__)

DEFAULT_MAX_RESIDENT = 4


class ShardManager:
    """LRU-managed loader for a fleet of repo-local graph shards."""

    def __init__(
        self,
        sources: dict[str, str | Path],
        max_resident: int = DEFAULT_MAX_RESIDENT,
        max_memory_mb: float | None = None,
    ) -> None:
        if max_resident < 1:
            raise ValueError("max_resident must be at least 1")
        self.sources = dict(sources)
        self.max_resident = max_resident
        self.max_memory_mb = max_memory_mb
        # repo → GraphStore, in least-recently-used-first order
        self._resident: OrderedDict[str, GraphStore] = OrderedDict()

    @classmethod
    def from_config(
        cls, sources: dict[str, str | Path], project_dir: str | Path = "."
    ) -> "ShardManager":
        """
        Build a manager with ceilings from ``.navegador/config.toml [cluster]``.

        Missing file or keys fall back to defaults (max_resident_shards=4,
        no memory ceiling).
        """
        max_resident = DEFAULT_MAX_RESIDENT
        max_memory_mb = None
        config_path = Path(project_dir) / ".navegador" / "config.toml"
        if config_path.exists():
            cluster = tomllib.loads(config_path.read_text(encoding="utf-8")).get("cluster", {})
            max_resident = int(cluster.get("max_resident_shards", DEFAULT_MAX_RESIDENT))
            if "max_shard_memory_mb" in cluster:
                max_memory_mb = float(cluster["max_shard_memory_mb"])
        return cls(sources, max_resident=max_resident, max_memory_mb=max_memory_mb)

    # ── Access ────────────────────────────────────────────────────────────

    def get(self, repo: str) -> GraphStore:
        """
        Return the shard's store, loading it on demand.

        Touches the LRU order and enforces the resident ceilings (evicting
        least-recently-used shards, never the one just requested).
        """
        if repo in self._resident:
            self._resident.move_to_end(repo)
            return self._resident[repo]

        if repo not in self.sources:
            raise KeyError(f"Unknown shard: {repo!r} (known: {sorted(self.sources)})")

        path = self._graph_path(self.sources[repo])
        store = GraphStore.sqlite(str(path))
        self._resident[repo] = store
        logger.info("Shard %s loaded from %s", repo, path)
        self._enforce(keep=repo)
        return store

    def query(self, repo: str, cypher: str, params: dict[str, Any] | None = None) -> Any:
        """Run a query against a shard, loading it if cold."""
        return self.get(repo).query(cypher, params)

    # ── Introspection ─────────────────────────────────────────────────────

    def resident(self) -> list[str]:
        """Resident shard names, least-recently-used first."""
        return list(self._resident)

    def memory_usage(self) -> dict[str, int]:
        """Bytes of redis ``used_memory`` per resident shard."""
        return {repo: self._used_memory(store) for repo, store in self._resident.items()}

    # ── Eviction ──────────────────────────────────────────────────────────

    def evict(self, repo: str) -> None:
        """Close a shard's store (persists its RDB); no-op when not resident."""
        store = self._resident.pop(repo, None)
        if store is not None:
            store.close()
            logger.info("Shard %s evicted", repo)

    def close_all(self) -> None:
        """Evict every resident shard."""
        for repo in list(self._resident):
            self.evict(repo)

    def __enter__(self) -> "ShardManager":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close_all()

    # ── Internals ─────────────────────────────────────────────────────────

    def _enforce(self, keep: str) -> None:
        """Evict LRU shards until both ceilings hold (keep stays resident)."""
        while len(self._resident) > self.max_resident:
            self._evict_lru(keep)

        if self.max_memory_mb is not None:
            ceiling = self.max_memory_mb * 1024 * 1024
            while len(self._resident) > 1 and self._total_memory() > ceiling:
                self._evict_lru(keep)

    def _evict_lru(self, keep: str) -> None:
        for repo in self._resident:
            if repo != keep:
                self.evict(repo)
                return

    def _total_memory(self) -> int:
        return sum(self._used_memory(store) for store in self._resident.values())

    @staticmethod
    def _used_memory(store: GraphStore) -> int:
        try:
            info = store._client.execute_command("INFO", "memory")
            return int(info.get("used_memory", 0))
        except Exception:
            return 0

    @staticmethod
    def _graph_path(source: str | Path) -> Path:
        path = Path(source)
        if path.is_dir():
            path = path / ".navegador" / "graph.db"
        return path
