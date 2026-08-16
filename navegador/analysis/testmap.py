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

# Every candidate production symbol with that name, with the repository that
# owns it. Deliberately not LIMIT 1: picking an arbitrary one of many identically
# named symbols is what produced confident cross-repository nonsense (#166).
_FIND_PRODUCTION_CANDIDATES = """
MATCH (n)
WHERE n.name = $name AND NOT n.name STARTS WITH 'test_'
  AND (n:Function OR n:Method OR n:Class)
OPTIONAL MATCH (f:File {path: n.file_path})-[:BELONGS_TO]->(r:Repository)
RETURN labels(n)[0] AS type, n.name AS name,
       coalesce(n.file_path, '') AS file_path,
       coalesce(r.path, '') AS repo
"""

# The repository a test function belongs to, for same-repo scoring.
_SYMBOL_REPO = """
MATCH (f:File {path: $file_path})-[:BELONGS_TO]->(r:Repository)
RETURN coalesce(r.path, '') AS repo
LIMIT 1
"""

# Create a TESTS edge carrying its evidence, so a consumer can filter on it.
_CREATE_TESTS_EDGE = """
MATCH (test), (prod)
WHERE (test.name = $test_name AND (test.file_path = $test_file OR $test_file = ''))
  AND (prod.name = $prod_name AND (prod.file_path = $prod_file OR $prod_file = ''))
MERGE (test)-[r:TESTS]->(prod)
SET r.confidence = $confidence, r.evidence = $evidence
"""

#: Names too common to carry any signal on their own. A test named
#: `test_request_returns_200` degrades to the candidate `request`, which in a
#: federated graph matches production code, shell helpers and other tests alike.
GENERIC_NAMES = frozenset(
    {
        "add",
        "all",
        "apply",
        "build",
        "call",
        "check",
        "clean",
        "clear",
        "close",
        "config",
        "connect",
        "create",
        "delete",
        "do",
        "execute",
        "exists",
        "fetch",
        "filter",
        "find",
        "format",
        "get",
        "handle",
        "init",
        "insert",
        "list",
        "load",
        "main",
        "make",
        "merge",
        "name",
        "new",
        "open",
        "parse",
        "process",
        "publish",
        "put",
        "query",
        "read",
        "remove",
        "render",
        "request",
        "reset",
        "resolve",
        "run",
        "save",
        "send",
        "set",
        "setup",
        "start",
        "stop",
        "sync",
        "update",
        "validate",
        "value",
        "write",
    }
)

#: Confidence floor for writing an edge. A direct CALLS edge is evidence; a name
#: that merely coincides across repositories is not.
DEFAULT_MIN_CONFIDENCE = 0.5


def _is_test_path(file_path: str) -> bool:
    """
    True when a path looks like test code rather than production code.

    Excluding only symbols *named* ``test_*`` was not enough: a helper called
    ``build_payload`` living in a test module is still test code, and mapping a
    test onto it says nothing about what the test covers.
    """
    if not file_path:
        return False
    lowered = file_path.replace("\\", "/").lower()
    parts = lowered.split("/")
    if any(part in ("test", "tests", "testing", "spec", "specs", "__tests__") for part in parts):
        return True
    leaf = parts[-1]
    stem = leaf.rsplit(".", 1)[0]
    return stem.startswith("test_") or stem.endswith(("_test", "_spec", ".test", ".spec"))


def _shared_prefix_depth(a: str, b: str) -> int:
    """Number of leading path segments two directories have in common."""
    left, right = a.split("/"), b.split("/")
    depth = 0
    for x, y in zip(left, right):
        if x != y:
            break
        depth += 1
    return depth


@dataclass
class TestLink:
    """A resolved link between a test function and a production symbol."""

    test_name: str
    test_file: str
    prod_name: str
    prod_file: str
    prod_type: str
    source: str  # "calls" | "heuristic"
    confidence: float = 1.0
    evidence: str = ""


