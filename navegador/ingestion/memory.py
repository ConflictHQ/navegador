"""
MemoryIngester — ingests CONFLICT-format memory/ directories into the graph.

Supports two file formats:

1. **Frontmatter format** (Claude's ~/.claude/projects memory files):

    ---
    name: <node name>
    description: <one-line description>
    type: feedback | project | reference | user
    ---

    <body — the actual knowledge content>

2. **Bare format** (repo-committed memory files with no frontmatter):

    <body — the actual knowledge content, first line may be a description>

   For bare files, name and description are derived from:
   - MEMORY.md index entries (human-readable link text + description after —)
   - Filename prefix as type: feedback_*, project_*, reference_*, user_*
   - First non-empty line of content as fallback description

Type mapping:
    feedback  → Rule        (constraints, invariants, coding conventions)
    project   → Decision    (project context, decisions, timelines)
    reference → WikiPage    (external references, documentation pointers)
    user      → Person      (user profile, role, responsibilities)

All memory nodes carry two extra properties:
    memory_type  — the original CONFLICT type string
    repo         — the repository name this memory was ingested from
"""

import logging
import re
from pathlib import Path
from typing import Any

from navegador.graph.schema import EdgeType, NodeLabel
from navegador.graph.store import GraphStore

logger = logging.getLogger(__name__)

# CONFLICT type → NodeLabel
_TYPE_MAP: dict[str, NodeLabel] = {
    "feedback": NodeLabel.Rule,
    "project": NodeLabel.Decision,
    "reference": NodeLabel.WikiPage,
    "user": NodeLabel.Person,
}

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
# Matches: - [Link text](filename.md) — description
_MEMORY_INDEX_RE = re.compile(r"-\s+\[([^\]]+)\]\(([^)]+\.md)\)\s+[—–-]+\s+(.+)")
# Filename prefix for bare format: feedback_*.md, project_*.md, etc.
_PREFIX_TYPE_RE = re.compile(r"^(feedback|project|reference|user)_")


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Split YAML frontmatter from body. Returns (meta, body)."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text

    meta: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()

    body = text[m.end():]
    return meta, body


def _parse_memory_index(memory_md_path: Path) -> dict[str, tuple[str, str]]:
    """
    Parse MEMORY.md and return a mapping of filename → (link_text, description).

    Handles both formats:
    - "- [Human name](file.md) — short description"
    - "- [file.md](file.md) — short description"  (filename as link text)
    """
    if not memory_md_path.exists():
        return {}
    index: dict[str, tuple[str, str]] = {}
    for line in memory_md_path.read_text(encoding="utf-8").splitlines():
        m = _MEMORY_INDEX_RE.search(line)
        if m:
            link_text, filename, description = m.group(1), m.group(2), m.group(3).strip()
            # If the link text is just the filename, don't use it as the name
            name = link_text if link_text != filename else ""
            index[filename] = (name, description)
    return index


def _first_line(text: str) -> str:
    """Return the first non-empty line of text."""
    for line in text.splitlines():
        line = line.strip().lstrip("#").strip()
        if line:
            return line
    return ""


