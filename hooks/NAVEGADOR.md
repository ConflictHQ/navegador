# Navegador — Project Knowledge Graph

This project uses **navegador** as its system of record for both code structure
and business knowledge. The graph lives at `.navegador/graph.db`.

## What's in the graph

- **Code layer** — all functions, classes, files, call graphs, decorators, imports
- **Knowledge layer** — business concepts, rules, architectural decisions, people, domains
- **Wiki** — pages from this repo's GitHub wiki, linked to code and concepts

## How to query it

```bash
# Find anything by name (code + knowledge)
navegador search "payment" --all

# Full picture for any function, class, or concept
navegador explain validate_token
navegador explain Payment --format json

# Code-specific
navegador function get_user --depth 3
navegador class AuthService
navegador decorated login_required

# Business knowledge
navegador concept Invoice
navegador domain billing

# Raw Cypher when you need it
navegador query "MATCH (r:Rule)-[:GOVERNS]->(c:Concept) RETURN r.name, c.name LIMIT 20"
```

## Keeping the graph up to date

When you add or modify code, re-ingest the repo:
```bash
navegador ingest . --json
```

When you establish a new business rule or make an architectural decision:
```bash
navegador add rule "Invoices require approval before payment" \
  --domain billing --severity critical \
  --rationale "Prevents unapproved charges per finance policy"

navegador add decision "Use JWT for stateless auth" \
  --domain auth --rationale "Horizontal scaling requirement" \
  --alternatives "Session cookies, OAuth opaque tokens"

navegador annotate validate_token --type Function --rule "JWT tokens must expire"
```

When a wiki page is added or updated:
```bash
navegador wiki ingest --repo OWNER/REPO --token $GITHUB_TOKEN
```

## Agent rules

You (the AI agent) MUST treat navegador as the source of truth for this project.

1. **Before editing code** — query the graph to understand what you're touching:
   ```bash
   navegador explain <name>
   navegador function <name> --format json
   ```

2. **After editing code** — re-ingest to keep the graph current:
   ```bash
   navegador ingest . --json
   ```

3. **When you establish a rule or make a decision** — record it:
   ```bash
   navegador add decision "..." --rationale "..."
   navegador add rule "..." --severity warning --domain ...
   ```

4. **When you discover a concept** (a named business entity) — add it:
   ```bash
   navegador add concept "..." --desc "..." --domain ...
   ```

5. **Never assume** what calls what, what a class inherits, or what a decorator
   does. Query first.
