# Analysis API Reference

```python
from navegador.analysis import (
    ImpactAnalyzer,
    FlowTracer,
    DeadCodeDetector,
    CycleDetector,
    TestMapper,
)
from navegador.graph import GraphStore
```

---

## ImpactAnalyzer

Traces downstream dependents of a function, class, or file by following `CALLS`, `INHERITS`, and `IMPORTS` edges.

```python
class ImpactAnalyzer:
    def __init__(self, store: GraphStore) -> None: ...
```

### `analyze`

```python
def analyze(
    self,
    name: str,
    *,
    node_type: str = "",
    file: str = "",
    depth: int = 0,
    include_tests: bool = False,
    include_knowledge: bool = False,
) -> ImpactResult
```

Compute the impact set for a given node.

**Parameters:**

| Name | Type | Default | Description |
|---|---|---|---|
| `name` | `str` | — | Name of the function, class, or file to analyze |
| `node_type` | `str` | `""` | Node type hint: `"Function"`, `"Class"`, `"File"` |
| `file` | `str` | `""` | Optional file path to disambiguate |
| `depth` | `int` | `0` | Maximum hops to follow (0 = unlimited) |
| `include_tests` | `bool` | `False` | Include test files in the impact set |
| `include_knowledge` | `bool` | `False` | Include linked knowledge nodes (rules, concepts, decisions) |

**Returns:** `ImpactResult`

---

### ImpactResult

```python
@dataclass
class ImpactResult:
    root: ContextNode
    direct_dependents: list[ContextNode]
    transitive_dependents: list[ContextNode]
    affected_files: list[str]
    knowledge_nodes: list[ContextNode]    # empty unless include_knowledge=True
    depth_reached: int
```

| Field | Type | Description |
|---|---|---|
| `root` | `ContextNode` | The analyzed node |
| `direct_dependents` | `list[ContextNode]` | Nodes one hop away |
| `transitive_dependents` | `list[ContextNode]` | All nodes reachable beyond one hop |
| `affected_files` | `list[str]` | Unique file paths in the full dependent set |
| `knowledge_nodes` | `list[ContextNode]` | Linked concepts, rules, decisions |
| `depth_reached` | `int` | Actual maximum depth traversed |

**Example:**

```python
store = GraphStore.sqlite(".navegador/navegador.db")
analyzer = ImpactAnalyzer(store)

result = analyzer.analyze("validate_token", depth=3, include_knowledge=True)
print(f"{len(result.direct_dependents)} direct dependents")
print(f"{len(result.transitive_dependents)} transitive dependents")
print(f"Affects {len(result.affected_files)} files")
for rule in [n for n in result.knowledge_nodes if n.label == "Rule"]:
    print(f"  Governed by: {rule.name} ({rule.properties.get('severity')})")
```

---

## FlowTracer

Finds call paths between two functions.

```python
class FlowTracer:
    def __init__(self, store: GraphStore) -> None: ...
```

### `trace`

```python
def trace(
    self,
    from_name: str,
    to_name: str,
    *,
    from_file: str = "",
    to_file: str = "",
    max_paths: int = 3,
    max_depth: int = 10,
) -> list[FlowPath]
```

Find call chains from `from_name` to `to_name`.

**Parameters:**

| Name | Type | Default | Description |
|---|---|---|---|
| `from_name` | `str` | — | Starting function name |
| `to_name` | `str` | — | Target function name |
| `from_file` | `str` | `""` | File path to disambiguate start |
| `to_file` | `str` | `""` | File path to disambiguate target |
| `max_paths` | `int` | `3` | Maximum number of paths to return |
| `max_depth` | `int` | `10` | Maximum call chain length |

**Returns:** `list[FlowPath]`

---

### FlowPath

```python
@dataclass
class FlowPath:
    nodes: list[str]        # function names in order
    node_details: list[ContextNode]
    length: int
```

**Example:**

```python
tracer = FlowTracer(store)
paths = tracer.trace("create_order", "process_payment", max_paths=5)
for i, path in enumerate(paths, 1):
    print(f"Path {i}: {' -> '.join(path.nodes)}")
```

---

## DeadCodeDetector

Identifies functions and classes that are never called, never imported, and not entry points.

```python
class DeadCodeDetector:
    def __init__(self, store: GraphStore) -> None: ...
```

### `find`

```python
def find(
    self,
    path: str | Path,
    *,
    exclude_tests: bool = False,
    min_confidence: int = 80,
) -> list[DeadCodeCandidate]
```

Find potentially dead code within a path.

**Parameters:**

