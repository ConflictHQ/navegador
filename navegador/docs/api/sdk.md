# Python SDK Reference

Full API reference for the navegador Python SDK.

```python
from navegador.graph import GraphStore
from navegador.context import ContextLoader, ContextBundle, ContextNode, ContextEdge
from navegador.ingest import RepoIngester, KnowledgeIngester, WikiIngester, PlanopticonIngester
```

---

## GraphStore

Database abstraction layer. Both SQLite and Redis backends implement this interface.

```python
class GraphStore:
    @classmethod
    def sqlite(cls, path: str | Path = "navegador.db") -> "GraphStore": ...

    @classmethod
    def redis(cls, url: str = "redis://localhost:6379") -> "GraphStore": ...
```

### Class methods

#### `GraphStore.sqlite`

```python
@classmethod
def sqlite(cls, path: str | Path = "navegador.db") -> "GraphStore"
```

Open a local SQLite-backed graph. The file is created if it does not exist. Uses `falkordblite` (embedded engine — no daemon required).

**Parameters:**

| Name | Type | Default | Description |
|---|---|---|---|
| `path` | `str \| Path` | `"navegador.db"` | Path to the `.db` file |

**Returns:** `GraphStore`

#### `GraphStore.redis`

```python
@classmethod
def redis(cls, url: str = "redis://localhost:6379") -> "GraphStore"
```

Connect to a Redis-backed FalkorDB instance.

**Parameters:**

| Name | Type | Default | Description |
|---|---|---|---|
| `url` | `str` | `"redis://localhost:6379"` | Redis connection URL |

**Returns:** `GraphStore`

---

### Instance methods

#### `query`

```python
def query(self, cypher: str, params: dict | None = None) -> list[dict]
```

Execute a Cypher query and return all result rows.

**Parameters:**

| Name | Type | Default | Description |
|---|---|---|---|
| `cypher` | `str` | — | Cypher query string |
| `params` | `dict \| None` | `None` | Optional query parameters |

**Returns:** `list[dict]` — one dict per result row, keyed by return variable name.

---

#### `create_node`

```python
def create_node(self, label: str, properties: dict) -> str
```

Create a new node and return its ID.

**Parameters:**

| Name | Type | Description |
|---|---|---|
| `label` | `str` | Node label (e.g., `"Function"`, `"Concept"`) |
| `properties` | `dict` | Node properties |

**Returns:** `str` — node ID

---

#### `merge_node`

```python
def merge_node(
    self,
    label: str,
    match_properties: dict,
    set_properties: dict | None = None,
) -> str
```

Upsert a node: create it if no node with `match_properties` exists; otherwise update `set_properties` on the existing node.

**Parameters:**

| Name | Type | Default | Description |
|---|---|---|---|
| `label` | `str` | — | Node label |
| `match_properties` | `dict` | — | Properties to match on (identity key) |
| `set_properties` | `dict \| None` | `None` | Properties to set on create or update |

**Returns:** `str` — node ID

---

#### `create_edge`

```python
def create_edge(
    self,
    from_id: str,
    to_id: str,
    edge_type: str,
    properties: dict | None = None,
) -> None
```

Create a directed edge between two nodes.

**Parameters:**

| Name | Type | Default | Description |
|---|---|---|---|
| `from_id` | `str` | — | Source node ID |
| `to_id` | `str` | — | Target node ID |
| `edge_type` | `str` | — | Relationship type (e.g., `"CALLS"`, `"ANNOTATES"`) |
| `properties` | `dict \| None` | `None` | Optional edge properties |

---

#### `merge_edge`

```python
def merge_edge(
    self,
    from_label: str,
    from_match: dict,
    to_label: str,
    to_match: dict,
    edge_type: str,
    properties: dict | None = None,
) -> None
```

Upsert an edge between two nodes matched by label and properties.

---

#### `clear`

```python
def clear(self) -> None
```

Delete all nodes and edges from the graph.

---

#### `close`

```python
def close(self) -> None
```

Release the database connection. Called automatically when used as a context manager.

---

#### Context manager

`GraphStore` implements `__enter__` / `__exit__`. Use with `with` to ensure the connection is closed:

```python
with GraphStore.sqlite(".navegador/navegador.db") as store:
    results = store.query("MATCH (n) RETURN count(n) AS total")
```

---

## ContextLoader

Builds structured context bundles from graph queries.

```python
class ContextLoader:
    def __init__(self, store: GraphStore) -> None: ...
```

### Methods

#### `load_file`

```python
def load_file(self, path: str) -> ContextBundle
```

Return the full context bundle for a source file: the file node, its modules, classes, functions, imports, and their relationships.

**Parameters:**

| Name | Type | Description |
|---|---|---|
| `path` | `str` | Relative or absolute path to the source file |

**Returns:** `ContextBundle`

---

#### `load_function`

