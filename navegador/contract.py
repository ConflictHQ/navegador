"""
Supergraph interop contract v1.0 — the code realm's output boundary.

The contract (ConflictHQ/project-brain#60) lets every graph in the system
address and traverse the others as one network. Navegador owns the **code
realm**: the live code graph, which is deliberately never compiled into a brain.
Brain-side traversals reach it through join edges and continue here.

Conformance is a mapping layer at the tool surface — internal ids, the graph
store, and the schema are untouched. Integrate, don't refactor.

Address grammar (global)::

    [<repo>/]<kind>:<id>

The code realm owns its own id convention: a repo-relative POSIX path,
optionally followed by ``#`` and a qualified symbol. So::

    calliope-astrolift/code:src/auth.py                    a file
    calliope-astrolift/code:src/auth.py#validate_token     a function
    calliope-astrolift/code:src/auth.py#Auth.validate      a method
    code:src/auth.py#validate_token                        instance-local

The brain is authoritative for its own kinds and decides which proposed join
edges to commit; navegador only proposes. Code addresses appear in brain
artifacts solely as join-edge targets.
"""

from dataclasses import dataclass, field
from typing import Any

from navegador.graph import GraphStore

#: Contract version emitted in every conformant tool response.
CONTRACT_VERSION = "1.0"

#: This producer's realm.
REALM = "code"

#: The join edge the contract declares for brain → code.
JOIN_EDGE = "implemented_in"

#: Edge types a brain instance may commit from our proposals. `implemented_in`
#: is the join edge; the other two are ordinary contract edges that our
#: doc-link inference can also evidence.
PROPOSABLE_EDGES = frozenset({"implemented_in", "references", "about"})

#: Graph labels that denote a code entity addressable in this realm.
_SYMBOL_LABELS = ("Function", "Method", "Class", "Variable")
_FILE_LABELS = ("File", "Document")


class AddressError(ValueError):
    """Raised when a string is not a well-formed contract address."""


@dataclass(frozen=True)
class CodeAddress:
    """A parsed code-realm address."""

    path: str
    symbol: str = ""
    repo: str = ""

    def __str__(self) -> str:
        return format_address(self.path, self.symbol, self.repo)

    def to_dict(self) -> dict[str, Any]:
        return {
            "address": str(self),
            "realm": REALM,
            "repo": self.repo,
            "path": self.path,
            "symbol": self.symbol,
            "contract": CONTRACT_VERSION,
        }


def format_address(path: str, symbol: str = "", repo: str = "") -> str:
    """
    Build a contract address for a code entity.

    Args:
        path: Repo-relative POSIX path. The file is the identity.
        symbol: Optional qualified symbol within the file (``Auth.validate``).
        repo: Optional federation namespace. Omitted for instance-local
            addresses, exactly as the grammar specifies.
    """
    path = (path or "").replace("\\", "/").lstrip("/")
    if not path:
        raise AddressError("A code address needs a repo-relative path.")
    body = f"{path}#{symbol}" if symbol else path
    return f"{repo}/{REALM}:{body}" if repo else f"{REALM}:{body}"


def parse_address(address: str) -> CodeAddress:
    """
    Parse ``[<repo>/]code:<path>[#<symbol>]``.

    Raises:
        AddressError: when the string is not a code-realm address. A brain
            passing an address for a kind we do not own should get a clear
            rejection, not a silent miss.
    """
    raw = (address or "").strip()
    if not raw:
        raise AddressError("Empty address.")

    repo = ""
    marker = f"{REALM}:"
    index = raw.find(marker)
    if index == -1:
        raise AddressError(
            f"{address!r} is not a code-realm address. Expected [<repo>/]{REALM}:<path>[#<symbol>]."
        )
    if index:
        repo = raw[:index].rstrip("/")
        if not repo:
            raise AddressError(f"{address!r} has an empty repo namespace.")

    body = raw[index + len(marker) :]
    if not body:
        raise AddressError(f"{address!r} names no path.")

    path, _, symbol = body.partition("#")
    path = path.replace("\\", "/").lstrip("/")
    if not path:
        raise AddressError(f"{address!r} names no path.")
    return CodeAddress(path=path, symbol=symbol, repo=repo)


@dataclass
class ResolvedNode:
    """A code-graph node reached through a contract address."""

    address: str
    label: str
    name: str
    path: str
    found: bool = True
    properties: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract": CONTRACT_VERSION,
            "realm": REALM,
            "address": self.address,
            "found": self.found,
            "label": self.label,
            "name": self.name,
            "path": self.path,
            "properties": self.properties,
        }


