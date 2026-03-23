"""
Navegador CLI — the single interface to your project's knowledge graph.

  CODE:      ingest, context, function, class, search, query
  KNOWLEDGE: add (concept/rule/decision/person/domain), wiki, annotate, domain
  UNIVERSAL: explain, search (spans both layers), stats
"""

import asyncio
import json
import logging

import click
from rich.console import Console
from rich.table import Table

console = Console()

DB_OPTION = click.option(
    "--db", default=".navegador/graph.db", show_default=True, help="Graph DB path."
)
FMT_OPTION = click.option(
    "--format",
    "fmt",
    type=click.Choice(["markdown", "json"]),
    default="markdown",
    show_default=True,
    help="Output format. Use json for agent/pipe consumption.",
)


def _get_store(db: str):
    from navegador.config import DEFAULT_DB_PATH, get_store

    return get_store(db if db != DEFAULT_DB_PATH else None)


def _emit(text: str, fmt: str) -> None:
    if fmt == "json":
        click.echo(text)
    else:
        console.print(text)


# ── Root group ────────────────────────────────────────────────────────────────


@click.group()
@click.version_option(package_name="navegador")
def main():
    """Navegador — project knowledge graph for AI coding agents.

    Combines code structure (AST, call graphs) with business knowledge
    (concepts, rules, decisions, wiki) into a single queryable graph.
    """
    logging.basicConfig(level=logging.WARNING)


# ── Init ──────────────────────────────────────────────────────────────────────


@main.command()
@click.argument("path", default=".", type=click.Path())
@click.option(
    "--redis",
    "redis_url",
    default="",
    help="Redis URL for centralized/production mode (e.g. redis://host:6379).",
)
@click.option(
    "--llm-provider",
    default="",
    help="LLM provider (e.g. anthropic, openai, ollama).",
)
@click.option("--llm-model", default="", help="LLM model name.")
@click.option("--cluster", is_flag=True, help="Enable cluster/swarm mode.")
def init(path: str, redis_url: str, llm_provider: str, llm_model: str, cluster: bool):
    """Initialise navegador in a project directory.

    Creates .navegador/ (gitignored), writes config.toml with storage,
    LLM, and cluster settings.

    \b
    Local SQLite (default — zero infra):
      navegador init

    Centralized Redis (production / multi-agent):
      navegador init --redis redis://host:6379

    With LLM:
      navegador init --llm-provider anthropic --llm-model claude-sonnet-4-6
    """
    from navegador.config import init_project

    storage = "redis" if redis_url else "sqlite"
    nav_dir = init_project(
        path,
        storage=storage,
        redis_url=redis_url,
        llm_provider=llm_provider,
        llm_model=llm_model,
        cluster=cluster,
    )
    console.print(f"[green]Initialised navegador[/green] → {nav_dir}")

    if redis_url:
        console.print(
            f"\n[bold]Redis mode:[/bold] set [cyan]NAVEGADOR_REDIS_URL={redis_url}[/cyan] "
            "in your environment or CI secrets."
        )
    else:
        console.print(
            "\n[bold]Local SQLite mode[/bold] (default). "
            "To use a shared Redis graph set [cyan]NAVEGADOR_REDIS_URL[/cyan]."
        )

    if llm_provider:
        console.print(f"\n[bold]LLM:[/bold] {llm_provider} / {llm_model or '(default)'}")

    if cluster:
        console.print("\n[bold]Cluster mode:[/bold] enabled")

    console.print("\nNext: [bold]navegador ingest .[/bold]")


# ── CODE: ingest ──────────────────────────────────────────────────────────────


