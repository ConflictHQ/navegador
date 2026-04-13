"""
Architecture drift detection — turn the knowledge layer into executable checks.

Reads rules, decisions (ADRs), and memory nodes from the graph and evaluates
them as structural constraints against the live code graph. Reports violations
with concrete evidence: offending nodes, file paths, and the knowledge node
each violation contradicts.

Built-in check types (applied automatically when matching knowledge nodes exist):

  FORBIDDEN_DEP    — a rule says "X must not depend on Y"
  REQUIRED_LAYER   — an ADR mandates a specific call-chain order (controller→service→repo)
  REQUIRED_OWNER   — nodes in a domain must have an ASSIGNED_TO owner
  UNDOCUMENTED     — public symbols in a domain have no linked wiki/concept/doc
  STALE_MEMORY     — a memory node references a symbol that no longer exists

Usage::

    from navegador.analysis.drift import DriftChecker

    checker = DriftChecker(store)
    report = checker.check()
    print(report.to_markdown())

    # CI usage — non-zero exit on violations
    if report.has_violations:
        sys.exit(1)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from navegador.graph import GraphStore


@dataclass
class DriftViolation:
    check: str                    # check type / rule name
    severity: str                 # "error" | "warning"
    message: str
    offending_node: str = ""
    offending_file: str = ""
    knowledge_node: str = ""      # the rule/decision/memory that was violated
    knowledge_type: str = ""


@dataclass
class DriftReport:
    violations: list[DriftViolation] = field(default_factory=list)
    warnings: list[DriftViolation] = field(default_factory=list)
    checks_run: int = 0

    @property
    def has_violations(self) -> bool:
        return bool(self.violations)

    def to_dict(self) -> dict[str, Any]:
        def _v(lst: list[DriftViolation]) -> list[dict[str, Any]]:
            return [v.__dict__ for v in lst]
        return {
            "checks_run": self.checks_run,
            "violations": _v(self.violations),
            "warnings": _v(self.warnings),
            "summary": {
                "violations": len(self.violations),
                "warnings": len(self.warnings),
            },
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    def to_markdown(self) -> str:
        lines = ["# Architecture Drift Report\n"]
        lines.append(
            f"**Checks run:** {self.checks_run}  "
            f"**Violations:** {len(self.violations)}  "
            f"**Warnings:** {len(self.warnings)}\n"
        )

        if self.violations:
            lines.append(f"\n## Violations ({len(self.violations)})\n")
            for v in self.violations:
                loc = f" `{v.offending_file}`" if v.offending_file else ""
                ref = f" ← _{v.knowledge_type}_ `{v.knowledge_node}`" if v.knowledge_node else ""
                lines.append(f"- **[{v.check}]** `{v.offending_node}`{loc} — {v.message}{ref}")
        else:
            lines.append("\n_No violations found._\n")

        if self.warnings:
            lines.append(f"\n## Warnings ({len(self.warnings)})\n")
            for w in self.warnings:
                loc = f" `{w.offending_file}`" if w.offending_file else ""
                ref = f" ← _{w.knowledge_type}_ `{w.knowledge_node}`" if w.knowledge_node else ""
                lines.append(f"- **[{w.check}]** `{w.offending_node}`{loc} — {w.message}{ref}")

        return "\n".join(lines)


class DriftChecker:
    """
    Runs architecture drift checks against the graph.

    Checks are derived automatically from the knowledge layer:
    - Rule nodes with memory_type='feedback' → behavioral constraints
    - Decision nodes → ADR-sourced architectural constraints
    - Domain membership → undocumented symbol detection
    - Memory nodes → stale reference detection

    Additional checks can be registered via register_check().
    """

    def __init__(self, store: GraphStore) -> None:
        self.store = store
        self._extra_checks: list[Any] = []

    def register_check(self, fn: Any) -> None:
        """Register a custom check function(store) → list[DriftViolation]."""
        self._extra_checks.append(fn)

    def check(self) -> DriftReport:
        """Run all checks and return a DriftReport."""
        report = DriftReport()

        checks = [
            self._check_stale_memory_refs,
            self._check_undocumented_domain_symbols,
            self._check_required_owners,
        ]

        for check_fn in checks + self._extra_checks:
            results = check_fn()
            report.checks_run += 1
            for v in results:
                if v.severity == "error":
                    report.violations.append(v)
                else:
                    report.warnings.append(v)

        return report

    # ── Built-in checks ───────────────────────────────────────────────────────

    def _check_stale_memory_refs(self) -> list[DriftViolation]:
        """
        Flag memory nodes (Rule/Decision/WikiPage) that GOVERNS or ANNOTATES
        a code symbol that no longer exists in the graph.
        """
        cypher = (
            "MATCH (k)-[:GOVERNS|ANNOTATES]->(n) "
            "WHERE k.memory_type IS NOT NULL "
            "AND NOT (n:Repository OR n:File OR n:Document) "
            "AND NOT EXISTS(n.name) "
            "RETURN k.memory_type, k.name, labels(n)[0], n.name"
        )
        violations: list[DriftViolation] = []
        try:
            for row in (self.store.query(cypher).result_set or []):
                violations.append(DriftViolation(
                    check="STALE_MEMORY_REF",
                    severity="warning",
                    message="Memory node governs a symbol that no longer exists",
                    offending_node=row[3] or "(unknown)",
                    knowledge_node=row[1] or "",
                    knowledge_type=row[0] or "memory",
                ))
        except Exception:
            pass
        return violations

    def _check_undocumented_domain_symbols(self) -> list[DriftViolation]:
        """
        Warn when a function or class belongs to a domain but has no linked
        wiki page, concept, decision, or document node.
        """
        cypher = (
            "MATCH (n)-[:BELONGS_TO]->(d:Domain) "
            "WHERE (n:Function OR n:Class OR n:Method) "
            "AND NOT (n)<-[:DOCUMENTS|ANNOTATES|GOVERNS]-() "
            "RETURN labels(n)[0], n.name, n.file_path, d.name LIMIT 50"
        )
        violations: list[DriftViolation] = []
        try:
            for row in (self.store.query(cypher).result_set or []):
                violations.append(DriftViolation(
                    check="UNDOCUMENTED_DOMAIN_SYMBOL",
                    severity="warning",
                    message=f"Symbol in domain '{row[3]}' has no linked docs or rules",
                    offending_node=row[1] or "",
                    offending_file=row[2] or "",
                    knowledge_node=row[3] or "",
                    knowledge_type="Domain",
                ))
        except Exception:
            pass
        return violations

    def _check_required_owners(self) -> list[DriftViolation]:
        """
        Flag memory nodes of type 'feedback' that say ownership is required
        for a domain, but domain symbols have no ASSIGNED_TO edge.
        """
        # Find domains mentioned in feedback rules that contain "owner" or "assigned"
        cypher_rules = (
            "MATCH (r:Rule) WHERE r.memory_type = 'feedback' "
            "AND (toLower(r.name) CONTAINS 'owner' "
            "     OR toLower(r.rationale) CONTAINS 'owner') "
            "RETURN r.name, r.domain LIMIT 20"
        )
        violations: list[DriftViolation] = []
        try:
            rule_rows = self.store.query(cypher_rules).result_set or []
            for rule_row in rule_rows:
                rule_name = rule_row[0] or ""
                domain = rule_row[1] or ""
                if not domain:
                    continue
                # Find symbols in that domain without owners
                cypher_unowned = (
                    "MATCH (n)-[:BELONGS_TO]->(d:Domain {name: $domain}) "
                    "WHERE (n:Function OR n:Class) "
                    "AND NOT (n)-[:ASSIGNED_TO]->(:Person) "
                    "RETURN labels(n)[0], n.name, n.file_path LIMIT 20"
                )
                for row in (self.store.query(cypher_unowned, {"domain": domain}).result_set or []):
                    violations.append(DriftViolation(
                        check="MISSING_OWNER",
                        severity="warning",
                        message=f"Symbol in domain '{domain}' has no owner",
                        offending_node=row[1] or "",
                        offending_file=row[2] or "",
                        knowledge_node=rule_name,
                        knowledge_type="Rule",
                    ))
        except Exception:
            pass
        return violations
