# Planopticon Integration

## What is Planopticon

Planopticon is a video, meeting, and document knowledge extraction tool. It ingests recordings, transcripts, notes, and supporting documents and produces structured knowledge artifacts: knowledge graphs, manifests, action items, key points, and exchange payloads.

Navegador treats Planopticon output as a first-class knowledge source. Where `navegador add concept` requires manual entry, Planopticon extracts concepts, rules, and decisions from meeting recordings automatically and navegador stores them alongside your code graph.

---

## How they connect

```
Video / transcript
       ↓
  Planopticon
       ↓  produces
  manifest.json / results/knowledge_graph.json / exchange.json
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

Single-run PlanOpticon manifest. This is the primary entry point for a completed analysis directory.

```json
{
  "version": "1.0",
  "video": { "title": "Sprint Planning" },
  "knowledge_graph_json": "results/knowledge_graph.json",
  "key_points_json": "results/key_points.json",
  "action_items_json": "results/action_items.json",
  "key_points": [...],
  "action_items": [...],
  "diagrams": [...]
}
```

### knowledge_graph.json

PlanOpticon's native graph export. Contains `nodes`, `relationships`, and optional `sources`:

```json
{
  "nodes": [
    { "name": "Redis", "type": "technology", "descriptions": ["Session storage"] },
    { "name": "Alice Chen", "type": "person", "descriptions": ["Lead engineer"] }
  ],
  "relationships": [
    { "source": "Redis", "target": "Session Store", "type": "depends_on" }
  ],
  "sources": [
    { "source_id": "meeting-1", "source_type": "video", "title": "Sprint Planning" }
  ]
}
```

### exchange.json / interchange.json

A PlanOpticonExchange payload used for interchange with downstream tools. `navegador` still uses the `interchange` type name for this format.

```json
{
  "version": "1.0",
  "project": { "name": "Sprint Reviews", "tags": ["backend", "payments"] },
  "entities": [...],
  "relationships": [...],
  "artifacts": [...],
  "sources": [...]
}
```

### Batch manifest

Current batch outputs also use `manifest.json` at the batch root. Older corpora may still contain `batch_manifest.json`, which `navegador` accepts as a legacy alias.

```json
{
  "version": "1.0",
  "title": "Sprint Reviews",
  "videos": [
    { "video_name": "meeting-01", "manifest_path": "videos/meeting-01/manifest.json", "status": "completed" }
  ],
  "total_videos": 2,
  "completed_videos": 2,
  "merged_knowledge_graph_json": "knowledge_graph.json"
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
navegador planopticon ingest ./meeting-output/results/knowledge_graph.json --type kg
navegador planopticon ingest ./exchange.json --type interchange
navegador planopticon ingest ./meeting-output/manifest.json --type manifest
navegador planopticon ingest ./batch-output/manifest.json --type batch
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
from navegador.graph import GraphStore
from navegador.ingestion import PlanopticonIngester

store = GraphStore.sqlite(".navegador/navegador.db")
ingester = PlanopticonIngester(store, source_tag="arch-review")

# ingest a completed analysis run
stats = ingester.ingest_manifest("./meeting-output/manifest.json")

print(f"Created {stats['nodes']} nodes, {stats['edges']} edges")

# ingest an exchange/interchange file
stats = ingester.ingest_interchange("./exchange.json")
```

### PlanopticonIngester methods

| Method | Description |
|---|---|
| `ingest_manifest(path)` | Ingest a manifest.json package |
| `ingest_kg(path)` | Ingest a knowledge_graph.json file |
| `ingest_interchange(path)` | Ingest an exchange/interchange JSON file |
| `ingest_batch(path)` | Ingest a batch manifest (`manifest.json` or legacy `batch_manifest.json`) |