@main.command()
@click.argument("repo_path", type=click.Path(exists=True))
@DB_OPTION
@click.option("--clear", is_flag=True, help="Clear existing graph before ingesting.")
@click.option("--incremental", is_flag=True, help="Only re-parse changed files.")
@click.option("--watch", is_flag=True, help="Watch for changes and re-ingest incrementally.")
@click.option("--interval", default=2.0, show_default=True, help="Watch poll interval (seconds).")
@click.option("--json", "as_json", is_flag=True, help="Output stats as JSON.")
@click.option(
    "--redact",
    is_flag=True,
    help="Scan each file for sensitive content and redact before storing in graph nodes.",
)
def ingest(repo_path: str, db: str, clear: bool, incremental: bool, watch: bool,
           interval: float, as_json: bool, redact: bool):
    """Ingest a repository's code into the graph (AST + call graph)."""
    from navegador.ingestion import RepoIngester

    store = _get_store(db)
    ingester = RepoIngester(store, redact=redact)

    if watch:
        console.print(f"[bold]Watching[/bold] {repo_path} (interval={interval}s, Ctrl-C to stop)")

        def _on_cycle(stats):
            changed = stats["files"]
            skipped = stats["skipped"]
            if changed:
                console.print(
                    f"  [green]{changed} changed[/green], {skipped} unchanged"
                )
            return True  # keep watching

        try:
            ingester.watch(repo_path, interval=interval, callback=_on_cycle)
        except KeyboardInterrupt:
            console.print("\n[yellow]Watch stopped.[/yellow]")
        return

    if as_json:
        stats = ingester.ingest(repo_path, clear=clear, incremental=incremental)
        click.echo(json.dumps(stats, indent=2))
    else:
        with console.status(f"[bold]Ingesting[/bold] {repo_path}..."):
            stats = ingester.ingest(repo_path, clear=clear, incremental=incremental)
        table = Table(title="Ingestion complete")
        table.add_column("Metric", style="cyan")
        table.add_column("Count", justify="right", style="green")
        for k, v in stats.items():
            table.add_row(k.capitalize(), str(v))
        console.print(table)


# ── CODE: context / function / class ─────────────────────────────────────────


@main.command()
@click.argument("file_path")
@DB_OPTION
@FMT_OPTION
def context(file_path: str, db: str, fmt: str):
    """Load context for a file — all symbols and their relationships."""
    from navegador.context import ContextLoader

    bundle = ContextLoader(_get_store(db)).load_file(file_path)
    _emit(bundle.to_json() if fmt == "json" else bundle.to_markdown(), fmt)


@main.command()
@click.argument("name")
@click.option("--file", "file_path", default="", help="Narrow to a specific file.")
@click.option("--depth", default=2, show_default=True)
@DB_OPTION
@FMT_OPTION
def function(name: str, file_path: str, db: str, depth: int, fmt: str):
    """Load context for a function — callers, callees, decorators."""
    from navegador.context import ContextLoader

    bundle = ContextLoader(_get_store(db)).load_function(name, file_path=file_path, depth=depth)
    _emit(bundle.to_json() if fmt == "json" else bundle.to_markdown(), fmt)


@main.command("class")
@click.argument("name")
@click.option("--file", "file_path", default="", help="Narrow to a specific file.")
@DB_OPTION
@FMT_OPTION
def class_(name: str, file_path: str, db: str, fmt: str):
    """Load context for a class — methods, inheritance, references."""
    from navegador.context import ContextLoader

    bundle = ContextLoader(_get_store(db)).load_class(name, file_path=file_path)
    _emit(bundle.to_json() if fmt == "json" else bundle.to_markdown(), fmt)


# ── UNIVERSAL: explain ────────────────────────────────────────────────────────


@main.command()
@click.argument("name")
@click.option("--file", "file_path", default="")
@DB_OPTION
@FMT_OPTION
def explain(name: str, file_path: str, db: str, fmt: str):
    """Full picture: all relationships in and out, code and knowledge layers."""
    from navegador.context import ContextLoader

    bundle = ContextLoader(_get_store(db)).explain(name, file_path=file_path)
    _emit(bundle.to_json() if fmt == "json" else bundle.to_markdown(), fmt)


# ── UNIVERSAL: search ─────────────────────────────────────────────────────────


