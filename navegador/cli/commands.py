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
@click.option(
    "--commit-graph",
    is_flag=True,
    default=False,
    help=(
        "Commit the graph DB to git. Skips .gitignore entry and writes "
        ".navegador/.gitkeep so the dir is tracked. "
        "Default: gitignore (treat graph as a build artifact)."
    ),
)
def init(
    path: str,
    redis_url: str,
    llm_provider: str,
    llm_model: str,
    cluster: bool,
    commit_graph: bool,
):
    """Initialise navegador in a project directory.

    Creates .navegador/, writes config.toml with storage, LLM, and cluster
    settings. By default the directory is gitignored (graph is a build
    artifact — rebuild with ``navegador ingest .``).

    Pass --commit-graph to track the DB in git instead (contributors get a
    ready-made graph on clone, at the cost of repo size growth).

    \b
    Local SQLite (default — zero infra):
      navegador init

    Commit graph to git (clone-and-go experience):
      navegador init --commit-graph

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
        commit_graph=commit_graph,
    )
    console.print(f"[green]Initialised navegador[/green] → {nav_dir}")

    if commit_graph:
        console.print(
            "\n[bold]Graph mode:[/bold] committed to git. "
            "Run [bold]navegador ingest .[/bold] then commit [cyan].navegador/graph.db[/cyan]. "
            "Keep it updated or contributors will see a stale graph."
        )
    else:
        console.print(
            "\n[bold]Graph mode:[/bold] gitignored (build artifact). "
            "Run [bold]navegador ingest .[/bold] to build. "
            "Use [cyan]--commit-graph[/cyan] to track in git instead."
        )

    if redis_url:
        console.print(
            f"\n[bold]Redis mode:[/bold] set [cyan]NAVEGADOR_REDIS_URL={redis_url}[/cyan] "
            "in your environment or CI secrets."
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
@click.option(
    "--monorepo",
    is_flag=True,
    help="Detect and ingest as a monorepo workspace (Turborepo, Nx, Yarn, pnpm, Cargo, Go).",
)
def ingest(
    repo_path: str,
    db: str,
    clear: bool,
    incremental: bool,
    watch: bool,
    interval: float,
    as_json: bool,
    redact: bool,
    monorepo: bool,
):
    """Ingest a repository's code into the graph (AST + call graph)."""
    if monorepo:
        from navegador.monorepo import MonorepoIngester

        store = _get_store(db)
        mono_ingester = MonorepoIngester(store)

        if as_json:
            stats = mono_ingester.ingest(repo_path, clear=clear)
            click.echo(json.dumps(stats, indent=2))
        else:
            with console.status(f"[bold]Ingesting monorepo[/bold] {repo_path}..."):
                stats = mono_ingester.ingest(repo_path, clear=clear)
            table = Table(title="Monorepo ingestion complete")
            table.add_column("Metric", style="cyan")
            table.add_column("Count", justify="right", style="green")
            for k, v in stats.items():
                table.add_row(str(k).capitalize(), str(v))
            console.print(table)
        return

    from navegador.ingestion import RepoIngester

    store = _get_store(db)
    ingester = RepoIngester(store, redact=redact)

    if watch:
        console.print(f"[bold]Watching[/bold] {repo_path} (interval={interval}s, Ctrl-C to stop)")

        def _on_cycle(stats):
            changed = stats["files"]
            skipped = stats["skipped"]
            if changed:
                console.print(f"  [green]{changed} changed[/green], {skipped} unchanged")
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
@click.option("--memory", default="", help="Link to a memory node by name (GOVERNS edge).")
@DB_OPTION
def annotate(code_name: str, code_label: str, concept: str, rule: str, memory: str, db: str):
    """Link a code node to a concept, rule, or memory node."""
    from navegador.ingestion import KnowledgeIngester

    k = KnowledgeIngester(_get_store(db))
    k.annotate_code(
        code_name,
        code_label,
        concept=concept or None,
        rule=rule or None,
        memory=memory or None,
    )
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


# ── KNOWLEDGE: memory ─────────────────────────────────────────────────────────


@main.group()
def memory():
    """Ingest and query CONFLICT-format memory/ directories."""