| Name | Type | Default | Description |
|---|---|---|---|
| `path` | `str \| Path` | — | Directory or file to analyze |
| `exclude_tests` | `bool` | `False` | Skip test files |
| `min_confidence` | `int` | `80` | Minimum confidence score to include (0–100) |

**Returns:** `list[DeadCodeCandidate]`

---

### DeadCodeCandidate

```python
@dataclass
class DeadCodeCandidate:
    node: ContextNode
    confidence: int      # 0–100
    reasons: list[str]   # e.g. ["no callers", "no imports", "no decorator entry point"]
```

| Field | Type | Description |
|---|---|---|
| `node` | `ContextNode` | The potentially dead node |
| `confidence` | `int` | Confidence that this is truly unreachable (higher = more confident) |
| `reasons` | `list[str]` | Reasons for the classification |

**Example:**

```python
detector = DeadCodeDetector(store)
candidates = detector.find("./src", exclude_tests=True, min_confidence=90)
for c in candidates:
    print(f"[{c.confidence}%] {c.node.label}: {c.node.name}  {c.node.properties['file']}")
    print(f"  Reasons: {', '.join(c.reasons)}")
```

---

## CycleDetector

Finds circular dependency chains in call and import graphs.

```python
class CycleDetector:
    def __init__(self, store: GraphStore) -> None: ...
```

### `find_import_cycles`

```python
def find_import_cycles(
    self,
    path: str | Path,
    *,
    min_length: int = 2,
) -> list[Cycle]
```

Find circular import chains within a path.

---

### `find_call_cycles`

```python
def find_call_cycles(
    self,
    path: str | Path,
    *,
    min_length: int = 2,
) -> list[Cycle]
```

Find circular call chains within a path.

---

### `find_all`

```python
def find_all(
    self,
    path: str | Path,
    *,
    min_length: int = 2,
) -> CycleReport
```

Find both import and call cycles.

**Parameters (all methods):**

| Name | Type | Default | Description |
|---|---|---|---|
| `path` | `str \| Path` | — | Directory or file to analyze |
| `min_length` | `int` | `2` | Minimum cycle length to report |

**Returns:** `list[Cycle]` or `CycleReport`

---

### Cycle

```python
@dataclass
class Cycle:
    path: list[str]       # node names (files or functions) forming the cycle
    cycle_type: str       # "import" or "call"
    length: int
```

### CycleReport

```python
@dataclass
class CycleReport:
    import_cycles: list[Cycle]
    call_cycles: list[Cycle]
    total: int
```

**Example:**

```python
detector = CycleDetector(store)
report = detector.find_all("./src")
print(f"{report.total} cycles found")
for cycle in report.import_cycles:
    print(f"  Import cycle: {' -> '.join(cycle.path)}")
```

---

## TestMapper

Maps test functions to the production code they exercise via call graph analysis.

```python
class TestMapper:
    def __init__(self, store: GraphStore) -> None: ...
```

### `map`

```python
def map(
    self,
    src_path: str | Path,
    test_path: str | Path,
    *,
    target: str = "",
) -> TestCoverageMap
```

Build a mapping of production functions to their covering tests.

**Parameters:**

| Name | Type | Default | Description |
|---|---|---|---|
| `src_path` | `str \| Path` | — | Production code directory |
| `test_path` | `str \| Path` | — | Test directory |
| `target` | `str` | `""` | If set, only map coverage for this specific function |

**Returns:** `TestCoverageMap`

---

### `uncovered`

```python
def uncovered(
    self,
    src_path: str | Path,
    test_path: str | Path,
) -> list[ContextNode]
```

Return production functions and classes with no covering tests.

---

### TestCoverageMap

```python
@dataclass
class TestCoverageMap:
    coverage: dict[str, list[ContextNode]]   # function name -> list of test nodes
    uncovered: list[ContextNode]
    coverage_percent: float
```

| Field | Type | Description |
|---|---|---|
| `coverage` | `dict[str, list[ContextNode]]` | Maps each production function to its test nodes |
| `uncovered` | `list[ContextNode]` | Production functions with no tests |
| `coverage_percent` | `float` | Percentage of functions with at least one test |

**Example:**

```python
mapper = TestMapper(store)
coverage_map = mapper.map("./src", "./tests")

print(f"Coverage: {coverage_map.coverage_percent:.1f}%")
print(f"Uncovered: {len(coverage_map.uncovered)} functions")

for fn_name, tests in coverage_map.coverage.items():
    print(f"  {fn_name}: {len(tests)} tests")
    for test in tests:
        print(f"    {test.properties['file']}::{test.name}")
```
