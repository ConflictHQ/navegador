"""
FossilIngester — ingests Fossil SCM wiki pages and tickets into the graph.
FossilWikiSync — syncs wiki content between Fossil and GitHub wiki.

Fossil is a self-contained DVCS that ships with its own wiki and bug-tracker.
This module provides two top-level classes:

FossilIngester
    Reads Fossil wiki pages and tickets into the navegador graph.

    Wiki pages  → WikiPage nodes (same label used for GitHub wiki)
    Tickets     → Ticket nodes  (label added in schema v12)

FossilWikiSync
    Copies wiki content between a Fossil checkout and a GitHub wiki.
    Direction is always explicit — caller chooses push (Fossil→GitHub) or
    pull (GitHub→Fossil).  Pages present only on the destination are left
    untouched; source pages overwrite their counterpart.

Usage::

    from navegador.vcs import FossilAdapter
    from navegador.ingestion.fossil import FossilIngester, FossilWikiSync

    adapter = FossilAdapter("/path/to/fossil-checkout")

    # Graph ingestion
    ingester = FossilIngester(store, adapter)
    ingester.ingest_wiki()
    ingester.ingest_tickets()

    # Wiki sync
    sync = FossilWikiSync(adapter, "owner/repo", token="ghp_...")
    sync.fossil_to_github()   # push Fossil → GitHub
    sync.github_to_fossil()   # pull GitHub → Fossil
"""

import logging
import re
from pathlib import Path

from navegador.graph.schema import EdgeType, NodeLabel
from navegador.graph.store import GraphStore
from navegador.vcs import FossilAdapter

logger = logging.getLogger(__name__)

_HEADING_RE = re.compile(r"^#{1,6}\s+(.+)", re.MULTILINE)
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")

# Fossil wiki can also contain WikiCreole markup; handle == headings ==
_CREOLE_HEADING_RE = re.compile(r"^={1,6}\s+(.+?)\s*=*$", re.MULTILINE)


def _extract_terms(content: str) -> list[str]:
    """Pull heading text and bold terms out of markdown/creole content."""
    terms: list[str] = []
    for m in _HEADING_RE.finditer(content):
        terms.append(m.group(1).strip())
    for m in _CREOLE_HEADING_RE.finditer(content):
        terms.append(m.group(1).strip())
    for m in _BOLD_RE.finditer(content):
        term = (m.group(1) or m.group(2) or "").strip()
        if term:
            terms.append(term)
    return list(dict.fromkeys(terms))  # dedupe, preserve order


