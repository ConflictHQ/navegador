# Targeting

Navegador's job for an agent is not to answer questions about your code. It is
to say **where to look**, so the agent stops searching and starts reading.

## Why this shape

Grep is not the problem. Once an agent knows where to point it, grep is fast
and precise. Telemetry across 151 real agent sessions and 25 projects found
that agents already scope **96.5%** of their searches to a named file, an
explicit file list, or a subdirectory. Only 3.4% sweep a whole tree.

What costs is the turns spent working out *where*:

| | median |
|---|---:|
| tool calls per session | 206 |
| filesystem reads per session | 35 |
| turns before touching the file that gets edited | 13 |
| reads that re-open a file already read this session | 32% |

Each of those turns is inference latency and context window. So the unit of
waste is a turn, not a byte, and these tools are built to reduce turns.

They return places and reasons. They never synthesise an answer — being handed
six files and told why beats being handed a paragraph you have to trust.

## `locate` — where should I look?

```bash
navegador locate "rate limiting"
```

Ranked candidates, each stating why it surfaced. Four sources are queried
independently and fused:

| source | what it contributes |
|---|---|
| exact | the literal appears at this line |
| symbol | a function, method or class with a matching name |
| prose | this file's comments, string literals or identifiers are about it |
| vector | semantically similar, when an LLM provider is configured |

Fusion is by **reciprocal rank**, not by adding scores — a trigram hit, a
cosine distance and a graph traversal are not on comparable scales. Each source
votes by position, so a place two sources agree on outranks one that only the
strongest source found.

A source that is unavailable contributes nothing and the rest still answer.

## `scope` — what is worth searching?

```bash
navegador scope validate_token
navegador scope validate_token --pattern "token"
```

The set of files reachable from a symbol through calls, references and imports.

**This is the reduction no flat text index can compute.** A text index finds
every file that mentions "token"; the graph knows which files are actually
connected to the one you are changing. On this repository,
`scope required_trigrams` returns 2 files out of 264.

With `--pattern`, the search runs only inside that scope. An unknown symbol
returns nothing rather than falling back to the whole repository — silently
widening would make every scoped search a full search.

Hand the file list to any tool you like:

```bash
rg "token" $(navegador scope validate_token --json | jq -r '.files[]')
```

## `grep` — exact text, cheaply

```bash
navegador grep "token expired"
navegador grep "def _\w+_store" --regex
```

Substring and regular-expression search over indexed content, returning file,
line number and the matching line.

Results are **exact**. Trigrams choose which files to look at; the real pattern
is then matched against stored text, so the index may over-select but never
under-select. This is verified by a differential test against ripgrep on
navegador's own source — eight patterns including regexes, zero disagreements.

The property that matters is how cost scales. Measured on a 263-file corpus
against warm ripgrep:

| pattern | candidate files | navegador | ripgrep |
|---|---:|---:|---:|
| `content_hash` | 16 | 7.2 ms | 11.5 ms |
| `GRAPH.LIST` | 4 | 1.9 ms | 12.6 ms |
| `queryNodes` | 1 | 1.0 ms | 12.2 ms |
| no match | 0 | **0.1 ms** | 13.2 ms |

Navegador's cost scales with the number of matches; grep's scales with the size
of the corpus. A search that finds nothing is nearly free, where grep pays a
full scan every time.

## `neighbourhood` — what surrounds this?

```bash
navegador explain validate_token
```

Callers, callees, tests and defining file in one call — what would otherwise
take several turns to assemble.

Call edges carry a `resolution` property:

- `inferred` — matched by import heuristics. tree-sitter is syntax only: it has
  no name resolution and no types, so `foo.bar()` cannot be proven to reach
  `Baz.bar`. Usually right, not guaranteed.
- `resolved` — reserved for compiler-accurate edges.

A uniformly confident graph cannot tell you which parts to trust. This one can.

## Over MCP

Agents get the same surface as tools: `locate`, `scope_for`, `neighbourhood`
and `grep_code`. All read-only.

`scope_for` accepts an optional `pattern`, so an agent can narrow and search in
a single call rather than two round trips.

## Requirements

Targeting needs content stored at ingest time, which is the default:

```bash
navegador ingest .            # stores content and prose
navegador ingest . --no-content   # opts out; grep and locate lose their corpus
```

Content is addressed by hash, so unchanged files and vendored copies cost
nothing to keep, and re-ingesting an unchanged repository stores zero blobs.

If a repository was ingested before content storage existed, re-ingest it —
`navegador grep` will say so when it finds no corpus.
