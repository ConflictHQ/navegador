# Python SDK

The navegador Python SDK lets you drive ingestion, query the graph, and load context from your own scripts and tools — without going through the CLI.

---

## Installation

```bash
pip install navegador
```

For Redis (production/team) support:

```bash
pip install "navegador[redis]"
```

---

## Connecting to the graph

=== "SQLite (local)"

    ```python
    from navegador.graph import GraphStore

    store = GraphStore.sqlite(".navegador/navegador.db")
    ```

=== "Redis (production)"

    ```python
    from navegador.graph import GraphStore

    store = GraphStore.redis("redis://localhost:6379")
    ```

Both backends implement the same interface. All examples below work with either.

Use the context manager to ensure the connection is closed:

```python
with GraphStore.sqlite(".navegador/navegador.db") as store:
    results = store.query("MATCH (n) RETURN count(n) AS total")
    print(results[0]["total"])
```

---

## Ingestion

### Ingest a repo

```python
from navegador.graph import GraphStore
from navegador.ingest import RepoIngester

store = GraphStore.sqlite(".navegador/navegador.db")
ingester = RepoIngester(store)

result = ingester.ingest("./src")
print(f"{result.nodes_created} nodes, {result.edges_created} edges in {result.duration_seconds:.2f}s")
```

### Incremental ingest (single file)

```python
result = ingester.ingest_file("./src/auth/service.py")
```

### Wipe and rebuild

```python
result = ingester.ingest("./src", clear=True)
```

### Add knowledge programmatically

```python
from navegador.ingest import KnowledgeIngester

ki = KnowledgeIngester(store)

ki.add_domain("Payments", description="Payment processing and billing")
ki.add_concept("Idempotency", domain="Payments",
    description="Operations safe to retry without side effects")
ki.add_rule("RequireIdempotencyKey",
    domain="Payments", severity="critical",
    rationale="Card networks retry on timeout")
ki.annotate("process_payment", node_type="Function",
    concept="Idempotency", rule="RequireIdempotencyKey")
```

---

## Loading context

`ContextLoader` builds structured context bundles from the graph. Each method corresponds to a CLI command.

```python
from navegador.graph import GraphStore
from navegador.context import ContextLoader

store = GraphStore.sqlite(".navegador/navegador.db")
loader = ContextLoader(store)
```

### File context

```python
bundle = loader.load_file("src/auth/service.py")
print(bundle.to_markdown())
```

### Function with call graph

```python
# depth controls how many hops of callers/callees to include
bundle = loader.load_function("validate_token", depth=2)
print(bundle.to_json())
```

### Class hierarchy

```python
bundle = loader.load_class("PaymentProcessor", file="src/payments/processor.py")
data = bundle.to_dict()
```

### Universal explain

```python
# works for any node type: function, class, file, concept, rule, decision
bundle = loader.explain("AuthService")
bundle = loader.explain("PaymentsMustBeIdempotent")
```

### Concept and domain

```python
bundle = loader.load_concept("Idempotency")
bundle = loader.load_domain("Payments")
```

---

## Search

```python
# search function and class names (default)
nodes = loader.search("rate limit")

# search all layers including knowledge and docs
nodes = loader.search("rate limit", all_layers=True, limit=50)

# search docstrings and wiki content only
nodes = loader.search_by_docstring("retry logic")

# find all functions using a specific decorator
nodes = loader.decorated_by("login_required")

for node in nodes:
    print(f"{node.label}: {node.name}  ({node.properties.get('file', '')})")
```

---

## Knowledge queries

```python
# everything in the Payments domain
bundle = loader.load_domain("Payments")
for node in bundle.nodes:
    print(f"  [{node.label}] {node.name}")

# all code annotated with a concept
bundle = loader.load_concept("Idempotency")
for node in bundle.nodes:
    if node.layer == "code":
        print(f"  {node.name}  {node.properties.get('file', '')}")
```

---

## Exporting output

Every `ContextBundle` supports three output formats:

```python
bundle = loader.load_function("process_payment")

# JSON string — for agents, APIs, CI
json_str = bundle.to_json()

# Markdown — readable by humans and LLMs
md_str = bundle.to_markdown()

# Python dict — for further processing
data = bundle.to_dict()
print(data["root"]["name"])
print(len(data["nodes"]))
```

---

## Raw Cypher queries

Drop to raw Cypher for anything the built-in methods don't cover:

```python
results = store.query(
    "MATCH (f:Function)-[:CALLS]->(g:Function) "
    "WHERE f.file = $file "
    "RETURN f.name, g.name",
    params={"file": "src/payments/processor.py"}
)
for row in results:
    print(f"{row['f.name']} -> {row['g.name']}")
```

!!! warning
    `store.query()` executes writes as well as reads. Stick to `MATCH` / `RETURN` for inspection queries.

---

## Wiki ingestion

```python
import os
from navegador.ingest import WikiIngester

ingester = WikiIngester(store)

# from GitHub API
result = ingester.ingest_repo("myorg/myrepo", token=os.environ["GITHUB_TOKEN"])

# from a locally cloned wiki directory
result = ingester.ingest_dir("./myrepo.wiki")
```

---

## Error handling

All ingesters return an `IngestionResult` dataclass. Check `errors` for per-file failures without crashing the whole run:

```python
result = ingester.ingest("./src")
if result.errors:
    for err in result.errors:
        print(f"Warning: {err}")
print(f"Processed {result.files_processed} files, {result.nodes_created} nodes created")
```