class FossilIngester:
    """
    Ingests Fossil SCM wiki pages and tickets into the navegador graph.

    Parameters
    ----------
    store:
        GraphStore to write into.
    adapter:
        FossilAdapter pointing at the Fossil checkout to read from.
    repo_name:
        Optional repository name stored on each node; defaults to the
        checkout directory name.
    """

    def __init__(
        self,
        store: GraphStore,
        adapter: FossilAdapter,
        repo_name: str = "",
    ) -> None:
        self.store = store
        self.adapter = adapter
        self.repo_name = repo_name or Path(adapter.repo_path).name

    # ── Wiki ──────────────────────────────────────────────────────────────────

    def ingest_wiki(self) -> dict[str, int]:
        """
        Ingest all wiki pages from the Fossil repo.

        Returns a stats dict with keys: ``pages``, ``edges``.
        """
        pages = self.adapter.wiki_pages()
        stats = {"pages": 0, "edges": 0}

        for page_name in pages:
            content = self.adapter.wiki_export(page_name)
            self._ingest_wiki_page(page_name, content, stats)

        logger.info(
            "FossilIngester.ingest_wiki [%s]: %d pages, %d edges",
            self.repo_name,
            stats["pages"],
            stats["edges"],
        )
        return stats

    def _ingest_wiki_page(self, name: str, content: str, stats: dict[str, int]) -> None:
        self.store.create_node(
            NodeLabel.WikiPage,
            {
                "name": name,
                "url": "",
                "source": "fossil",
                "content": content,
                "updated_at": "",
            },
        )
        stats["pages"] += 1

        # Link page to known code symbols via DOCUMENTS edges
        for term in _extract_terms(content):
            for label in (NodeLabel.Function, NodeLabel.Class, NodeLabel.Method):
                result = self.store.query(
                    "MATCH (n:%s {name: $name}) RETURN n.name LIMIT 1" % label,
                    {"name": term},
                )
                if result and result.result_set:
                    self.store.create_edge(
                        NodeLabel.WikiPage,
                        {"name": name},
                        EdgeType.DOCUMENTS,
                        label,
                        {"name": term},
                    )
                    stats["edges"] += 1
                    break  # only one DOCUMENTS edge per term

    # ── Tickets ───────────────────────────────────────────────────────────────

    def ingest_tickets(self, limit: int = 200) -> dict[str, int]:
        """
        Ingest all Fossil tickets from the repo.

        Returns a stats dict with keys: ``tickets``, ``edges``.
        """
        tickets = self.adapter.ticket_list(limit=limit)
        stats = {"tickets": 0, "edges": 0}

        for ticket in tickets:
            self._ingest_ticket(ticket, stats)

        logger.info(
            "FossilIngester.ingest_tickets [%s]: %d tickets, %d edges",
            self.repo_name,
            stats["tickets"],
            stats["edges"],
        )
        return stats

    def _ingest_ticket(self, ticket: dict, stats: dict[str, int]) -> None:
        # Map common Fossil column names; fall back gracefully
        ticket_id = ticket.get("tkt_uuid") or ticket.get("uuid") or ticket.get("ticket_id", "")
        title = ticket.get("title") or ticket.get("summary", "")
        status = ticket.get("status") or ticket.get("tkt_status", "")
        ttype = ticket.get("type") or ticket.get("tkt_type", "")
        priority = ticket.get("priority") or ticket.get("tkt_priority", "")
        severity = ticket.get("severity") or ticket.get("tkt_severity", "")
        assignee = ticket.get("assignee") or ticket.get("assigned_to", "")
        resolution = ticket.get("resolution") or ticket.get("tkt_resolution", "")
        content = ticket.get("comment") or ticket.get("description", "")
        updated_at = ticket.get("tkt_mtime") or ticket.get("mtime", "")

        if not ticket_id and not title:
            return  # skip empty rows

        name = title or ticket_id
        self.store.create_node(
            NodeLabel.Ticket,
            {
                "name": name,
                "ticket_id": ticket_id,
                "title": title,
                "status": status,
                "type": ttype,
                "priority": priority,
                "severity": severity,
                "assignee": assignee,
                "resolution": resolution,
                "content": content,
                "repo": self.repo_name,
                "source": "fossil",
                "updated_at": updated_at,
            },
        )
        stats["tickets"] += 1

        # Attach to repo node if it exists
        result = self.store.query(
            "MATCH (r:Repository {name: $name}) RETURN r.name LIMIT 1",
            {"name": self.repo_name},
        )
        if result and result.result_set:
            self.store.create_edge(
                NodeLabel.Ticket,
                {"name": name},
                EdgeType.BELONGS_TO,
                NodeLabel.Repository,
                {"name": self.repo_name},
            )
            stats["edges"] += 1


# ── Wiki sync ─────────────────────────────────────────────────────────────────


