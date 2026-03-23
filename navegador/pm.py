"""
PM tool integration — ingest project management tickets and cross-link to code.

Issue: #53

Supports:
  - GitHub Issues (fully implemented)
  - Linear (stub — raises NotImplementedError)
  - Jira (stub — raises NotImplementedError)

Tickets are stored as Rule nodes (they represent commitments/requirements)
and linked to code symbols by name mention similarity.

Usage::

    from navegador.pm import TicketIngester

    ing = TicketIngester(store)

    # GitHub
    stats = ing.ingest_github_issues("owner/repo", token="ghp_...")

    # Linear (stub)
    stats = ing.ingest_linear(api_key="lin_...", project="MyProject")

    # Jira (stub)
    stats = ing.ingest_jira(url="https://company.atlassian.net", token="...")
"""

from __future__ import annotations

import logging
import re
from typing import Any

from navegador.graph.schema import EdgeType, NodeLabel
from navegador.graph.store import GraphStore

logger = logging.getLogger(__name__)


# ── Ticket node label ─────────────────────────────────────────────────────────
# Tickets are stored under a synthetic "Ticket" label that maps to Rule in the
# schema — they represent requirements and commitments from the PM tool.
_TICKET_LABEL = NodeLabel.Rule


class TicketIngester:
    """
    Ingests project management tickets into the knowledge graph.

    Each ticket becomes a Rule node with::

        name        — "#<number>: <title>" (GitHub) or the ticket ID
        description — ticket body / description
        domain      — repo name or project name
        severity    — "info" | "warning" | "critical" mapped from priority/label
        rationale   — ticket URL for traceability

    After ingestion, ``_link_to_code`` runs a lightweight name-match pass to
    create ANNOTATES edges from each ticket to code symbols it mentions.
    """

    def __init__(self, store: GraphStore) -> None:
        self.store = store

    # ── GitHub Issues ─────────────────────────────────────────────────────────

    def ingest_github_issues(
        self,
        repo: str,
        token: str = "",
        state: str = "open",
        limit: int = 100,
    ) -> dict[str, Any]:
        """
        Fetch GitHub issues for *repo* and ingest them into the graph.

        Parameters
        ----------
        repo:
            Repository in ``"owner/repo"`` format.
        token:
            GitHub personal access token (or ``GITHUB_TOKEN`` env var value).
            If empty, unauthenticated requests are used (60 req/h rate limit).
        state:
            ``"open"``, ``"closed"``, or ``"all"``.
        limit:
            Maximum number of issues to fetch (GitHub paginates at 100/page).

        Returns
        -------
        dict with keys: tickets, linked
        """
        import urllib.request

        headers: dict[str, str] = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "navegador/0.4",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"

        per_page = min(limit, 100)
        url = (
            f"https://api.github.com/repos/{repo}/issues"
            f"?state={state}&per_page={per_page}&page=1"
        )

        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as resp:
                import json

                issues: list[dict] = json.loads(resp.read().decode())
        except Exception as exc:
            raise RuntimeError(f"Failed to fetch GitHub issues for {repo!r}: {exc}") from exc

        # Filter out pull requests (GitHub issues API returns both)
        issues = [i for i in issues if "pull_request" not in i]

        domain = repo.split("/")[-1] if "/" in repo else repo
        tickets_created = 0

        for issue in issues[:limit]:
            number = issue.get("number", 0)
            title = (issue.get("title") or "").strip()
            body = (issue.get("body") or "").strip()
            html_url = issue.get("html_url", "")
            labels = [lbl.get("name", "") for lbl in issue.get("labels", [])]
            severity = self._github_severity(labels)

            node_name = f"#{number}: {title}"[:200]
            self.store.create_node(
                _TICKET_LABEL,
                {
                    "name": node_name,
                    "description": body[:2000],
                    "domain": domain,
                    "severity": severity,
                    "rationale": html_url,
                    "examples": "",
                },
            )
            tickets_created += 1

            # Assignees → Person nodes + ASSIGNED_TO edges
            for assignee in issue.get("assignees", []) or []:
                login = (assignee.get("login") or "").strip()
                if login:
                    self.store.create_node(
                        NodeLabel.Person,
                        {"name": login, "email": "", "role": "", "team": ""},
                    )
                    self.store.create_edge(
                        _TICKET_LABEL,
                        {"name": node_name},
                        EdgeType.ASSIGNED_TO,
                        NodeLabel.Person,
                        {"name": login},
                    )

        linked = self._link_to_code(domain)
        logger.info(
            "TicketIngester.ingest_github_issues(%s): tickets=%d linked=%d",
            repo,
            tickets_created,
            linked,
        )
        return {"tickets": tickets_created, "linked": linked}

    # ── Linear (stub) ─────────────────────────────────────────────────────────

    def ingest_linear(self, api_key: str, project: str = "") -> dict[str, Any]:
        """
        Ingest Linear issues into the knowledge graph.

        .. note::
            Not yet implemented.  Linear GraphQL API support is planned
            for a future release.  Track progress at:
            https://github.com/weareconflict/navegador/issues/53

        Raises
        ------
        NotImplementedError
        """
        raise NotImplementedError(
            "Linear ingestion is not yet implemented. "
            "Planned for a future release — see GitHub issue #53. "
            "To contribute, implement ingest_linear() in navegador/pm.py "
            "using the Linear GraphQL API (https://developers.linear.app/docs)."
        )

    # ── Jira (stub) ───────────────────────────────────────────────────────────

    def ingest_jira(self, url: str, token: str = "", project: str = "") -> dict[str, Any]:
        """
        Ingest Jira tickets into the knowledge graph.

        .. note::
            Not yet implemented.  Jira REST API support is planned
            for a future release.  Track progress at:
            https://github.com/weareconflict/navegador/issues/53

        Raises
        ------
        NotImplementedError
        """
        raise NotImplementedError(
            "Jira ingestion is not yet implemented. "
            "Planned for a future release — see GitHub issue #53. "
            "To contribute, implement ingest_jira() in navegador/pm.py "
            "using the Jira REST API v3 "
            "(https://developer.atlassian.com/cloud/jira/platform/rest/v3/)."
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _link_to_code(self, domain: str = "") -> int:
        """
        Create ANNOTATES edges from ticket Rule nodes to matching code symbols.

        Matches code node names against significant words (≥4 chars) in each
        ticket's name and description.

        Returns
        -------
        int — number of edges created
        """
        ticket_cypher = (
            "MATCH (t:Rule) WHERE t.domain = $domain "
            "RETURN t.name, t.description"
        )
        code_cypher = (
            "MATCH (c) WHERE c:Function OR c:Class OR c:Method "
            "RETURN labels(c)[0], c.name"
        )

        try:
            t_result = self.store.query(ticket_cypher, {"domain": domain})
            c_result = self.store.query(code_cypher)
        except Exception:
            logger.warning("TicketIngester._link_to_code: queries failed", exc_info=True)
            return 0

        tickets = [
            (str(row[0]), str(row[1] or ""))
            for row in (t_result.result_set or [])
            if row[0]
        ]
        code_nodes = [
            (str(row[0]), str(row[1]))
            for row in (c_result.result_set or [])
            if row[0] and row[1]
        ]

        if not tickets or not code_nodes:
            return 0

        linked = 0
        for t_name, t_desc in tickets:
            combined = f"{t_name} {t_desc}"
            tokens = {
                w.lower()
                for w in re.split(r"[\s\W]+", combined)
                if len(w) >= 4
            }
            if not tokens:
                continue

            for c_label, c_name in code_nodes:
                if any(tok in c_name.lower() for tok in tokens):
                    cypher = (
                        "MATCH (t:Rule {name: $tn}), (c:"
                        + c_label
                        + " {name: $cn}) "
                        "MERGE (t)-[r:ANNOTATES]->(c)"
                    )
                    try:
                        self.store.query(cypher, {"tn": t_name, "cn": c_name})
                        linked += 1
                    except Exception:
                        logger.debug(
                            "TicketIngester: could not link %s → %s", t_name, c_name
                        )
        return linked

    @staticmethod
    def _github_severity(labels: list[str]) -> str:
        """Map GitHub label names to navegador severity levels."""
        label_lower = {lbl.lower() for lbl in labels}
        if label_lower & {"critical", "blocker", "urgent", "p0"}:
            return "critical"
        if label_lower & {"bug", "high", "p1", "important"}:
            return "warning"
        return "info"
