"""
MCP server security — query validation and complexity checks.

Two layers of protection:
  1. validate_cypher  — blocks write operations and injection patterns
  2. check_complexity — enforces depth and result-set size limits
"""

from __future__ import annotations

import re


class QueryValidationError(Exception):
    """Raised when a Cypher query contains a disallowed pattern."""


class QueryComplexityError(Exception):
    """Raised when a Cypher query exceeds complexity limits."""


# ── Write-operation keywords ───────────────────────────────────────────────────

_WRITE_KEYWORDS: tuple[str, ...] = (
    "CREATE",
    "MERGE",
    "SET",
    "DELETE",
    "REMOVE",
    "DROP",
)

# ── Injection patterns ────────────────────────────────────────────────────────

# CALL procedure invocations  (e.g.  CALL db.labels())
_CALL_RE = re.compile(r"\bCALL\b", re.IGNORECASE)

# Nested / sub-queries introduced by  { ... }  preceded by a Cypher keyword.
# We detect the presence of balanced braces that follow MATCH/WITH/WHERE/RETURN
# as a heuristic for sub-query injection.
_SUBQUERY_RE = re.compile(
    r"\b(?:MATCH|WITH|WHERE|RETURN)\s*\{",
    re.IGNORECASE,
)

# ── Variable-length path pattern ─────────────────────────────────────────────

# Matches relationship depth specifiers like  *1..100  or  *..50  or  *5..
# Group 1 = lower bound (may be empty), Group 2 = upper bound (may be empty).
_VARLEN_RE = re.compile(r"\*(\d*)\.\.((\d*))|\*(\d+)")


def validate_cypher(query: str) -> None:
    """
    Validate *query* for dangerous or disallowed patterns.

    Raises:
        QueryValidationError: if the query contains write operations or
                              injection patterns (CALL, nested sub-queries).
    """
    # Strip single-line comments before analysis
    stripped = re.sub(r"//[^\n]*", "", query)

    upper = stripped.upper()

    # Check for write-operation keywords as whole words
    for kw in _WRITE_KEYWORDS:
        pattern = re.compile(rf"\b{kw}\b")
        if pattern.search(upper):
            raise QueryValidationError(
                f"Write operation '{kw}' is not allowed in read-only mode."
            )

    # Check for CALL procedure injection
    if _CALL_RE.search(stripped):
        raise QueryValidationError(
            "CALL procedures are not allowed in read-only mode."
        )

    # Check for nested / sub-query patterns
    if _SUBQUERY_RE.search(stripped):
        raise QueryValidationError(
            "Nested sub-queries are not allowed in read-only mode."
        )


def check_complexity(
    query: str,
    max_depth: int = 5,
    max_results: int = 1000,
) -> None:
    """
    Check *query* for complexity issues.

    Raises:
        QueryComplexityError: if variable-length paths exceed *max_depth* or
                              if the query has no LIMIT and could be unbounded.
    """
    # ── 1. Variable-length path depth check ───────────────────────────────────
    for m in _VARLEN_RE.finditer(query):
        # Pattern: *lower..upper
        if m.group(1) is not None or m.group(2) is not None:
            upper_str = m.group(2)  # may be empty string (open-ended)
            if upper_str == "":
                # Open-ended: *1..  — treat as unbounded
                raise QueryComplexityError(
                    f"Variable-length path with no upper bound is not allowed "
                    f"(max depth: {max_depth})."
                )
            upper_val = int(upper_str)
            if upper_val > max_depth:
                raise QueryComplexityError(
                    f"Variable-length path depth {upper_val} exceeds maximum "
                    f"allowed depth of {max_depth}."
                )
        else:
            # Pattern: *N  (exact repetition — check against max_depth)
            exact_str = m.group(4)
            if exact_str:
                exact_val = int(exact_str)
                if exact_val > max_depth:
                    raise QueryComplexityError(
                        f"Variable-length path depth {exact_val} exceeds maximum "
                        f"allowed depth of {max_depth}."
                    )

    # ── 2. Unbounded result check ──────────────────────────────────────────────
    # Queries that contain MATCH/RETURN but no LIMIT clause may return huge
    # result sets.  Skip the check for queries that look purely structural
    # (e.g. COUNT aggregations that are inherently bounded).
    upper = query.upper()
    has_match = bool(re.search(r"\bMATCH\b", upper))
    has_return = bool(re.search(r"\bRETURN\b", upper))
    has_limit = bool(re.search(r"\bLIMIT\b", upper))
    has_count = bool(re.search(r"\bCOUNT\s*\(", upper))

    if has_match and has_return and not has_limit and not has_count:
        raise QueryComplexityError(
            f"Query has no LIMIT clause and could return unbounded results "
            f"(max: {max_results}). Add 'LIMIT {max_results}' or fewer."
        )
