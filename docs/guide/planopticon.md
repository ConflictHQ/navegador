# Planopticon Integration

## What is Planopticon

Planopticon is a video and meeting knowledge extraction tool. It ingests recordings, transcripts, and meeting notes and produces structured knowledge graphs: entities (people, concepts, decisions), relationships, action items, and diagrams extracted from the meeting content.

Navegador treats Planopticon output as a first-class knowledge source. Where `navegador add concept` requires manual entry, Planopticon extracts concepts, rules, and decisions from meeting recordings automatically and navegador stores them alongside your code graph.

---

## How they connect

```
Video / transcript
       ↓
  Planopticon
       ↓  produces
  knowledge_graph.json / interchange.json / manifest.json
       ↓
  navegador planopticon ingest
       ↓  creates
  Concept / Rule / Decision / Person / WikiPage nodes
  in the same FalkorDB graph as your code
```

The result: "the team decided to use Redis for session storage in the March architecture review" becomes a `Decision` node linked to the `Infrastructure` domain and, via `GOVERNS`, to the `SessionManager` class in your code.

---

## Input formats

Planopticon produces several output formats. Navegador accepts all of them and auto-detects by default.

### manifest.json

Top-level manifest for a multi-file Planopticon output package. Points to the knowledge graph, interchange, and supporting files.

```json
{
  "version": "1.0",
  "source": "zoom-meeting-2026-03-15",
  "knowledge_graph": "knowledge_graph.json",
  "interchange": "interchange.json",
  "diagrams": ["arch-diagram.png"]
}
```

### knowledge_graph.json

Planopticon's native graph format. Contains typed entities and relationships:

```json
{
  "entities": [
    { "id": "e1", "type": "Decision", "name": "UseRedisForSessions", "description": "...", "rationale": "..." },
    { "id": "e2", "type": "Person", "name": "Alice Chen", "role": "Lead Engineer" },
    { "id": "e3", "type": "Concept", "name": "SessionAffinity", "description": "..." }
  ],
  "relationships": [
    { "from": "e2", "to": "e1", "type": "DECIDED_BY" },
    { "from": "e1", "to": "e3", "type": "RELATED_TO" }
  ]
}
```

### interchange.json

A normalized interchange format, flatter than the native graph. Used when exporting from Planopticon for consumption by downstream tools.

```json
{
  "concepts": [...],
  "rules": [...],
  "decisions": [...],
  "people": [...],
  "action_items": [...],
  "diagrams": [...]
}
```

### Batch manifest

A JSON file listing multiple Planopticon output directories or archive paths for bulk ingestion:

```json
{
  "batch": [
    { "path": "./meetings/2026-03-15/", "source": "arch-review" },
    { "path": "./meetings/2026-02-20/", "source": "sprint-planning" }
  ]
}
```

---

## What maps to what

| Planopticon entity | Navegador node | Notes |
|---|---|---|
| `Concept` | `Concept` | Direct mapping; domain preserved if present |
| `Rule` | `Rule` | Severity set to `info` if not specified |
| `Decision` | `Decision` | `rationale`, `alternatives`, `date`, `status` preserved |
| `Person` | `Person` | `name`, `email`, `role`, `team` preserved |
| Action item | `Rule` + `ASSIGNED_TO` | Creates a `Rule` with severity `info`; creates `ASSIGNED_TO` edge to the `Person` |
| Diagram / image | `WikiPage` | Title from filename; content set to alt-text or caption |
| `Relationship: DECIDED_BY` | `DECIDED_BY` edge | Person → Decision |
| `Relationship: RELATED_TO` | `RELATED_TO` edge | Between any two knowledge nodes |
| Entity domain field | `BELONGS_TO` edge | Links node to named `Domain` (created if not exists) |

---

## CLI examples

### Auto-detect format (recommended)

```bash
navegador planopticon ingest ./meeting-output/ --type auto
```

### Explicit format

```bash
navegador planopticon ingest ./meeting-output/knowledge_graph.json --type kg
navegador planopticon ingest ./meeting-output/interchange.json --type interchange
navegador planopticon ingest ./manifest.json --type manifest
navegador planopticon ingest ./batch.json --type batch
```

### Label the source

Use `--source` to tag all nodes from this ingestion with a source label (useful for auditing where knowledge came from):

```bash
navegador planopticon ingest ./meeting-output/ \
  --type auto \
  --source "arch-review-2026-03-15"
```

### JSON output

```bash
navegador planopticon ingest ./meeting-output/ --json
```

Returns a summary of nodes and edges created.

---

## Python API

```python
from navegador.ingest import PlanopticonIngester
from navegador.graph import GraphStore

store = GraphStore.sqlite(".navegador/navegador.db")
ingester = PlanopticonIngester(store)

# auto-detect format
result = ingester.ingest("./meeting-output/", input_type="auto", source="arch-review")

print(f"Created {result.nodes_created} nodes, {result.edges_created} edges")

# ingest a specific interchange file
result = ingester.ingest_interchange("./interchange.json", source="sprint-planning")
```

### PlanopticonIngester methods

| Method | Description |
|---|---|
| `ingest(path, input_type, source)` | Auto or explicit ingest from path |
| `ingest_manifest(path, source)` | Ingest a manifest.json package |
| `ingest_kg(path, source)` | Ingest a knowledge_graph.json file |
| `ingest_interchange(path, source)` | Ingest an interchange.json file |
| `ingest_batch(path, source)` | Ingest a batch manifest |