def resolve(store: GraphStore, address: str) -> ResolvedNode:
    """
    Resolve a contract address to a node in the code graph.

    This is the supergraph hop: a brain-side ``implemented_in`` edge carries a
    code address, and resolving it here is the entry point from which ordinary
    traversal — callers, blast radius, owners — continues.

    A path may legitimately be recorded with or without its repo prefix
    depending on whether the graph was built by a plain or a workspace ingest,
    so both spellings are tried before reporting a miss.
    """
    parsed = parse_address(address)
    candidates = [parsed.path]
    if parsed.repo:
        # Workspace ingests record repo-prefixed paths (#144).
        candidates.append(f"{parsed.repo}/{parsed.path}")

    for path in candidates:
        if parsed.symbol:
            node = _find_symbol(store, path, parsed.symbol)
        else:
            node = _find_file(store, path)
        if node is not None:
            return ResolvedNode(
                address=str(parsed),
                label=node["label"],
                name=node["name"],
                path=node["path"],
                properties=node["properties"],
            )

    return ResolvedNode(address=str(parsed), label="", name="", path=parsed.path, found=False)


def _find_symbol(store: GraphStore, path: str, symbol: str) -> dict | None:
    """
    Locate a symbol in a file, accepting a qualified or bare name.

    ``Auth.validate`` should resolve to the method ``validate``; the qualifier
    is the class it lives in, which the graph records as a separate node.
    """
    bare = symbol.rsplit(".", 1)[-1]
    labels = " OR ".join(f"n:{label}" for label in _SYMBOL_LABELS)
    rows = store.query(
        f"MATCH (n) WHERE ({labels}) AND n.file_path = $path AND n.name IN $names "
        "RETURN labels(n)[0], n.name, n.file_path, properties(n) LIMIT 1",
        {"path": path, "names": list({symbol, bare})},
    ).result_set
    return _row_to_node(rows)


def _find_file(store: GraphStore, path: str) -> dict | None:
    labels = " OR ".join(f"n:{label}" for label in _FILE_LABELS)
    rows = store.query(
        f"MATCH (n) WHERE ({labels}) AND n.path = $path "
        "RETURN labels(n)[0], n.name, n.path, properties(n) LIMIT 1",
        {"path": path},
    ).result_set
    return _row_to_node(rows)


def _row_to_node(rows) -> dict | None:
    if not rows:
        return None
    row = rows[0]
    return {
        "label": row[0] or "",
        "name": row[1] or "",
        "path": row[2] or "",
        "properties": row[3] if isinstance(row[3], dict) else {},
    }


def address_for_node(label: str, name: str, path: str, repo: str = "") -> str | None:
    """
    Contract address for a graph node, or None when it is not a code entity.

    Knowledge nodes (Concept, Rule, Decision) belong to the brain realm — the
    brain is authoritative for those, so emitting a code address for one would
    claim ownership we do not have.
    """
    if label in _FILE_LABELS:
        return format_address(path, repo=repo) if path else None
    if label in _SYMBOL_LABELS:
        return format_address(path, symbol=name, repo=repo) if path else None
    return None


def propose_join_edges(
    store: GraphStore,
    repo: str = "",
    min_confidence: float = 0.5,
    limit: int = 200,
) -> dict[str, Any]:
    """
    Propose contract-format join edges from inferred doc↔code affinity.

    Navegador proposes; the brain decides. Each proposal carries the confidence
    and evidence the inference produced, so a brain instance can review before
    committing anything to files in git.

    Only candidates whose target is a code entity become proposals — a
    doc→concept affinity is entirely within the brain realm and is not ours to
    propose.
    """
    from navegador.intelligence.doclink import DocLinker

    candidates = DocLinker(store).suggest_links(min_confidence=min_confidence)

    proposals: list[dict[str, Any]] = []
    for candidate in candidates:
        target = address_for_node(
            candidate.target_label, candidate.target_name, candidate.target_file, repo=repo
        )
        if target is None:
            continue
        proposals.append(
            {
                "edge": JOIN_EDGE,
                "source": {
                    "kind": _brain_kind(candidate.source_label),
                    "name": candidate.source_name,
                },
                "target": target,
                "confidence": round(candidate.confidence, 3),
                "evidence": {
                    "strategy": candidate.strategy,
                    "rationale": candidate.rationale,
                    "producer": "navegador",
                },
            }
        )
        if len(proposals) >= limit:
            break

    return {
        "contract": CONTRACT_VERSION,
        "realm": REALM,
        "repo": repo,
        "join_edge": JOIN_EDGE,
        "proposals": proposals,
        "count": len(proposals),
    }


#: Graph labels on the documentation side mapped to the brain kinds they
#: correspond to. Unmapped labels pass through lowercased — the brain validates
#: kinds against its own schema and is free to reject one.
_BRAIN_KINDS = {
    "Document": "doc",
    "WikiPage": "wiki-page",
    "Decision": "decision",
    "Rule": "decision",
    "Concept": "glossary-term",
    "Memory": "memory-note",
}


def _brain_kind(label: str) -> str:
    return _BRAIN_KINDS.get(label, (label or "").lower())
