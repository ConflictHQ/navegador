"""
WikiIngester — pulls pages from a project wiki into the knowledge graph.

Supports:
  - GitHub wiki  (cloned as a git repo at <repo>.wiki.git)
  - Local markdown directory  (any folder of .md files)

Each page becomes a WikiPage node. Headings and bold terms are scanned for
names that match existing Concept/Function/Class nodes — matches get a
DOCUMENTS edge so agents can traverse wiki ↔ code.
"""

import logging
import re
import urllib.request
from pathlib import Path

from navegador.graph.schema import EdgeType, NodeLabel
from navegador.graph.store import GraphStore

logger = logging.getLogger(__name__)

_HEADING_RE = re.compile(r"^#{1,6}\s+(.+)", re.MULTILINE)
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")


def _extract_terms(markdown: str) -> list[str]:
    """Pull heading text and bold terms out of a markdown string."""
    terms = [m.group(1).strip() for m in _HEADING_RE.finditer(markdown)]
    for m in _BOLD_RE.finditer(markdown):
        term = (m.group(1) or m.group(2) or "").strip()
        if term:
            terms.append(term)
    return list(dict.fromkeys(terms))  # dedupe, preserve order


class WikiIngester:
    """
    Ingests wiki pages into the navegador graph.

    Usage:
        ingester = WikiIngester(store)

        # From a local directory of markdown files
        ingester.ingest_local("/path/to/wiki-clone")

        # From GitHub (clones the wiki repo)
        ingester.ingest_github("owner/repo")
    """

    def __init__(self, store: GraphStore) -> None:
        self.store = store

    # ── Entry points ──────────────────────────────────────────────────────────

    def ingest_local(self, wiki_dir: str | Path) -> dict[str, int]:
        """Ingest all .md files in a local directory."""
        wiki_dir = Path(wiki_dir)
        if not wiki_dir.exists():
            raise FileNotFoundError(f"Wiki directory not found: {wiki_dir}")

        stats = {"pages": 0, "links": 0}
        for md_file in sorted(wiki_dir.rglob("*.md")):
            content = md_file.read_text(encoding="utf-8", errors="replace")
            page_name = md_file.stem.replace("-", " ").replace("_", " ")
            links = self._ingest_page(
                name=page_name,
                content=content,
                source="local",
                url=str(md_file),
            )
            stats["pages"] += 1
            stats["links"] += links

        logger.info("Wiki (local): %d pages, %d links", stats["pages"], stats["links"])
        return stats

    def ingest_github(
        self,
        repo: str,
        token: str = "",
        clone_dir: str | Path | None = None,
    ) -> dict[str, int]:
        """
        Ingest a GitHub wiki by cloning it then processing locally.

        Args:
            repo: "owner/repo" — the GitHub repository.
            token: GitHub personal access token (needed for private repos).
            clone_dir: Where to clone. Defaults to a temp directory.
        """
        import subprocess
        import tempfile

        wiki_url = f"https://github.com/{repo}.wiki.git"
        if token:
            wiki_url = f"https://{token}@github.com/{repo}.wiki.git"

        if clone_dir is None:
            tmp = tempfile.mkdtemp(prefix="navegador-wiki-")
            clone_dir = Path(tmp)
        else:
            clone_dir = Path(clone_dir)

        logger.info("Cloning wiki %s → %s", wiki_url, clone_dir)
        result = subprocess.run(
            ["git", "clone", "--depth=1", wiki_url, str(clone_dir)],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            # Wiki may not exist yet — treat as empty
            logger.warning("Wiki clone failed: %s", result.stderr.strip())
            return {"pages": 0, "links": 0}

        return self.ingest_local(clone_dir)

    def ingest_github_api(self, repo: str, token: str = "") -> dict[str, int]:
        """
        Ingest GitHub wiki using the GitHub REST API (no git required).
        Falls back gracefully if the wiki is empty or API rate-limited.

        Args:
            repo: "owner/repo"
            token: GitHub personal access token (recommended to avoid rate limits).
        """
        headers = {"Accept": "application/vnd.github.v3+json"}
        if token:
            headers["Authorization"] = f"token {token}"

        owner, name = repo.split("/", 1)
        # GitHub doesn't expose wiki pages via REST — use GraphQL or clone
        # For now we pull the repo's README and docs/ folder as knowledge seeds
        stats = {"pages": 0, "links": 0}

        for path in ["README.md", "CONTRIBUTING.md", "ARCHITECTURE.md", "docs/index.md"]:
            url = f"https://api.github.com/repos/{owner}/{name}/contents/{path}"
            try:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=10) as resp:
                    import base64
                    import json as _json
                    data = _json.loads(resp.read().decode())
                    content = base64.b64decode(data["content"]).decode("utf-8", errors="replace")  # noqa: E501
                    page_name = Path(path).stem.replace("-", " ").replace("_", " ")
                    html_url = data.get("html_url", "")
                    links = self._ingest_page(page_name, content, source="github", url=html_url)
                    stats["pages"] += 1
                    stats["links"] += links
            except Exception as exc:
                logger.debug("Skipping %s: %s", path, exc)

        logger.info("Wiki (GitHub API): %d pages, %d links", stats["pages"], stats["links"])
        return stats

    # ── Internal ──────────────────────────────────────────────────────────────

    def _ingest_page(
        self,
        name: str,
        content: str,
        source: str,
        url: str,
    ) -> int:
        """Store one wiki page and return the number of DOCUMENTS links created."""
        self.store.create_node(NodeLabel.WikiPage, {
            "name": name,
            "url": url,
            "source": source,
            "content": content[:4000],  # cap stored content
        })

        links = 0
        for term in _extract_terms(content):
            links += self._try_link(name, term)

        return links

    def _try_link(self, wiki_page_name: str, term: str) -> int:
        """
        If term matches an existing graph node by name, create a DOCUMENTS edge.
        Returns 1 if a link was created, 0 otherwise.
        """
        # Check across all likely node types
        cypher = """
        MATCH (n)
        WHERE (n:Concept OR n:Class OR n:Function OR n:Method OR n:Rule OR n:Decision)
          AND toLower(n.name) = toLower($term)
        RETURN labels(n)[0] AS label, n.name AS name
        LIMIT 1
        """
        result = self.store.query(cypher, {"term": term})
        rows = result.result_set or []
        if not rows:
            return 0

        label_str, node_name = rows[0][0], rows[0][1]
        try:
            label = NodeLabel(label_str)
            self.store.create_edge(
                NodeLabel.WikiPage, {"name": wiki_page_name},
                EdgeType.DOCUMENTS,
                label, {"name": node_name},
            )
            return 1
        except (ValueError, Exception):
            return 0
