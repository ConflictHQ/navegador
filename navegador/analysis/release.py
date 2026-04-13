"""
Release readiness checks -- compose diff, impact, ownership, tests, and knowledge
into a single pass/warn/fail report for a release candidate.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from navegador.graph import GraphStore

# ── Cypher queries ───────────────────────────────────────────────────────────

_TEST_COVERAGE_QUERY = """
MATCH (t)-[:CALLS*1..2]->(n)
WHERE n.name = $name AND ($file_path = '' OR n.file_path = $file_path)
AND (t.name STARTS WITH 'test_' OR t.file_path CONTAINS 'test')
RETURN count(t)
"""

_KNOWLEDGE_LINKS_QUERY = """
MATCH (n)<-[:DOCUMENTS|ANNOTATES|GOVERNS]-(k)
WHERE n.name = $name
RETURN labels(k)[0], k.name
"""

_OWNER_QUERY = """
MATCH (n)-[:ASSIGNED_TO]->(p:Person)
WHERE n.name = $name
RETURN DISTINCT p.name
"""

# Cap: never process more than this many changed symbols in one check run.
_MAX_SYMBOLS = 50


# ── Data models ──────────────────────────────────────────────────────────────


@dataclass
class ReleaseItem:
    category: str  # "changed_symbol" | "missing_test" | "stale_doc"
    #                "owner_required" | "cross_repo_impact"
    severity: str  # "info" | "warning" | "error"
    symbol: str
    file_path: str = ""
    detail: str = ""
    knowledge_node: str = ""  # related rule/ADR/wiki


@dataclass
class ReleaseReport:
    base_ref: str
    head_ref: str
    items: list[ReleaseItem] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)
    owners_required: list[str] = field(default_factory=list)

    @property
    def errors(self) -> list[ReleaseItem]:
        return [i for i in self.items if i.severity == "error"]

    @property
    def warnings(self) -> list[ReleaseItem]:
        return [i for i in self.items if i.severity == "warning"]

    @property
    def passed(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "base_ref": self.base_ref,
            "head_ref": self.head_ref,
            "passed": self.passed,
            "changed_files": self.changed_files,
            "owners_required": self.owners_required,
            "items": [
                {
                    "category": i.category,
                    "severity": i.severity,
                    "symbol": i.symbol,
                    "file_path": i.file_path,
                    "detail": i.detail,
                    "knowledge_node": i.knowledge_node,
                }
                for i in self.items
            ],
            "summary": {
                "errors": len(self.errors),
                "warnings": len(self.warnings),
                "total_items": len(self.items),
            },
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    def to_markdown(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        lines = [
            f"# Release Readiness -- `{self.base_ref}` -> `{self.head_ref}`\n",
            f"**Status:** {status}   "
            f"**Changed files:** {len(self.changed_files)}   "
            f"**Errors:** {len(self.errors)}   "
            f"**Warnings:** {len(self.warnings)}\n",
        ]

        if self.changed_files:
            lines.append(f"\n## Changed Files ({len(self.changed_files)})\n")
            for f in self.changed_files:
                lines.append(f"- `{f}`")

        if self.errors:
            lines.append(f"\n## Errors ({len(self.errors)})\n")
            for i in self.errors:
                loc = f" `{i.file_path}`" if i.file_path else ""
                detail = f" -- {i.detail}" if i.detail else ""
                kn = f"  <- {i.knowledge_node}" if i.knowledge_node else ""
                lines.append(f"- **[{i.category}]** `{i.symbol}`{loc}{detail}{kn}")

        if self.warnings:
            lines.append(f"\n## Warnings ({len(self.warnings)})\n")
            for i in self.warnings:
                loc = f" `{i.file_path}`" if i.file_path else ""
                detail = f" -- {i.detail}" if i.detail else ""
                kn = f"  <- {i.knowledge_node}" if i.knowledge_node else ""
                lines.append(f"- **[{i.category}]** `{i.symbol}`{loc}{detail}{kn}")

        if self.owners_required:
            lines.append(f"\n## Required Sign-offs ({len(self.owners_required)})\n")
            for owner in self.owners_required:
                lines.append(f"- {owner}")

        return "\n".join(lines)


# ── Checker ──────────────────────────────────────────────────────────────────


class ReleaseChecker:
    """
    Compose diff-graph, impact, ownership, test-coverage, and knowledge checks
    into a single release-readiness report.

    Usage::

        from navegador.analysis.release import ReleaseChecker

        checker = ReleaseChecker(store, repo_path="/path/to/repo")
        report = checker.check(base="main", head="HEAD")
        if not report.passed:
            print(report.to_markdown())
    """

    def __init__(self, store: GraphStore, repo_path: str | Path = ".") -> None:
        self.store = store
        self.repo_path = Path(repo_path)

    def check(self, base: str = "main", head: str = "HEAD") -> ReleaseReport:
        """
        Run all release readiness checks for the given ref range.

        1. Diff-graph: find changed/new symbols via DiffGraphAnalyzer.
        2. Missing tests: warn when a changed symbol has no test callers.
        3. Stale docs: warn when a changed symbol has linked knowledge nodes
           (those docs likely need review).
        4. Owner sign-offs: collect owners of changed symbols.
        5. Cross-repo impact: optional blast-radius for high-centrality symbols.
        """
        from navegador.analysis.diffgraph import DiffGraphAnalyzer

        report = ReleaseReport(base_ref=base, head_ref=head)

        # Step 1: get changed symbols from diff-graph
        diff_analyzer = DiffGraphAnalyzer(self.store, self.repo_path)
        diff_report = diff_analyzer.diff_refs(base, head)

        report.changed_files = list(diff_report.affected_files)

        # Collect all changed/new symbols (capped)
        all_symbols = list(diff_report.new_symbols + diff_report.changed_symbols)[:_MAX_SYMBOLS]

        for sc in all_symbols:
            report.items.append(
                ReleaseItem(
                    category="changed_symbol",
                    severity="info",
                    symbol=sc.symbol,
                    file_path=sc.file_path,
                )
            )

        # Steps 2-4: per-symbol checks
        owners_seen: set[str] = set()

        for sc in all_symbols:
            self._check_test_coverage(report, sc.symbol, sc.file_path)
            self._check_knowledge_links(report, sc.symbol)
            self._collect_owners(report, sc.symbol, owners_seen)

        report.owners_required = sorted(owners_seen)

        # Step 5: cross-repo impact (optional, best-effort)
        self._check_cross_repo_impact(report, all_symbols)

        return report

    # ── Per-symbol checks ────────────────────────────────────────────────────

    def _check_test_coverage(self, report: ReleaseReport, symbol: str, file_path: str) -> None:
        """Warn when a changed symbol has no test callers in the graph."""
        try:
            result = self.store.query(
                _TEST_COVERAGE_QUERY,
                {"name": symbol, "file_path": file_path},
            )
            rows = result.result_set or []
            count = rows[0][0] if rows else 0
            if count == 0:
                report.items.append(
                    ReleaseItem(
                        category="missing_test",
                        severity="warning",
                        symbol=symbol,
                        file_path=file_path,
                        detail="no tests found",
                    )
                )
        except Exception:
            pass

    def _check_knowledge_links(self, report: ReleaseReport, symbol: str) -> None:
        """Warn when a changed symbol has linked knowledge nodes that may be stale."""
        try:
            result = self.store.query(_KNOWLEDGE_LINKS_QUERY, {"name": symbol})
            for row in result.result_set or []:
                k_name = row[1] or ""
                report.items.append(
                    ReleaseItem(
                        category="stale_doc",
                        severity="warning",
                        symbol=symbol,
                        detail=f"review: {k_name}",
                        knowledge_node=k_name,
                    )
                )
        except Exception:
            pass

    def _collect_owners(
        self,
        report: ReleaseReport,
        symbol: str,
        owners_seen: set[str],
    ) -> None:
        """Collect unique owners of changed symbols."""
        try:
            result = self.store.query(_OWNER_QUERY, {"name": symbol})
            for row in result.result_set or []:
                name = row[0]
                if name:
                    owners_seen.add(name)
        except Exception:
            pass

    def _check_cross_repo_impact(
        self,
        report: ReleaseReport,
        all_symbols: list,
    ) -> None:
        """Best-effort cross-repo blast-radius check for high-centrality symbols."""
        try:
            from navegador.analysis.crossrepo import CrossRepoImpactAnalyzer

            analyzer = CrossRepoImpactAnalyzer(self.store)

            # Only check top 3 symbols by position (proxy for centrality)
            for sc in all_symbols[:3]:
                result = analyzer.blast_radius(sc.symbol, file_path=sc.file_path, depth=2)
                if result.affected_repos:
                    report.items.append(
                        ReleaseItem(
                            category="cross_repo_impact",
                            severity="info",
                            symbol=sc.symbol,
                            file_path=sc.file_path,
                            detail=f"impacts repos: {', '.join(sorted(result.affected_repos))}",
                        )
                    )
        except Exception:
            # CrossRepoImpactAnalyzer may not be available or graph may not
            # have cross-repo data -- this is entirely optional.
            pass