@memory.command("ingest")
@click.argument("memory_path", type=click.Path(exists=True))
@click.option("--repo", "repo_name", default="", help="Repository name to scope nodes to.")
@click.option("--clear", is_flag=True, help="Remove existing memory nodes for this repo first.")
@click.option("--workspace", is_flag=True, help="Traverse all submodule memory/ dirs + root.")
@click.option(
    "--recursive",
    is_flag=True,
    help="Find all memory/ dirs under path (for monorepos with per-service memory).",
)
@DB_OPTION
def memory_ingest(
    memory_path: str, repo_name: str, clear: bool, workspace: bool, recursive: bool, db: str
):
    """Ingest a CONFLICT-format memory/ directory into the graph."""
    from navegador.ingestion import MemoryIngester

    ingester = MemoryIngester(_get_store(db))

    if recursive:
        stats = ingester.ingest_recursive(memory_path, clear=clear)
        console.print(
            f"[green]Memory (recursive):[/green] {stats['ingested']} nodes ingested, "
            f"{stats['skipped']} skipped across {len(stats['repos'])} scopes"
        )
    elif workspace:
        stats = ingester.ingest_workspace(memory_path, clear=clear)
        console.print(
            f"[green]Memory (workspace):[/green] {stats['ingested']} nodes ingested, "
            f"{stats['skipped']} skipped across {len(stats['repos'])} repos"
        )
    else:
        stats = ingester.ingest(memory_path, repo_name=repo_name, clear=clear)
        console.print(
            f"[green]Memory ingested:[/green] {stats['ingested']} nodes "
            f"({', '.join(f'{v} {k}' for k, v in stats.get('by_type', {}).items())}) "
            f"for repo [bold]{stats['repo']}[/bold]"
        )


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
        needs_migration,
    )
    from navegador.graph.migrations import (
        migrate as do_migrate,
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


# ── Graph explorer ────────────────────────────────────────────────────────────


@main.command()
@DB_OPTION
@click.option("--host", default="127.0.0.1", show_default=True, help="Bind address.")
@click.option("--port", default=8080, show_default=True, help="TCP port.")
@click.option(
    "--no-browser",
    is_flag=True,
    default=False,
    help="Don't open a browser tab automatically.",
)
def explore(db: str, host: str, port: int, no_browser: bool):
    """Launch the browser-based graph explorer.

    Starts an HTTP server and opens the interactive force-directed
    visualisation in your default browser.

    \b
    Examples:
      navegador explore
      navegador explore --port 9000
      navegador explore --no-browser
    """
    import time
    import webbrowser

    from navegador.explorer import ExplorerServer

    store = _get_store(db)
    server = ExplorerServer(store, host=host, port=port)
    server.start()
    url = server.url

    console.print(f"[green]Graph explorer running[/green] → {url}")
    console.print("Press [bold]Ctrl-C[/bold] to stop.")

    if not no_browser:
        # Small delay so the server is accepting connections before the browser hits it
        time.sleep(0.3)
        webbrowser.open(url)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        console.print("\n[yellow]Stopping explorer…[/yellow]")
    finally:
        server.stop()


# ── Enrichment ───────────────────────────────────────────────────────────────


@main.command()
@DB_OPTION
@click.option(
    "--framework",
    "framework_name",
    default="",
    help="Framework to enrich (e.g. django, fastapi). Auto-detects if omitted.",
)
@click.option("--json", "as_json", is_flag=True, help="Output results as JSON.")
def enrich(db: str, framework_name: str, as_json: bool):
    """Run framework enrichment on the graph.

    Promotes generic Function/Class nodes to semantic framework types
    by detecting framework patterns and adding labels/properties.

    \b
    Auto-detect all frameworks:
      navegador enrich

    \b
    Target a specific framework:
      navegador enrich --framework django
    """
    import importlib
    import pkgutil

    import navegador.enrichment as _enrichment_pkg
    from navegador.enrichment.base import FrameworkEnricher

    store = _get_store(db)

    # Discover all FrameworkEnricher subclasses in the enrichment package.
    def _load_enrichers() -> dict[str, type[FrameworkEnricher]]:
        enrichers: dict[str, type[FrameworkEnricher]] = {}
        pkg_path = _enrichment_pkg.__path__
        pkg_name = _enrichment_pkg.__name__
        for _finder, mod_name, _ispkg in pkgutil.iter_modules(pkg_path):
            if mod_name == "base":
                continue
            mod = importlib.import_module(f"{pkg_name}.{mod_name}")
            for attr in vars(mod).values():
                if (
                    isinstance(attr, type)
                    and issubclass(attr, FrameworkEnricher)
                    and attr is not FrameworkEnricher
                ):
                    try:
                        instance = attr.__new__(attr)
                        instance.store = store
                        enrichers[attr(store).framework_name] = attr
                    except Exception:  # noqa: BLE001
                        pass
        return enrichers

    available = _load_enrichers()

    if framework_name:
        if framework_name not in available:
            raise click.BadParameter(
                f"Unknown framework {framework_name!r}. "
                f"Available: {', '.join(sorted(available)) or '(none registered)'}",
                param_hint="--framework",
            )
        targets = {framework_name: available[framework_name]}
    else:
        # Auto-detect: only run enrichers whose detect() returns True.
        targets = {name: cls for name, cls in available.items() if cls(store).detect()}
        if not targets and not as_json:
            console.print("[yellow]No frameworks detected in the graph.[/yellow]")
            return

    all_results: dict[str, dict] = {}
    for name, cls in targets.items():
        enricher = cls(store)
        result = enricher.enrich()
        all_results[name] = {
            "promoted": result.promoted,
            "edges_added": result.edges_added,
            "patterns_found": result.patterns_found,
        }

    if as_json:
        click.echo(json.dumps(all_results, indent=2))
        return

    for name, data in all_results.items():
        table = Table(title=f"Enrichment: {name}")
        table.add_column("Metric", style="cyan")
        table.add_column("Value", justify="right", style="green")
        table.add_row("Nodes promoted", str(data["promoted"]))
        table.add_row("Edges added", str(data["edges_added"]))
        for pattern, count in data["patterns_found"].items():
            table.add_row(f"  {pattern}", str(count))
        console.print(table)


# ── Diff: map uncommitted changes to affected graph nodes ─────────────────────


@main.command("diff")
@DB_OPTION
@FMT_OPTION
@click.option(
    "--repo",
    "repo_path",
    default=".",
    show_default=True,
    type=click.Path(exists=True),
    help="Repository root to inspect (default: current directory).",
)
def diff_cmd(db: str, fmt: str, repo_path: str):
    """Show which graph nodes are affected by uncommitted changes.

    Reads the current git diff, finds every function/class/method whose
    line range overlaps a changed hunk, then follows knowledge edges to
    surface impacted concepts, rules, and decisions.

    \b
    Examples:
      navegador diff
      navegador diff --format json
      navegador diff --repo /path/to/project
    """
    from pathlib import Path as P

    from navegador.diff import DiffAnalyzer

    analyzer = DiffAnalyzer(_get_store(db), P(repo_path))

    if fmt == "json":
        click.echo(analyzer.to_json())
        return

    # Rich markdown output
    md = analyzer.to_markdown()
    console.print(md)


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
            reporter.add_warning(f"Schema migration needed: v{current} → v{CURRENT_SCHEMA_VERSION}")
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
        console.print(f"\nOr run: [bold]navegador completions {shell} --install[/bold]")


# ── Churn / behavioural coupling ─────────────────────────────────────────────


@main.command()
@click.argument("repo_path", default=".", type=click.Path(exists=True))
@DB_OPTION
@click.option("--limit", default=500, show_default=True, help="Max commits to inspect.")
@click.option(
    "--min-confidence",
    default=0.5,
    show_default=True,
    type=float,
    help="Minimum coupling confidence (0–1).",
)
@click.option(
    "--min-co-changes",
    default=3,
    show_default=True,
    type=int,
    help="Minimum co-change count for a coupling pair.",
)
@click.option("--store", "do_store", is_flag=True, help="Write results to the graph.")
@click.option("--json", "as_json", is_flag=True, help="Output results as JSON.")
def churn(
    repo_path: str,
    db: str,
    limit: int,
    min_confidence: float,
    min_co_changes: int,
    do_store: bool,
    as_json: bool,
):
    """Analyze git history for file churn and behavioural coupling.

    Shows files that change most often and pairs of files that
    frequently change together (co-evolution / logical coupling).

    \b
    Examples:
      navegador churn .
      navegador churn . --limit 200 --min-confidence 0.7
      navegador churn . --store          # persist to graph
      navegador churn . --json           # machine-readable output
    """
    from pathlib import Path as P

    from navegador.churn import ChurnAnalyzer

    analyzer = ChurnAnalyzer(P(repo_path), limit=limit)

    with console.status("[bold]Analysing git history…[/bold]"):
        churn_entries = analyzer.file_churn()
        pairs = analyzer.coupling_pairs(
            min_co_changes=min_co_changes, min_confidence=min_confidence
        )

    if do_store:
        store = _get_store(db)
        stats = analyzer.store_churn(store)
        if as_json:
            click.echo(json.dumps(stats, indent=2))
        else:
            console.print(
                f"[green]Churn stored:[/green] "
                f"{stats['churn_updated']} files updated, "
                f"{stats['couplings_written']} coupling edges written"
            )
        return

    if as_json:
        click.echo(
            json.dumps(
                {
                    "churn": [
                        {
                            "file_path": e.file_path,
                            "commit_count": e.commit_count,
                            "lines_changed": e.lines_changed,
                        }
                        for e in churn_entries
                    ],
                    "coupling_pairs": [
                        {
                            "file_a": p.file_a,
                            "file_b": p.file_b,
                            "co_change_count": p.co_change_count,
                            "confidence": p.confidence,
                        }
                        for p in pairs
                    ],
                },
                indent=2,
            )
        )
        return

    # ── Rich tables ───────────────────────────────────────────────────────────
    churn_table = Table(title=f"File churn (top {min(20, len(churn_entries))})")
    churn_table.add_column("File", style="cyan")
    churn_table.add_column("Commits", justify="right", style="green")
    churn_table.add_column("Lines changed", justify="right")
    for entry in churn_entries[:20]:
        churn_table.add_row(entry.file_path, str(entry.commit_count), str(entry.lines_changed))
    console.print(churn_table)

    if pairs:
        pair_table = Table(title=f"Behavioural coupling ({len(pairs)} pairs)")
        pair_table.add_column("File A", style="cyan")
        pair_table.add_column("File B", style="cyan")
        pair_table.add_column("Co-changes", justify="right", style="green")
        pair_table.add_column("Confidence", justify="right")
        for pair in pairs[:20]:
            pair_table.add_row(
                pair.file_a,
                pair.file_b,
                str(pair.co_change_count),
                f"{pair.confidence:.2f}",
            )
        console.print(pair_table)
    else:
        console.print(
            f"[yellow]No coupling pairs found[/yellow] "
            f"(min_co_changes={min_co_changes}, min_confidence={min_confidence})"
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
        "Start in read-only mode: disables ingest_repo and blocks write operations in query_graph."
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


# ── ANALYSIS: impact ──────────────────────────────────────────────────────────


@main.command()
@click.argument("name")
@click.option("--file", "file_path", default="", help="Narrow to a specific file.")
@click.option("--depth", default=3, show_default=True, help="Traversal depth.")
@DB_OPTION
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def impact(name: str, file_path: str, depth: int, db: str, as_json: bool):
    """Blast-radius analysis — what does changing NAME affect?

    Traverses CALLS, REFERENCES, INHERITS, IMPLEMENTS, ANNOTATES edges
    outward to find all downstream symbols and files affected by a change.
    """
    from navegador.analysis.impact import ImpactAnalyzer

    result = ImpactAnalyzer(_get_store(db)).blast_radius(name, file_path=file_path, depth=depth)

    if as_json:
        click.echo(json.dumps(result.to_dict(), indent=2))
        return

    console.print(f"[bold]Blast radius:[/bold] [cyan]{name}[/cyan] (depth={depth})")
    if not result.affected_nodes:
        console.print("[yellow]No affected nodes found.[/yellow]")
        return

    table = Table(title=f"Affected nodes ({len(result.affected_nodes)})")
    table.add_column("Type", style="cyan")
    table.add_column("Name", style="bold")
    table.add_column("File")
    table.add_column("Line", justify="right")
    for node in result.affected_nodes:
        table.add_row(node["type"], node["name"], node["file_path"], str(node["line_start"] or ""))
    console.print(table)

    if result.affected_files:
        console.print(f"\n[bold]Affected files ({len(result.affected_files)}):[/bold]")
        for fp in result.affected_files:
            console.print(f"  {fp}")

    if result.affected_knowledge:
        console.print(f"\n[bold]Affected knowledge ({len(result.affected_knowledge)}):[/bold]")
        for kn in result.affected_knowledge:
            console.print(f"  [{kn['type']}] {kn['name']}")


# ── ANALYSIS: architecture drift ──────────────────────────────────────────────


@main.command("drift")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON (CI-friendly).")
@click.option(
    "--fail-on-violations",
    is_flag=True,
    help="Exit with code 1 if any violations are found.",
)
@DB_OPTION
def drift(as_json: bool, fail_on_violations: bool, db: str):
    """Detect architecture drift — compare rules, ADRs, and memory against live code.

    Runs built-in checks derived from the knowledge layer and reports
    violations with concrete evidence. Use --fail-on-violations for CI gating.
    """
    from navegador.analysis.drift import DriftChecker

    report = DriftChecker(_get_store(db)).check()

    if as_json:
        click.echo(report.to_json())
    else:
        console.print(report.to_markdown())

    if fail_on_violations and report.has_violations:
        raise SystemExit(1)


# ── ANALYSIS: structural diff graph ───────────────────────────────────────────


@main.command("diff-graph")
@click.option("--base", default="HEAD", show_default=True, help="Base ref (branch, tag, SHA).")
@click.option("--head", default="working tree", show_default=True, help="Head ref to compare.")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
@click.option(
    "--repo-path",
    default=".",
    type=click.Path(exists=True),
    help="Path to the git repo.",
)
@DB_OPTION
def diff_graph(base: str, head: str, as_json: bool, repo_path: str, db: str):
    """Structural diff — what graph changes did this branch introduce?

    Reports new/changed symbols, blast-radius summary, and affected knowledge
    nodes for all lines changed between BASE and HEAD.

    \b
    Examples:
      navegador diff-graph                        # working tree vs HEAD
      navegador diff-graph --base main            # current branch vs main
      navegador diff-graph --base main --head HEAD
    """
    from navegador.analysis.diffgraph import DiffGraphAnalyzer

    analyzer = DiffGraphAnalyzer(_get_store(db), repo_path)
    if base == "HEAD" and head == "working tree":
        report = analyzer.diff_working_tree()
    else:
        report = analyzer.diff_refs(base=base, head=head)

    if as_json:
        click.echo(report.to_json())
    else:
        console.print(report.to_markdown())


# ── ANALYSIS: cross-repo blast radius ─────────────────────────────────────────


@main.command("cross-impact")
@click.argument("name")
@click.option("--file", "file_path", default="", help="Narrow to a specific file.")
@click.option("--repo", default="", help="Source repository name for attribution.")
@click.option("--depth", default=3, show_default=True, help="Traversal depth.")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
@DB_OPTION
def cross_impact(name: str, file_path: str, repo: str, depth: int, as_json: bool, db: str):
    """Cross-repo blast-radius — find impact across all repos in a unified graph.

    Traverses the graph across repository boundaries to find every downstream
    symbol, file, and repo that would be affected by changing NAME.

    Requires a unified workspace graph (navegador workspace ingest ...).
    """
    from navegador.analysis.crossrepo import CrossRepoImpactAnalyzer

    result = CrossRepoImpactAnalyzer(_get_store(db)).blast_radius(
        name, file_path=file_path, repo=repo, depth=depth
    )

    if as_json:
        click.echo(result.to_json())
        return

    console.print(result.to_markdown())


# ── ANALYSIS: flow trace ──────────────────────────────────────────────────────


@main.command()
@click.argument("name")
@click.option("--file", "file_path", default="", help="Narrow to a specific file.")
@click.option("--depth", default=10, show_default=True, help="Maximum call depth.")
@DB_OPTION
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def trace(name: str, file_path: str, depth: int, db: str, as_json: bool):
    """Execution flow trace — follow call chains from an entry point.

    Traverses CALLS edges forward from NAME, returning all execution paths
    up to the given depth.
    """
    from navegador.analysis.flow import FlowTracer

    chains = FlowTracer(_get_store(db)).trace(name, file_path=file_path, max_depth=depth)

    if as_json:
        click.echo(json.dumps([c.to_list() for c in chains], indent=2))
        return

    if not chains:
        console.print(f"[yellow]No call chains found from[/yellow] [cyan]{name}[/cyan].")
        return

    console.print(f"[bold]Call chains from[/bold] [cyan]{name}[/cyan] — {len(chains)} path(s)")
    for i, chain in enumerate(chains, 1):
        steps = chain.to_list()
        path_str = (
            " → ".join([steps[0]["caller"]] + [s["callee"] for s in steps]) if steps else name
        )
        console.print(f"  {i}. {path_str}")


# ── ANALYSIS: dead code ───────────────────────────────────────────────────────


@main.command()
@DB_OPTION
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def deadcode(db: str, as_json: bool):
    """Detect dead code — unreachable functions, classes, and orphan files.

    A function/class is dead if nothing calls, references, or imports it.
    An orphan file is one that no other file imports.
    """
    from navegador.analysis.deadcode import DeadCodeDetector

    report = DeadCodeDetector(_get_store(db)).detect()

    if as_json:
        click.echo(json.dumps(report.to_dict(), indent=2))
        return

    summary = report.to_dict()["summary"]
    console.print(
        f"[bold]Dead code report:[/bold] "
        f"{summary['unreachable_functions']} dead functions, "
        f"{summary['unreachable_classes']} dead classes, "
        f"{summary['orphan_files']} orphan files"
    )

    if report.unreachable_functions:
        fn_table = Table(
            title=f"Unreachable functions/methods ({len(report.unreachable_functions)})"
        )
        fn_table.add_column("Type", style="cyan")
        fn_table.add_column("Name", style="bold")
        fn_table.add_column("File")
        fn_table.add_column("Line", justify="right")
        for fn in report.unreachable_functions:
            fn_table.add_row(fn["type"], fn["name"], fn["file_path"], str(fn["line_start"] or ""))
        console.print(fn_table)

    if report.unreachable_classes:
        cls_table = Table(title=f"Unreachable classes ({len(report.unreachable_classes)})")
        cls_table.add_column("Name", style="bold")
        cls_table.add_column("File")
        cls_table.add_column("Line", justify="right")
        for cls in report.unreachable_classes:
            cls_table.add_row(cls["name"], cls["file_path"], str(cls["line_start"] or ""))
        console.print(cls_table)

    if report.orphan_files:
        console.print(f"\n[bold]Orphan files ({len(report.orphan_files)}):[/bold]")
        for fp in report.orphan_files:
            console.print(f"  {fp}")

    if not any([report.unreachable_functions, report.unreachable_classes, report.orphan_files]):
        console.print("[green]No dead code found.[/green]")


# ── ANALYSIS: test mapping ────────────────────────────────────────────────────


@main.command()
@DB_OPTION
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def testmap(db: str, as_json: bool):
    """Map test functions to production code via TESTS edges.

    Finds functions starting with test_, resolves the production symbol
    via CALLS edges and name heuristics, then writes TESTS edges to the graph.
    """
    from navegador.analysis.testmap import TestMapper

    result = TestMapper(_get_store(db)).map_tests()

    if as_json:
        click.echo(json.dumps(result.to_dict(), indent=2))
        return

    console.print(
        f"[bold]Test map:[/bold] {len(result.links)} linked, "
        f"{len(result.unmatched_tests)} unmatched, "
        f"{result.edges_created} TESTS edges created"
    )

    if result.links:
        table = Table(title=f"Test -> production links ({len(result.links)})")
        table.add_column("Test", style="cyan")
        table.add_column("Production symbol", style="bold")
        table.add_column("File")
        table.add_column("Source")
        for lnk in result.links:
            table.add_row(lnk.test_name, lnk.prod_name, lnk.prod_file, lnk.source)
        console.print(table)

    if result.unmatched_tests:
        console.print(f"\n[yellow]Unmatched tests ({len(result.unmatched_tests)}):[/yellow]")
        for t in result.unmatched_tests:
            console.print(f"  {t['name']}  ({t['file_path']})")


# ── ANALYSIS: cycles ──────────────────────────────────────────────────────────


@main.command()
@DB_OPTION
@click.option(
    "--imports", "check_imports", is_flag=True, default=False, help="Check import cycles only."
)
@click.option("--calls", "check_calls", is_flag=True, default=False, help="Check call cycles only.")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def cycles(db: str, check_imports: bool, check_calls: bool, as_json: bool):
    """Detect circular dependencies in import and call graphs.

    By default checks both import cycles and call cycles.
    Use --imports or --calls to restrict to one graph.
    """
    from navegador.analysis.cycles import CycleDetector

    detector = CycleDetector(_get_store(db))
    run_imports = check_imports or (not check_imports and not check_calls)
    run_calls = check_calls or (not check_imports and not check_calls)

    import_cycles = detector.detect_import_cycles() if run_imports else []
    call_cycles = detector.detect_call_cycles() if run_calls else []

    if as_json:
        click.echo(
            json.dumps({"import_cycles": import_cycles, "call_cycles": call_cycles}, indent=2)
        )
        return

    if not import_cycles and not call_cycles:
        console.print("[green]No circular dependencies found.[/green]")
        return

    if import_cycles:
        table = Table(title=f"Import cycles ({len(import_cycles)})")
        table.add_column("#", justify="right")
        table.add_column("Cycle")
        for i, cycle in enumerate(import_cycles, 1):
            table.add_row(str(i), " -> ".join(cycle) + f" -> {cycle[0]}")
        console.print(table)

    if call_cycles:
        table = Table(title=f"Call cycles ({len(call_cycles)})")
        table.add_column("#", justify="right")
        table.add_column("Cycle")
        for i, cycle in enumerate(call_cycles, 1):
            table.add_row(str(i), " -> ".join(cycle) + f" -> {cycle[0]}")
        console.print(table)


# ── Multi-repo (#16) ─────────────────────────────────────────────────────────


@main.group()
def repo():
    """Manage and query across multiple repositories."""


@repo.command("add")
@click.argument("name")
@click.argument("path", type=click.Path())
@DB_OPTION
def repo_add(name: str, path: str, db: str):
    """Register a repository by NAME and PATH."""
    from navegador.multirepo import MultiRepoManager

    mgr = MultiRepoManager(_get_store(db))
    mgr.add_repo(name, path)
    console.print(f"[green]Repo registered:[/green] {name} → {path}")


@repo.command("list")
@DB_OPTION
@click.option("--json", "as_json", is_flag=True)
def repo_list(db: str, as_json: bool):
    """List all registered repositories."""
    from navegador.multirepo import MultiRepoManager

    repos = MultiRepoManager(_get_store(db)).list_repos()
    if as_json:
        click.echo(json.dumps(repos, indent=2))
        return
    if not repos:
        console.print("[yellow]No repositories registered.[/yellow]")
        return
    table = Table(title="Registered repositories")
    table.add_column("Name", style="cyan")
    table.add_column("Path")
    for r in repos:
        table.add_row(r["name"], r["path"])
    console.print(table)


@repo.command("ingest-all")
@DB_OPTION
@click.option("--clear", is_flag=True, help="Clear graph before ingesting.")
@click.option("--json", "as_json", is_flag=True)
def repo_ingest_all(db: str, clear: bool, as_json: bool):
    """Ingest all registered repositories."""
    from navegador.multirepo import MultiRepoManager

    mgr = MultiRepoManager(_get_store(db))
    with console.status("[bold]Ingesting all repos…[/bold]"):
        summary = mgr.ingest_all(clear=clear)
    if as_json:
        click.echo(json.dumps(summary, indent=2))
        return
    for name, stats in summary.items():
        table = Table(title=f"Repo: {name}")
        table.add_column("Metric", style="cyan")
        table.add_column("Count", justify="right", style="green")
        for k, v in stats.items():
            table.add_row(str(k).capitalize(), str(v))
        console.print(table)


@repo.command("search")
@click.argument("query")
@DB_OPTION
@click.option("--limit", default=20, show_default=True)
@click.option("--json", "as_json", is_flag=True)
def repo_search(query: str, db: str, limit: int, as_json: bool):
    """Search across all registered repositories."""
    from navegador.multirepo import MultiRepoManager

    results = MultiRepoManager(_get_store(db)).cross_repo_search(query, limit=limit)
    if as_json:
        click.echo(json.dumps(results, indent=2))
        return
    if not results:
        console.print("[yellow]No results.[/yellow]")
        return
    table = Table(title=f"Cross-repo search: {query!r}")
    table.add_column("Label", style="cyan")
    table.add_column("Name", style="bold")
    table.add_column("File/Path")
    for r in results:
        table.add_row(r["label"], r["name"], r["file_path"])
    console.print(table)


# ── Rename (#26) ──────────────────────────────────────────────────────────────


@main.command()
@click.argument("old_name")
@click.argument("new_name")
@DB_OPTION
@click.option("--preview", is_flag=True, help="Show what would change without applying.")
@click.option("--json", "as_json", is_flag=True)
def rename(old_name: str, new_name: str, db: str, preview: bool, as_json: bool):
    """Rename a symbol across the graph (coordinated rename).

    \b
    Examples:
      navegador rename old_func new_func --preview
      navegador rename MyClass RenamedClass
    """
    from navegador.refactor import SymbolRenamer

    renamer = SymbolRenamer(_get_store(db))
    if preview:
        result = renamer.preview_rename(old_name, new_name)
        data = {
            "old_name": result.old_name,
            "new_name": result.new_name,
            "affected_files": result.affected_files,
            "affected_nodes": len(result.affected_nodes),
            "edges_updated": result.edges_updated,
        }
    else:
        result = renamer.apply_rename(old_name, new_name)
        data = {
            "old_name": result.old_name,
            "new_name": result.new_name,
            "affected_files": result.affected_files,
            "affected_nodes": len(result.affected_nodes),
            "edges_updated": result.edges_updated,
        }

    if as_json:
        click.echo(json.dumps(data, indent=2))
        return

    action = "Preview" if preview else "Renamed"
    console.print(f"[green]{action}:[/green] {old_name!r} → {new_name!r}")
    console.print(f"  Nodes affected : {data['affected_nodes']}")
    console.print(f"  Edges updated  : {data['edges_updated']}")
    if data["affected_files"]:
        console.print("  Files:")
        for f in data["affected_files"]:
            console.print(f"    {f}")


# ── CODEOWNERS (#39) ──────────────────────────────────────────────────────────


@main.command()
@click.argument("repo_path", type=click.Path(exists=True))
@DB_OPTION
@click.option("--json", "as_json", is_flag=True)
def codeowners(repo_path: str, db: str, as_json: bool):
    """Parse CODEOWNERS and map ownership to Person nodes."""
    from navegador.codeowners import CodeownersIngester

    stats = CodeownersIngester(_get_store(db)).ingest(repo_path)
    if as_json:
        click.echo(json.dumps(stats, indent=2))
        return
    console.print(
        f"[green]CODEOWNERS ingested:[/green] "
        f"{stats['owners']} owners, {stats['patterns']} patterns, {stats['edges']} edges"
    )


# ── ADR (#40) ─────────────────────────────────────────────────────────────────


@main.group()
def adr():
    """Ingest Architecture Decision Records (ADRs) into the knowledge graph."""


@adr.command("ingest")
@click.argument("adr_dir", type=click.Path(exists=True))
@DB_OPTION
@click.option("--json", "as_json", is_flag=True)
def adr_ingest(adr_dir: str, db: str, as_json: bool):
    """Parse ADR markdown files and create Decision nodes."""
    from navegador.adr import ADRIngester

    stats = ADRIngester(_get_store(db)).ingest(adr_dir)
    if as_json:
        click.echo(json.dumps(stats, indent=2))
        return
    console.print(
        f"[green]ADRs ingested:[/green] {stats['decisions']} decisions, {stats['skipped']} skipped"
    )


# ── API schema (#41) ─────────────────────────────────────────────────────────


@main.group()
def api():
    """Ingest API schema files (OpenAPI, GraphQL) into the graph."""


@api.command("ingest")
@click.argument("path", type=click.Path(exists=True))
@DB_OPTION
@click.option(
    "--type",
    "schema_type",
    type=click.Choice(["openapi", "graphql", "auto"]),
    default="auto",
    show_default=True,
    help="Schema type. auto detects from file extension.",
)
@click.option("--json", "as_json", is_flag=True)
def api_ingest(path: str, db: str, schema_type: str, as_json: bool):
    """Parse an OpenAPI or GraphQL schema and create API endpoint nodes.

    \b
    Examples:
      navegador api ingest openapi.yaml
      navegador api ingest schema.graphql --type graphql
      navegador api ingest swagger.json --type openapi
    """
    from pathlib import Path as P

    from navegador.api_schema import APISchemaIngester

    ingester = APISchemaIngester(_get_store(db))
    p = P(path)

    if schema_type == "auto":
        if p.suffix.lower() in (".graphql", ".gql"):
            schema_type = "graphql"
        else:
            schema_type = "openapi"

    if schema_type == "graphql":
        stats = ingester.ingest_graphql(path)
        label = "GraphQL"
    else:
        stats = ingester.ingest_openapi(path)
        label = "OpenAPI"

    if as_json:
        click.echo(json.dumps(stats, indent=2))
        return

    table = Table(title=f"{label} schema ingested")
    table.add_column("Metric", style="cyan")
    table.add_column("Count", justify="right", style="green")
    for k, v in stats.items():
        table.add_row(k.replace("_", " ").capitalize(), str(v))
    console.print(table)


# ── PM: project management ticket ingestion (#53) ─────────────────────────────


@main.group()
def pm():
    """Ingest project management tickets (GitHub Issues, Linear, Jira)."""


@pm.command("ingest")
@click.option(
    "--github",
    "github_repo",
    default="",
    metavar="OWNER/REPO",
    help="GitHub repository in owner/repo format.",
)
@click.option("--token", default="", envvar="GITHUB_TOKEN", help="GitHub personal access token.")
@click.option(
    "--state",
    default="open",
    type=click.Choice(["open", "closed", "all"]),
    show_default=True,
    help="GitHub issue state filter.",
)
@click.option("--limit", default=100, show_default=True, help="Maximum number of issues to fetch.")
@DB_OPTION
@click.option("--json", "as_json", is_flag=True)
def pm_ingest(github_repo: str, token: str, state: str, limit: int, db: str, as_json: bool):
    """Ingest tickets from a PM tool into the knowledge graph.

    \b
    Examples:
      navegador pm ingest --github owner/repo
      navegador pm ingest --github owner/repo --token ghp_...
      navegador pm ingest --github owner/repo --state all --limit 200
    """
    if not github_repo:
        raise click.UsageError(
            "Provide --github <owner/repo> (more backends coming in a future release)."
        )

    from navegador.pm import TicketIngester

    ing = TicketIngester(_get_store(db))
    stats = ing.ingest_github_issues(github_repo, token=token, state=state, limit=limit)

    if as_json:
        click.echo(json.dumps(stats, indent=2))
    else:
        table = Table(title=f"PM import: {github_repo}")
        table.add_column("Metric", style="cyan")
        table.add_column("Count", justify="right", style="green")
        for k, v in stats.items():
            table.add_row(k.capitalize(), str(v))
        console.print(table)


# ── Dependencies: external package ingestion (#58) ────────────────────────────


@main.group()
def deps():
    """Ingest external package dependencies (npm, pip, cargo)."""


@deps.command("ingest")
@click.argument("path", type=click.Path(exists=True))
@click.option(
    "--type",
    "dep_type",
    type=click.Choice(["auto", "npm", "pip", "cargo"]),
    default="auto",
    show_default=True,
    help="Manifest type. auto detects from filename.",
)
@DB_OPTION
@click.option("--json", "as_json", is_flag=True)
def deps_ingest(path: str, dep_type: str, db: str, as_json: bool):
    """Ingest external dependencies from a package manifest.

    \b
    PATH can be:
      package.json         (npm)
      requirements.txt     (pip)
      pyproject.toml       (pip)
      Cargo.toml           (cargo)

    \b
    Examples:
      navegador deps ingest package.json
      navegador deps ingest requirements.txt
      navegador deps ingest Cargo.toml --type cargo
    """
    from pathlib import Path as P

    from navegador.dependencies import DependencyIngester

    ing = DependencyIngester(_get_store(db))
    p = P(path)

    if dep_type == "auto":
        name = p.name.lower()
        if name == "package.json":
            dep_type = "npm"
        elif name in ("requirements.txt", "pyproject.toml"):
            dep_type = "pip"
        elif name == "cargo.toml":
            dep_type = "cargo"
        else:
            raise click.UsageError(
                f"Cannot auto-detect type for {p.name!r}. Use --type npm|pip|cargo."
            )

    dispatch = {
        "npm": ing.ingest_npm,
        "pip": ing.ingest_pip,
        "cargo": ing.ingest_cargo,
    }
    stats = dispatch[dep_type](path)

    if as_json:
        click.echo(json.dumps(stats, indent=2))
    else:
        console.print(
            f"[green]Dependencies ingested[/green] ({dep_type}): {stats['packages']} packages"
        )


# ── Submodules: ingest parent + submodules (#61) ──────────────────────────────


@main.group()
def submodules():
    """Ingest a parent repository and all its git submodules."""


@submodules.command("ingest")
@click.argument("repo_path", type=click.Path(exists=True))
@DB_OPTION
@click.option("--clear", is_flag=True, help="Clear existing graph before ingesting.")
@click.option("--json", "as_json", is_flag=True)
def submodules_ingest(repo_path: str, db: str, clear: bool, as_json: bool):
    """Ingest a repository and all its git submodules as linked nodes.

    \b
    Examples:
      navegador submodules ingest .
      navegador submodules ingest /path/to/repo --clear
    """
    from navegador.submodules import SubmoduleIngester

    ing = SubmoduleIngester(_get_store(db))
    stats = ing.ingest_with_submodules(repo_path, clear=clear)

    if as_json:
        click.echo(json.dumps(stats, indent=2))
    else:
        sub_names = list(stats.get("submodules", {}).keys())
        console.print(
            f"[green]Submodule ingestion complete[/green]: "
            f"{stats.get('total_files', 0)} total files, "
            f"{len(sub_names)} submodule(s)"
        )
        if sub_names:
            console.print("  Submodules: " + ", ".join(sub_names))


@submodules.command("list")
@click.argument("repo_path", type=click.Path(exists=True), default=".")
def submodules_list(repo_path: str):
    """List git submodules found in REPO_PATH."""
    from navegador.submodules import SubmoduleIngester

    subs = SubmoduleIngester.__new__(SubmoduleIngester)
    subs.store = None  # type: ignore[assignment]
    items = subs.detect_submodules(repo_path)

    if not items:
        console.print("[yellow]No submodules found (no .gitmodules).[/yellow]")
        return

    table = Table(title=f"Submodules in {repo_path}")
    table.add_column("Name", style="cyan")
    table.add_column("Path")
    table.add_column("URL")
    for item in items:
        table.add_row(item["name"], item["path"], item.get("url", ""))
    console.print(table)


# ── Workspace: multi-repo (#62) ────────────────────────────────────────────────


@main.group()
def workspace():
    """Manage a multi-repo workspace (unified or federated graph)."""


@workspace.command("ingest")
@click.argument("repos", nargs=-1, metavar="NAME=PATH ...")
@click.option(
    "--mode",
    type=click.Choice(["unified", "federated"]),
    default="unified",
    show_default=True,
    help="Graph mode: unified (shared graph) or federated (per-repo graphs).",
)
@DB_OPTION
@click.option("--clear", is_flag=True)
@click.option("--json", "as_json", is_flag=True)
def workspace_ingest(repos: tuple, mode: str, db: str, clear: bool, as_json: bool):
    """Ingest multiple repositories as a workspace.

    \b
    REPOS is a list of NAME=PATH pairs, e.g.:
      navegador workspace ingest backend=/path/to/backend frontend=/path/to/frontend

    \b
    Examples:
      navegador workspace ingest backend=. frontend=../frontend --mode unified
      navegador workspace ingest api=./api worker=./worker --mode federated
    """
    from navegador.multirepo import WorkspaceManager, WorkspaceMode

    if not repos:
        raise click.UsageError("Provide at least one NAME=PATH repo.")

    wm = WorkspaceManager(_get_store(db), mode=WorkspaceMode(mode))
    for repo_spec in repos:
        if "=" not in repo_spec:
            raise click.UsageError(f"Invalid repo spec {repo_spec!r}. Expected NAME=PATH format.")
        name, path = repo_spec.split("=", 1)
        wm.add_repo(name.strip(), path.strip())

    stats = wm.ingest_all(clear=clear)

    if as_json:
        click.echo(json.dumps(stats, indent=2))
    else:
        for repo_name, repo_stats in stats.items():
            if "error" in repo_stats:
                console.print(f"[red]Error ingesting {repo_name}:[/red] {repo_stats['error']}")
            else:
                console.print(
                    f"[green]{repo_name}[/green]: "
                    f"{repo_stats.get('files', 0)} files, "
                    f"{repo_stats.get('nodes', 0)} nodes"
                )


# ── Task packs ────────────────────────────────────────────────────────────────


@main.command("pack")
@click.argument("target")
@click.option("--file", "file_path", default="", help="Narrow to a specific file.")
@click.option(
    "--mode",
    default="implement",
    type=click.Choice(["implement", "review", "debug", "refactor"]),
    show_default=True,
    help="Agent workflow mode — shapes which context is prioritised.",
)
@click.option("--depth", default=2, show_default=True, help="Call graph traversal depth.")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
@DB_OPTION
def pack(target: str, file_path: str, mode: str, depth: int, as_json: bool, db: str):
    """Build a compact task pack for a symbol or file.

    TARGET can be a symbol name (function, class) or a relative file path.

    \b
    Examples:
      navegador pack validate_token --file app/auth.py
      navegador pack app/payments/service.py
      navegador pack AuthService --mode review
    """
    from navegador.taskpack import TaskPackBuilder

    store = _get_store(db)
    builder = TaskPackBuilder(store)

    # Treat as file if TARGET looks like a path
    if "/" in target or target.endswith((".py", ".ts", ".go", ".rb", ".java")):
        pack_obj = builder.for_file(target, mode=mode)
    else:
        pack_obj = builder.for_symbol(target, file_path=file_path, depth=depth, mode=mode)

    if as_json:
        click.echo(pack_obj.to_json())
    else:
        console.print(pack_obj.to_markdown())


# ── Intelligence: semantic search ─────────────────────────────────────────────


@main.command("semantic-search")
@click.argument("query")
@DB_OPTION
@click.option("--limit", default=10, show_default=True, help="Maximum results to return.")
@click.option(
    "--index",
    "do_index",
    is_flag=True,
    help="(Re-)build the embedding index before searching.",
)
@click.option(
    "--provider",
    "llm_provider",
    default="",
    help="LLM provider to use (anthropic, openai, ollama). Auto-detected if omitted.",
)
@click.option("--model", "llm_model", default="", help="LLM model name.")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def semantic_search(
    query: str,
    db: str,
    limit: int,
    do_index: bool,
    llm_provider: str,
    llm_model: str,
    as_json: bool,
):
    """Semantic similarity search using embeddings.

    Embeds QUERY and returns the most similar symbols from the graph.
    Use --index to (re-)build the embedding index before searching.

    \b
    Examples:
      navegador semantic-search "validates JWT tokens"
      navegador semantic-search "database connection" --index --provider openai
    """
    from navegador.intelligence.search import SemanticSearch
    from navegador.llm import auto_provider, get_provider

    store = _get_store(db)
    provider = (
        get_provider(llm_provider, model=llm_model)
        if llm_provider
        else auto_provider(model=llm_model)
    )
    ss = SemanticSearch(store, provider)

    if do_index:
        n = ss.index()
        if not as_json:
            console.print(f"[green]Indexed[/green] {n} nodes.")

    results = ss.search(query, limit=limit)

    if as_json:
        click.echo(json.dumps(results, indent=2))
        return

    if not results:
        console.print("[yellow]No results found.  Try --index to build the index first.[/yellow]")
        return

    table = Table(title=f"Semantic search: {query!r}")
    table.add_column("Score", style="cyan", justify="right")
    table.add_column("Type", style="yellow")
    table.add_column("Name", style="bold")
    table.add_column("File", style="dim")
    for r in results:
        table.add_row(
            f"{r['score']:.3f}",
            r.get("type", ""),
            r.get("name", ""),
            r.get("file_path", ""),
        )
    console.print(table)


# ── Intelligence: community detection ─────────────────────────────────────────


@main.command("communities")
@DB_OPTION
@click.option("--min-size", default=2, show_default=True, help="Minimum community size.")
@click.option("--store-labels", is_flag=True, help="Write community labels back onto nodes.")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def communities(db: str, min_size: int, store_labels: bool, as_json: bool):
    """Detect communities in the graph via label propagation.

    \b
    Examples:
      navegador communities
      navegador communities --min-size 3 --store-labels
    """
    from navegador.intelligence.community import CommunityDetector

    store = _get_store(db)
    detector = CommunityDetector(store)
    detected = detector.detect(min_size=min_size)

    if store_labels:
        n = detector.store_communities()
        if not as_json:
            console.print(f"[green]Community labels written to[/green] {n} nodes.")

    if as_json:
        click.echo(
            json.dumps(
                [
                    {
                        "name": c.name,
                        "members": c.members,
                        "size": c.size,
                        "density": c.density,
                    }
                    for c in detected
                ],
                indent=2,
            )
        )
        return

    if not detected:
        console.print("[yellow]No communities found (graph may be empty).[/yellow]")
        return

    table = Table(title=f"Communities (min_size={min_size})")
    table.add_column("Community", style="cyan")
    table.add_column("Size", justify="right", style="green")
    table.add_column("Density", justify="right", style="yellow")
    table.add_column("Members (preview)", style="dim")
    for c in detected:
        preview = ", ".join(c.members[:5])
        if c.size > 5:
            preview += f" …+{c.size - 5}"
        table.add_row(c.name, str(c.size), f"{c.density:.3f}", preview)
    console.print(table)


# ── Intelligence: natural language query ──────────────────────────────────────


@main.command("ask")
@click.argument("question")
@DB_OPTION
@click.option(
    "--provider",
    "llm_provider",
    default="",
    help="LLM provider (anthropic, openai, ollama). Auto-detected if omitted.",
)
@click.option("--model", "llm_model", default="", help="LLM model name.")
def ask(question: str, db: str, llm_provider: str, llm_model: str):
    """Ask a natural-language question about the codebase.

    Converts the question to Cypher, executes it, and returns a
    human-readable answer.

    \b
    Examples:
      navegador ask "Which functions call authenticate_user?"
      navegador ask "What concepts are in the auth domain?"
    """
    from navegador.intelligence.nlp import NLPEngine
    from navegador.llm import auto_provider, get_provider

    store = _get_store(db)
    provider = (
        get_provider(llm_provider, model=llm_model)
        if llm_provider
        else auto_provider(model=llm_model)
    )
    engine = NLPEngine(store, provider)

    with console.status("[bold]Thinking...[/bold]"):
        answer = engine.natural_query(question)

    console.print(answer)


# ── Intelligence: generate docs ───────────────────────────────────────────────


@main.command("generate-docs")
@click.argument("name")
@DB_OPTION
@click.option(
    "--provider",
    "llm_provider",
    default="",
    help="LLM provider (anthropic, openai, ollama). Auto-detected if omitted.",
)
@click.option("--model", "llm_model", default="", help="LLM model name.")
@click.option("--file", "file_path", default="", help="Narrow to a specific file.")
def generate_docs_cmd(name: str, db: str, llm_provider: str, llm_model: str, file_path: str):
    """Generate LLM-powered documentation for a named symbol.

    \b
    Examples:
      navegador generate-docs authenticate_user
      navegador generate-docs GraphStore --file navegador/graph/store.py
    """
    from navegador.intelligence.nlp import NLPEngine
    from navegador.llm import auto_provider, get_provider

    store = _get_store(db)
    provider = (
        get_provider(llm_provider, model=llm_model)
        if llm_provider
        else auto_provider(model=llm_model)
    )
    engine = NLPEngine(store, provider)

    with console.status("[bold]Generating docs...[/bold]"):
        docs = engine.generate_docs(name, file_path=file_path)

    console.print(docs)


# ── Intelligence: docs (template + LLM) ──────────────────────────────────────


@main.command("docs")
@click.argument("target")
@DB_OPTION
@click.option("--project", is_flag=True, help="Generate full project documentation.")
@click.option(
    "--provider",
    "llm_provider",
    default="",
    help="LLM provider (anthropic, openai, ollama). Template mode if omitted.",
)
@click.option("--model", "llm_model", default="", help="LLM model name.")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON (wraps markdown in a dict).")
def docs(target: str, db: str, project: bool, llm_provider: str, llm_model: str, as_json: bool):
    """Generate markdown documentation from the graph.

    TARGET can be a file path or a module name (dotted or partial).
    Use --project to generate full project docs instead.

    \b
    Examples:
      navegador docs navegador/graph/store.py
      navegador docs navegador.graph
      navegador docs . --project
      navegador docs . --project --provider openai
    """
    from navegador.intelligence.docgen import DocGenerator

    store = _get_store(db)

    provider = None
    if llm_provider:
        from navegador.llm import get_provider

        provider = get_provider(llm_provider, model=llm_model)

    gen = DocGenerator(store, provider=provider)

    if project:
        with console.status("[bold]Generating project docs...[/bold]"):
            output = gen.generate_project_docs()
    elif "/" in target or target.endswith(".py"):
        with console.status(f"[bold]Generating docs for file[/bold] {target}..."):
            output = gen.generate_file_docs(target)
    else:
        with console.status(f"[bold]Generating docs for module[/bold] {target}..."):
            output = gen.generate_module_docs(target)

    if as_json:
        click.echo(json.dumps({"docs": output}, indent=2))
    else:
        console.print(output)


# ── History: time-travel graph (#78) ─────────────────────────────────────────


@main.command("snapshot")
@click.argument("ref", default="HEAD")
@DB_OPTION
def snapshot(ref: str, db: str):
    """
    Capture a graph snapshot for a git ref.

    Links all Function/Class/Method nodes currently in the graph to
    a Snapshot node keyed by REF.  Ingest the ref first if you want
    a faithful before/after comparison.

    Examples::

      navegador snapshot HEAD
      navegador snapshot v1.0.0
      navegador snapshot main
    """
    from navegador.history import HistoryStore

    store = _get_store(db)
    h = HistoryStore(store)
    info = h.snapshot(ref)
    console.print(
        f"[green]Snapshot[/green] [bold]{info.ref}[/bold] "
        f"({info.commit_sha}) — {info.symbol_count} symbols"
    )


@main.command("history")
@click.argument("name")
@click.option("--file", "file_path", default="", help="Narrow to a specific file.")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
@DB_OPTION
def history_cmd(name: str, file_path: str, as_json: bool, db: str):
    """
    Show history of a symbol across graph snapshots.

    Displays first-seen, moves, and removal events for NAME across
    all recorded snapshots.

    Examples::

      navegador history AuthService
      navegador history parse_token --file app/auth.py
    """
    from navegador.history import HistoryStore

    store = _get_store(db)
    report = HistoryStore(store).history(name, file_path=file_path)
    if as_json:
        click.echo(report.to_json())
    else:
        console.print(report.to_markdown())


@main.command("graph-at")
@click.argument("ref")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
@DB_OPTION
def graph_at(ref: str, as_json: bool, db: str):
    """
    List all symbols captured in a snapshot at REF.

    Examples::

      navegador graph-at v1.0.0
      navegador graph-at main --json
    """
    from navegador.history import HistoryStore

    store = _get_store(db)
    symbols = HistoryStore(store).symbols_at(ref)
    if as_json:
        click.echo(json.dumps([s.__dict__ for s in symbols], indent=2))
    else:
        if not symbols:
            console.print(f"[yellow]No snapshot found for ref[/yellow] [bold]{ref}[/bold]")
            return
        console.print(
            f"[bold]{len(symbols)}[/bold] symbols at [bold]{ref}[/bold]\n"
        )
        for s in symbols:
            console.print(f"  [{s.label}] {s.name}  [dim]{s.file_path}[/dim]")


@main.command("lineage")
@click.argument("name")
@click.option("--file", "file_path", default="", help="Narrow to a specific file.")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
@DB_OPTION
def lineage_cmd(name: str, file_path: str, as_json: bool, db: str):
    """
    Trace the lineage of a symbol across snapshots.

    Detects renames and moves using name and path similarity.

    Examples::

      navegador lineage AuthService
      navegador lineage parse_token --file app/auth.py
    """
    from navegador.history import HistoryStore

    store = _get_store(db)
    report = HistoryStore(store).lineage(name, file_path=file_path)
    if as_json:
        click.echo(report.to_json())
    else:
        console.print(report.to_markdown())


# ── DocLink: confidence-ranked doc-to-code linking ───────────────────────────


@main.group()
def doclink():
    """Confidence-ranked linking from documentation to code symbols."""


@doclink.command("suggest")
@click.option(
    "--min-confidence",
    type=float,
    default=0.5,
    show_default=True,
    help="Minimum confidence threshold.",
)
@click.option(
    "--strategy",
    type=str,
    default="",
    help="Filter by strategy (EXACT_NAME, FUZZY, SEMANTIC).",
)
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
@DB_OPTION
def doclink_suggest(min_confidence: float, strategy: str, as_json: bool, db: str):
    """List doc-to-code link candidates above a confidence threshold.

    Scans documentation nodes (Document, WikiPage, Decision, Rule) and
    suggests confidence-ranked links to code symbols (Function, Class,
    Method, Concept).

    \b
    Examples:
      navegador doclink suggest
      navegador doclink suggest --min-confidence 0.8
      navegador doclink suggest --strategy EXACT_NAME --json
    """
    from navegador.intelligence.doclink import DocLinker

    store = _get_store(db)
    linker = DocLinker(store)
    candidates = linker.suggest_links(min_confidence=min_confidence)

    if strategy:
        candidates = [c for c in candidates if c.strategy == strategy]

    if as_json:
        click.echo(json.dumps([c.__dict__ for c in candidates], indent=2))
        return

    if not candidates:
        console.print("No link candidates found.")
        return

    table = Table(title="Doc Link Candidates")
    table.add_column("Source", style="bold")
    table.add_column("Target", style="cyan")
    table.add_column("File", style="dim")
    table.add_column("Strategy")
    table.add_column("Confidence", justify="right")
    for c in candidates:
        table.add_row(
            c.source_name,
            c.target_name,
            c.target_file,
            c.strategy,
            f"{c.confidence:.2f}",
        )
    console.print(table)


@doclink.command("accept")
@click.argument("source")
@click.argument("target")
@click.option(
    "--edge-type",
    type=str,
    default="DOCUMENTS",
    show_default=True,
    help="Edge type for the accepted link.",
)
@DB_OPTION
def doclink_accept(source: str, target: str, edge_type: str, db: str):
    """Accept a single doc-to-code link candidate.

    SOURCE and TARGET are node names. If a matching candidate is found
    via suggest_links(), its metadata is preserved; otherwise a link
    with confidence=1.0 is created.

    \b
    Examples:
      navegador doclink accept "API Guide" "AuthService"
      navegador doclink accept "README" "parse_token" --edge-type ANNOTATES
    """
    from navegador.intelligence.doclink import DocLinker, LinkCandidate

    store = _get_store(db)
    linker = DocLinker(store)

    # Try to find an existing candidate to preserve metadata
    candidates = linker.suggest_links(min_confidence=0.0)
    match = next(
        (c for c in candidates if c.source_name == source and c.target_name == target),
        None,
    )

    if match:
        match.edge_type = edge_type
        linker.accept(match)
    else:
        candidate = LinkCandidate(
            source_label="Document",
            source_name=source,
            target_label="Function",
            target_name=target,
            edge_type=edge_type,
            confidence=1.0,
            strategy="MANUAL",
            rationale="manually accepted via CLI",
        )
        linker.accept(candidate)

    console.print(f"Accepted: {source} -> {target}")


@doclink.command("accept-all")
@click.option(
    "--min-confidence",
    type=float,
    default=0.8,
    show_default=True,
    help="Minimum confidence threshold for acceptance.",
)
@click.option("--dry-run", is_flag=True, help="Preview without writing edges.")
@DB_OPTION
def doclink_accept_all(min_confidence: float, dry_run: bool, db: str):
    """Bulk-accept doc-to-code link candidates above a confidence threshold.

    \b
    Examples:
      navegador doclink accept-all
      navegador doclink accept-all --min-confidence 0.9
      navegador doclink accept-all --dry-run
    """
    from navegador.intelligence.doclink import DocLinker

    store = _get_store(db)
    linker = DocLinker(store)
    candidates = linker.suggest_links(min_confidence=min_confidence)

    if dry_run:
        console.print(
            f"[yellow]Dry run:[/yellow] would accept {len(candidates)} links "
            f"(min_confidence={min_confidence})"
        )
        return

    count = linker.accept_all(candidates, min_confidence=min_confidence)
    console.print(
        f"[green]Accepted[/green] {count} doc links (min_confidence={min_confidence})"
    )
