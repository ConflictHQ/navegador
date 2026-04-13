"""
TaskPack — compact, high-signal context bundles for AI agents.

Given a symbol, file, or ticket reference, assembles everything an agent
needs into a single artifact: code structure, callers/callees, linked rules,
decisions, wiki pages, memory nodes, owners, and related tests — without
requiring the agent to orchestrate multiple separate queries.

Usage::

    from navegador.taskpack import TaskPackBuilder, PackMode

    builder = TaskPackBuilder(store)

    # Symbol pack — context for modifying a function or class
    pack = builder.for_symbol("validate_token", file_path="app/auth.py")

    # File pack — context before editing a whole file
    pack = builder.for_file("app/payments/service.py")

    print(pack.to_markdown())   # inject directly into agent prompt
    print(pack.to_json())       # use in editor integrations / agent workflows
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from navegador.graph import GraphStore, queries

# ── Data model ───────────────────────────────────────────────────────────────


@dataclass
class TaskPackNode:
    type: str
    name: str
    file_path: str = ""
    line_start: int | None = None
    summary: str = ""
    relation: str = ""  # how this node relates to the target


@dataclass
class TaskPack:
    """A compact, structured context bundle ready for agent injection."""

    target_type: str  # "symbol" | "file"
    target_name: str
    target_file: str = ""
    mode: str = "implement"

    # Structured sections
    code: list[TaskPackNode] = field(default_factory=list)       # direct symbols
    callers: list[TaskPackNode] = field(default_factory=list)
    callees: list[TaskPackNode] = field(default_factory=list)
    rules: list[TaskPackNode] = field(default_factory=list)      # governing rules + memory
    docs: list[TaskPackNode] = field(default_factory=list)       # wiki + decisions + documents
    owners: list[TaskPackNode] = field(default_factory=list)
    tests: list[TaskPackNode] = field(default_factory=list)      # inferred test files/symbols
    imports: list[TaskPackNode] = field(default_factory=list)

    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        def _nodes(lst: list[TaskPackNode]) -> list[dict[str, Any]]:
            return [
                {k: v for k, v in n.__dict__.items() if v not in (None, "", [])}
                for n in lst
            ]

        return {
            "target": {
                "type": self.target_type,
                "name": self.target_name,
                "file": self.target_file,
                "mode": self.mode,
            },
            "code": _nodes(self.code),
            "callers": _nodes(self.callers),
            "callees": _nodes(self.callees),
            "rules": _nodes(self.rules),
            "docs": _nodes(self.docs),
            "owners": _nodes(self.owners),
            "tests": _nodes(self.tests),
            "imports": _nodes(self.imports),
            "metadata": self.metadata,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    def to_markdown(self) -> str:
        lines: list[str] = []
        target_label = (
            f"`{self.target_name}`"
            + (f" in `{self.target_file}`" if self.target_file else "")
        )
        lines.append(f"# Task Pack — {target_label}\n")
        lines.append(f"**Mode:** {self.mode}  **Type:** {self.target_type}\n")

        def _section(title: str, nodes: list[TaskPackNode]) -> None:
            if not nodes:
                return
            lines.append(f"\n## {title}\n")
            for n in nodes:
                loc = f"`{n.file_path}`" + (f":{n.line_start}" if n.line_start else "")
                summary = f" — {n.summary}" if n.summary else ""
                rel = f" _{n.relation}_" if n.relation else ""
                lines.append(f"- **{n.type}** `{n.name}` {loc}{rel}{summary}")

        _section("Target", self.code)
        _section("Callers", self.callers)
        _section("Callees", self.callees)
        _section("Imports", self.imports)
        _section("Governing Rules & Memory", self.rules)
        _section("Docs & Decisions", self.docs)
        _section("Owners", self.owners)
        _section("Related Tests", self.tests)

        if self.metadata:
            lines.append("\n## Metadata\n")
            for k, v in self.metadata.items():
                lines.append(f"- **{k}:** {v}")

        return "\n".join(lines)


# ── Builder ───────────────────────────────────────────────────────────────────


class TaskPackBuilder:
    """
    Assembles TaskPack objects from a GraphStore.

    Usage::

        builder = TaskPackBuilder(store)
        pack = builder.for_symbol("AuthService", file_path="app/auth/service.py")
        pack = builder.for_file("app/payments/service.py")
    """

    def __init__(self, store: GraphStore) -> None:
        self.store = store

    # ── Entry points ──────────────────────────────────────────────────────────

    def for_symbol(
        self,
        name: str,
        file_path: str = "",
        depth: int = 2,
        mode: str = "implement",
    ) -> TaskPack:
        """
        Build a task pack for a named function or class.

        Includes: symbol definition, callers, callees, imports, governing
        rules/memory, related docs/decisions, owners, and inferred tests.
        """
        pack = TaskPack(
            target_type="symbol",
            target_name=name,
            target_file=file_path,
            mode=mode,
        )

        self._add_symbol_code(pack, name, file_path)
        self._add_callers_callees(pack, name, file_path, depth)
        self._add_knowledge(pack, name, file_path)
        self._add_owners(pack, name, file_path)
        self._add_tests(pack, name, file_path)

        pack.metadata["depth"] = depth
        pack.metadata["sections"] = {
            k: len(getattr(pack, k))
            for k in ("code", "callers", "callees", "rules", "docs", "owners", "tests", "imports")
        }
        return pack

    def for_file(self, file_path: str, mode: str = "implement") -> TaskPack:
        """
        Build a task pack for a whole file.

        Includes: all symbols, imports, governing rules/memory, related docs,
        owners, and inferred tests.
        """
        pack = TaskPack(
            target_type="file",
            target_name=Path(file_path).name,
            target_file=file_path,
            mode=mode,
        )

        self._add_file_symbols(pack, file_path)
        self._add_file_knowledge(pack, file_path)
        self._add_owners(pack, file_path, "")
        self._add_tests(pack, Path(file_path).stem, file_path)

        pack.metadata["sections"] = {
            k: len(getattr(pack, k))
            for k in ("code", "callers", "callees", "rules", "docs", "owners", "tests", "imports")
        }
        return pack

    # ── Private helpers ───────────────────────────────────────────────────────

    def _add_symbol_code(self, pack: TaskPack, name: str, file_path: str) -> None:
        """Add the target symbol itself to pack.code."""
        cypher = (
            "MATCH (n) WHERE n.name = $name "
            "AND ($file_path = '' OR n.file_path = $file_path) "
            "AND (n:Function OR n:Class OR n:Method) "
            "RETURN labels(n)[0], n.name, n.file_path, n.line_start, "
            "coalesce(n.docstring, n.signature, '') LIMIT 1"
        )
        rows = (self.store.query(cypher, {"name": name, "file_path": file_path}).result_set or [])
        for row in rows:
            pack.code.append(
                TaskPackNode(type=row[0], name=row[1], file_path=row[2] or "",
                             line_start=row[3], summary=row[4] or "")
            )

    def _add_callers_callees(
        self, pack: TaskPack, name: str, file_path: str, depth: int
    ) -> None:
        params = {"name": name, "file_path": file_path, "depth": depth}

        for row in (self.store.query(queries.CALLERS, params).result_set or []):
            pack.callers.append(
                TaskPackNode(type=row[0], name=row[1], file_path=row[2] or "",
                             line_start=row[3], relation="calls this")
            )
        for row in (self.store.query(queries.CALLEES, params).result_set or []):
            pack.callees.append(
                TaskPackNode(type=row[0], name=row[1], file_path=row[2] or "",
                             line_start=row[3], relation="called by this")
            )

    def _add_knowledge(self, pack: TaskPack, name: str, file_path: str) -> None:
        """Collect governing rules, memory nodes, wiki pages, and decisions."""
        # Rules/memory that GOVERNS or ANNOTATES this symbol
        cypher = (
            "MATCH (k)-[:GOVERNS|ANNOTATES]->(n) "
            "WHERE n.name = $name AND ($file_path = '' OR n.file_path = $file_path) "
            "RETURN labels(k)[0], k.name, coalesce(k.description, k.rationale, '') "
            "LIMIT 20"
        )
        rows = self.store.query(
            cypher, {"name": name, "file_path": file_path}
        ).result_set or []
        for row in rows:
            label = row[0]
            if label in ("Rule", "Decision", "WikiPage", "Document"):
                target = pack.rules if label == "Rule" else pack.docs
            else:
                target = pack.rules
            target.append(TaskPackNode(type=label, name=row[1], summary=row[2] or ""))

        # Memory nodes for this file
        if file_path:
            mem_rows = (
                self.store.query(queries.MEMORY_FOR_FILE, {"path": file_path}).result_set or []
            )
            for row in mem_rows:
                pack.rules.append(
                    TaskPackNode(
                        type=row[0], name=row[1], summary=row[2] or "",
                        relation=f"memory:{row[3]}"
                    )
                )

    def _add_file_symbols(self, pack: TaskPack, file_path: str) -> None:
        """Add all symbols and imports from a file."""
        result = self.store.query(queries.FILE_CONTENTS, {"path": file_path})
        for row in (result.result_set or []):
            node_type = row[0] or "Unknown"
            node = TaskPackNode(
                type=node_type,
                name=row[1] or "",
                file_path=file_path,
                line_start=row[2],
                summary=row[3] or row[4] or "",
            )
            if node_type == "Import":
                pack.imports.append(node)
            else:
                pack.code.append(node)

    def _add_file_knowledge(self, pack: TaskPack, file_path: str) -> None:
        """Add all knowledge nodes linked to symbols in this file."""
        # Rules/memory for all symbols in the file
        cypher = (
            "MATCH (f:File {path: $path})-[:CONTAINS]->(sym)"
            "<-[:GOVERNS|ANNOTATES]-(k) "
            "RETURN DISTINCT labels(k)[0], k.name, "
            "coalesce(k.description, k.rationale, '') LIMIT 30"
        )
        for row in (self.store.query(cypher, {"path": file_path}).result_set or []):
            label = row[0]
            node = TaskPackNode(type=label, name=row[1], summary=row[2] or "")
            if label in ("Rule",):
                pack.rules.append(node)
            else:
                pack.docs.append(node)

        # Memory nodes for this file
        file_mem = (
            self.store.query(queries.MEMORY_FOR_FILE, {"path": file_path}).result_set or []
        )
        for row in file_mem:
            pack.rules.append(
                TaskPackNode(
                    type=row[0], name=row[1], summary=row[2] or "",
                    relation=f"memory:{row[3]}"
                )
            )

    def _add_owners(self, pack: TaskPack, name: str, file_path: str) -> None:
        cypher = (
            "MATCH (n)-[:ASSIGNED_TO]->(p:Person) "
            "WHERE n.name = $name AND ($file_path = '' OR n.file_path = $file_path) "
            "RETURN p.name, p.role, p.email LIMIT 10"
        )
        rows = self.store.query(
            cypher, {"name": name, "file_path": file_path}
        ).result_set or []
        for row in rows:
            pack.owners.append(
                TaskPackNode(type="Person", name=row[0] or "", summary=row[1] or row[2] or "")
            )

    def _add_tests(self, pack: TaskPack, name: str, file_path: str) -> None:
        """Infer test files/symbols by name convention or CALLS edges."""
        # Symbols named test_* or *Test* that call this symbol
        cypher = (
            "MATCH (t)-[:CALLS*1..2]->(n) "
            "WHERE n.name = $name "
            "AND ($file_path = '' OR n.file_path = $file_path) "
            "AND (t.name STARTS WITH 'test_' OR t.name ENDS WITH 'Test' "
            "     OR t.file_path CONTAINS 'test') "
            "RETURN DISTINCT labels(t)[0], t.name, t.file_path, t.line_start LIMIT 15"
        )
        rows = self.store.query(
            cypher, {"name": name, "file_path": file_path}
        ).result_set or []
        for row in rows:
            pack.tests.append(
                TaskPackNode(type=row[0], name=row[1], file_path=row[2] or "",
                             line_start=row[3], relation="tests this")
            )