@main.command()
@click.argument("query")
@DB_OPTION
@click.option("--limit", default=20, show_default=True)
@click.option(
    "--all", "search_all", is_flag=True, help="Include knowledge layer (concepts, rules, wiki)."
)
@click.option("--docs", "by_doc", is_flag=True, help="Search docstrings instead of names.")
@FMT_OPTION
def search(query: str, db: str, limit: int, search_all: bool, by_doc: bool, fmt: str):
    """Search symbols, concepts, rules, and wiki pages."""
    from navegador.context import ContextLoader

    loader = ContextLoader(_get_store(db))

    if by_doc:
        results = loader.search_by_docstring(query, limit=limit)
    elif search_all:
        results = loader.search_all(query, limit=limit)
    else:
        results = loader.search(query, limit=limit)

    if fmt == "json":
        click.echo(
            json.dumps(
                [
                    {
                        "type": r.type,
                        "name": r.name,
                        "file_path": r.file_path,
                        "line_start": r.line_start,
                        "docstring": r.docstring,
                        "description": r.description,
                    }
                    for r in results
                ],
                indent=2,
            )
        )
        return

    if not results:
        console.print("[yellow]No results.[/yellow]")
        return

    table = Table(title=f"Search: {query!r}")
    table.add_column("Type", style="cyan")
    table.add_column("Name", style="bold")
    table.add_column("File / Domain")
    table.add_column("Line", justify="right")
    for r in results:
        loc = r.file_path or r.domain or ""
        table.add_row(r.type, r.name, loc, str(r.line_start or ""))
    console.print(table)


# ── CODE: decorator / query ───────────────────────────────────────────────────


@main.command()
@click.argument("decorator_name")
@DB_OPTION
@FMT_OPTION
def decorated(decorator_name: str, db: str, fmt: str):
    """Find all functions/methods carrying a decorator."""
    from navegador.context import ContextLoader

    results = ContextLoader(_get_store(db)).decorated_by(decorator_name)

    if fmt == "json":
        click.echo(
            json.dumps(
                [
                    {"type": r.type, "name": r.name, "file_path": r.file_path, "line": r.line_start}
                    for r in results
                ],
                indent=2,
            )
        )
        return

    if not results:
        console.print(f"[yellow]No functions decorated with @{decorator_name}[/yellow]")
        return

    table = Table(title=f"@{decorator_name}")
    table.add_column("Type", style="cyan")
    table.add_column("Name", style="bold")
    table.add_column("File")
    table.add_column("Line", justify="right")
    for r in results:
        table.add_row(r.type, r.name, r.file_path, str(r.line_start or ""))
    console.print(table)


@main.command()
@click.argument("cypher")
@DB_OPTION
def query(cypher: str, db: str):
    """Run a raw Cypher query — output is always JSON."""
    result = _get_store(db).query(cypher)
    click.echo(json.dumps(result.result_set or [], default=str, indent=2))


# ── KNOWLEDGE: add group ──────────────────────────────────────────────────────


@main.group()
def add():
    """Add knowledge nodes — concepts, rules, decisions, people, domains."""


@add.command("concept")
@click.argument("name")
@click.option("--desc", default="", help="Description / definition.")
@click.option("--domain", default="")
@click.option("--status", default="", help="e.g. stable, proposed, deprecated")
@click.option("--rules", default="", help="Comma-separated rule names.")
@click.option("--wiki", default="", help="Wiki URL or reference.")
@DB_OPTION
def add_concept(name: str, desc: str, domain: str, status: str, rules: str, wiki: str, db: str):
    """Add a business concept to the knowledge graph."""
    from navegador.ingestion import KnowledgeIngester

    k = KnowledgeIngester(_get_store(db))
    k.add_concept(name, description=desc, domain=domain, status=status, rules=rules, wiki_refs=wiki)
    console.print(f"[green]Concept added:[/green] {name}")


@add.command("rule")
@click.argument("name")
@click.option("--desc", default="")
@click.option("--domain", default="")
@click.option("--severity", default="info", type=click.Choice(["info", "warning", "critical"]))
@click.option("--rationale", default="")
@DB_OPTION
def add_rule(name: str, desc: str, domain: str, severity: str, rationale: str, db: str):
    """Add a business rule or constraint."""
    from navegador.ingestion import KnowledgeIngester

    k = KnowledgeIngester(_get_store(db))
    k.add_rule(name, description=desc, domain=domain, severity=severity, rationale=rationale)
    console.print(f"[green]Rule added:[/green] {name}")


@add.command("decision")
@click.argument("name")
@click.option("--desc", default="")
@click.option("--domain", default="")
@click.option("--rationale", default="")
@click.option("--alternatives", default="")
@click.option("--date", default="")
@click.option(
    "--status", default="accepted", type=click.Choice(["proposed", "accepted", "deprecated"])
)
@DB_OPTION
def add_decision(name, desc, domain, rationale, alternatives, date, status, db):
    """Add an architectural or product decision."""
    from navegador.ingestion import KnowledgeIngester

    k = KnowledgeIngester(_get_store(db))
    k.add_decision(
        name,
        description=desc,
        domain=domain,
        status=status,
        rationale=rationale,
        alternatives=alternatives,
        date=date,
    )
    console.print(f"[green]Decision added:[/green] {name}")