```python
def load_function(
    self,
    name: str,
    *,
    file: str = "",
    depth: int = 1,
) -> ContextBundle
```

Return a function node with its callers, callees, decorators, containing class, and source.

**Parameters:**

| Name | Type | Default | Description |
|---|---|---|---|
| `name` | `str` | — | Function name |
| `file` | `str` | `""` | Optional file path to disambiguate |
| `depth` | `int` | `1` | Call graph traversal depth (1 = direct callers/callees only) |

**Returns:** `ContextBundle`

---

#### `load_class`

```python
def load_class(
    self,
    name: str,
    *,
    file: str = "",
) -> ContextBundle
```

Return a class node with its methods, base classes, subclasses, and references from other files.

**Parameters:**

| Name | Type | Default | Description |
|---|---|---|---|
| `name` | `str` | — | Class name |
| `file` | `str` | `""` | Optional file path to disambiguate |

**Returns:** `ContextBundle`

---

#### `explain`

```python
def explain(
    self,
    name: str,
    *,
    file: str = "",
) -> ContextBundle
```

Universal lookup: explain any node (function, class, file, concept, rule, decision) by name.

**Parameters:**

| Name | Type | Default | Description |
|---|---|---|---|
| `name` | `str` | — | Node name or file path |
| `file` | `str` | `""` | Optional file path to disambiguate code nodes |

**Returns:** `ContextBundle`

---

#### `load_concept`

```python
def load_concept(self, name: str) -> ContextBundle
```

Return a concept node with its rules, linked wiki pages, and annotated code nodes.

---

#### `load_domain`

```python
def load_domain(self, name: str) -> ContextBundle
```

Return a domain and all nodes belonging to it: concepts, rules, decisions, people, and annotated code.

---

#### `search`

```python
def search(
    self,
    query: str,
    *,
    all_layers: bool = False,
    docs_only: bool = False,
    limit: int = 20,
) -> list[ContextNode]
```

Search the graph by text query.

**Parameters:**

| Name | Type | Default | Description |
|---|---|---|---|
| `query` | `str` | — | Search string |
| `all_layers` | `bool` | `False` | Search all layers including knowledge and docs |
| `docs_only` | `bool` | `False` | Search docstrings and wiki content only |
| `limit` | `int` | `20` | Maximum number of results |

**Returns:** `list[ContextNode]`

---

#### `search_by_docstring`

```python
def search_by_docstring(self, query: str, *, limit: int = 20) -> list[ContextNode]
```

Search docstrings and wiki page content. Equivalent to `search(query, docs_only=True)`.

---

#### `decorated_by`

```python
def decorated_by(self, decorator: str) -> list[ContextNode]
```

Find all functions and classes that use a specific decorator.

**Parameters:**

| Name | Type | Description |
|---|---|---|
| `decorator` | `str` | Decorator name (e.g., `"login_required"`, `"pytest.mark.parametrize"`) |

**Returns:** `list[ContextNode]`

---

## ContextBundle

Structured result returned by `ContextLoader` methods.

```python
@dataclass
class ContextBundle:
    root: ContextNode
    nodes: list[ContextNode]
    edges: list[ContextEdge]
    metadata: dict
```

### Fields

| Field | Type | Description |
|---|---|---|
| `root` | `ContextNode` | The primary node (function, class, file, etc.) |
| `nodes` | `list[ContextNode]` | All nodes in the bundle, including `root` |
| `edges` | `list[ContextEdge]` | All edges between nodes in the bundle |
| `metadata` | `dict` | Query metadata (depth, timing, node counts) |

### Methods

#### `to_json`

```python
def to_json(self) -> str
```

Serialize the bundle to a JSON string.

#### `to_markdown`

```python
def to_markdown(self) -> str
```

Render the bundle as a Markdown string. Suitable for pasting into agent context.

#### `to_dict`

```python
def to_dict(self) -> dict
```

Return the bundle as a plain Python dict.

---

## ContextNode

A single node in a context bundle.

```python
@dataclass
class ContextNode:
    id: str
    label: str        # e.g. "Function", "Concept"
    name: str
    properties: dict  # all node properties from the graph
    layer: str        # "code" or "knowledge"
    score: float      # relevance score (search results only)
```

---

## ContextEdge

A relationship between two nodes in a context bundle.

```python
@dataclass
class ContextEdge:
    from_id: str
    to_id: str
    edge_type: str   # e.g. "CALLS", "ANNOTATES", "INHERITS"
    properties: dict
```

---

## IngestionResult

Returned by all ingest methods.

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

| Field | Type | Description |
|---|---|---|
| `nodes_created` | `int` | New nodes written to the graph |
| `nodes_updated` | `int` | Existing nodes updated |
| `edges_created` | `int` | New edges written |
| `files_processed` | `int` | Source files walked |
| `errors` | `list[str]` | Per-file parse errors (non-fatal) |
| `duration_seconds` | `float` | Wall time for the ingest operation |
