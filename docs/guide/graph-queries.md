# Graph Queries

## Raw Cypher passthrough

Every high-level command is built on Cypher queries against FalkorDB. You can drop to raw Cypher for anything the built-in commands don't cover:

```bash
navegador query "MATCH (f:Function) RETURN f.name, f.file LIMIT 10"
```

Results are printed as a table to stdout. Pipe with `--format json` if you need machine-readable output:

```bash
navegador query "MATCH (f:Function) RETURN f.name, f.file" --format json
```

!!! warning
    `navegador query` executes writes as well as reads. Use `MATCH` / `RETURN` for inspection. Use `CREATE` / `MERGE` / `DELETE` only if you know what you're doing — there is no undo.

---

## Useful example queries

### Find all functions decorated with `@login_required`

```cypher
MATCH (d:Decorator {name: "login_required"})-[:DECORATES]->(f:Function)
RETURN f.name, f.file, f.line
ORDER BY f.file
```

### Find all functions in a specific file

```cypher
MATCH (file:File {path: "src/auth/service.py"})-[:CONTAINS]->(f:Function)
RETURN f.name, f.line, f.signature
```

### Find everything a function calls (two hops)

```cypher
MATCH (f:Function {name: "process_payment"})-[:CALLS*1..2]->(callee:Function)
RETURN DISTINCT callee.name, callee.file
```

### Find all callers of a function

```cypher
MATCH (caller:Function)-[:CALLS]->(f:Function {name: "validate_token"})
RETURN caller.name, caller.file
```

### Find all rules in a domain

```cypher
MATCH (d:Domain {name: "Payments"})<-[:BELONGS_TO]-(r:Rule)
RETURN r.name, r.severity, r.description
ORDER BY r.severity
```

### Find all concepts implemented by code in a file

```cypher
MATCH (file:File {path: "src/payments/processor.py"})-[:CONTAINS]->(f)
      -[:ANNOTATES]-(c:Concept)
RETURN DISTINCT c.name, c.description
```

### Find all decisions that relate to a domain

```cypher
MATCH (d:Domain {name: "Infrastructure"})<-[:BELONGS_TO]-(dec:Decision)
RETURN dec.name, dec.status, dec.date, dec.rationale
ORDER BY dec.date DESC
```

### Find classes that inherit from a base class

```cypher
MATCH (child:Class)-[:INHERITS]->(parent:Class {name: "BaseProcessor"})
RETURN child.name, child.file
```

### Find the full inheritance chain for a class

```cypher
MATCH path = (c:Class {name: "StripeProcessor"})-[:INHERITS*]->(ancestor)
RETURN [node IN nodes(path) | node.name] AS hierarchy
```

### Find wiki pages that document a concept

```cypher
MATCH (wp:WikiPage)-[:DOCUMENTS]->(c:Concept {name: "Idempotency"})
RETURN wp.title, wp.url
```

### Find all functions annotated with a specific rule

```cypher
MATCH (r:Rule {name: "RequireIdempotencyKey"})-[:GOVERNS]->(f:Function)
RETURN f.name, f.file, f.line
```

### Find what a person is assigned to

```cypher
MATCH (p:Person {name: "Alice Chen"})<-[:ASSIGNED_TO]-(item)
RETURN labels(item)[0] AS type, item.name
```

---

## navegador stats

Get a high-level count of everything in the graph:

```bash
navegador stats
navegador stats --json
```

Output breakdown:

| Metric | What it counts |
|---|---|
| Repositories | `Repository` nodes |
| Files | `File` nodes |
| Classes | `Class` nodes |
| Functions + Methods | `Function` + `Method` nodes combined |
| Decorators | `Decorator` nodes |
| Imports | `Import` nodes |
| Domains | `Domain` nodes |
| Concepts | `Concept` nodes |
| Rules | `Rule` nodes |
| Decisions | `Decision` nodes |
| People | `Person` nodes |
| WikiPages | `WikiPage` nodes |
| Total edges | All relationship edges |

Use `--json` to feed stats into CI dashboards or coverage checks.