@add.command("person")
@click.argument("name")
@click.option("--email", default="")
@click.option("--role", default="")
@click.option("--team", default="")
@DB_OPTION
def add_person(name: str, email: str, role: str, team: str, db: str):
    """Add a person (contributor, owner, stakeholder)."""
    from navegador.ingestion import KnowledgeIngester

    k = KnowledgeIngester(_get_store(db))
    k.add_person(name, email=email, role=role, team=team)
    console.print(f"[green]Person added:[/green] {name}")


@add.command("domain")
@click.argument("name")
@click.option("--desc", default="")
@DB_OPTION
def add_domain(name: str, desc: str, db: str):
    """Add a business domain (auth, billing, notifications…)."""
    from navegador.ingestion import KnowledgeIngester

    k = KnowledgeIngester(_get_store(db))
    k.add_domain(name, description=desc)
    console.print(f"[green]Domain added:[/green] {name}")


# ── KNOWLEDGE: annotate ───────────────────────────────────────────────────────


@main.command()
@click.argument("code_name")
@click.option(
    "--type",
    "code_label",
    default="Function",
    type=click.Choice(["Function", "Class", "Method", "File", "Module"]),
)
@click.option("--concept", default="", help="Link to this concept.")
@click.option("--rule", default="", help="Link to this rule.")
@DB_OPTION
def annotate(code_name: str, code_label: str, concept: str, rule: str, db: str):
    """Link a code node to a concept or rule in the knowledge graph."""
    from navegador.ingestion import KnowledgeIngester

    k = KnowledgeIngester(_get_store(db))
    k.annotate_code(code_name, code_label, concept=concept or None, rule=rule or None)
    console.print(f"[green]Annotated:[/green] {code_name}")


# ── KNOWLEDGE: domain view ────────────────────────────────────────────────────


@main.command()
@click.argument("name")
@DB_OPTION
@FMT_OPTION
def domain(name: str, db: str, fmt: str):
    """Show everything belonging to a domain — code and knowledge."""
    from navegador.context import ContextLoader

    bundle = ContextLoader(_get_store(db)).load_domain(name)
    _emit(bundle.to_json() if fmt == "json" else bundle.to_markdown(), fmt)


@main.command()
@click.argument("name")
@DB_OPTION
@FMT_OPTION
def concept(name: str, db: str, fmt: str):
    """Load a business concept — rules, related concepts, implementing code, wiki."""
    from navegador.context import ContextLoader

    bundle = ContextLoader(_get_store(db)).load_concept(name)
    _emit(bundle.to_json() if fmt == "json" else bundle.to_markdown(), fmt)


# ── KNOWLEDGE: wiki ───────────────────────────────────────────────────────────


@main.group()
def wiki():
    """Ingest and manage wiki pages in the knowledge graph."""


@wiki.command("ingest")
@click.option("--repo", default="", help="GitHub repo (owner/repo) — clones the wiki.")
@click.option("--dir", "wiki_dir", default="", help="Local directory of markdown files.")
@click.option("--token", default="", envvar="GITHUB_TOKEN", help="GitHub token.")
@click.option("--api", is_flag=True, help="Use GitHub API instead of git clone.")
@DB_OPTION
def wiki_ingest(repo: str, wiki_dir: str, token: str, api: bool, db: str):
    """Pull wiki pages into the knowledge graph."""
    from navegador.ingestion import WikiIngester

    w = WikiIngester(_get_store(db))

    if wiki_dir:
        stats = w.ingest_local(wiki_dir)
    elif repo:
        if api:
            stats = w.ingest_github_api(repo, token=token)
        else:
            stats = w.ingest_github(repo, token=token)
    else:
        raise click.UsageError("Provide --repo or --dir")

    console.print(f"[green]Wiki ingested:[/green] {stats['pages']} pages, {stats['links']} links")


# ── Stats ─────────────────────────────────────────────────────────────────────


