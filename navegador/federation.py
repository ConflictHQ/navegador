"""
Federated super-graph aggregation — roll repo-local graphs up into one
central meta-graph.

Every repo builds its own local Navegador graph; the aggregator merges N of
them (open stores, repo roots, or graph files) bottom-up into a single
central FalkorDB super-graph:

- **Namespacing** — non-knowledge nodes get a ``repo`` property and their
  ``file_path``/``path`` prefixed ``<repo>/`` so merge keys never collide
  across repos. Name-keyed nodes without a path get the prefix on ``name``
  (original kept as ``local_name``).
- **Anchoring** — a synthetic ``Repository`` node per repo, with CONTAINS
  edges to that repo's File nodes.
- **Knowledge dedup** — Concept / Person / Domain / Rule merge by name into
  one un-namespaced node carrying an accumulated ``repos`` property, so
  IMPLEMENTS / ANNOTATES / knowledge edges from every repo resolve to the
  same node. Cross-repo linkage is persisted in the central graph, not
  merged per-query.

Aggregation goes through MERGE semantics end to end, so re-running after a
repo re-ingest is idempotent.

Usage::

    from navegador.federation import SuperGraphAggregator

    agg = SuperGraphAggregator(central_store)
    stats = agg.aggregate({
        "backend": "/path/to/backend",            # repo root or graph file
        "frontend": frontend_store,               # or an open GraphStore
    })
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from navegador.graph.interchange import collect_graph
from navegador.graph.schema import EdgeType, NodeLabel
from navegador.graph.store import GraphStore

logger = logging.getLogger(__name__)

# Knowledge labels unified across repos instead of namespaced per repo.
KNOWLEDGE_DEDUP_LABELS = frozenset(
    {NodeLabel.Concept, NodeLabel.Person, NodeLabel.Domain, NodeLabel.Rule}
)

_REPOS_SEPARATOR = ","

# Brain-side proposal contract (project-brain schemas/proposals.schema.json).
# The central code graph merges knowledge nodes by name — that is this realm's
# own store and its own call. But a brain must never INHERIT a merge it did not
# decide: identity across brains is an edge a curator accepts, never a merge
# (project-brain docs/patterns/brain-scope.md). So every knowledge unification
# the aggregator performs is ALSO recorded as a same_as proposal in the brain's
# proposals/v1 shape, with the evidence stated, for the brain to accept or not.
PROPOSALS_FORMAT = "proposals/v1"
PROPOSAL_CONFIDENCE = 0.6
_LABEL_TO_BRAIN_KIND = {
    "Concept": "glossary-term",
    "Person": "person",
    "Domain": "kg-entity",
    "Rule": "decision",
}


def _slug(text: str) -> str:
    out = []
    for ch in str(text).strip().lower():
        if ch.isalnum():
            out.append(ch)
        elif out and out[-1] != "-":
            out.append("-")
    return "".join(out).strip("-") or "item"


def _brain_address(repo: str, label: str, name: str) -> str:
    kind = _LABEL_TO_BRAIN_KIND.get(str(label), str(label).lower())
    return f"{repo}/{kind}:{_slug(name)}"


class SuperGraphAggregator:
    """Merge repo-local graphs bottom-up into a central super-graph."""

    def __init__(self, central: GraphStore) -> None:
        self.central = central
        self.proposals: list[dict] = []

    def aggregate(
        self, sources: dict[str, GraphStore | str | Path], clear: bool = False
    ) -> dict[str, Any]:
        """
        Aggregate every source graph into the central store.

        Args:
            sources: repo name → open GraphStore, repo root directory
                (resolves ``<root>/.navegador/graph.db``), a graph file path,
                or the name of a graph already resident in the central
                database (``<name>`` or ``navegador_<name>``, as written by
                ``workspace ingest --mode federated``).
            clear: If True, wipe the central graph first.

        Returns:
            Dict keyed by repo name → per-repo stats (or ``{"error": ...}``).
        """
        if clear:
            self.central.clear()

        summary: dict[str, Any] = {}
        for name, source in sources.items():
            opened: GraphStore | None = None
            try:
                if isinstance(source, GraphStore):
                    store = source
                else:
                    try:
                        opened = GraphStore.sqlite(str(resolve_graph_path(source)))
                        store = opened
                    except FileNotFoundError:
                        # Not a local graph — try graphs resident in the
                        # central database. Shares the central client, so it
                        # must not be closed here.
                        resident = self._resolve_central_graph(str(source))
                        if resident is None:
                            raise FileNotFoundError(
                                f"No graph found at {source} — run `navegador ingest` "
                                f"first, or pass the name of a graph resident in the "
                                f"connected database (no '{source}' or "
                                f"'navegador_{source}' graph found there)"
                            ) from None
                        store = resident
                summary[name] = self.aggregate_repo(name, store)
            except Exception as exc:  # noqa: BLE001
                logger.error("Aggregation failed for repo %s: %s", name, exc)
                summary[name] = {"error": str(exc)}
            finally:
                if opened is not None:
                    opened.close()
        return summary

    def _resolve_central_graph(self, name: str) -> GraphStore | None:
        """
        Resolve *name* to a graph resident in the central database.

        Tries the exact name first, then the ``navegador_<name>`` convention
        used by federated workspace ingest. Returns None when neither exists.
        """
        resident = set(self.central.list_graphs())
        for candidate in (name, f"navegador_{name}"):
            if candidate in resident:
                if candidate == self.central.graph_name:
                    raise ValueError(
                        f"Source graph {candidate!r} is the aggregation target itself — "
                        f"aggregate into a different graph (e.g. --graph navegador_supergraph)"
                    )
                return self.central.with_graph(candidate)
        return None

    def proposals_payload(self, scope: str = "") -> dict:
        """Equivalence proposals in the brain's proposals/v1 shape, deduplicated
        and ordered. ``scope`` is carried when stated (the brain's policy
        namespace beside repo)."""
        seen: set[str] = set()
        out = []
        for p in sorted(self.proposals, key=lambda x: x["id"]):
            if p["id"] in seen:
                continue
            seen.add(p["id"])
            out.append(p)
        payload = {
            "format": PROPOSALS_FORMAT,
            "generator": "navegador",
            "contract": "1.0",
            "realm": "code",
            "proposals": out,
        }
        if scope:
            payload["scope"] = scope
        return payload

    def aggregate_repo(self, repo: str, source: GraphStore) -> dict[str, int]:
        """
        Merge one repo-local graph into the central store.

        Returns:
            Stats dict: nodes, edges, deduped (knowledge nodes unified).
        """
        nodes, edges = collect_graph(source)

        # Synthetic per-repo anchor node
        self.central.create_node(
            NodeLabel.Repository,
            {"name": repo, "path": repo, "description": "federated-repo-anchor"},
        )

        # id (source conflict-kg id) → (label, merge-key props in central)
        key_map: dict[str, tuple[str, dict]] = {}
        deduped = 0

        for node in nodes:
            label, name, props = node["type"], node["name"], dict(node["props"])
            if label in KNOWLEDGE_DEDUP_LABELS:
                self._merge_knowledge_node(label, name, props, repo)
                key_map[node["id"]] = (label, {"name": name})
                deduped += 1
                continue

            props["name"] = name
            props["repo"] = repo
            if props.get("file_path"):
                props["file_path"] = f"{repo}/{props['file_path']}"
            if props.get("path"):
                props["path"] = f"{repo}/{props['path']}"
            if not props.get("file_path") and not props.get("path"):
                props["local_name"] = name
                props["name"] = f"{repo}/{name}"

            self.central.create_node(label, props)
            key_map[node["id"]] = (label, _central_merge_key(label, props))

            if label == NodeLabel.File:
                self.central.create_edge(
                    NodeLabel.Repository,
                    {"name": repo, "path": repo},
                    EdgeType.CONTAINS,
                    NodeLabel.File,
                    {"path": props.get("path", "")},
                )

        edge_count = 0
        for edge in edges:
            src, tgt = key_map.get(edge["source"]), key_map.get(edge["target"])
            if src is None or tgt is None:
                logger.warning("Skipping edge with unknown endpoint: %s", edge)
                continue
            self.central.create_edge(
                src[0], src[1], edge["type"], tgt[0], tgt[1], edge.get("props") or None
            )
            edge_count += 1

        logger.info(
            "Aggregated repo %s: %d nodes (%d knowledge-deduped), %d edges",
            repo,
            len(nodes),
            deduped,
            edge_count,
        )
        return {"nodes": len(nodes), "edges": edge_count, "deduped": deduped}

    def _merge_knowledge_node(self, label: str, name: str, props: dict, repo: str) -> None:
        """Upsert a shared knowledge node and add ``repo`` to its repos property."""
        props.pop("repos", None)
        self.central.create_node(label, {"name": name, **props})

        result = self.central.query(
            f"MATCH (n:{label} {{name: $name}}) RETURN n.repos", {"name": name}
        )
        current = result.result_set[0][0] if result.result_set else None
        repos = set(filter(None, (current or "").split(_REPOS_SEPARATOR)))
        # The central graph unifies; the brain decides. Record the unification
        # as a proposal against every repo this node was already seen in.
        for other in sorted(repos - {repo}):
            a, b = sorted((_brain_address(other, label, name), _brain_address(repo, label, name)))
            self.proposals.append(
                {
                    "id": f"eq:{a}~{b}",
                    "kind": "equivalence",
                    "rel": "same_as",
                    "source": a,
                    "target": b,
                    "confidence": PROPOSAL_CONFIDENCE,
                    "evidence": f"identical {label} name {name!r} in repos {other!r} and {repo!r}",
                    "proposed_by": "navegador",
                    "status": "proposed",
                }
            )
        repos.add(repo)
        self.central.query(
            f"MATCH (n:{label} {{name: $name}}) SET n.repos = $repos",
            {"name": name, "repos": _REPOS_SEPARATOR.join(sorted(repos))},
        )


def repo_name_from_path(source: str | Path) -> str:
    """Default repo namespace for a source path: the repo directory's name."""
    path = Path(source)
    if path.name == "graph.db" and path.parent.name == ".navegador":
        return path.parent.parent.name
    if path.is_file():
        return path.stem
    return path.name


def resolve_graph_path(source: str | Path) -> Path:
    """
    Resolve a source argument to a graph file.

    A directory resolves to ``<dir>/.navegador/graph.db``; a file is used
    as-is. Raises FileNotFoundError if no graph file exists there.
    """
    path = Path(source)
    if path.is_dir():
        path = path / ".navegador" / "graph.db"
    if not path.exists():
        raise FileNotFoundError(f"No graph found at {path} — run `navegador ingest` first")
    return path


def _central_merge_key(label: str, props: dict) -> dict:
    """Merge-key props in the central graph, mirroring GraphStore.create_node."""
    if label in GraphStore._PATH_KEYED_LABELS:
        return {"path": props.get("path", "")}
    if props.get("memory_type", "") and props.get("repo", ""):
        return {"name": props.get("name", ""), "repo": props["repo"]}
    if props.get("file_path", ""):
        return {"name": props.get("name", ""), "file_path": props["file_path"]}
    return {"name": props.get("name", "")}
