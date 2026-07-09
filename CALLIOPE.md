# Calliope — Navegador
<!-- Agent shim for https://github.com/calliopeai/calliope-cli -->

Primary conventions doc: [`bootstrap.md`](bootstrap.md)

Read it before writing any code.

---

## Project-specific notes

- Python 3.12+ standalone package (no Django) — CLI + library + MCP server
- CLI: Click + Rich, entry point `navegador`, 50+ subcommands in `navegador/cli/`
- Graph store: FalkorDB (Redis, production) or falkordblite (embedded, local zero-infra; graph file is an RDB snapshot, not SQLite)
- Parsing: tree-sitter, 13 languages — core 6 bundled, extras via `.[languages]` / `.[iac]`
- Tests: `pytest tests/ -v` (coverage on by default); install with `pip install -e ".[dev]"`
- Lint/format: `ruff check navegador/` + `ruff format navegador/` — line length 100, py312
- Docs: mkdocs-material — `mkdocs serve` locally, `mkdocs gh-deploy --force` → navegador.dev
