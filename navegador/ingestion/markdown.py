"""
MarkdownParser — ingests *.md files as Document nodes in the navegador graph.

Each markdown file becomes a Document node. Inter-document links (via
[text](other.md) syntax) become REFERENCES edges between Document nodes.

This makes documentation and scaffold-stage repos (no code yet) navigable
through navegador — bootstrap.md, CLAUDE.md, AGENTS.md, README.md, and
any other markdown documentation are first-class graph nodes.

Note: memory/ directory files are intentionally excluded here — they are
handled by MemoryIngester which maps them to typed knowledge nodes
(Rule, Decision, WikiPage, Person).
"""

import logging
import re
from pathlib import Path

from navegador.graph.schema import EdgeType, NodeLabel
from navegador.graph.store import GraphStore
from navegador.ingestion.parser import LanguageParser

logger = logging.getLogger(__name__)

_H1_RE = re.compile(r"^#\s+(.+)", re.MULTILINE)
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+\.md[^)]*)\)")


def _extract_title(content: str, filename: str) -> str:
    """Use first H1 heading, fall back to filename stem."""
    m = _H1_RE.search(content)
    if m:
        return m.group(1).strip()
    return Path(filename).stem.replace("-", " ").replace("_", " ")


def _extract_md_links(content: str) -> list[str]:
    """Return relative .md paths linked from the document."""
    links = []
    for m in _MD_LINK_RE.finditer(content):
        href = m.group(2).strip()
        # Skip anchors and absolute URLs
        if href.startswith(("http://", "https://", "#")):
            continue
        # Strip any anchor fragment
        href = href.split("#")[0]
        if href.endswith(".md"):
            links.append(href)
    return links


class MarkdownParser(LanguageParser):
    """
    Ingests markdown files as Document nodes.

    Skips files inside memory/ directories (handled by MemoryIngester).
    """

    def parse_file(self, path: Path, repo_root: Path, store: GraphStore) -> dict[str, int]:
        # Skip memory directory files — MemoryIngester owns those
        if "memory" in path.parts:
            return {}

        content = path.read_text(encoding="utf-8", errors="replace")
        rel_path = str(path.relative_to(repo_root))
        title = _extract_title(content, path.name)

        store.create_node(
            NodeLabel.Document,
            {
                "name": title,
                "path": rel_path,
                "title": title,
                "content": content[:4000],  # cap stored content to 4KB
            },
        )

        # Create REFERENCES edges for internal markdown links
        links_created = 0
        for linked_rel in _extract_md_links(content):
            # Resolve relative to the document's directory
            linked_path = (path.parent / linked_rel).resolve()
            try:
                linked_rel_path = str(linked_path.relative_to(repo_root))
            except ValueError:
                continue  # link escapes repo root — skip

            if linked_path.exists():
                linked_title = _extract_title(
                    linked_path.read_text(encoding="utf-8", errors="replace"),
                    linked_path.name,
                )
                store.create_node(
                    NodeLabel.Document,
                    {
                        "name": linked_title,
                        "path": linked_rel_path,
                        "title": linked_title,
                        "content": "",
                    },
                )
                try:
                    store.create_edge(
                        NodeLabel.Document,
                        {"name": title},
                        EdgeType.REFERENCES,
                        NodeLabel.Document,
                        {"name": linked_title},
                    )
                    links_created += 1
                except Exception:
                    pass

        logger.debug("Document: %s (%d links)", rel_path, links_created)
        return {"documents": 1, "links": links_created}
