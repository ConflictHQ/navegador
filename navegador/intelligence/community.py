"""
CommunityDetector — label propagation community detection over the navegador graph.

Implements a simple synchronous label-propagation algorithm:
1. Each node starts as its own community (label = node id).
2. On each iteration every node adopts the most common label among its
   neighbours (ties broken by lowest label value).
3. Repeat until stable or ``max_iter`` reached.
4. Collect nodes sharing the same label into ``Community`` objects.

Usage::

    from navegador.graph import GraphStore
    from navegador.intelligence.community import CommunityDetector

    store = GraphStore.sqlite(".navegador/graph.db")
    detector = CommunityDetector(store)
    communities = detector.detect(min_size=2)
    detector.store_communities()
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from navegador.graph.store import GraphStore


# ── Cypher helpers ────────────────────────────────────────────────────────────

# Fetch all node ids and names (we use the internal FalkorDB node id via id(n))
_ALL_NODES = """
MATCH (n)
WHERE n.name IS NOT NULL
RETURN id(n) AS id, n.name AS name,
       coalesce(n.file_path, '') AS file_path,
       labels(n)[0] AS type
"""

# Fetch all edges as (from_id, to_id) — undirected for community purposes
_ALL_EDGES = """
MATCH (a)-[r]->(b)
WHERE a.name IS NOT NULL AND b.name IS NOT NULL
RETURN id(a) AS src, id(b) AS dst
"""

# Write the community label back onto a node
_SET_COMMUNITY = """
MATCH (n)
WHERE id(n) = $node_id
SET n.community = $community
"""


# ── Data model ────────────────────────────────────────────────────────────────


@dataclass
class Community:
    """A detected community in the graph.

    Attributes:
        name: Auto-generated name (e.g. ``"community_3"``).  Can be replaced
            by :class:`~navegador.intelligence.nlp.NLPEngine.name_communities`.
        members: Node names belonging to this community.
        size: ``len(members)``.
        density: Fraction of possible internal edges that actually exist
            (0.0–1.0).  Computed lazily; ``-1.0`` means not yet calculated.
    """

    name: str
    members: list[str] = field(default_factory=list)
    size: int = 0
    density: float = -1.0

    def __post_init__(self) -> None:
        if self.size == 0:
            self.size = len(self.members)


# ── Detector ─────────────────────────────────────────────────────────────────


class CommunityDetector:
    """
    Detect communities in the navegador graph via label propagation.

    Args:
        store: A :class:`~navegador.graph.GraphStore` instance.
    """

    def __init__(self, store: "GraphStore") -> None:
        self._store = store
        # Populated after detect()
        self._communities: list[Community] = []
        # id -> label mapping after propagation
        self._labels: dict[int, int] = {}
        # id -> name/meta mapping
        self._nodes: dict[int, dict[str, Any]] = {}

    # ── Public API ────────────────────────────────────────────────────────────

    def detect(self, min_size: int = 2, max_iter: int = 50) -> list[Community]:
        """
        Run label-propagation and return communities with at least *min_size*
        members.

        Args:
            min_size: Minimum community size to include in results.
            max_iter: Maximum number of propagation iterations.

        Returns:
            List of :class:`Community` objects, sorted largest-first.
        """
        nodes, edges = self._load_graph()
        if not nodes:
            self._communities = []
            return []

        # Initialise: each node is its own community
        labels: dict[int, int] = {nid: nid for nid in nodes}

        # Build adjacency list (undirected)
        adj: dict[int, list[int]] = {nid: [] for nid in nodes}
        for src, dst in edges:
            if src in adj and dst in adj:
                adj[src].append(dst)
                adj[dst].append(src)

        # Propagation loop
        for _ in range(max_iter):
            changed = False
            for nid in sorted(nodes):  # deterministic order
                neighbours = adj[nid]
                if not neighbours:
                    continue
                counts: Counter = Counter(labels[nb] for nb in neighbours if nb in labels)
                if not counts:
                    continue
                best_label = min(counts, key=lambda lbl: (-counts[lbl], lbl))
                if best_label != labels[nid]:
                    labels[nid] = best_label
                    changed = True
            if not changed:
                break

        self._labels = labels
        self._nodes = nodes
        self._communities = self._build_communities(labels, nodes, adj, min_size)
        return self._communities

    def store_communities(self) -> int:
        """
        Write community labels as a ``community`` property on all nodes.

        Must call :meth:`detect` first.

        Returns:
            Number of nodes updated.
        """
        updated = 0
        for node_id, label in self._labels.items():
            # Find community name for this label
            comm_name = f"community_{label}"
            for c in self._communities:
                if c.name == comm_name:
                    break
            # Use numeric label as community identifier
            self._store.query(_SET_COMMUNITY, {"node_id": node_id, "community": label})
            updated += 1
        return updated

    # ── Internals ─────────────────────────────────────────────────────────────

    def _load_graph(
        self,
    ) -> tuple[dict[int, dict[str, Any]], list[tuple[int, int]]]:
        """Load all nodes and edges from the store."""
        node_result = self._store.query(_ALL_NODES, {})
        nodes: dict[int, dict[str, Any]] = {}
        for row in node_result.result_set or []:
            nid, name, file_path, node_type = row[0], row[1], row[2], row[3]
            nodes[nid] = {"name": name, "file_path": file_path, "type": node_type}

        edge_result = self._store.query(_ALL_EDGES, {})
        edges: list[tuple[int, int]] = []
        for row in edge_result.result_set or []:
            src, dst = row[0], row[1]
            edges.append((src, dst))

        return nodes, edges

    @staticmethod
    def _build_communities(
        labels: dict[int, int],
        nodes: dict[int, dict[str, Any]],
        adj: dict[int, list[int]],
        min_size: int,
    ) -> list[Community]:
        """Group nodes by label, compute density, filter by min_size."""
        groups: dict[int, list[int]] = {}
        for nid, lbl in labels.items():
            groups.setdefault(lbl, []).append(nid)

        communities: list[Community] = []
        for lbl, members_ids in groups.items():
            if len(members_ids) < min_size:
                continue

            member_set = set(members_ids)
            member_names = [nodes[nid]["name"] for nid in members_ids if nid in nodes]

            # Density = actual internal edges / possible internal edges
            internal_edges = sum(
                1
                for nid in members_ids
                for nb in adj.get(nid, [])
                if nb in member_set
            )
            # Each undirected edge counted twice in the adjacency list
            internal_edges //= 2
            n = len(members_ids)
            possible = n * (n - 1) / 2
            density = internal_edges / possible if possible > 0 else 0.0

            communities.append(
                Community(
                    name=f"community_{lbl}",
                    members=member_names,
                    size=len(member_names),
                    density=round(density, 4),
                )
            )

        communities.sort(key=lambda c: c.size, reverse=True)
        return communities
