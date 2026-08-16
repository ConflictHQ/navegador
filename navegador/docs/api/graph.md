# Graph API

```python
from navegador.graph import GraphStore
from navegador.context import ContextLoader, ContextBundle, ContextNode
```

---

## GraphStore

The database abstraction layer. Both SQLite and Redis backends implement this interface.

```python
class GraphStore:
    @classmethod
    def sqlite(cls, path: str | Path = "navegador.db") -> "GraphStore": ...

    @classmethod
    def redis(cls, url: str = "redis://localhost:6379") -> "GraphStore": ...

    def query(
        self,
        cypher: str,
        params: dict | None = None,
    ) -> list[dict]: ...

    def create_node(
        self,
        label: str,
        properties: dict,
    ) -> str: ...  # returns node ID

    def merge_node(
        self,
        label: str,
        match_properties: dict,
        set_properties: dict | None = None,
    ) -> str: ...  # upsert by match_properties, returns node ID

    def create_edge(
        self,
        from_id: str,
        to_id: str,
        edge_type: str,
        properties: dict | None = None,
    ) -> None: ...

    def merge_edge(
        self,
        from_label: str,
        from_match: dict,
        to_label: str,
        to_match: dict,
        edge_type: str,
        properties: dict | None = None,
    ) -> None: ...

    def clear(self) -> None: ...

    def close(self) -> None: ...

    def export_jsonl(self, fp: IO[str]) -> None: ...
    """Write all nodes and edges to a JSONL stream."""

    def import_jsonl(self, fp: IO[str]) -> None: ...
    """Read nodes and edges from a JSONL stream and merge into the graph."""
```

### Usage

```python
# SQLite (local dev)
store = GraphStore.sqlite(".navegador/navegador.db")

# Redis (production)
store = GraphStore.redis("redis://localhost:6379")

# raw Cypher query
results = store.query(
    "MATCH (f:Function {name: $name}) RETURN f",
    params={"name": "validate_token"}
)

# create a node
node_id = store.create_node("Concept", {
    "name": "Idempotency",
    "description": "Operations safe to retry"
})

# upsert a node (match by name, update description)
node_id = store.merge_node(
    "Concept",
    match_properties={"name": "Idempotency"},
    set_properties={"description": "Updated description"}
)

# create an edge
store.create_edge(from_id, to_id, "ANNOTATES")

# wipe the graph
store.clear()
```

### Context manager

`GraphStore` implements the context manager protocol:

```python
with GraphStore.sqlite(".navegador/navegador.db") as store:
    results = store.query("MATCH (n) RETURN count(n) AS total")
```

---

## ContextLoader

Builds structured context bundles from graph queries. Each method corresponds to a CLI command.

```python
class ContextLoader:
    def __init__(self, store: GraphStore) -> None: ...

    def load_file(self, path: str) -> ContextBundle: ...

    def load_function(
        self,
        name: str,
        *,
        file: str = "",
        depth: int = 1,
    ) -> ContextBundle: ...

    def load_class(
        self,
        name: str,
        *,
        file: str = "",
    ) -> ContextBundle: ...

    def explain(
        self,
        name: str,
        *,
        file: str = "",
    ) -> ContextBundle: ...

    def load_concept(self, name: str) -> ContextBundle: ...

    def load_domain(self, name: str) -> ContextBundle: ...

    def search(
        self,
        query: str,
        *,
        all_layers: bool = False,
        docs_only: bool = False,
        limit: int = 20,
    ) -> list[ContextNode]: ...

    def search_by_docstring(
        self,
        query: str,
        *,
        limit: int = 20,
    ) -> list[ContextNode]: ...

    def decorated_by(
        self,
        decorator: str,
    ) -> list[ContextNode]: ...
```

### Usage

```python
store = GraphStore.sqlite(".navegador/navegador.db")
loader = ContextLoader(store)

# file context
bundle = loader.load_file("src/auth/service.py")

# function with 2-hop call graph
bundle = loader.load_function("validate_token", depth=2)

# class hierarchy
bundle = loader.load_class("PaymentProcessor", file="src/payments/processor.py")

# universal explain
bundle = loader.explain("AuthService")

# concept with annotated code
bundle = loader.load_concept("Idempotency")

# domain overview
bundle = loader.load_domain("Payments")

# search
nodes = loader.search("rate limit", all_layers=True, limit=10)

# all @login_required functions
nodes = loader.decorated_by("login_required")
```

---

## ContextBundle

The structured result type returned by `ContextLoader` methods.

```python
@dataclass
class ContextBundle:
    root: ContextNode
    nodes: list[ContextNode]
    edges: list[ContextEdge]
    metadata: dict

    def to_json(self) -> str: ...
    def to_markdown(self) -> str: ...
    def to_dict(self) -> dict: ...
```

---

## ContextNode

A single node in a context bundle.

```python
@dataclass
class ContextNode:
    id: str
    label: str           # e.g. "Function", "Concept"
    name: str
    properties: dict     # all node properties
    layer: str           # "code" or "knowledge"
```

---

## ContextEdge

```python
@dataclass
class ContextEdge:
    from_id: str
    to_id: str
    edge_type: str       # e.g. "CALLS", "ANNOTATES"
    properties: dict
```

---

## Schema migrations

```python
from navegador.graph import migrate

store = GraphStore.sqlite(".navegador/navegador.db")
migrate(store)   # applies any pending schema migrations; idempotent
```

The `migrate()` function is safe to call on every startup. It compares the stored migration version against the current schema and applies only missing migrations.

---

## Formatting output

```python
bundle = loader.load_function("validate_token")

# JSON string
print(bundle.to_json())

# Markdown string (for agent consumption)
print(bundle.to_markdown())

# Python dict (for further processing)
data = bundle.to_dict()
```
