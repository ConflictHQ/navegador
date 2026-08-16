# Supergraph Contract

Navegador is one graph in a larger network. The supergraph interop contract
(v1.0) makes every graph in that network addressable and traversable from every
other one, and navegador owns the **code realm**.

Two properties follow from that ownership, and they explain the rest of this
page:

- The code graph is a live database, not files in git. It is deliberately
  **never compiled into a brain** — a brain records an address and traversal
  continues here.
- Navegador is authoritative for code and for nothing else. It **proposes** edges
  into other realms; the owning graph reviews and commits them.

Conformance is a mapping layer at the tool surface. Internal ids, the graph
store, and the schema are untouched.

## Addresses

The global grammar is:

```
[<repo>/]<kind>:<id>
```

Each realm owns its id convention. In the code realm an id is a repo-relative
POSIX path, optionally followed by `#` and a qualified symbol:

```
calliope-astrolift/code:src/auth.py                    a file
calliope-astrolift/code:src/auth.py#validate_token     a function
calliope-astrolift/code:src/auth.py#Auth.validate      a method
code:src/auth.py#validate_token                        instance-local
```

The repo prefix is a federation namespace. Omit it for an address that only has
to mean something on this instance; include it whenever the address will be
stored somewhere else, which is nearly always.

A qualified symbol names the path to the symbol, not a distinct node — the class
in `Auth.validate` is stored separately, and the address resolves to the method.

An address for a kind navegador does not own is rejected rather than missed:

```console
$ navegador contract resolve "myproj/story:AUTH-1"
Error: 'myproj/story:AUTH-1' is not a code-realm address.
Expected [<repo>/]code:<path>[#<symbol>].
```

## Resolving a join edge

`implemented_in` is the join edge from a work or record node to the code that
realizes it. The brain holds the edge; its target is a code address; resolving
that address is the hop into this graph.

```console
$ navegador contract resolve "calliope-astrolift/code:src/auth.py#validate_token"
Function validate_token
  address: calliope-astrolift/code:src/auth.py#validate_token
  path:    src/auth.py
```

From the resolved node, ordinary code-graph queries carry on — callers, blast
radius, owners. Over MCP, `resolve_address` returns the immediate neighbourhood
with the response, each neighbour carrying its own address, so a traversal does
not need a second round trip to keep moving:

```json
{
  "contract": "1.0",
  "realm": "code",
  "address": "calliope-astrolift/code:src/auth.py#validate_token",
  "found": true,
  "label": "Function",
  "name": "validate_token",
  "path": "src/auth.py",
  "neighbourhood": {
    "callers": [
      {
        "name": "login",
        "label": "Function",
        "address": "calliope-astrolift/code:src/views.py#login"
      }
    ],
    "callees": []
  }
}
```

A miss is reported as `"found": false`, not raised. The usual cause is that the
repo has not been ingested, or that its paths are recorded relative to a
different root — check `navegador repo nodes`.

## Proposing join edges

Navegador can infer which documentation describes which code, and emit those
inferences as contract-format proposals:

```console
$ navegador contract propose --repo calliope-astrolift --json
```

```json
{
  "contract": "1.0",
  "realm": "code",
  "join_edge": "implemented_in",
  "count": 1,
  "proposals": [
    {
      "edge": "implemented_in",
      "source": {"kind": "doc", "name": "auth-design.md"},
      "target": "calliope-astrolift/code:src/auth.py#validate_token",
      "confidence": 0.95,
      "evidence": {
        "strategy": "EXACT_NAME",
        "rationale": "`validate_token` appears in auth-design.md",
        "producer": "navegador"
      }
    }
  ]
}
```

Three things about this output are deliberate:

- **Nothing is written.** Proposing does not mutate the graph. The brain decides
  what becomes an edge, because the brain is where the edge lives.
- **Confidence and evidence travel with the proposal**, so review is possible
  without re-deriving the inference. Raise the bar with `--min-confidence`.
- **Targets are always code entities.** A documentation-to-concept affinity is
  entirely within the brain realm, and minting a code address for it would claim
  ownership navegador does not have. Those candidates are dropped.

Source labels are mapped into brain kinds (`Document` → `doc`, `Rule` →
`decision`, `Concept` → `glossary-term`) so the receiving brain can validate them
against its own schema. An unmapped label passes through lowercased; the brain is
free to reject it.

## Version declaration

Every response that emits a contract address states the version it conforms to:

```json
{"contract": "1.0", "realm": "code"}
```

`search_symbols` results also carry the address a brain would record to point
back at each hit, which is usually how a join edge gets proposed in the first
place.

## Python API

```python
from navegador.contract import (
    format_address, parse_address, resolve, propose_join_edges,
)

address = format_address("src/auth.py", "validate_token", repo="myproj")
# 'myproj/code:src/auth.py#validate_token'

parsed = parse_address(address)
# CodeAddress(path='src/auth.py', symbol='validate_token', repo='myproj')

node = resolve(store, address)
if node.found:
    ...  # continue traversal from node.name / node.path

payload = propose_join_edges(store, repo="myproj", min_confidence=0.8)
```
