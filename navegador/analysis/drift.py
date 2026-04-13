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
    check: str  # check type / rule name
    severity: str  # "error" | "warning"
    message: str
    offending_node: str = ""
    offending_file: str = ""
    knowledge_node: str = ""  # the rule/decision/memory that was violated
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
            self._check_declarative_rules,
            self._check_declarative_decisions,
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
            for row in self.store.query(cypher).result_set or []:
                violations.append(
                    DriftViolation(
                        check="STALE_MEMORY_REF",
                        severity="warning",
                        message="Memory node governs a symbol that no longer exists",
                        offending_node=row[3] or "(unknown)",
                        knowledge_node=row[1] or "",
                        knowledge_type=row[0] or "memory",
                    )
                )
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
            for row in self.store.query(cypher).result_set or []:
                violations.append(
                    DriftViolation(
                        check="UNDOCUMENTED_DOMAIN_SYMBOL",
                        severity="warning",
                        message=f"Symbol in domain '{row[3]}' has no linked docs or rules",
                        offending_node=row[1] or "",
                        offending_file=row[2] or "",
                        knowledge_node=row[3] or "",
                        knowledge_type="Domain",
                    )
                )
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
                for row in self.store.query(cypher_unowned, {"domain": domain}).result_set or []:
                    violations.append(
                        DriftViolation(
                            check="MISSING_OWNER",
                            severity="warning",
                            message=f"Symbol in domain '{domain}' has no owner",
                            offending_node=row[1] or "",
                            offending_file=row[2] or "",
                            knowledge_node=rule_name,
                            knowledge_type="Rule",
                        )
                    )
        except Exception:
            pass
        return violations

    # ── Declarative constraint checks ─────────────────────────────────────────

    def _check_declarative_rules(self) -> list[DriftViolation]:
        """
        Evaluate Rule nodes that carry a 'constraint' property.
        Parses the constraint JSON and dispatches to the appropriate evaluator.
        """
        cypher = (
            "MATCH (r:Rule) WHERE r.constraint IS NOT NULL "
            "RETURN r.name, r.constraint, coalesce(r.severity, 'warning') LIMIT 100"
        )
        return self._eval_constraint_nodes(cypher, "Rule")

    def _check_declarative_decisions(self) -> list[DriftViolation]:
        """
        Evaluate Decision nodes that carry a 'constraint' property.
        Deprecated decisions are skipped.
        """
        cypher = (
            "MATCH (d:Decision) WHERE d.constraint IS NOT NULL "
            "AND d.status <> 'deprecated' "
            "RETURN d.name, d.constraint, 'warning' LIMIT 100"
        )
        return self._eval_constraint_nodes(cypher, "Decision")

    def _eval_constraint_nodes(self, cypher: str, knowledge_type: str) -> list[DriftViolation]:
        """Shared driver: query for constraint-bearing nodes and dispatch each."""
        violations: list[DriftViolation] = []
        try:
            rows = self.store.query(cypher).result_set or []
        except Exception:
            return violations

        for row in rows:
            node_name = row[0] or ""
            raw_constraint = row[1] or ""
            severity = row[2] or "warning"

            try:
                constraint = json.loads(raw_constraint)
            except (json.JSONDecodeError, TypeError):
                continue

            ctype = constraint.get("type", "")
            if ctype == "FORBIDDEN_DEP":
                violations.extend(
                    self._eval_forbidden_dep(node_name, constraint, severity, knowledge_type)
                )
            elif ctype == "REQUIRED_LAYER":
                violations.extend(
                    self._eval_required_layer(node_name, constraint, severity, knowledge_type)
                )
            elif ctype == "MAX_OUTGOING_CALLS":
                violations.extend(
                    self._eval_max_calls(node_name, constraint, severity, knowledge_type)
                )
            # Unknown constraint types are silently skipped.

        return violations

    def _eval_forbidden_dep(
        self,
        rule_name: str,
        constraint: dict,
        severity: str,
        knowledge_type: str,
    ) -> list[DriftViolation]:
        """Evaluate a FORBIDDEN_DEP constraint: symbols in 'from' domain must not
        depend on symbols in 'to' domain via CALLS/IMPORTS/INHERITS."""
        from_domain = constraint.get("from", "")
        to_domain = constraint.get("to", "")
        if not from_domain or not to_domain:
            return []

        cypher = (
            "MATCH (a)-[:CALLS|IMPORTS|INHERITS]->(b) "
            "WHERE (a)-[:BELONGS_TO]->(:Domain {name: $from_domain}) "
            "AND (b)-[:BELONGS_TO]->(:Domain {name: $to_domain}) "
            "RETURN a.name, a.file_path, b.name LIMIT 20"
        )
        violations: list[DriftViolation] = []
        try:
            for row in (
                self.store.query(
                    cypher, {"from_domain": from_domain, "to_domain": to_domain}
                ).result_set
                or []
            ):
                violations.append(
                    DriftViolation(
                        check="FORBIDDEN_DEP",
                        severity=severity,
                        message=(
                            f"'{row[0]}' in domain '{from_domain}' depends on "
                            f"'{row[2]}' in domain '{to_domain}'"
                        ),
                        offending_node=row[0] or "",
                        offending_file=row[1] or "",
                        knowledge_node=rule_name,
                        knowledge_type=knowledge_type,
                    )
                )
        except Exception:
            pass
        return violations

    def _eval_required_layer(
        self,
        rule_name: str,
        constraint: dict,
        severity: str,
        knowledge_type: str,
    ) -> list[DriftViolation]:
        """Evaluate a REQUIRED_LAYER constraint: calls must flow in the declared
        layer order, adjacent layers only (no skipping, no backward calls)."""
        layers = constraint.get("order", [])
        if len(layers) < 2:
            return []

        cypher = (
            "MATCH (a)-[:CALLS]->(b) "
            "WHERE labels(a)[0] IN $layers AND labels(b)[0] IN $layers "
            "AND labels(a)[0] <> labels(b)[0] "
            "RETURN labels(a)[0], a.name, a.file_path, labels(b)[0], b.name LIMIT 30"
        )
        violations: list[DriftViolation] = []
        try:
            for row in self.store.query(cypher, {"layers": layers}).result_set or []:
                a_label = row[0]
                b_label = row[3]
                try:
                    a_idx = layers.index(a_label)
                    b_idx = layers.index(b_label)
                except ValueError:
                    continue
                if a_idx + 1 != b_idx:
                    direction = "backward" if b_idx < a_idx else "skips layers"
                    violations.append(
                        DriftViolation(
                            check="REQUIRED_LAYER",
                            severity=severity,
                            message=(
                                f"'{row[1]}' ({a_label}) calls '{row[4]}' ({b_label}) "
                                f"— {direction} in layer order "
                                f"{' -> '.join(layers)}"
                            ),
                            offending_node=row[1] or "",
                            offending_file=row[2] or "",
                            knowledge_node=rule_name,
                            knowledge_type=knowledge_type,
                        )
                    )
        except Exception:
            pass
        return violations

    def _eval_max_calls(
        self,
        rule_name: str,
        constraint: dict,
        severity: str,
        knowledge_type: str,
    ) -> list[DriftViolation]:
        """Evaluate a MAX_OUTGOING_CALLS constraint: nodes of a given label
        (optionally scoped to a domain) must not exceed N outgoing CALLS."""
        label = constraint.get("label", "")
        domain = constraint.get("domain", "")
        max_calls = constraint.get("max")
        if not label or max_calls is None:
            return []

        cypher = (
            "MATCH (n)-[:CALLS]->(target) "
            "WHERE labels(n)[0] = $label "
            "AND ($domain = '' OR (n)-[:BELONGS_TO]->(:Domain {name: $domain})) "
            "WITH n, count(target) AS cnt "
            "WHERE cnt > $max "
            "RETURN n.name, n.file_path, cnt LIMIT 20"
        )
        violations: list[DriftViolation] = []
        try:
            for row in (
                self.store.query(
                    cypher, {"label": label, "domain": domain, "max": max_calls}
                ).result_set
                or []
            ):
                violations.append(
                    DriftViolation(
                        check="MAX_OUTGOING_CALLS",
                        severity=severity,
                        message=(f"'{row[0]}' has {row[2]} outgoing calls (max {max_calls})"),
                        offending_node=row[0] or "",
                        offending_file=row[1] or "",
                        knowledge_node=rule_name,
                        knowledge_type=knowledge_type,
                    )
                )
        except Exception:
            pass
        return violations