class FossilWikiSync:
    """
    Sync wiki content between a Fossil checkout and a GitHub wiki.

    Direction is always explicit — call :meth:`fossil_to_github` to push
    Fossil pages to GitHub, or :meth:`github_to_fossil` to pull GitHub pages
    into Fossil.  Pages that exist only on the destination are left untouched.
    Pages present on the source side overwrite their counterpart on the
    destination (last-write-wins per sync run).

    The GitHub wiki is accessed as a bare git repo
    (``https://github.com/<repo>.wiki.git``).  A *token* is required for
    private wikis and for any push (``fossil_to_github``).

    Page name mapping
    -----------------
    Fossil page names use spaces: ``"Auth Setup"``
    GitHub wiki filenames use hyphens and a ``.md`` extension: ``"Auth-Setup.md"``
    The conversion is reversible for ASCII names.  Unicode spaces are
    preserved through the round-trip.

    Markup
    ------
    Fossil supports both WikiCreole (default) and Markdown.  This class
    writes Fossil pages with ``--mimetype text/x-markdown`` so that content
    authored on the GitHub side (Markdown) renders correctly in Fossil.
    Pages already in WikiCreole format will still be committed but may not
    render as intended on the GitHub side — that is a content problem, not
    a sync problem.
    """

    def __init__(
        self,
        fossil_adapter: FossilAdapter,
        gh_repo: str,
        token: str = "",
    ) -> None:
        self.fossil = fossil_adapter
        self.gh_repo = gh_repo  # "owner/repo"
        self.token = token

    # ── Name mapping ──────────────────────────────────────────────────────────

    @staticmethod
    def fossil_name_to_github_filename(name: str) -> str:
        """``'Auth Setup'`` → ``'Auth-Setup.md'``"""
        return name.replace(" ", "-") + ".md"

    @staticmethod
    def github_filename_to_fossil_name(filename: str) -> str:
        """``'Auth-Setup.md'`` → ``'Auth Setup'``"""
        return Path(filename).stem.replace("-", " ")

    # ── Fossil → GitHub ───────────────────────────────────────────────────────

    def fossil_to_github(self, work_dir: str | Path | None = None) -> dict[str, int]:
        """
        Push all Fossil wiki pages to the GitHub wiki.

        Clones the GitHub wiki repo into a temporary directory (or *work_dir*),
        writes one ``.md`` file per Fossil page, then commits and pushes.

        Parameters
        ----------
        work_dir:
            Parent directory for the wiki clone.  Defaults to a
            system-managed temp directory that is cleaned up on return.

        Returns
        -------
        dict with keys ``pages`` (synced) and ``skipped`` (empty pages).

        Raises
        ------
        subprocess.CalledProcessError
            If the git clone or push fails (e.g. bad token, no network).
        """
        import shutil
        import tempfile

        stats: dict[str, int] = {"pages": 0, "skipped": 0}
        own_tmp = work_dir is None
        root = Path(tempfile.mkdtemp(prefix="navegador-fsync-")) if own_tmp else Path(work_dir)

        try:
            wiki_dir = self._clone_gh_wiki(root)

            for page_name in self.fossil.wiki_pages():
                content = self.fossil.wiki_export(page_name)
                if not content.strip():
                    stats["skipped"] += 1
                    continue
                filename = self.fossil_name_to_github_filename(page_name)
                (wiki_dir / filename).write_text(content, encoding="utf-8")
                stats["pages"] += 1

            if stats["pages"] > 0:
                self._git_commit_push(
                    wiki_dir,
                    f"sync {stats['pages']} page(s) from Fossil",
                )
        finally:
            if own_tmp:
                shutil.rmtree(root, ignore_errors=True)

        logger.info(
            "FossilWikiSync.fossil_to_github [%s]: %d synced, %d skipped",
            self.gh_repo,
            stats["pages"],
            stats["skipped"],
        )
        return stats

    # ── GitHub → Fossil ───────────────────────────────────────────────────────

    def github_to_fossil(self, work_dir: str | Path | None = None) -> dict[str, int]:
        """
        Pull all GitHub wiki pages into Fossil.

        Clones the GitHub wiki repo, reads each ``.md`` file, and writes it
        to Fossil via ``fossil wiki commit``.

        Parameters
        ----------
        work_dir:
            Parent directory for the wiki clone.  Defaults to a
            system-managed temp directory that is cleaned up on return.

        Returns
        -------
        dict with keys ``pages`` (synced) and ``skipped`` (empty pages).

        Raises
        ------
        subprocess.CalledProcessError
            If the git clone fails (e.g. no network, private wiki without token).
        """
        import shutil
        import tempfile

        stats: dict[str, int] = {"pages": 0, "skipped": 0}
        own_tmp = work_dir is None
        root = Path(tempfile.mkdtemp(prefix="navegador-fsync-")) if own_tmp else Path(work_dir)

        try:
            wiki_dir = self._clone_gh_wiki(root)

            for md_file in sorted(wiki_dir.glob("*.md")):
                content = md_file.read_text(encoding="utf-8")
                if not content.strip():
                    stats["skipped"] += 1
                    continue
                page_name = self.github_filename_to_fossil_name(md_file.name)
                self.fossil.wiki_commit(page_name, content)
                stats["pages"] += 1
        finally:
            if own_tmp:
                shutil.rmtree(root, ignore_errors=True)

        logger.info(
            "FossilWikiSync.github_to_fossil [%s]: %d synced, %d skipped",
            self.gh_repo,
            stats["pages"],
            stats["skipped"],
        )
        return stats

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _clone_url(self) -> str:
        if self.token:
            return f"https://x-access-token:{self.token}@github.com/{self.gh_repo}.wiki.git"
        return f"https://github.com/{self.gh_repo}.wiki.git"

    def _clone_gh_wiki(self, parent_dir: Path) -> Path:
        """Clone the GitHub wiki repo into *parent_dir*/wiki. Returns the clone path."""
        import subprocess

        wiki_dir = parent_dir / "wiki"
        result = subprocess.run(
            ["git", "clone", "--depth=1", self._clone_url(), str(wiki_dir)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise subprocess.CalledProcessError(
                result.returncode,
                "git clone",
                output=result.stdout,
                stderr=result.stderr,
            )
        return wiki_dir

    def _git_commit_push(self, wiki_dir: Path, message: str) -> None:
        """Stage all changes in *wiki_dir*, commit, and push."""
        import subprocess

        subprocess.run(["git", "add", "-A"], cwd=wiki_dir, check=True, capture_output=True)
        # Exit code 0 means no diff — nothing to commit
        diff = subprocess.run(
            ["git", "diff", "--staged", "--quiet"],
            cwd=wiki_dir,
            capture_output=True,
        )
        if diff.returncode == 0:
            logger.info("FossilWikiSync: no changes to push")
            return
        subprocess.run(
            ["git", "commit", "-m", message],
            cwd=wiki_dir,
            check=True,
            capture_output=True,
        )
        subprocess.run(["git", "push"], cwd=wiki_dir, check=True, capture_output=True)
