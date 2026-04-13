"""
Rule-aware review comments — generate structured review feedback from graph context.

Takes a diff (or a list of changed symbols) and emits ReviewComment objects
tying each finding back to the exact Rule, Decision, or WikiPage that defines it.

Usage::

    from navegador.analysis.review import ReviewGenerator

    gen = ReviewGenerator(store)
    report = gen.review_diff(
        changed_symbols=[{"name": "authorize", "file_path": "auth.py"}],
        changed_files=["auth.py"],
    )
    print(report.to_markdown())
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from navegador.graph import GraphStore

# ── Cypher queries ───────────────────────────────────────────────────────────

_RULE_VIOLATIONS_QUERY = """
MATCH (r:Rule)-[:GOVERNS]->(n)
WHERE n.name = $name AND ($file_path = '' OR n.file_path = $file_path)
RETURN r.name, r.severity, coalesce(r.rationale, r.description, '') LIMIT 10
"""

_ADR_CONFLICTS_QUERY = """
MATCH (n)-[:BELONGS_TO]->(d:Domain)<-[:BELONGS_TO]-(dec:Decision)
WHERE n.name = $name AND ($file_path = '' OR n.file_path = $file_path)
  AND dec.status = 'accepted'
RETURN dec.name, coalesce(dec.rationale, dec.description, '') LIMIT 5
"""

_UNDOCUMENTED_SYMBOL_QUERY = """
MATCH (n) WHERE n.name = $name AND ($file_path = '' OR n.file_path = $file_path)
  AND NOT (n)<-[:GOVERNS|ANNOTATES|DOCUMENTS]-()
