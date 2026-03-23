# Copyright CONFLICT LLC 2026 (weareconflict.com)
"""
Test coverage mapping — link test functions to production code via TESTS edges.

Finds test functions (name starts with test_), resolves the production
symbol they exercise via:
  1. Existing CALLS edges from the test function to non-test symbols
  2. Name heuristics: test_foo → foo, test_foo_bar → foo / foo_bar

Creates TESTS edges in the graph for discovered links.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from navegador.graph import GraphStore

# All functions starting with test_
_TEST_FUNCTIONS_QUERY = """
MATCH (fn)
WHERE (fn:Function OR fn:Method) AND fn.name STARTS WITH 'test_'
RETURN fn.name AS name, coalesce(fn.file_path, '') AS file_path,
       fn.line_start AS line_start
ORDER BY fn.file_path, fn.name
"""

# Functions directly called by a test function
_CALLS_FROM_TEST = """
MATCH (test {name: $test_name})-[:CALLS]->(callee)
WHERE NOT callee.name STARTS WITH 'test_'
  AND ($file_path = '' OR test.file_path = $file_path)
RETURN labels(callee)[0] AS type, callee.name AS name,
       coalesce(callee.file_path, '') AS file_path
"""

# Lookup a production symbol by name (not a test function)
_FIND_PRODUCTION_SYMBOL = """
MATCH (n)
WHERE n.name = $name AND NOT n.name STARTS WITH 'test_'
  AND (n:Function OR n:Method OR n:Class)
RETURN labels(n)[0] AS type, n.name AS name,
       coalesce(n.file_path, '') AS file_path
LIMIT 1
"""

# Create a TESTS edge
_CREATE_TESTS_EDGE = """
MATCH (test), (prod)
WHERE (test.name = $test_name AND (test.file_path = $test_file OR $test_file = ''))
  AND (prod.name = $prod_name AND (prod.file_path = $prod_file OR $prod_file = ''))
MERGE (test)-[r:TESTS]->(prod)
"""


@dataclass
class TestLink:
    """A resolved link between a test function and a production symbol."""

    test_name: str
    test_file: str
    prod_name: str
    prod_file: str
    prod_type: str
    source: str  # "calls" | "heuristic"


@dataclass
class TestMapResult:
    """Result of running test coverage mapping."""

    links: list[TestLink] = field(default_factory=list)
    unmatched_tests: list[dict[str, Any]] = field(default_factory=list)
    edges_created: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "links": [
                {
                    "test_name": lnk.test_name,
                    "test_file": lnk.test_file,
                    "prod_name": lnk.prod_name,
                    "prod_file": lnk.prod_file,
                    "prod_type": lnk.prod_type,
                    "source": lnk.source,
                }
                for lnk in self.links
            ],
            "unmatched_tests": self.unmatched_tests,
            "edges_created": self.edges_created,
            "summary": {
                "matched": len(self.links),
                "unmatched": len(self.unmatched_tests),
                "edges_created": self.edges_created,
            },
        }


class TestMapper:
    """
    Map test functions to production code and persist TESTS edges.

    Usage::

        store = GraphStore.sqlite()
        mapper = TestMapper(store)
        result = mapper.map_tests()
        print(result.links)
    """

    def __init__(self, store: GraphStore) -> None:
        self.store = store

    def map_tests(self) -> TestMapResult:
        """
        Discover test → production mappings and write TESTS edges.

        Strategy per test function:
        1. Follow existing CALLS edges to non-test symbols (direct call evidence).
        2. Apply name heuristics: strip test_ prefix and look for matching symbol.

        Returns:
            TestMapResult with links, unmatched_tests, and edges_created count.
        """
        test_fns = self._get_test_functions()
        if not test_fns:
            return TestMapResult()

        links: list[TestLink] = []
        unmatched: list[dict[str, Any]] = []
        edges_created = 0

        for test in test_fns:
            test_name = test["name"]
            test_file = test["file_path"]

            resolved = self._resolve_via_calls(test_name, test_file)
            if not resolved:
                resolved = self._resolve_via_heuristic(test_name)

            if resolved:
                prod_type, prod_name, prod_file = resolved
                link = TestLink(
                    test_name=test_name,
                    test_file=test_file,
                    prod_name=prod_name,
                    prod_file=prod_file,
                    prod_type=prod_type,
                    source=(
                        "calls"
                        if self._resolve_via_calls(test_name, test_file)
                        else "heuristic"
                    ),
                )
                links.append(link)
                # Persist the TESTS edge
                try:
                    self.store.query(
                        _CREATE_TESTS_EDGE,
                        {
                            "test_name": test_name,
                            "test_file": test_file,
                            "prod_name": prod_name,
                            "prod_file": prod_file,
                        },
                    )
                    edges_created += 1
                except Exception:
                    pass
            else:
                unmatched.append(test)

        return TestMapResult(
            links=links,
            unmatched_tests=unmatched,
            edges_created=edges_created,
        )

    def _get_test_functions(self) -> list[dict[str, Any]]:
        try:
            result = self.store.query(_TEST_FUNCTIONS_QUERY)
            rows = result.result_set or []
        except Exception:
            return []

        return [
            {"name": row[0] or "", "file_path": row[1] or "", "line_start": row[2]}
            for row in rows
        ]

    def _resolve_via_calls(
        self, test_name: str, test_file: str
    ) -> tuple[str, str, str] | None:
        """Return (type, name, file_path) of the first non-test callee, or None."""
        try:
            result = self.store.query(
                _CALLS_FROM_TEST, {"test_name": test_name, "file_path": test_file}
            )
            rows = result.result_set or []
        except Exception:
            return None

        if rows:
            row = rows[0]
            return (row[0] or "Function", row[1] or "", row[2] or "")
        return None

    def _resolve_via_heuristic(self, test_name: str) -> tuple[str, str, str] | None:
        """
        Strip test_ prefix and try increasingly shorter name suffixes.

        test_validate_token → validate_token, then validate
        """
        if not test_name.startswith("test_"):
            return None

        stripped = test_name[len("test_"):]
        parts = stripped.split("_")

        # Try full stripped name first, then progressively shorter prefixes
        candidates = []
        for i in range(len(parts), 0, -1):
            candidates.append("_".join(parts[:i]))

        for candidate in candidates:
            try:
                result = self.store.query(_FIND_PRODUCTION_SYMBOL, {"name": candidate})
                rows = result.result_set or []
            except Exception:
                continue

            if rows:
                row = rows[0]
                return (row[0] or "Function", row[1] or "", row[2] or "")

        return None
