# Copyright CONFLICT LLC 2026 (weareconflict.com)
"""
Execution flow tracing — follow CALLS edges forward from an entry point
to produce concrete call chains.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from navegador.graph import GraphStore

# Cypher: one hop of CALLS from a set of names → (caller, callee, callee_file)
_CALLS_FROM = """
MATCH (caller)-[:CALLS]->(callee)
WHERE caller.name IN $names AND ($file_path = '' OR caller.file_path = $file_path)
RETURN DISTINCT
    caller.name AS caller_name,
    callee.name AS callee_name,
    coalesce(callee.file_path, '') AS callee_file_path
"""

# Entry-point lookup: resolve the starting node's file_path
_RESOLVE_ENTRY = """
MATCH (n)
WHERE n.name = $name AND ($file_path = '' OR n.file_path = $file_path)
RETURN n.name AS name, coalesce(n.file_path, '') AS file_path
LIMIT 1
"""


# A single step in a call chain: (caller_name, callee_name, file_path of callee)
CallStep = tuple[str, str, str]


@dataclass
class CallChain:
    """A single execution path from an entry point."""

    steps: list[CallStep] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.steps)

    def to_list(self) -> list[dict[str, str]]:
        return [
            {"caller": caller, "callee": callee, "file_path": fp}
            for caller, callee, fp in self.steps
        ]


class FlowTracer:
    """
    Execution flow tracer: follows CALLS edges forward from an entry point.

    Usage::

        store = GraphStore.sqlite()
        tracer = FlowTracer(store)
        chains = tracer.trace("handle_request", max_depth=5)
        for chain in chains:
            print(chain.to_list())
    """

    def __init__(self, store: GraphStore) -> None:
        self.store = store

    def trace(
        self,
        entry_name: str,
        file_path: str = "",
        max_depth: int = 10,
    ) -> list[CallChain]:
        """
        Trace execution flow forward from *entry_name*.

        Performs a BFS over CALLS edges, collecting one CallChain per
        unique path. Cycles are broken by tracking visited (caller, callee)
        pairs per path.

        Args:
            entry_name: Name of the entry-point function/method.
            file_path:  Narrow to a specific file (optional).
            max_depth:  Maximum call depth to traverse.

        Returns:
            List of CallChain objects, each representing one execution path.
        """
        params: dict[str, Any] = {"name": entry_name, "file_path": file_path}
        try:
            entry_result = self.store.query(_RESOLVE_ENTRY, params)
            entry_rows = entry_result.result_set or []
        except Exception:
            entry_rows = []

        if not entry_rows:
            # Entry point not found — return empty
            return []

        # BFS frontier: list of (current_path: list[CallStep], frontier_names: set[str])
        # Start with the entry point as the initial frontier
        chains: list[CallChain] = []
        # Each frontier item: (path_so_far, {caller_names at this depth}, visited_names_in_path)
        frontier: list[tuple[list[CallStep], set[str], set[str]]] = [
            ([], {entry_name}, {entry_name})
        ]
        seen_paths: set[tuple[CallStep, ...]] = set()

        for _depth in range(max_depth):
            if not frontier:
                break

            next_frontier: list[tuple[list[CallStep], set[str], set[str]]] = []

            for path, current_names, visited in frontier:
                if not current_names:
                    continue

                query_file = file_path if len(path) == 0 else ""
                try:
                    result = self.store.query(
                        _CALLS_FROM,
                        {"names": list(current_names), "file_path": query_file},
                    )
                    rows = result.result_set or []
                except Exception:
                    rows = []

                if not rows:
                    # Dead end — record the chain if it has steps
                    if path:
                        key = tuple(path)
                        if key not in seen_paths:
                            seen_paths.add(key)
                            chains.append(CallChain(steps=list(path)))
                    continue

                # Group by caller to expand each step
                by_caller: dict[str, list[tuple[str, str]]] = {}
                for row in rows:
                    caller = row[0] or ""
                    callee = row[1] or ""
                    callee_fp = row[2] or ""
                    if caller not in by_caller:
                        by_caller[caller] = []
                    by_caller[caller].append((callee, callee_fp))

                for caller, callees in by_caller.items():
                    for callee, callee_fp in callees:
                        if callee in visited:
                            # Cycle — close this chain
                            new_path = path + [(caller, callee, callee_fp)]
                            key = tuple(new_path)
                            if key not in seen_paths:
                                seen_paths.add(key)
                                chains.append(CallChain(steps=new_path))
                            continue

                        new_path = path + [(caller, callee, callee_fp)]
                        new_visited = visited | {callee}
                        next_frontier.append((new_path, {callee}, new_visited))

            frontier = next_frontier

        # Flush remaining frontier chains
        for path, _, _ in frontier:
            if path:
                key = tuple(path)
                if key not in seen_paths:
                    seen_paths.add(key)
                    chains.append(CallChain(steps=list(path)))

        return chains
