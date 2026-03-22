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
    ) -> IngestionResult: ...

    def ingest_file(
        self,
        path: str | Path,
    ) -> IngestionResult: ...
```

### Usage

```python
store = GraphStore.sqlite(".navegador/navegador.db")
ingester = RepoIngester(store)

# full repo ingest
result = ingester.ingest("./src")
print(f"{result.nodes_created} nodes, {result.edges_created} edges")

# incremental: single file
result = ingester.ingest_file("./src/auth/service.py")

# wipe + rebuild
result = ingester.ingest("./src", clear=True)
```

### Supported languages

| Language | File extensions | Parser |
|---|---|---|
| Python | `.py` | tree-sitter-python |
| TypeScript | `.ts`, `.tsx` | tree-sitter-typescript |
| JavaScript | `.js`, `.jsx` | tree-sitter-javascript |

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