@dataclass
class TestMapResult:
    """Result of running test coverage mapping."""

    links: list[TestLink] = field(default_factory=list)
    unmatched_tests: list[dict[str, Any]] = field(default_factory=list)
    ambiguous: list[dict[str, Any]] = field(default_factory=list)
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
                    "confidence": lnk.confidence,
                    "evidence": lnk.evidence,
                }
                for lnk in self.links
            ],
            "unmatched_tests": self.unmatched_tests,
            "ambiguous": self.ambiguous,
            "edges_created": self.edges_created,
            "summary": {
                "matched": len(self.links),
                "unmatched": len(self.unmatched_tests),
                "ambiguous": len(self.ambiguous),
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

    def map_tests(self, min_confidence: float = DEFAULT_MIN_CONFIDENCE) -> TestMapResult:
        """
        Discover test → production mappings and write TESTS edges.

        Per test function:

        1. Follow an existing CALLS edge to a non-test symbol. This is real
           evidence and is taken at full confidence.
        2. Otherwise score name-derived candidates, scoped to the repository and
           module the test lives in. A candidate that only matches because two
           repositories happen to use the same generic verb scores below the
           floor and produces no edge.

        A wrong TESTS edge is worse than a missing one: impact and context
        queries cite it confidently, and users stop trusting the graph (#166).

        Args:
            min_confidence: Floor for writing an edge. Weaker matches are
                reported as unmatched or ambiguous instead.
        """
        test_fns = self._get_test_functions()
        if not test_fns:
            return TestMapResult()

        links: list[TestLink] = []
        unmatched: list[dict[str, Any]] = []
        ambiguous: list[dict[str, Any]] = []
        edges_created = 0

        for test in test_fns:
            test_name = test["name"]
            test_file = test["file_path"]

            resolved = self._resolve_via_calls(test_name, test_file)
            if resolved:
                prod_type, prod_name, prod_file = resolved
                link = TestLink(
                    test_name=test_name,
                    test_file=test_file,
                    prod_name=prod_name,
                    prod_file=prod_file,
                    prod_type=prod_type,
                    source="calls",
                    confidence=1.0,
                    evidence="direct CALLS edge",
                )
            else:
                scored = self._score_heuristic_candidates(test_name, test_file)
                if not scored:
                    unmatched.append(test)
                    continue

                best = scored[0]
                rivals = [c for c in scored[1:] if abs(c["score"] - best["score"]) < 0.05]
                if rivals:
                    # Several candidates are equally plausible. Reporting the
                    # ambiguity is honest; picking one at random is not.
                    ambiguous.append(
                        {
                            "test_name": test_name,
                            "test_file": test_file,
                            "candidates": [
                                {"name": c["name"], "file_path": c["file_path"], "repo": c["repo"]}
                                for c in [best, *rivals][:5]
                            ],
                        }
                    )
                    continue

                if best["score"] < min_confidence:
                    unmatched.append(test)
                    continue

                link = TestLink(
                    test_name=test_name,
                    test_file=test_file,
                    prod_name=best["name"],
                    prod_file=best["file_path"],
                    prod_type=best["type"],
                    source="heuristic",
                    confidence=round(best["score"], 2),
                    evidence=best["evidence"],
                )

            links.append(link)
            try:
                self.store.query(
                    _CREATE_TESTS_EDGE,
                    {
                        "test_name": link.test_name,
                        "test_file": link.test_file,
                        "prod_name": link.prod_name,
                        "prod_file": link.prod_file,
                        "confidence": link.confidence,
                        "evidence": link.evidence,
                    },
                )
                edges_created += 1
            except Exception:
                pass

        return TestMapResult(
            links=links,
            unmatched_tests=unmatched,
            ambiguous=ambiguous,
            edges_created=edges_created,
        )

    def _get_test_functions(self) -> list[dict[str, Any]]:
        try:
            result = self.store.query(_TEST_FUNCTIONS_QUERY)
            rows = result.result_set or []
        except Exception:
            return []

        return [
            {"name": row[0] or "", "file_path": row[1] or "", "line_start": row[2]} for row in rows
        ]

    def _resolve_via_calls(self, test_name: str, test_file: str) -> tuple[str, str, str] | None:
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

    def _score_heuristic_candidates(self, test_name: str, test_file: str) -> list[dict[str, Any]]:
        """
        Rank name-derived candidates, best first.

        The old resolver stripped ``test_`` and tried ever-shorter prefixes,
        taking the first symbol with that name anywhere in the graph. In a
        federated graph ``test_request_returns_200`` degraded to ``request`` and
        matched an unrelated repository's method with total confidence.

        Scoring rewards evidence and penalises coincidence:

        - the full stripped name beats a truncated prefix
        - the same repository beats a different one
        - a neighbouring module beats an unrelated path
        - a generic verb on its own scores near zero
        """
        if not test_name.startswith("test_"):
            return []

        stripped = test_name[len("test_") :]
        parts = [p for p in stripped.split("_") if p]
        if not parts:
            return []

        test_repo = self._repo_of(test_file)
        test_dir = test_file.rsplit("/", 1)[0] if "/" in test_file else ""

        scored: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()

        for length in range(len(parts), 0, -1):
            candidate = "_".join(parts[:length])
            # Longer names are more distinctive; a bare generic verb is not a
            # signal at all.
            specificity = length / len(parts)
            if candidate in GENERIC_NAMES and length == 1:
                continue

            for row in self._production_candidates(candidate):
                key = (row["name"], row["file_path"])
                if key in seen:
                    continue
                seen.add(key)

                if _is_test_path(row["file_path"]):
                    # Mapping a test onto another test is never what was meant.
                    continue

                score = 0.35 * specificity
                evidence = [f"name match on {candidate!r}"]

                if test_repo and row["repo"] == test_repo:
                    score += 0.35
                    evidence.append("same repository")
                elif test_repo and row["repo"]:
                    score -= 0.15
                    evidence.append("different repository")

                prod_dir = row["file_path"].rsplit("/", 1)[0] if "/" in row["file_path"] else ""
                if test_dir and prod_dir and _shared_prefix_depth(test_dir, prod_dir) >= 1:
                    score += 0.25
                    evidence.append("neighbouring module")

                if length == len(parts):
                    score += 0.15
                    evidence.append("full name")

                scored.append(
                    {
                        **row,
                        "score": max(0.0, min(1.0, score)),
                        "evidence": ", ".join(evidence),
                    }
                )

        scored.sort(key=lambda c: -c["score"])
        return scored

    def _repo_of(self, file_path: str) -> str:
        if not file_path:
            return ""
        try:
            rows = self.store.query(_SYMBOL_REPO, {"file_path": file_path}).result_set or []
        except Exception:
            return ""
        return (rows[0][0] or "") if rows else ""

    def _production_candidates(self, name: str) -> list[dict[str, Any]]:
        try:
            rows = self.store.query(_FIND_PRODUCTION_CANDIDATES, {"name": name}).result_set or []
        except Exception:
            return []
        return [
            {
                "type": row[0] or "Function",
                "name": row[1] or "",
                "file_path": row[2] or "",
                "repo": row[3] or "",
            }
            for row in rows
        ]
