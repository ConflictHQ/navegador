# Copyright CONFLICT LLC 2026 (weareconflict.com)
"""
Circular dependency detection — find cycles in import and call graphs.

Uses iterative DFS with back-edge detection on the adjacency extracted from
the graph.  Two detectors:

  detect_import_cycles()  — cycles over IMPORTS edges between File/Module nodes
  detect_call_cycles()    — cycles over CALLS edges between Function/Method nodes
"""

from __future__ import annotations

from navegador.graph import GraphStore

# All IMPORTS edges: (source_name, source_file, target_name, target_file)
_ALL_IMPORTS_QUERY = """
MATCH (src)-[:IMPORTS]->(tgt)
RETURN src.name AS src_name, coalesce(src.file_path, src.path, '') AS src_path,
       tgt.name AS tgt_name, coalesce(tgt.file_path, tgt.path, '') AS tgt_path
"""

# All CALLS edges: (caller_name, callee_name)
_ALL_CALLS_QUERY = """
MATCH (caller)-[:CALLS]->(callee)
WHERE (caller:Function OR caller:Method) AND (callee:Function OR callee:Method)
RETURN caller.name AS caller, callee.name AS callee
"""

# A Cycle is a list of node names forming the cycle path
Cycle = list[str]


def _find_cycles(adjacency: dict[str, list[str]]) -> list[Cycle]:
    """
    Find all simple cycles in a directed graph using iterative DFS.

    Returns a list of cycles, each as an ordered list of node names
    from the back-edge target to the node that closes the cycle.
    Each cycle is normalised (rotated to start from its lexicographically
    smallest node) and de-duplicated.
    """
    seen_cycles: set[tuple[str, ...]] = set()
    cycles: list[Cycle] = []

    # Colour: 0=white, 1=grey (in stack), 2=black (done)
    colour: dict[str, int] = {}
    parent: dict[str, str | None] = {}

    def _normalize(cycle: list[str]) -> tuple[str, ...]:
        """Rotate cycle so smallest element is first."""
        if not cycle:
            return ()
        min_idx = cycle.index(min(cycle))
        rotated = cycle[min_idx:] + cycle[:min_idx]
        return tuple(rotated)

    for start in list(adjacency.keys()):
        if colour.get(start, 0) != 0:
            continue

        # Iterative DFS using an explicit stack
        # Stack items: (node, iterator over neighbors, path so far)
        stack: list[tuple[str, list[str], list[str]]] = [
            (start, list(adjacency.get(start, [])), [start])
        ]
        colour[start] = 1
        parent[start] = None

        while stack:
            node, neighbors, path = stack[-1]

            if not neighbors:
                colour[node] = 2
                stack.pop()
                continue

            neighbor = neighbors.pop(0)
            stack[-1] = (node, neighbors, path)

            n_colour = colour.get(neighbor, 0)

            if n_colour == 0:
                colour[neighbor] = 1
                parent[neighbor] = node
                stack.append((neighbor, list(adjacency.get(neighbor, [])), path + [neighbor]))

            elif n_colour == 1:
                # Back edge → cycle found
                # Extract cycle from path
                try:
                    idx = path.index(neighbor)
                    cycle = path[idx:]
                except ValueError:
                    cycle = [neighbor]

                norm = _normalize(cycle)
                if norm and norm not in seen_cycles:
                    seen_cycles.add(norm)
                    cycles.append(list(norm))

    return cycles


class CycleDetector:
    """
    Detect circular dependencies in the navegador graph.

    Usage::

        store = GraphStore.sqlite()
        detector = CycleDetector(store)
        import_cycles = detector.detect_import_cycles()
        call_cycles   = detector.detect_call_cycles()
    """

    def __init__(self, store: GraphStore) -> None:
        self.store = store

    def detect_import_cycles(self) -> list[Cycle]:
        """
        Detect cycles in the IMPORTS edge graph.

        Returns:
            List of cycles; each cycle is a list of file-path/module strings.
        """
        adjacency = self._build_import_adjacency()
        return _find_cycles(adjacency)

    def detect_call_cycles(self) -> list[Cycle]:
        """
        Detect cycles in the CALLS edge graph (functions/methods only).

        Returns:
            List of cycles; each cycle is a list of function name strings.
        """
        adjacency = self._build_call_adjacency()
        return _find_cycles(adjacency)

    # ── Private helpers ───────────────────────────────────────────────────────

    def _build_import_adjacency(self) -> dict[str, list[str]]:
        """Build adjacency dict from IMPORTS edges, keyed by file_path."""
        try:
            result = self.store.query(_ALL_IMPORTS_QUERY)
            rows = result.result_set or []
        except Exception:
            return {}

        adjacency: dict[str, list[str]] = {}
        for row in rows:
            src_path = row[1] or row[0] or ""
            tgt_path = row[3] or row[2] or ""
            if not src_path or not tgt_path or src_path == tgt_path:
                continue
            if src_path not in adjacency:
                adjacency[src_path] = []
            if tgt_path not in adjacency[src_path]:
                adjacency[src_path].append(tgt_path)

        return adjacency

    def _build_call_adjacency(self) -> dict[str, list[str]]:
        """Build adjacency dict from CALLS edges, keyed by function name."""
        try:
            result = self.store.query(_ALL_CALLS_QUERY)
            rows = result.result_set or []
        except Exception:
            return {}

        adjacency: dict[str, list[str]] = {}
        for row in rows:
            caller = row[0] or ""
            callee = row[1] or ""
            if not caller or not callee or caller == callee:
                continue
            if caller not in adjacency:
                adjacency[caller] = []
            if callee not in adjacency[caller]:
                adjacency[caller].append(callee)

        return adjacency