RETURN n.name, n.file_path LIMIT 1
"""

_FILE_KNOWLEDGE_QUERY = """
MATCH (f:File {path: $path})-[:CONTAINS]->(n)<-[:GOVERNS|ANNOTATES]-(r)
RETURN DISTINCT r.name, labels(r)[0], coalesce(r.rationale, r.description, '') LIMIT 10
"""

# Caps
_MAX_COMMENTS_PER_SYMBOL = 5
_MAX_SYMBOLS = 100

_SEVERITY_MAP = {
    "critical": "error",
    "warning": "warning",
}


def _map_severity(raw: str | None) -> str:
    """Map a Rule severity value to a ReviewComment severity."""
    if not raw:
        return "suggestion"
    return _SEVERITY_MAP.get(raw.lower().strip(), "suggestion")


# ── Data models ──────────────────────────────────────────────────────────────


@dataclass
class ReviewComment:
    severity: str  # "error" | "warning" | "suggestion"
    title: str  # short one-line summary
    body: str  # longer detail/evidence
    symbol: str = ""  # offending symbol
    file_path: str = ""
    line_start: int | None = None
    knowledge_ref: str = ""  # Rule/Decision/WikiPage name
    knowledge_type: str = ""  # "Rule" | "Decision" | "WikiPage"
    confidence: float = 1.0  # 0.0–1.0 (inferred vs definitive)


@dataclass
class ReviewReport:
    comments: list[ReviewComment] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)

    @property
    def errors(self) -> list[ReviewComment]:
        return [c for c in self.comments if c.severity == "error"]

    @property
    def warnings(self) -> list[ReviewComment]:
        return [c for c in self.comments if c.severity == "warning"]

    @property
    def suggestions(self) -> list[ReviewComment]:
        return [c for c in self.comments if c.severity == "suggestion"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "changed_files": self.changed_files,
            "comments": [c.__dict__ for c in self.comments],
            "summary": {
                "errors": len(self.errors),
                "warnings": len(self.warnings),
                "suggestions": len(self.suggestions),
                "total": len(self.comments),
            },
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    def to_markdown(self) -> str:
        total = len(self.comments)
        lines = [f"## Review — {total} comment(s)\n"]

        for section_name, items in [
            ("Errors", self.errors),
            ("Warnings", self.warnings),
            ("Suggestions", self.suggestions),
        ]:
            if not items:
                continue
            lines.append(f"\n### {section_name} ({len(items)})\n")
            for c in items:
                ref_label = f"{c.knowledge_type}: {c.knowledge_ref}" if c.knowledge_ref else ""
                sym_label = f" {c.symbol}" if c.symbol else ""
                heading = f"[{ref_label}]{sym_label}" if ref_label else sym_label.strip()
                lines.append(f"#### {heading}")
                lines.append(f"> {c.body}")
                if c.file_path:
                    loc = f"{c.file_path}:{c.line_start}" if c.line_start else c.file_path
                    lines.append(f"- File: `{loc}`")
                if c.knowledge_ref:
                    lines.append(f"- Knowledge: {c.knowledge_type} `{c.knowledge_ref}`")
                lines.append(f"- Confidence: {c.confidence}")
                lines.append("")

        return "\n".join(lines)


# ── Generator ────────────────────────────────────────────────────────────────


class ReviewGenerator:
    """
    Generate structured review comments for changed symbols by querying the
    knowledge graph for governing rules, ADRs, and documentation links.
    """

    def __init__(self, store: GraphStore) -> None:
        self.store = store

    def review_diff(
        self,
        changed_symbols: list[dict],
        changed_files: list[str] | None = None,
    ) -> ReviewReport:
        """
        Generate review comments for changed symbols and files.

        Parameters
        ----------
        changed_symbols
            List of dicts with at least ``name`` key and optional ``file_path``.
        changed_files
            List of file paths that were changed in the diff.
        """
        if changed_files is None:
            changed_files = []

        report = ReviewReport(changed_files=list(changed_files))

        # Track all comments before dedup
        raw_comments: list[ReviewComment] = []

        # Process symbols (capped)
        for sym in changed_symbols[:_MAX_SYMBOLS]:
            name = sym.get("name", "")
            file_path = sym.get("file_path", "")
            if not name:
                continue

            sym_comments: list[ReviewComment] = []

            # Pass 1 — Rule violations
            sym_comments.extend(self._check_rule_violations(name, file_path))

            # Pass 2 — ADR conflicts
            sym_comments.extend(self._check_adr_conflicts(name, file_path))

            # Pass 3 — Undocumented public symbols
            sym_comments.extend(self._check_undocumented(name, file_path))

            # Cap per symbol
            raw_comments.extend(sym_comments[:_MAX_COMMENTS_PER_SYMBOL])

        # File-level knowledge comments
        for fp in changed_files:
            raw_comments.extend(self._check_file_knowledge(fp))

        # Deduplicate by (symbol, knowledge_ref) — keep highest confidence
        report.comments = self._deduplicate(raw_comments)

        return report

    # ── Pass implementations ─────────────────────────────────────────────────

    def _check_rule_violations(self, name: str, file_path: str) -> list[ReviewComment]:
        """Pass 1: find rules that GOVERNS nodes related to this symbol."""
        comments: list[ReviewComment] = []
        try:
            result = self.store.query(
                _RULE_VIOLATIONS_QUERY,
                {"name": name, "file_path": file_path},
            )
            for row in result.result_set or []:
                comments.append(
                    ReviewComment(
                        severity=_map_severity(row[1]),
                        title=f"Rule '{row[0]}' governs this symbol",
                        body=row[2] or "",
                        symbol=name,
                        file_path=file_path,
                        knowledge_ref=row[0] or "",
                        knowledge_type="Rule",
                    )
                )
        except Exception:
            pass
        return comments

    def _check_adr_conflicts(self, name: str, file_path: str) -> list[ReviewComment]:
        """Pass 2: find accepted Decisions linked to this symbol's domain."""
        comments: list[ReviewComment] = []
        try:
            result = self.store.query(
                _ADR_CONFLICTS_QUERY,
                {"name": name, "file_path": file_path},
            )
            for row in result.result_set or []:
                comments.append(
                    ReviewComment(
                        severity="suggestion",
                        title=f"ADR '{row[0]}' applies to this domain",
                        body=row[1] or "",
                        symbol=name,
                        file_path=file_path,
                        knowledge_ref=row[0] or "",
                        knowledge_type="Decision",
                        confidence=0.7,
                    )
                )
        except Exception:
            pass
        return comments

    def _check_undocumented(self, name: str, file_path: str) -> list[ReviewComment]:
        """Pass 3: flag symbols with no GOVERNS/ANNOTATES/DOCUMENTS edges."""
        comments: list[ReviewComment] = []
        try:
            result = self.store.query(
                _UNDOCUMENTED_SYMBOL_QUERY,
                {"name": name, "file_path": file_path},
            )
            if result.result_set:
                comments.append(
                    ReviewComment(
                        severity="suggestion",
                        title="No knowledge links found for this symbol",
                        body="Consider adding a Rule, ADR, or WikiPage that governs this symbol.",
                        symbol=name,
                        file_path=file_path,
                        confidence=0.5,
                    )
                )
        except Exception:
            pass
        return comments

    def _check_file_knowledge(self, path: str) -> list[ReviewComment]:
        """Check if a changed file contains symbols governed by rules/decisions."""
        comments: list[ReviewComment] = []
        try:
            result = self.store.query(
                _FILE_KNOWLEDGE_QUERY,
                {"path": path},
            )
            for row in result.result_set or []:
                comments.append(
                    ReviewComment(
                        severity="suggestion",
                        title=f"File touches symbol governed by '{row[0]}'",
                        body=row[2] or "",
                        file_path=path,
                        knowledge_ref=row[0] or "",
                        knowledge_type=row[1] or "",
                        confidence=0.6,
                    )
                )
        except Exception:
            pass
        return comments

    # ── Deduplication ────────────────────────────────────────────────────────

    @staticmethod
    def _deduplicate(comments: list[ReviewComment]) -> list[ReviewComment]:
        """
        Deduplicate by (symbol, knowledge_ref) — keep the entry with highest
        confidence. Comments without a knowledge_ref are always kept.
        """
        best: dict[tuple[str, str], ReviewComment] = {}
        ungrouped: list[ReviewComment] = []

        for c in comments:
            if not c.knowledge_ref:
                ungrouped.append(c)
                continue

            key = (c.symbol, c.knowledge_ref)
            existing = best.get(key)
            if existing is None or c.confidence > existing.confidence:
                best[key] = c

        return list(best.values()) + ungrouped