class MemoryIngester:
    """
    Ingests CONFLICT-format memory/ directories into the navegador graph.

    Usage:
        ingester = MemoryIngester(store)
        stats = ingester.ingest("path/to/memory/", repo_name="my-app")
        stats = ingester.ingest("path/to/memory/", repo_name="my-app", clear=True)
    """

    def __init__(self, store: GraphStore) -> None:
        self.store = store

    def ingest(
        self,
        memory_dir: str | Path,
        repo_name: str = "",
        clear: bool = False,
    ) -> dict[str, Any]:
        """
        Ingest all memory files in a directory.

        Handles both frontmatter-annotated files (Claude ~/.claude/projects format)
        and bare markdown files (repo-committed format with filename prefix as type).

        Args:
            memory_dir: Path to the memory/ directory.
            repo_name:  Repository name to scope nodes to. If empty, attempts
                        to resolve from a Repository node in the graph.
            clear:      Remove existing memory nodes for this repo first.
        """
        memory_dir = Path(memory_dir)
        if not memory_dir.exists():
            raise FileNotFoundError(f"Memory directory not found: {memory_dir}")

        if not repo_name:
            repo_name = self._resolve_repo_name(memory_dir)

        if clear:
            self._clear_memory_nodes(repo_name)

        # Parse MEMORY.md index for name/description of bare files
        index = _parse_memory_index(memory_dir / "MEMORY.md")

        stats: dict[str, Any] = {"ingested": 0, "skipped": 0, "repo": repo_name}
        type_counts: dict[str, int] = {}

        for md_file in sorted(memory_dir.glob("*.md")):
            if md_file.name.upper() == "MEMORY.MD":
                continue  # index file — skip

            content_text = md_file.read_text(encoding="utf-8", errors="replace")
            meta, body = _parse_frontmatter(content_text)

            if meta:
                # Frontmatter format
                name = meta.get("name", "").strip()
                description = meta.get("description", "").strip()
                mem_type = meta.get("type", "").strip().lower()
                # Some files have frontmatter but omit type — derive from filename prefix
                if not mem_type:
                    prefix_m = _PREFIX_TYPE_RE.match(md_file.name)
                    if prefix_m:
                        mem_type = prefix_m.group(1)
            else:
                # Bare format — derive from filename and MEMORY.md index
                prefix_m = _PREFIX_TYPE_RE.match(md_file.name)
                if not prefix_m:
                    logger.debug("Skipping %s — no type prefix", md_file.name)
                    stats["skipped"] += 1
                    continue
                mem_type = prefix_m.group(1)
                body = content_text

                idx_name, idx_desc = index.get(md_file.name, ("", ""))
                # Name: prefer MEMORY.md link text, fall back to slug from filename
                if idx_name:
                    name = idx_name
                else:
                    slug = md_file.stem[len(mem_type) + 1:]  # strip "type_" prefix
                    name = slug.replace("_", " ").replace("-", " ")
                # Description: prefer MEMORY.md entry, fall back to first content line
                description = idx_desc or _first_line(body)

            if not name or mem_type not in _TYPE_MAP:
                logger.warning(
                    "Skipping %s — missing name or unknown type %r", md_file.name, mem_type
                )
                stats["skipped"] += 1
                continue

            node_label = _TYPE_MAP[mem_type]
            self._upsert_memory_node(
                node_label, name, description, body.strip(), mem_type, repo_name
            )
            self._link_to_repo(node_label, name, repo_name)

            type_counts[mem_type] = type_counts.get(mem_type, 0) + 1
            stats["ingested"] += 1
            logger.info("Memory [%s] %s → %s", mem_type, name, node_label)

        stats["by_type"] = type_counts
        logger.info(
            "Memory ingest complete: %d nodes, %d skipped (repo=%s)",
            stats["ingested"],
            stats["skipped"],
            repo_name,
        )
        return stats

    def ingest_workspace(
        self,
        workspace_root: str | Path,
        clear: bool = False,
    ) -> dict[str, Any]:
        """
        Traverse all sub-repos (.gitmodules) + workspace root, ingesting each
        memory/ directory scoped to its respective repo.

        Args:
            workspace_root: Root of the workspace / meta-repo.
            clear:          Clear existing memory nodes per repo before re-ingesting.
        """
        workspace_root = Path(workspace_root)
        totals: dict[str, Any] = {"ingested": 0, "skipped": 0, "repos": []}

        # Workspace-root memory/
        root_memory = workspace_root / "memory"
        if root_memory.exists():
            repo_name = workspace_root.name
            result = self.ingest(root_memory, repo_name=repo_name, clear=clear)
            totals["ingested"] += result["ingested"]
            totals["skipped"] += result["skipped"]
            totals["repos"].append(repo_name)

        # Sub-repos from .gitmodules
        gitmodules = workspace_root / ".gitmodules"
        if gitmodules.exists():
            for sub_path in _parse_gitmodules(gitmodules):
                sub_root = workspace_root / sub_path
                sub_memory = sub_root / "memory"
                if sub_memory.exists():
                    repo_name = sub_root.name
                    result = self.ingest(sub_memory, repo_name=repo_name, clear=clear)
                    totals["ingested"] += result["ingested"]
                    totals["skipped"] += result["skipped"]
                    totals["repos"].append(repo_name)

        return totals

    def ingest_recursive(
        self,
        root: str | Path,
        clear: bool = False,
    ) -> dict[str, Any]:
        """
        Find all memory/ directories anywhere under root and ingest each one,
        scoping nodes to the name of the directory containing the memory/ dir.

        Useful for monorepos with per-service memory dirs (e.g. src/{service}/memory/).

        Args:
            root:  Root directory to search under (repo root or any ancestor).
            clear: Clear existing memory nodes per scope before re-ingesting.
        """
        root = Path(root)
        totals: dict[str, Any] = {"ingested": 0, "skipped": 0, "repos": []}

        for memory_dir in sorted(root.rglob("memory")):
            if not memory_dir.is_dir():
                continue
            # Must contain at least one .md file to be a real memory dir
            if not any(memory_dir.glob("*.md")):
                continue
            scope_name = memory_dir.parent.name
            result = self.ingest(memory_dir, repo_name=scope_name, clear=clear)
            totals["ingested"] += result["ingested"]
            totals["skipped"] += result["skipped"]
            totals["repos"].append(scope_name)

        return totals

    # ── Internals ─────────────────────────────────────────────────────────────

    def _upsert_memory_node(
        self,
        label: NodeLabel,
        name: str,
        description: str,
        content: str,
        memory_type: str,
        repo: str,
    ) -> None:
        props: dict[str, Any] = {
            "name": name,
            "description": description,
            "memory_type": memory_type,
            "repo": repo,
        }
        # Map content to the right property per label
        if label == NodeLabel.Rule:
            props["rationale"] = content
        elif label == NodeLabel.Decision:
            props["rationale"] = content
            props["status"] = "accepted"
        elif label == NodeLabel.WikiPage:
            props["content"] = content
            props["source"] = "memory"
        elif label == NodeLabel.Person:
            # For user-type memories: combine description + body as extended description
            props["description"] = f"{description}\n\n{content}".strip() if content else description

        self.store.create_node(label, props)

    def _link_to_repo(self, label: NodeLabel, name: str, repo_name: str) -> None:
        """Link a memory node to its Repository node (if one exists)."""
        if not repo_name:
            return
        try:
            self.store.create_edge(
                NodeLabel.Repository,
                {"name": repo_name},
                EdgeType.CONTAINS,
                label,
                {"name": name},
            )
        except Exception:
            # Repository node may not exist yet — non-fatal
            logger.debug("Could not link %s → Repository %r (node may not exist)", name, repo_name)

    def _clear_memory_nodes(self, repo_name: str) -> None:
        """Remove all memory-typed nodes for a given repo."""
        cypher = (
            "MATCH (n) "
            "WHERE n.memory_type IS NOT NULL AND n.repo = $repo "
            "DETACH DELETE n"
        )
        self.store.query(cypher, {"repo": repo_name})
        logger.info("Cleared memory nodes for repo=%s", repo_name)

    def _resolve_repo_name(self, memory_dir: Path) -> str:
        """Guess repo name from directory structure (parent of memory/)."""
        parent = memory_dir.parent
        # Try to find a matching Repository node in the graph
        try:
            result = self.store.query(
                "MATCH (r:Repository) WHERE r.path = $path RETURN r.name LIMIT 1",
                {"path": str(parent)},
            )
            if result.result_set:
                return result.result_set[0][0]
        except Exception:
            pass
        return parent.name


def _parse_gitmodules(gitmodules_path: Path) -> list[str]:
    """Extract submodule paths from a .gitmodules file."""
    paths: list[str] = []
    for line in gitmodules_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("path"):
            _, _, value = line.partition("=")
            path = value.strip()
            if path:
                paths.append(path)
    return paths
