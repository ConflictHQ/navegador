# Dependency posture

**`pip install navegador` is the whole installation.**

Navegador must not require you to install, pin, audit and track a collection of
other open-source tools. Every capability either works with what is already in
the box, or degrades gracefully when an optional input is absent.

## Why

The tool exists to reduce the work of understanding a system. A context engine
that itself demands seven external binaries — each with its own release
cadence, provenance story and supply-chain exposure — has moved that cost
rather than removed it.

Lightness is a feature, not an accident of being early.

## What this rules in

Capabilities built on what is already present cost nothing new and are
preferred:

| capability | built on |
|---|---|
| vector search | FalkorDB's native vector index |
| full-text search | FalkorDB's full-text index |
| trigram search | Redis sets, on the server already running |
| content store | stdlib `zlib` |
| parsing | tree-sitter grammars, already the parsing layer |

Notably, the vector and full-text indexes were sitting unused on the server for
some time before anything queried them. Checking what the platform already does
is the first step, not the last resort.

## What this rules out

**Embedded language servers.** Thirteen languages means thirteen runtimes, each
stateful, slow to warm and often measured in gigabytes of memory. That directly
contradicts being a compact shared index. An LSP is also workspace-scoped, with
no concept of the many sibling repositories that are the interesting case.

**Shelling out to external search binaries.** No Zoekt, no ripgrep as a
load-bearing component. The trigram index is built in-house specifically
because integrating a battle-tested Go binary would mean shipping a Go binary.

That decision is worth being explicit about, since "just use the existing tool"
is usually right: the build cost of a trigram index is real and unchanged, but
the dependency cost is what tipped it.

**Anything that turns an optional capability into a hard requirement.**

## The optional-input pattern

Where an external artifact genuinely adds value, consume it if present and
degrade if not — never generate it, never require it.

Precise call edges are the reference case. A SCIP or LSIF index gives
compiler-grade name resolution that tree-sitter cannot, and if one exists
navegador should use it. But navegador will not invoke an indexer, vendor one,
or add one to its dependencies. If you want precise edges, you generate the
index with your own toolchain, on your own terms; without it, edges fall back
to import heuristics and are marked `inferred` so the difference is visible.

## Applying it

The review question on any new work: **does this add something the user has to
track?**

If yes, it needs an explicit justification or an optional-input design. If a
feature can only be built by taking on a dependency, that is a real trade to
discuss — not a detail to slip into `pyproject.toml`.
