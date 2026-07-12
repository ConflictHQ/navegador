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

# Comment threads can run long; keep the stored discussion bounded.
_DISCUSSION_MAX_CHARS = 8000

_DECISION_PROMPT = """\
You are extracting architectural/product decisions from a project ticket thread.

Return a JSON array (and nothing else). Each element:
  {{"name": "short imperative decision title",
    "description": "what was decided",
    "rationale": "why, per the thread",
    "alternatives": "alternatives considered, or empty string",
    "code_refs": ["function or class names the decision concerns"]}}

Only include genuine decisions (a choice was made or a direction settled).
Return [] if the thread contains none.

Thread:

{thread}
"""


def _strip_code_fences(text: str) -> str:
    """Strip a surrounding ```json ... ``` fence if the LLM added one."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    return text.strip()


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60] or "decision"


def retrofit_decisions(
    store: GraphStore,
    memory_dir: str | None = None,
    json_path: str | None = None,
    domain: str = "",
) -> dict[str, Any]:
    """
    Write Decision nodes back into a brain's memory store.

    Markdown files use the frontmatter format MemoryIngester ingests
    (``type: project`` maps back to Decision), so the loop
    issues → graph → brain memory round-trips. JSON output is a plain list
    suitable for an ``app/decisions.json`` store.

    Args:
        store: Graph to read Decision nodes from.
        memory_dir: When set, write one ``project_<slug>.md`` per decision here.
        json_path: When set, write the full decision list as JSON here.
        domain: Optional domain filter.

    Returns:
        dict with keys: decisions, markdown_files, json_path
    """
    from pathlib import Path

    cypher = "MATCH (d:Decision)"
    params: dict[str, Any] = {}
    if domain:
        cypher += " WHERE d.domain = $domain"
        params["domain"] = domain
    cypher += (
        " RETURN d.name, d.description, d.rationale, d.alternatives, d.status, d.date, d.domain"
        " ORDER BY d.name"
    )
    result = store.query(cypher, params)

    decisions = [
        {
            "name": str(row[0]),
            "description": str(row[1] or ""),
            "rationale": str(row[2] or ""),
            "alternatives": str(row[3] or ""),
            "status": str(row[4] or ""),
            "date": str(row[5] or ""),
            "domain": str(row[6] or ""),
        }
        for row in (result.result_set or [])
        if row[0]
    ]

    markdown_files = 0
    if memory_dir:
        out_dir = Path(memory_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        for decision in decisions:
            first_line = decision["description"].splitlines()[0] if decision["description"] else ""
            body_parts = [decision["description"]]
            if decision["rationale"]:
                body_parts.append(f"**Rationale:** {decision['rationale']}")
            if decision["alternatives"]:
                body_parts.append(f"**Alternatives considered:** {decision['alternatives']}")
            content = (
                "---\n"
                f"name: {decision['name']}\n"
                f"description: {first_line[:200]}\n"
                "type: project\n"
                "---\n\n" + "\n\n".join(part for part in body_parts if part) + "\n"
            )
            (out_dir / f"project_{_slugify(decision['name'])}.md").write_text(
                content, encoding="utf-8"
            )
            markdown_files += 1

    if json_path:
        import json as _json

        path = Path(json_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_json.dumps(decisions, indent=2) + "\n", encoding="utf-8")

    logger.info(
        "retrofit_decisions: %d decisions → markdown=%s json=%s",
        len(decisions),
        memory_dir or "-",
        json_path or "-",
    )
    return {
        "decisions": len(decisions),
        "markdown_files": markdown_files,
        "json_path": json_path or "",
    }


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
        include_comments: bool = True,
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
        include_comments:
            When True (default), each issue's comment thread is fetched and
            stored on the Ticket node as a ``discussion`` property — that is
            where most architectural "why" discussion actually lives.

        Returns
        -------
        dict with keys: tickets, comments, linked
        """
        headers: dict[str, str] = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "navegador/0.4",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"

        per_page = min(limit, 100)
        url = f"https://api.github.com/repos/{repo}/issues?state={state}&per_page={per_page}&page=1"

        try:
            issues: list[dict] = self._fetch_json(url, headers)
        except Exception as exc:
            raise RuntimeError(f"Failed to fetch GitHub issues for {repo!r}: {exc}") from exc

        # Filter out pull requests (GitHub issues API returns both)
        issues = [i for i in issues if "pull_request" not in i]

        domain = repo.split("/")[-1] if "/" in repo else repo
        tickets_created = 0
        comments_ingested = 0

        for issue in issues[:limit]:
            number = issue.get("number", 0)
            title = (issue.get("title") or "").strip()
            body = (issue.get("body") or "").strip()
            html_url = issue.get("html_url", "")
            labels = [lbl.get("name", "") for lbl in issue.get("labels", [])]
            severity = self._github_severity(labels)

            discussion = ""
            comments_count = int(issue.get("comments") or 0)
            if include_comments and comments_count:
                comments = self._fetch_issue_comments(repo, number, headers)
                comments_ingested += len(comments)
                discussion = "\n\n".join(
                    f"**{c.get('user', {}).get('login', 'unknown')}**: "
                    f"{(c.get('body') or '').strip()}"
                    for c in comments
                )

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
                    "discussion": discussion[:_DISCUSSION_MAX_CHARS],
                    "comments_count": comments_count,
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
            "TicketIngester.ingest_github_issues(%s): tickets=%d comments=%d linked=%d",
            repo,
            tickets_created,
            comments_ingested,
            linked,
        )
        return {"tickets": tickets_created, "comments": comments_ingested, "linked": linked}

    @staticmethod
    def _fetch_json(url: str, headers: dict[str, str]) -> Any:
        import json
        import urllib.request

        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())

    def _fetch_issue_comments(self, repo: str, number: int, headers: dict[str, str]) -> list[dict]:
        """Fetch one page (up to 100) of comments for an issue; [] on failure."""
        url = f"https://api.github.com/repos/{repo}/issues/{number}/comments?per_page=100"
        try:
            comments = self._fetch_json(url, headers)
            return comments if isinstance(comments, list) else []
        except Exception:
            logger.warning("Could not fetch comments for %s#%d", repo, number, exc_info=True)
            return []

    # ── Decision extraction (LLM) ─────────────────────────────────────────────

    def extract_decisions(
        self,
        domain: str = "",
        llm_provider: str = "anthropic",
        llm_model: str = "",
    ) -> dict[str, Any]:
        """
        Surface architectural decisions from ingested ticket threads.

        Runs each ticket's title + body + discussion through an LLM (reuses
        the ``[llm]`` provider abstraction) prompting for a strict-JSON list
        of decisions. Each becomes a Decision node linked DOCUMENTS → the
        Ticket, and DOCUMENTS → any code symbols the decision references by
        name.

        Returns
        -------
        dict with keys: tickets_scanned, decisions, code_links
        """
        import json as _json

        from navegador.llm import get_provider

        provider = get_provider(llm_provider, llm_model)

        result = self.store.query(
            "MATCH (t:Rule) WHERE t.domain = $domain AND t.rationale STARTS WITH 'http' "
            "RETURN t.name, t.description, t.discussion",
            {"domain": domain},
        )
        tickets = [
            (str(row[0]), str(row[1] or ""), str(row[2] or ""))
            for row in (result.result_set or [])
            if row[0]
        ]

        decisions_created = 0
        code_links = 0
        for t_name, t_desc, t_discussion in tickets:
            thread = f"# {t_name}\n\n{t_desc}\n\n## Discussion\n\n{t_discussion}".strip()
            try:
                response = provider.complete(_DECISION_PROMPT.format(thread=thread[:12000]))
                decisions = _json.loads(_strip_code_fences(response))
            except Exception:
                logger.warning("Decision extraction failed for %s", t_name, exc_info=True)
                continue
            if not isinstance(decisions, list):
                continue

            for decision in decisions:
                if not isinstance(decision, dict) or not decision.get("name"):
                    continue
                d_name = str(decision["name"]).strip()[:200]
                self.store.create_node(
                    NodeLabel.Decision,
                    {
                        "name": d_name,
                        "description": str(decision.get("description") or "")[:2000],
                        "domain": domain,
                        "rationale": str(decision.get("rationale") or "")[:2000],
                        "alternatives": str(decision.get("alternatives") or "")[:2000],
                        "date": "",
                        "status": "extracted",
                    },
                )
                self.store.create_edge(
                    NodeLabel.Decision,
                    {"name": d_name},
                    EdgeType.DOCUMENTS,
                    _TICKET_LABEL,
                    {"name": t_name},
                )
                decisions_created += 1
                code_refs = decision.get("code_refs") or []
                if isinstance(code_refs, list):
                    code_links += self._link_decision_to_code(d_name, code_refs)

        logger.info(
            "TicketIngester.extract_decisions(%s): tickets=%d decisions=%d code_links=%d",
            domain,
            len(tickets),
            decisions_created,
            code_links,
        )
        return {
            "tickets_scanned": len(tickets),
            "decisions": decisions_created,
            "code_links": code_links,
        }

    def _link_decision_to_code(self, decision_name: str, code_refs: list) -> int:
        """DOCUMENTS edges from a Decision to code symbols it references by name."""
        linked = 0
        for ref in code_refs:
            ref = str(ref).strip()
            if not ref:
                continue
            for label in ("Function", "Class", "Method"):
                cypher = (
                    "MATCH (d:Decision {name: $dn}), (c:" + label + " {name: $cn}) "
                    "MERGE (d)-[r:DOCUMENTS]->(c) RETURN count(r)"
                )
                try:
                    result = self.store.query(cypher, {"dn": decision_name, "cn": ref})
                    if result.result_set and result.result_set[0][0]:
                        linked += int(result.result_set[0][0])
                except Exception:
                    logger.debug("Could not link decision %s → %s", decision_name, ref)
        return linked

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
        ticket_cypher = "MATCH (t:Rule) WHERE t.domain = $domain RETURN t.name, t.description"
        code_cypher = (
            "MATCH (c) WHERE c:Function OR c:Class OR c:Method RETURN labels(c)[0], c.name"
        )

        try:
            t_result = self.store.query(ticket_cypher, {"domain": domain})
            c_result = self.store.query(code_cypher)
        except Exception:
            logger.warning("TicketIngester._link_to_code: queries failed", exc_info=True)
            return 0

        tickets = [
            (str(row[0]), str(row[1] or "")) for row in (t_result.result_set or []) if row[0]
        ]
        code_nodes = [
            (str(row[0]), str(row[1])) for row in (c_result.result_set or []) if row[0] and row[1]
        ]

        if not tickets or not code_nodes:
            return 0

        linked = 0
        for t_name, t_desc in tickets:
            combined = f"{t_name} {t_desc}"
            tokens = {w.lower() for w in re.split(r"[\s\W]+", combined) if len(w) >= 4}
            if not tokens:
                continue

            for c_label, c_name in code_nodes:
                if any(tok in c_name.lower() for tok in tokens):
                    cypher = (
                        "MATCH (t:Rule {name: $tn}), (c:" + c_label + " {name: $cn}) "
                        "MERGE (t)-[r:ANNOTATES]->(c)"
                    )
                    try:
                        self.store.query(cypher, {"tn": t_name, "cn": c_name})
                        linked += 1
                    except Exception:
                        logger.debug("TicketIngester: could not link %s → %s", t_name, c_name)
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
