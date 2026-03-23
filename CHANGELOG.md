# Changelog

## 0.1.0 — 2026-03-22

First public release.

### Features

- **7-language AST ingestion** — Python, TypeScript, JavaScript, Go, Rust, Java via tree-sitter
- **Property graph storage** — FalkorDB-lite (SQLite, zero-infra) or Redis-backed FalkorDB
- **Context bundles** — file, function, class, concept, and explain context loading
- **MCP server** — 7 tools for AI agent integration (`ingest_repo`, `load_file_context`, `load_function_context`, `load_class_context`, `search_symbols`, `query_graph`, `graph_stats`)
- **CLI** — `ingest`, `context`, `function`, `class`, `explain`, `search`, `decorated`, `query`, `stats`, `add`, `annotate`, `domain`, `concept`, `wiki ingest`, `planopticon ingest`, `mcp`
- **Knowledge ingestion** — concepts, rules, decisions, persons, domains, wiki pages, PlanOpticon video analysis outputs
- **Wiki ingestion** — local Markdown directories, GitHub repo docs via API or git clone

### Quality

- 100% test coverage (426 tests)
- mypy clean (`--ignore-missing-imports`)
- ruff lint + format passing
- CI matrix: Ubuntu + macOS, Python 3.12 / 3.13 / 3.14
