"""
WorkPartitioner — divide graph work across N agents.

Splits the set of ingested files into roughly equal partitions so that
multiple agents can ingest or analyse different parts of a repository
concurrently without overlap.

Usage:
    from navegador.graph.store import GraphStore
    from navegador.cluster.partitioning import WorkPartitioner

    store = GraphStore.sqlite(".navegador/graph.db")
    partitioner = WorkPartitioner(store)
    partitions = partitioner.partition(n_agents=4)

    for p in partitions:
        print(p.agent_id, p.file_paths, p.estimated_work)
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class Partition:
    """A slice of repository work assigned to one agent."""

    agent_id: str
    file_paths: list[str]
    estimated_work: int  # proxy = number of files

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "file_paths": self.file_paths,
            "estimated_work": self.estimated_work,
        }


class WorkPartitioner:
    """
    Partition repository files across N agents.

    The current implementation uses a simple round-robin file-count split.
    A future version can replace this with a graph community detection
    algorithm (e.g. Louvain via `networkx`) for tighter semantic cohesion.

    Parameters
    ----------
    store:
        A ``GraphStore`` instance to query file paths from.
    """

    def __init__(self, store: Any) -> None:
        self._store = store

    # ── Internal ──────────────────────────────────────────────────────────────

    def _get_all_file_paths(self) -> list[str]:
        """Retrieve distinct file paths recorded in the graph."""
        result = self._store.query(
            "MATCH (n) WHERE n.file_path IS NOT NULL "
            "RETURN DISTINCT n.file_path AS fp ORDER BY fp"
        )
        if not result.result_set:
            return []
        paths: list[str] = []
        for row in result.result_set:
            fp = row[0]
            if fp and fp not in paths:
                paths.append(fp)
        return paths

    @staticmethod
    def _split_evenly(items: list[str], n: int) -> list[list[str]]:
        """Split *items* into *n* roughly equal-sized buckets."""
        if n <= 0:
            raise ValueError("n_agents must be >= 1")
        if not items:
            return [[] for _ in range(n)]
        chunk_size = math.ceil(len(items) / n)
        buckets = []
        for i in range(0, len(items), chunk_size):
            buckets.append(items[i: i + chunk_size])
        # Pad with empty lists if fewer chunks than agents
        while len(buckets) < n:
            buckets.append([])
        return buckets[:n]

    # ── Public API ────────────────────────────────────────────────────────────

    def partition(self, n_agents: int) -> list[Partition]:
        """
        Divide all graph file paths into *n_agents* partitions.

        Parameters
        ----------
        n_agents:
            Number of agents (partitions) to create.  Must be >= 1.

        Returns
        -------
        list[Partition]
            One ``Partition`` per agent.  Agent IDs are ``"agent-0"``,
            ``"agent-1"``, … ``"agent-(n-1)"``.
        """
        if n_agents < 1:
            raise ValueError("n_agents must be >= 1")

        file_paths = self._get_all_file_paths()
        buckets = self._split_evenly(file_paths, n_agents)

        partitions = [
            Partition(
                agent_id=f"agent-{i}",
                file_paths=bucket,
                estimated_work=len(bucket),
            )
            for i, bucket in enumerate(buckets)
        ]

        logger.info(
            "Partitioned %d files across %d agents",
            len(file_paths),
            n_agents,
        )
        return partitions