@main.command()
@DB_OPTION
@click.option("--json", "as_json", is_flag=True)
def stats(db: str, as_json: bool):
    """Graph statistics broken down by node and edge type."""
    from navegador.graph import queries as q

    store = _get_store(db)

    node_rows = store.query(q.NODE_TYPE_COUNTS).result_set or []
    edge_rows = store.query(q.EDGE_TYPE_COUNTS).result_set or []

    total_nodes = sum(r[1] for r in node_rows)
    total_edges = sum(r[1] for r in edge_rows)

    if as_json:
        click.echo(
            json.dumps(
                {
                    "total_nodes": total_nodes,
                    "total_edges": total_edges,
                    "nodes": {r[0]: r[1] for r in node_rows},
                    "edges": {r[0]: r[1] for r in edge_rows},
                },
                indent=2,
            )
        )
        return

    node_table = Table(title=f"Nodes ({total_nodes:,})")
    node_table.add_column("Type", style="cyan")
    node_table.add_column("Count", justify="right", style="green")
    for row in node_rows:
        node_table.add_row(row[0], f"{row[1]:,}")

    edge_table = Table(title=f"Edges ({total_edges:,})")
    edge_table.add_column("Type", style="cyan")
    edge_table.add_column("Count", justify="right", style="green")
    for row in edge_rows:
        edge_table.add_row(row[0], f"{row[1]:,}")

    console.print(node_table)
    console.print(edge_table)


# ── PLANOPTICON ingestion ──────────────────────────────────────────────────────


@main.group()
def planopticon():
    """Ingest planopticon output (meetings, videos, docs) into the knowledge graph."""


@planopticon.command("ingest")
@click.argument("path", type=click.Path(exists=True))
@click.option(
    "--type",
    "input_type",
    type=click.Choice(["auto", "manifest", "kg", "interchange", "batch"]),
    default="auto",
    show_default=True,
    help="Input format. auto detects from filename.",
)
@click.option("--source", default="", help="Source label for provenance (e.g. 'Q4 planning').")
@click.option("--json", "as_json", is_flag=True)
@DB_OPTION
def planopticon_ingest(path: str, input_type: str, source: str, as_json: bool, db: str):
    """Load a planopticon output directory or file into the knowledge graph.

    PATH can be:
      - A manifest.json file
      - A knowledge_graph.json file
      - An interchange.json file
      - A batch manifest JSON
      - A planopticon output directory (auto-detects manifest.json inside)
    """
    from pathlib import Path as P

    from navegador.ingestion import PlanopticonIngester

    p = P(path)
    # Resolve directory → manifest.json
    if p.is_dir():
        candidates = ["manifest.json", "results/knowledge_graph.json", "interchange.json"]
        for c in candidates:
            if (p / c).exists():
                p = p / c
                break
        else:
            raise click.UsageError(f"No recognised planopticon file found in {path}")

    # Auto-detect type from filename
    if input_type == "auto":
        name = p.name.lower()
        if "manifest" in name:
            input_type = "manifest"
        elif "interchange" in name:
            input_type = "interchange"
        elif "batch" in name:
            input_type = "batch"
        else:
            input_type = "kg"

    ing = PlanopticonIngester(_get_store(db), source_tag=source)

    dispatch = {
        "manifest": ing.ingest_manifest,
        "kg": ing.ingest_kg,
        "interchange": ing.ingest_interchange,
        "batch": ing.ingest_batch,
    }
    stats = dispatch[input_type](p)

    if as_json:
        click.echo(json.dumps(stats, indent=2))
    else:
        table = Table(title=f"Planopticon import ({input_type})")
        table.add_column("Metric", style="cyan")
        table.add_column("Count", justify="right", style="green")
        for k, v in stats.items():
            table.add_row(k.capitalize(), str(v))
        console.print(table)


# ── Export / Import ──────────────────────────────────────────────────────────


@main.command("export")
@click.argument("output", type=click.Path())
@DB_OPTION
@click.option("--json", "as_json", is_flag=True, help="Output stats as JSON.")
def export_cmd(output: str, db: str, as_json: bool):
    """Export the graph to a text-based JSONL file (git-friendly)."""
    from navegador.graph.export import export_graph

    store = _get_store(db)
    stats = export_graph(store, output)

    if as_json:
        click.echo(json.dumps(stats, indent=2))
    else:
        console.print(
            f"[green]Exported[/green] {stats['nodes']} nodes, {stats['edges']} edges → {output}"
        )


