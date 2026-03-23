# Loading Context

These commands retrieve structured context from the graph. All commands support `--format json` for machine-readable output (useful in agent tool definitions) and default to rich terminal output.

---

## explain — universal lookup

`explain` is the single command for "what is this thing?" It works for any node type: functions, classes, files, concepts, rules, decisions, and domains.

```bash
navegador explain AuthService
navegador explain validate_token
navegador explain src/auth/service.py
navegador explain PaymentsMustBeIdempotent
```

Output includes:
- Node type, name, and properties
- Source location and docstring (for code nodes)
- Related knowledge (concepts, rules, decisions) via ANNOTATES edges
- Related code (for knowledge nodes) that implements or is governed by the node

```bash
navegador explain AuthService --format json
navegador explain AuthService --file src/auth/service.py  # disambiguate by file
```

---

## context — file contents

Returns everything navegador knows about a file: the file node, its modules, classes, functions, imports, and their relationships.

```bash
navegador context src/auth/service.py
navegador context src/auth/service.py --format json
navegador context src/auth/service.py --format markdown
```

Useful as a pre-edit context load: give the agent the full graph context for a file before it starts editing.

---

## function — call graph view

Returns a function node with its callers, callees, decorators, containing class, and source.

```bash
navegador function validate_token
navegador function validate_token --file src/auth/service.py
navegador function validate_token --depth 2
navegador function validate_token --format json
```

`--depth` controls how many hops of the call graph to traverse (default: 1). At depth 2, you get callers-of-callers and callees-of-callees.

---

## class — hierarchy and references

Returns a class node with its methods, base classes, subclasses, and references from other files.

```bash
navegador class PaymentProcessor
navegador class PaymentProcessor --file src/payments/processor.py
navegador class PaymentProcessor --format json
```

Output includes:
- Class properties (file, line, docstring)
- Methods with signatures
- INHERITS chain (parents and children)
- IMPLEMENTS edges (for abstract base classes / interfaces)
- Files that import or reference this class

---

## concept — knowledge + implementing code

Returns a concept node with its rules, linked wiki pages, and annotated code nodes.

```bash
navegador concept Idempotency
navegador concept Idempotency --format json
```

Output includes:
- Concept description and domain
- Rules in the same domain that reference this concept
- WikiPage nodes linked via DOCUMENTS
- All code nodes (functions, classes, files) annotated with this concept via ANNOTATES edges

---

## domain — everything in a domain

Returns a domain and all nodes belonging to it: concepts, rules, decisions, people, and code annotated via those knowledge nodes.

```bash
navegador domain Payments
navegador domain Payments --format json
```

Useful for onboarding: a new contributor can run `navegador domain Payments` to get the full business context before reading any code.

---

## search — text search across the graph

```bash
navegador search "rate limit"
```

By default, searches function and class names. Flags expand the scope:

| Flag | What it searches |
|---|---|
| (default) | Function, class, method names |
| `--all` | Names + docstrings + knowledge layer (concepts, rules, decisions, wiki) |
| `--docs` | Docstrings and wiki page content only |
| `--limit N` | Max results (default: 20) |
| `--format json` | JSON output |

Examples:

```bash
# find anything about rate limiting, anywhere
navegador search "rate limit" --all

# find code with docstrings mentioning retry logic
navegador search "retry" --docs

# search with a higher limit
navegador search "auth" --all --limit 50 --format json
```

---

## decorated — find by decorator

Find all functions and classes that use a specific decorator:

```bash
navegador decorated login_required
navegador decorated pytest.mark.parametrize
navegador decorated --format json login_required
```

Returns function/class nodes with their file paths, line numbers, and the full decorator expression.

---

## impact — blast radius analysis

Return the set of code nodes that could be affected if a given node changes, traversing CALLS, IMPORTS, and INHERITS edges transitively.

```bash
navegador impact validate_token
navegador impact validate_token --depth 3
navegador impact validate_token --format json
```

Useful before a refactor to understand the blast radius.

---

## trace — execution flow

Trace the execution path through the call graph from a starting function:

```bash
navegador trace process_payment
navegador trace process_payment --depth 4 --format json
```

Output shows the call chain as a tree, with each node annotated by file and line.

---

## diff — graph diff between refs

Show what changed in the graph between two Git refs:

```bash
navegador diff HEAD~1 HEAD
navegador diff main feature-branch
```

Reports added, removed, and changed nodes and edges.

---

## churn — code churn analysis

Identify files and functions that change most frequently, based on Git history:

```bash
navegador churn
navegador churn --days 30
navegador churn --format json
```

High-churn nodes are often candidates for stabilization or better test coverage.

---

## deadcode — find unreachable code

Find functions and classes with no callers and no references from outside their defining file:

```bash
navegador deadcode
navegador deadcode --format json
```

---

## cycles — dependency cycle detection

Detect cycles in the IMPORTS and CALLS graphs:

```bash
navegador cycles
navegador cycles --format json
```

Reports each cycle as an ordered list of node names.

---

## testmap — test-to-source mapping

Map test functions to the source functions they exercise (based on naming conventions and import analysis):

```bash
navegador testmap
navegador testmap src/auth/service.py
navegador testmap --format json
```

Creates `TESTS` edges between test functions and their targets.

---

## semantic-search — vector similarity search

Search using natural language against embeddings of docstrings and code. Requires `pip install "navegador[llm]"`.

```bash
navegador semantic-search "functions that validate user input"
navegador semantic-search "payment retry logic" --limit 10
```

---

## ask — NLP query interface

Ask a natural language question about the codebase. Requires `pip install "navegador[llm]"`.

```bash
navegador ask "What handles authentication in this codebase?"
navegador ask "Which functions touch the database?"
```

The answer is grounded in graph queries — not hallucinated from code text.

---

## rename — coordinated rename

Rename a function or class across the graph and get a list of all files that reference the old name:

```bash
navegador rename validate_token validate_access_token
```

Output is a structured change plan. The command does not modify source files — it produces the list of locations to update.

---

## codeowners — ownership queries

Query CODEOWNERS assignments and domain ownership:

```bash
navegador codeowners src/auth/service.py
navegador codeowners AuthService
```

Returns owning teams and people from CODEOWNERS file and from `Person` nodes annotated to the matching code nodes.

---

## communities — module cluster detection

Detect communities of highly-coupled modules using graph clustering:

```bash
navegador communities
navegador communities --format json
```

---

## explore — interactive graph explorer

Open an interactive graph explorer in the terminal:

```bash
navegador explore
navegador explore --start AuthService
```
