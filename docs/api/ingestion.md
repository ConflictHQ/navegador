# Ingestion API

All ingesters accept a `GraphStore` instance and return an `IngestionResult` dataclass.

```python
from navegador.graph import GraphStore
from navegador.ingest import RepoIngester, KnowledgeIngester, WikiIngester, PlanopticonIngester
```

---

## IngestionResult

```python
@dataclass
class IngestionResult:
    nodes_created: int
    nodes_updated: int
    edges_created: int
    files_processed: int
    errors: list[str]
    duration_seconds: float
```

---

## RepoIngester

Parses a source tree and writes code layer nodes and edges.

```python
class RepoIngester:
    def __init__(self, store: GraphStore) -> None: ...

    def ingest(
        self,
        path: str | Path,
        *,
        clear: bool = False,
        incremental: bool = False,
        redact: bool = False,
        monorepo: bool = False,
    ) -> IngestionResult: ...

    def ingest_file(
        self,
        path: str | Path,
        *,
        redact: bool = False,
    ) -> IngestionResult: ...
```

### Usage

```python
store = GraphStore.sqlite(".navegador/navegador.db")
ingester = RepoIngester(store)

# full repo ingest
result = ingester.ingest("./src")
print(f"{result.nodes_created} nodes, {result.edges_created} edges")

# incremental ingest — only reprocesses files whose content hash has changed
result = ingester.ingest("./src", incremental=True)

# incremental: single file
result = ingester.ingest_file("./src/auth/service.py")

# wipe + rebuild
result = ingester.ingest("./src", clear=True)

# redact sensitive content (strips tokens, passwords, keys from string literals)
result = ingester.ingest("./src", redact=True)

# monorepo — traverse workspace sub-packages
result = ingester.ingest("./monorepo", monorepo=True)
```

### Supported languages

| Language | File extensions | Parser | Extra required |
|---|---|---|---|
| Python | `.py` | tree-sitter-python | — (included) |
| TypeScript | `.ts`, `.tsx` | tree-sitter-typescript | — (included) |
| JavaScript | `.js`, `.jsx` | tree-sitter-javascript | — (included) |
| Go | `.go` | tree-sitter-go | — (included) |
| Rust | `.rs` | tree-sitter-rust | — (included) |
| Java | `.java` | tree-sitter-java | — (included) |
| Kotlin | `.kt`, `.kts` | tree-sitter-kotlin | `navegador[languages]` |
| C# | `.cs` | tree-sitter-c-sharp | `navegador[languages]` |
| PHP | `.php` | tree-sitter-php | `navegador[languages]` |
| Ruby | `.rb` | tree-sitter-ruby | `navegador[languages]` |
| Swift | `.swift` | tree-sitter-swift | `navegador[languages]` |
| C | `.c`, `.h` | tree-sitter-c | `navegador[languages]` |
| C++ | `.cpp`, `.cc`, `.cxx`, `.hpp` | tree-sitter-cpp | `navegador[languages]` |

### Adding a new language parser

1. Install the tree-sitter grammar: `pip install tree-sitter-<lang>`
2. Subclass `navegador.ingest.base.LanguageParser`:

```python
from navegador.ingest.base import LanguageParser, ParseResult

class RubyParser(LanguageParser):
    language = "ruby"
    extensions = [".rb"]

    def parse(self, source: str, file_path: str) -> ParseResult:
        # use self.tree_sitter_language to build the tree
        # return ParseResult with nodes and edges
        ...
```

3. Register in `navegador/ingest/registry.py`:

```python
from .ruby import RubyParser
PARSERS["ruby"] = RubyParser
```

`RepoIngester` dispatches to registered parsers by file extension.

### Framework enrichers

After parsing, `RepoIngester` runs framework-specific enrichers that annotate nodes with framework context. Enrichers are discovered automatically based on what frameworks are detected in the repo.

| Framework | What gets enriched |
|---|---|
| Django | Models, views, URL patterns, admin registrations |
| FastAPI | Route handlers, dependency injections, Pydantic schemas |
| React | Components, hooks, prop types |
| Express | Route handlers, middleware chains |
| React Native | Screens, navigators |
| Rails | Controllers, models, routes |
| Spring Boot | Beans, controllers, repositories |
| Laravel | Controllers, models, routes |

---

## KnowledgeIngester

Writes knowledge layer nodes. Wraps the `navegador add` commands programmatically.