@main.command("import")
@click.argument("input_path", type=click.Path(exists=True))
@DB_OPTION
@click.option("--no-clear", is_flag=True, help="Don't wipe graph before importing.")
@click.option("--json", "as_json", is_flag=True, help="Output stats as JSON.")
def import_cmd(input_path: str, db: str, no_clear: bool, as_json: bool):
    """Import a graph from a JSONL export file."""
    from navegador.graph.export import import_graph

    store = _get_store(db)
    stats = import_graph(store, input_path, clear=not no_clear)

    if as_json:
        click.echo(json.dumps(stats, indent=2))
    else:
        console.print(
            f"[green]Imported[/green] {stats['nodes']} nodes, {stats['edges']} edges ← {input_path}"
        )


# ── Schema migrations ────────────────────────────────────────────────────────


@main.command()
@DB_OPTION
@click.option("--check", is_flag=True, help="Check if migration is needed without applying.")
def migrate(db: str, check: bool):
    """Apply pending schema migrations to the graph."""
    from navegador.graph.migrations import (
        CURRENT_SCHEMA_VERSION,
        get_schema_version,
        migrate as do_migrate,
        needs_migration,
    )

    store = _get_store(db)

    if check:
        current = get_schema_version(store)
        if needs_migration(store):
            console.print(
                f"[yellow]Migration needed:[/yellow] v{current} → v{CURRENT_SCHEMA_VERSION}"
            )
        else:
            console.print(f"[green]Schema is up to date[/green] (v{current})")
        return

    current = get_schema_version(store)
    applied = do_migrate(store)
    if applied:
        console.print(
            f"[green]Migrated[/green] v{current} → v{CURRENT_SCHEMA_VERSION} "
            f"({len(applied)} migration{'s' if len(applied) != 1 else ''})"
        )
    else:
        console.print(f"[green]Schema is up to date[/green] (v{current})")


# ── Editor integrations ───────────────────────────────────────────────────────


@main.group()
def editor():
    """Generate MCP config snippets for AI coding editors."""


@editor.command("setup")
@click.argument("editor_name", metavar="EDITOR")
@DB_OPTION
@click.option(
    "--write",
    "do_write",
    is_flag=True,
    help="Write the config file to the expected path in the current directory.",
)
def editor_setup(editor_name: str, db: str, do_write: bool):
    """Generate the MCP config snippet for an editor.

    \b
    EDITOR is one of: claude-code, cursor, codex, windsurf, all

    \b
    Examples:
      navegador editor setup claude-code
      navegador editor setup cursor --db .navegador/graph.db
      navegador editor setup all --write
    """
    from navegador.editor import SUPPORTED_EDITORS, EditorIntegration

    if editor_name not in SUPPORTED_EDITORS and editor_name != "all":
        raise click.BadParameter(
            f"Unknown editor {editor_name!r}. "
            f"Choose from: {', '.join(SUPPORTED_EDITORS + ['all'])}",
            param_hint="EDITOR",
        )

    integration = EditorIntegration(db=db)
    targets = SUPPORTED_EDITORS if editor_name == "all" else [editor_name]

    for target in targets:
        config_json = integration.config_json(target)
        config_path = integration.config_path(target)

        if len(targets) > 1:
            console.print(f"\n[bold cyan]{target}[/bold cyan] ({config_path})")

        click.echo(config_json)

        if do_write:
            written = integration.write_config(target)
            console.print(f"[green]Written:[/green] {written}")


# ── CI/CD ─────────────────────────────────────────────────────────────────────


@main.group()
def ci():
    """CI/CD mode — machine-readable output and structured exit codes.

    All subcommands emit JSON to stdout and exit with:
      0  success
      1  error
      2  warnings only
    """


@ci.command("ingest")
@click.argument("repo_path", type=click.Path(exists=True))
@DB_OPTION
@click.option("--clear", is_flag=True, help="Clear existing graph before ingesting.")
@click.option("--incremental", is_flag=True, help="Only re-parse changed files.")
def ci_ingest(repo_path: str, db: str, clear: bool, incremental: bool):
    """Ingest a repository and exit non-zero on errors or empty results."""
    import sys

    from navegador.cicd import CICDReporter
    from navegador.ingestion import RepoIngester

    reporter = CICDReporter()
    data: dict = {}

    try:
        store = _get_store(db)
        ingester = RepoIngester(store)
        stats = ingester.ingest(repo_path, clear=clear, incremental=incremental)
        data = stats
        if stats.get("files", 0) == 0:
            reporter.add_warning("No source files were ingested.")
    except Exception as exc:  # noqa: BLE001
        reporter.add_error(str(exc))

    reporter.emit(data=data or None)
    sys.exit(reporter.exit_code())


