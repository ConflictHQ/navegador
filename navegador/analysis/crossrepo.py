"""
Cross-repo blast radius — impact analysis that spans repository boundaries.

Given a symbol change in one repo, finds all affected symbols, files, and
knowledge nodes across repos in a unified or federated workspace graph.

Works with both WorkspaceMode.UNIFIED (single graph, repo tagged by
Repository node) and WorkspaceMode.FEDERATED (per-repo graph shards,
merged in Python).

Usage::

    from navegador.analysis.crossrepo import CrossRepoImpactAnalyzer

    analyzer = CrossRepoImpactAnalyzer(store)
    result = analyzer.blast_radius("UserSchema", repo="shared-models", depth=3)
    print(result.to_markdown())
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from navegador.graph import GraphStore

# Traverse outward from the root symbol, then find Repository nodes that
# contain (directly or transitively) any affected node — cross-repo awareness.
_CROSS_REPO_BLAST = """
MATCH (root)-[:CALLS|REFERENCES|INHERITS|IMPLEMENTS|ANNOTATES*1..$depth]->(affected)
WHERE root.name = $name AND ($file_path = '' OR root.file_path = $file_path)
WITH DISTINCT affected
OPTIONAL MATCH (repo:Repository)-[:CONTAINS*1..3]->(affected)
RETURN DISTINCT
    labels(affected)[0] AS node_type,
    affected.name AS node_name,
    coalesce(affected.file_path, '') AS node_file,
    affected.line_start AS line,
    coalesce(repo.name, '') AS repo_name