```python
class KnowledgeIngester:
    def __init__(self, store: GraphStore) -> None: ...

    def add_concept(
        self,
        name: str,
        *,
        description: str = "",
        domain: str = "",
        status: str = "",
    ) -> str: ...  # returns node ID

    def add_rule(
        self,
        name: str,
        *,
        description: str = "",
        domain: str = "",
        severity: str = "info",
        rationale: str = "",
    ) -> str: ...

    def add_decision(
        self,
        name: str,
        *,
        description: str = "",
        domain: str = "",
        rationale: str = "",
        alternatives: str = "",
        date: str = "",
        status: str = "proposed",
    ) -> str: ...

    def add_person(
        self,
        name: str,
        *,
        email: str = "",
        role: str = "",
        team: str = "",
    ) -> str: ...

    def add_domain(
        self,
        name: str,
        *,
        description: str = "",
    ) -> str: ...

    def annotate(
        self,
        code_name: str,
        *,
        node_type: str = "Function",
        concept: str = "",
        rule: str = "",
    ) -> None: ...
```

### Usage

```python
store = GraphStore.sqlite(".navegador/navegador.db")
ingester = KnowledgeIngester(store)

ingester.add_domain("Payments", description="Payment processing and billing")
ingester.add_concept("Idempotency", domain="Payments",
    description="Operations safe to retry without side effects")
ingester.add_rule("RequireIdempotencyKey",
    domain="Payments", severity="critical",
    rationale="Card networks retry on timeout")
ingester.annotate("process_payment", node_type="Function",
    concept="Idempotency", rule="RequireIdempotencyKey")
```

---

## WikiIngester

Fetches GitHub wiki pages and writes `WikiPage` nodes.

```python
class WikiIngester:
    def __init__(self, store: GraphStore) -> None: ...

    def ingest_repo(
        self,
        repo: str,
        *,
        token: str = "",
        use_api: bool = False,
    ) -> IngestionResult: ...

    def ingest_dir(
        self,
        path: str | Path,
    ) -> IngestionResult: ...
```

### Usage

```python
import os
store = GraphStore.sqlite(".navegador/navegador.db")
ingester = WikiIngester(store)

# from GitHub API
result = ingester.ingest_repo("myorg/myrepo", token=os.environ["GITHUB_TOKEN"])

# from local clone
result = ingester.ingest_dir("./myrepo.wiki")
```

---

## PlanopticonIngester

Ingests Planopticon knowledge graph output into the knowledge layer.

```python
class PlanopticonIngester:
    def __init__(self, store: GraphStore) -> None: ...

    def ingest(
        self,
        path: str | Path,
        *,
        input_type: str = "auto",
        source: str = "",
    ) -> IngestionResult: ...

    def ingest_manifest(
        self,
        path: str | Path,
        *,
        source: str = "",
    ) -> IngestionResult: ...

    def ingest_kg(
        self,
        path: str | Path,
        *,
        source: str = "",
    ) -> IngestionResult: ...

    def ingest_interchange(
        self,
        path: str | Path,
        *,
        source: str = "",
    ) -> IngestionResult: ...

    def ingest_batch(
        self,
        path: str | Path,
        *,
        source: str = "",
    ) -> IngestionResult: ...
```

`input_type` values: `"auto"`, `"manifest"`, `"kg"`, `"interchange"`, `"batch"`.

See [Planopticon guide](../guide/planopticon.md) for format details and entity mapping.

---

## Export and import

Navegador can export the full graph (or a subset) to JSONL for backup, migration, or sharing. The JSONL format is one JSON object per line, where each object is either a node or an edge.

```bash
navegador export > graph.jsonl
navegador export --nodes-only > nodes.jsonl
navegador import graph.jsonl
```

Python API:

```python
from navegador.graph import GraphStore

store = GraphStore.sqlite(".navegador/navegador.db")

# export
with open("graph.jsonl", "w") as f:
    store.export_jsonl(f)

# import into a new store
new_store = GraphStore.sqlite(".navegador/new.db")
with open("graph.jsonl") as f:
    new_store.import_jsonl(f)
```

---

## Schema migrations

When upgrading navegador, run `navegador migrate` before re-ingesting to apply schema changes (new node properties, new edge types, index updates):

```bash
navegador migrate
```

Migrations are idempotent — safe to run multiple times. The migration state is stored in the graph itself under a `_MigrationState` node.

Python API:

```python
from navegador.graph import GraphStore, migrate

store = GraphStore.sqlite(".navegador/navegador.db")
migrate(store)   # applies any pending migrations
```