@ci.command("stats")
@DB_OPTION
def ci_stats(db: str):
    """Emit graph statistics as JSON (for CI consumption)."""
    import sys

    from navegador.cicd import CICDReporter
    from navegador.graph import queries as q

    reporter = CICDReporter()
    data: dict = {}

    try:
        store = _get_store(db)
        node_rows = store.query(q.NODE_TYPE_COUNTS).result_set or []
        edge_rows = store.query(q.EDGE_TYPE_COUNTS).result_set or []
        data = {
            "total_nodes": sum(r[1] for r in node_rows),
            "total_edges": sum(r[1] for r in edge_rows),
            "nodes": {r[0]: r[1] for r in node_rows},
            "edges": {r[0]: r[1] for r in edge_rows},
        }
    except Exception as exc:  # noqa: BLE001
        reporter.add_error(str(exc))

    reporter.emit(data=data or None)
    sys.exit(reporter.exit_code())


@ci.command("check")
@DB_OPTION
def ci_check(db: str):
    """Check schema version — exits 2 if migration is needed, 1 on hard error."""
    import sys

    from navegador.cicd import CICDReporter
    from navegador.graph.migrations import (
        CURRENT_SCHEMA_VERSION,
        get_schema_version,
        needs_migration,
    )

    reporter = CICDReporter()
    data: dict = {}

    try:
        store = _get_store(db)
        current = get_schema_version(store)
        data = {"schema_version": current, "current_schema_version": CURRENT_SCHEMA_VERSION}
        if needs_migration(store):
            reporter.add_warning(
                f"Schema migration needed: v{current} → v{CURRENT_SCHEMA_VERSION}"
            )
    except Exception as exc:  # noqa: BLE001
        reporter.add_error(str(exc))

    reporter.emit(data=data or None)
    sys.exit(reporter.exit_code())


# ── Shell completions ─────────────────────────────────────────────────────────


@main.command()
@click.argument("shell", type=click.Choice(["bash", "zsh", "fish"]))
@click.option(
    "--install",
    "do_install",
    is_flag=True,
    help="Append the completion line to the default shell rc file.",
)
@click.option(
    "--rc-path",
    default="",
    help="Override the rc file path used by --install.",
)
def completions(shell: str, do_install: bool, rc_path: str):
    """Print (or install) tab-completion for bash, zsh, or fish.

    \b
    Print the line to add manually:
      navegador completions bash
      navegador completions zsh
      navegador completions fish

    \b
    Auto-append to your rc file:
      navegador completions bash --install
      navegador completions zsh --install
      navegador completions fish --install
    """
    from navegador.completions import get_eval_line, get_rc_path, install_completion

    if do_install:
        target = install_completion(shell, rc_path=rc_path or None)
        console.print(f"[green]Completion installed[/green] → {target}")
        console.print(f"Restart your shell or run: [bold]source {target}[/bold]")
    else:
        line = get_eval_line(shell)
        rc = rc_path or get_rc_path(shell)
        console.print(f"Add the following line to [bold]{rc}[/bold]:\n")
        click.echo(f"  {line}")
        console.print(
            f"\nOr run: [bold]navegador completions {shell} --install[/bold]"
        )


# ── MCP ───────────────────────────────────────────────────────────────────────


@main.command()
@DB_OPTION
@click.option(
    "--read-only",
    "read_only",
    is_flag=True,
    default=False,
    help=(
        "Start in read-only mode: disables ingest_repo and blocks write "
        "operations in query_graph."
    ),
)
def mcp(db: str, read_only: bool):
    """Start the MCP server for AI agent integration (stdio)."""
    from mcp.server.stdio import stdio_server  # type: ignore[import]

    from navegador.mcp import create_mcp_server

    server = create_mcp_server(lambda: _get_store(db), read_only=read_only)
    mode = "read-only" if read_only else "read-write"
    console.print(f"[green]Navegador MCP server running[/green] (stdio, {mode})")

    async def _run():
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())

    asyncio.run(_run())