"""

_REPO_DEPENDENCIES = """
MATCH (r1:Repository)-[:DEPENDS_ON]->(r2:Repository)
WHERE r1.name = $repo
RETURN r2.name AS dep_repo, r2.path AS dep_path
"""


@dataclass
class CrossRepoImpactResult:
    name: str
    source_repo: str
    depth: int
    affected_nodes: list[dict[str, Any]] = field(default_factory=list)
    affected_files: list[str] = field(default_factory=list)
    affected_repos: list[str] = field(default_factory=list)
    affected_knowledge: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "source_repo": self.source_repo,
            "depth": self.depth,
            "affected_nodes": self.affected_nodes,
            "affected_files": self.affected_files,
            "affected_repos": self.affected_repos,
            "affected_knowledge": self.affected_knowledge,
            "counts": {
                "nodes": len(self.affected_nodes),
                "files": len(self.affected_files),
                "repos": len(self.affected_repos),
                "knowledge": len(self.affected_knowledge),
            },
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    def to_markdown(self) -> str:
        lines = [f"# Cross-Repo Blast Radius — `{self.name}`\n"]
        lines.append(f"**Source repo:** {self.source_repo or 'unknown'}  "
                     f"**Depth:** {self.depth}\n")

        if self.affected_repos:
            lines.append(f"\n## Affected Repos ({len(self.affected_repos)})\n")
            for r in sorted(self.affected_repos):
                lines.append(f"- `{r}`")

        if self.affected_files:
            lines.append(f"\n## Affected Files ({len(self.affected_files)})\n")
            for f in sorted(self.affected_files):
                lines.append(f"- `{f}`")

        if self.affected_nodes:
            lines.append(f"\n## Affected Symbols ({len(self.affected_nodes)})\n")
            for n in self.affected_nodes[:50]:  # cap for readability
                repo = f" [{n.get('repo', '')}]" if n.get("repo") else ""
                loc = f":{n['line_start']}" if n.get("line_start") else ""
                lines.append(
                    f"- **{n['type']}** `{n['name']}` — "
                    f"`{n['file_path']}`{loc}{repo}"
                )
            if len(self.affected_nodes) > 50:
                lines.append(f"  _…and {len(self.affected_nodes) - 50} more_")

        if self.affected_knowledge:
            lines.append(f"\n## Affected Knowledge ({len(self.affected_knowledge)})\n")
            for k in self.affected_knowledge:
                lines.append(f"- **{k['type']}** `{k['name']}`")

        return "\n".join(lines)


class CrossRepoImpactAnalyzer:
    """
    Blast-radius analysis spanning multiple repos in a unified workspace graph.

    For UNIFIED graphs (all repos in one graph), traversal happens in a single
    Cypher query. Repo attribution is inferred from Repository→CONTAINS edges.

    For FEDERATED setups, call blast_radius_federated() instead, passing a
    list of per-repo GraphStore instances.
    """

    def __init__(self, store: GraphStore) -> None:
        self.store = store

    def blast_radius(
        self,
        name: str,
        file_path: str = "",
        repo: str = "",
        depth: int = 3,
    ) -> CrossRepoImpactResult:
        """
        Unified-graph cross-repo blast radius.

        Args:
            name:      Symbol name.
            file_path: Narrow to a specific file (optional).
            repo:      Source repository name for attribution (optional).
            depth:     Traversal depth.
        """
        params: dict[str, Any] = {"name": name, "file_path": file_path, "depth": depth}

        try:
            result = self.store.query(_CROSS_REPO_BLAST, params)
            rows = result.result_set or []
        except Exception:
            rows = []

        affected_nodes: list[dict[str, Any]] = []
        affected_files: set[str] = set()
        affected_repos: set[str] = set()

        for row in rows:
            node_type = row[0] or "Unknown"
            node_name = row[1] or ""
            node_file = row[2] or ""
            line = row[3]
            repo_name = row[4] or ""

            affected_nodes.append({
                "type": node_type,
                "name": node_name,
                "file_path": node_file,
                "line_start": line,
                "repo": repo_name,
            })
            if node_file:
                affected_files.add(node_file)
            if repo_name:
                affected_repos.add(repo_name)

        # Knowledge layer
        _KNOWLEDGE = (
            "MATCH (root)-[:ANNOTATES|IMPLEMENTS|GOVERNS*1..2]->(kn) "
            "WHERE root.name = $name AND ($file_path = '' OR root.file_path = $file_path) "
            "AND (kn:Concept OR kn:Rule OR kn:Decision OR kn:WikiPage) "
            "RETURN DISTINCT labels(kn)[0], kn.name"
        )
        affected_knowledge: list[dict[str, str]] = []
        try:
            k_rows = self.store.query(_KNOWLEDGE, {"name": name, "file_path": file_path})
            for row in k_rows.result_set or []:
                affected_knowledge.append({"type": row[0] or "", "name": row[1] or ""})
        except Exception:
            pass

        return CrossRepoImpactResult(
            name=name,
            source_repo=repo,
            depth=depth,
            affected_nodes=affected_nodes,
            affected_files=sorted(affected_files),
            affected_repos=sorted(affected_repos),
            affected_knowledge=affected_knowledge,
        )

    def blast_radius_federated(
        self,
        name: str,
        stores: dict[str, GraphStore],
        file_path: str = "",
        depth: int = 3,
    ) -> CrossRepoImpactResult:
        """
        Federated cross-repo blast radius — queries each per-repo graph
        independently and merges results.

        Args:
            name:      Symbol name.
            stores:    Mapping of repo_name → GraphStore for each shard.
            file_path: Narrow initial lookup (optional).
            depth:     Traversal depth per repo.
        """
        from navegador.analysis.impact import _BLAST_RADIUS_SIMPLE

        affected_nodes: list[dict[str, Any]] = []
        affected_files: set[str] = set()
        affected_repos: set[str] = set()
        affected_knowledge: list[dict[str, str]] = []

        for repo_name, repo_store in stores.items():
            params = {"name": name, "file_path": file_path, "depth": depth}
            try:
                rows = repo_store.query(_BLAST_RADIUS_SIMPLE, params).result_set or []
            except Exception:
                rows = []

            for row in rows:
                node_type = row[0] or "Unknown"
                node_name = row[1] or ""
                node_file = row[2] or ""
                line = row[3]
                affected_nodes.append({
                    "type": node_type,
                    "name": node_name,
                    "file_path": node_file,
                    "line_start": line,
                    "repo": repo_name,
                })
                if node_file:
                    affected_files.add(node_file)
                if rows:
                    affected_repos.add(repo_name)

        return CrossRepoImpactResult(
            name=name,
            source_repo="",
            depth=depth,
            affected_nodes=affected_nodes,
            affected_files=sorted(affected_files),
            affected_repos=sorted(affected_repos),
            affected_knowledge=affected_knowledge,
        )
