# Framework Enrichment

## What enrichment does

After ingestion, navegador's graph contains generic structural nodes: `Function`, `Class`, `File`, `Import`. Enrichment promotes those generic nodes to **semantic types** that reflect how the code is actually used.

For example, a Django view function becomes a `View` node. A pytest function becomes a `Test` node. A Flask route decorator triggers creation of a `Route` node with the URL pattern extracted.

This lets you ask questions that wouldn't be possible from structure alone:

```bash
# without enrichment: grep for "def test_"
# with enrichment: query the graph by semantic type
navegador query "MATCH (t:Test) RETURN t.name, t.file ORDER BY t.file"

# find all API routes
navegador query "MATCH (r:Route) RETURN r.method, r.path, r.handler ORDER BY r.path"
```

---

## How it works

Enrichment runs as a post-ingest pass. It reads existing nodes and edges, applies framework-specific pattern matching (decorator names, base class names, naming conventions), and:

1. Adds semantic labels to matched nodes (e.g., adds `View` label to Django view functions)
2. Creates typed edges where the framework implies relationships (e.g., `HANDLES` from a `Route` to its handler function)
3. Extracts framework-specific properties (e.g., HTTP method and URL pattern from route decorators)

Enrichment is **non-destructive** — it never removes or modifies existing nodes, only adds labels and edges.

---

## Supported frameworks

| Framework | Language | Detected patterns | Semantic types added |
|---|---|---|---|
| Django | Python | `View` subclasses, `urlpatterns`, `@login_required` | `View`, `Route`, `Model`, `Form`, `Middleware` |
| Flask | Python | `@app.route`, `@blueprint.route`, `MethodView` | `Route`, `View`, `Blueprint` |
| FastAPI | Python | `@router.get/post/put/delete/patch`, `APIRouter` | `Route`, `Schema`, `Dependency` |
| pytest | Python | `def test_*`, `@pytest.mark.*`, `conftest.py` | `Test`, `Fixture`, `TestSuite` |
| SQLAlchemy | Python | `Base` subclasses, `Column`, `relationship()` | `Model`, `Column`, `Relation` |
| Next.js | TypeScript | `pages/`, `app/`, `getServerSideProps` | `Page`, `Route`, `ServerComponent` |
| Express | JavaScript | `app.get/post/put/delete`, `Router` | `Route`, `Middleware` |
| NestJS | TypeScript | `@Controller`, `@Injectable`, `@Module` | `Controller`, `Service`, `Module` |
| **Terraform** | HCL | `main.tf`, `variables.tf`, `outputs.tf` | Cross-file module resolution, provider grouping |
| **Chef** | Ruby | `metadata.rb`, `Berksfile` | `chef_recipe`, `chef_resource`, `chef_cookbook`, `chef_include` |

!!! note
    Framework detection is automatic when `--framework auto` is used (the default). Navegador inspects imports and decorator patterns to identify which frameworks are present.

---

## Usage

### Auto-detect and enrich all frameworks

```bash
navegador enrich ./src
```

This runs after ingestion and enriches everything it can detect automatically.

### Enrich immediately after ingestion

```bash
navegador ingest ./src && navegador enrich ./src
```

Or use the `--enrich` flag on ingest:

```bash
navegador ingest ./src --enrich
```

### Target a specific framework

```bash
navegador enrich ./src --framework django
navegador enrich ./src --framework fastapi
navegador enrich ./src --framework pytest
```

Valid values: `django`, `flask`, `fastapi`, `pytest`, `sqlalchemy`, `nextjs`, `express`, `nestjs`, `terraform`, `chef`, `auto` (default).

### JSON output

```bash
navegador enrich ./src --json
```

Returns a summary of labels and edges added per framework.

---

## Querying enriched nodes

Once enriched, the semantic types are queryable via Cypher:

```bash
# all FastAPI routes with their HTTP methods
navegador query "MATCH (r:Route) RETURN r.method, r.path, r.handler ORDER BY r.path"

# all SQLAlchemy models and their columns
navegador query "MATCH (m:Model)-[:HAS_COLUMN]->(c:Column) RETURN m.name, c.name, c.type ORDER BY m.name"

# all pytest tests that reference a specific function
navegador query "MATCH (t:Test)-[:CALLS]->(f:Function {name: 'process_payment'}) RETURN t.name, t.file"

# all Django views governed by a rule
navegador query "MATCH (r:Rule)-[:GOVERNS]->(v:View) RETURN r.name, v.name, v.file"
```

---

## Adding custom enrichers

Enrichers are subclasses of `FrameworkEnricher`. Create one to add support for an internal framework or library.

### 1. Create the enricher

```python
# myproject/enrichers/celery.py
from navegador.enrichment.base import FrameworkEnricher, EnrichmentResult
from navegador.graph import GraphStore

class CeleryEnricher(FrameworkEnricher):
    name = "celery"

    def detect(self, store: GraphStore) -> bool:
        """Return True if this framework is present in the graph."""
        results = store.query(
            "MATCH (i:Import {name: 'celery'}) RETURN count(i) AS n"
        )
        return results[0]["n"] > 0

    def enrich(self, store: GraphStore) -> EnrichmentResult:
        """Add semantic labels and edges for Celery tasks."""
        # Find functions decorated with @shared_task or @app.task
        tasks = store.query(
            "MATCH (d:Decorator)-[:DECORATES]->(f:Function) "
            "WHERE d.name IN ['shared_task', 'task'] "
            "RETURN f.id, f.name"
        )
        labels_added = 0
        for row in tasks:
            store.query(
                "MATCH (f:Function) WHERE id(f) = $id "
                "SET f:Task",
                params={"id": row["f.id"]}
            )
            labels_added += 1

        return EnrichmentResult(labels_added=labels_added, edges_added=0)
```

### 2. Register the enricher

```python
# myproject/enrichers/__init__.py
from navegador.enrichment.registry import register_enricher
from .celery import CeleryEnricher

register_enricher(CeleryEnricher())
```

### 3. Load at startup

Import the registration module before running enrichment. In a CLI wrapper or agent hook:

```python
import myproject.enrichers  # registers the enricher
from navegador.enrichment import run_enrichment
from navegador.graph import GraphStore

store = GraphStore.sqlite(".navegador/navegador.db")
result = run_enrichment(store, framework="celery")
print(f"Added {result.labels_added} labels")
```

Or pass the module path to the CLI via `NAVEGADOR_ENRICHERS`:

```bash
NAVEGADOR_ENRICHERS=myproject.enrichers navegador enrich ./src --framework celery
```
