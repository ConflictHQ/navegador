"""
FossilIngester — ingests Fossil SCM wiki pages and tickets into the graph.

Fossil is a self-contained DVCS that ships with its own wiki and bug-tracker.
This ingester reads both, creating WikiPage nodes for wiki pages and Ticket
nodes for tracker items.

    Wiki pages  → WikiPage nodes (same label used for GitHub wiki)
    Tickets     → Ticket nodes  (label added in schema v12)

Each WikiPage gets the usual DOCUMENTS edges to code symbols found in its
headings and bold terms.  Each Ticket gets a BELONGS_TO edge to the
Repository it was read from.

Usage:
    from navegador.vcs import FossilAdapter
    from navegador.ingestion.fossil import FossilIngester

    adapter = FossilAdapter("/path/to/fossil-checkout")
    ingester = FossilIngester(store, adapter)

    wiki_stats   = ingester.ingest_wiki()
    ticket_stats = ingester.ingest_tickets()
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
