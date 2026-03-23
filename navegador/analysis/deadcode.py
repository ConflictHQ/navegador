# Copyright CONFLICT LLC 2026 (weareconflict.com)
"""
Dead code detection — find functions, classes, and files with no inbound
references, calls, imports, or inheritance relationships.

A symbol is considered "dead" if nothing in the graph CALLS, REFERENCES,
INHERITS, or IMPORTS it.  Files are "orphan" if no other File IMPORTS them
and they contain at least one dead symbol.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from navegador.graph import GraphStore

# Functions/methods with no inbound CALLS or REFERENCES edges
_DEAD_FUNCTIONS_QUERY = """
MATCH (fn)
WHERE (fn:Function OR fn:Method)
  AND NOT ()-[:CALLS]->(fn)
  AND NOT ()-[:REFERENCES]->(fn)
RETURN labels(fn)[0] AS type, fn.name AS name,
       coalesce(fn.file_path, '') AS file_path,
       fn.line_start AS line_start
ORDER BY fn.file_path, fn.name
"""

# Classes with no inbound REFERENCES, INHERITS, IMPLEMENTS, or CALLS edges
_DEAD_CLASSES_QUERY = """
MATCH (cls:Class)
WHERE NOT ()-[:REFERENCES]->(cls)
  AND NOT ()-[:INHERITS]->(cls)
  AND NOT ()-[:IMPLEMENTS]->(cls)
  AND NOT ()-[:CALLS]->(cls)
RETURN cls.name AS name,
       coalesce(cls.file_path, '') AS file_path,
       cls.line_start AS line_start
ORDER BY cls.file_path, cls.name
"""

# Files that are not IMPORTED by anything
_ORPHAN_FILES_QUERY = """
MATCH (f:File)
WHERE NOT ()-[:IMPORTS]->(f)
RETURN f.path AS path
ORDER BY f.path
"""


@dataclass
class DeadCodeReport:
    """Report of unreachable symbols in the graph."""

    unreachable_functions: list[dict[str, Any]] = field(default_factory=list)
    unreachable_classes: list[dict[str, Any]] = field(default_factory=list)
    orphan_files: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "unreachable_functions": self.unreachable_functions,
            "unreachable_classes": self.unreachable_classes,
            "orphan_files": self.orphan_files,
            "summary": {
                "unreachable_functions": len(self.unreachable_functions),
                "unreachable_classes": len(self.unreachable_classes),
                "orphan_files": len(self.orphan_files),
            },
        }


class DeadCodeDetector:
    """
    Detect unreachable code in the navegador graph.

    A function/method is "dead" if nothing CALLS or REFERENCES it.
    A class is "dead" if nothing REFERENCES, INHERITS, IMPLEMENTS, or CALLS it.
    A file is an "orphan" if nothing IMPORTS it.

    Usage::

        store = GraphStore.sqlite()
        detector = DeadCodeDetector(store)
        report = detector.detect()
        print(report.unreachable_functions)
    """

    def __init__(self, store: GraphStore) -> None:
        self.store = store

    def detect(self) -> DeadCodeReport:
        """
        Run dead code detection across the full graph.

        Returns:
            DeadCodeReport with unreachable_functions, unreachable_classes,
            and orphan_files.
        """
        unreachable_functions = self._detect_dead_functions()
        unreachable_classes = self._detect_dead_classes()
        orphan_files = self._detect_orphan_files()

        return DeadCodeReport(
            unreachable_functions=unreachable_functions,
            unreachable_classes=unreachable_classes,
            orphan_files=orphan_files,
        )

    def _detect_dead_functions(self) -> list[dict[str, Any]]:
        try:
            result = self.store.query(_DEAD_FUNCTIONS_QUERY)
            rows = result.result_set or []
        except Exception:
            return []

        return [
            {
                "type": row[0] or "Function",
                "name": row[1] or "",
                "file_path": row[2] or "",
                "line_start": row[3],
            }
            for row in rows
        ]

    def _detect_dead_classes(self) -> list[dict[str, Any]]:
        try:
            result = self.store.query(_DEAD_CLASSES_QUERY)
            rows = result.result_set or []
        except Exception:
            return []

        return [
            {
                "name": row[0] or "",
                "file_path": row[1] or "",
                "line_start": row[2],
            }
            for row in rows
        ]

    def _detect_orphan_files(self) -> list[str]:
        try:
            result = self.store.query(_ORPHAN_FILES_QUERY)
            rows = result.result_set or []
        except Exception:
            return []

        return [row[0] or "" for row in rows if row[0]]
