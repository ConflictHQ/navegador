"""
Navegador CLI — the single interface to your project's knowledge graph.

  CODE:      ingest, context, function, class, search, query
  KNOWLEDGE: add (concept/rule/decision/person/domain), wiki, annotate, domain
  UNIVERSAL: explain, search (spans both layers), stats
"""

import json
import logging
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

# asyncio and rich.markdown are imported where they are used, not here. They
# cost ~95ms of the ~137ms it took to import this module, and every CLI
# invocation paid it — including the agent hooks, which shell out per call
# (#190). Only the MCP server needs asyncio; only `manual` renders markdown.

console = Console()
# Progress narration goes to stderr so that --json output on stdout stays
# parseable. A caller piping JSON into a parser must not have to strip
# human-facing lines out of it first.
progress = Console(stderr=True)

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


def _get_store(db: str, target: str | None = None):
    """
    Open the graph store for a command.

    *target* is the path the command operates on (a repo root or graph file).
    Passing it lets project configuration be discovered from that repo rather
    than from the caller's working directory, so naming a repo elsewhere on
    disk uses that repo's configured backend.
    """
    return _open_store(db, target)[0]


def _open_store(db: str, target: str | None = None):
    """Open the store and also return the :class:`StorageConfig` that chose it."""
    from navegador.config import (
        DEFAULT_DB_PATH,
        StorageResolutionError,
        open_store,
        resolve_storage,
    )

    config = resolve_storage(db if db != DEFAULT_DB_PATH else None, target=target)
    try:
        return open_store(config), config
    except StorageResolutionError as e:
        raise click.ClickException(str(e)) from e


def _get_llm(llm_provider: str, llm_model: str, target: str | None = None):
    """
    Build the configured LLM provider, or fail with something actionable.

    Resolves through the same layering as storage — flags, environment, project
    config, user config — so `[llm]` in config.toml is finally honoured (#164),
    and turns provider/credential problems into a Click error naming the
    provider, the layer that chose it, and the fix.
    """
    from navegador.config import resolve_llm
    from navegador.llm import auto_provider, get_provider

    config = resolve_llm(llm_provider or None, llm_model or None, target=target)
    try:
        if config.provider:
            return get_provider(config.provider, model=config.model)
        return auto_provider(model=config.model)
    except (RuntimeError, ValueError, ImportError) as e:
        raise click.ClickException(f"Cannot use LLM provider {config.describe()}.\n{e}") from e


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
@click.option(
    "--graph",
    "graph_name",
    default="",
    metavar="NAME",
    help="Named graph on the shared server. Defaults to navegador_<directory>, "
    "which keeps this project separate from others using the same server.",
)
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
    graph_name: str,
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

    storage = "redis" if (redis_url or graph_name) else "sqlite"
    nav_dir = init_project(
        path,
        storage=storage,
        redis_url=redis_url,
        graph_name=graph_name,
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
@click.option(
    "--repo-name",
    "repo_key",
    default="",
    metavar="NAME",
    help="Pin the Repository node's identity. Defaults to the git remote's "
    "owner/repo, falling back to the directory name — so a worktree or a "
    "renamed clone does not create a second, phantom repository.",
)
@click.option(
    "--exclude",
    "excludes",
    multiple=True,
    metavar="GLOB",
    help="Exclude paths matching GLOB (repeatable). Matches repo-relative "
    "paths or single path components; a repo-root .navignore is honored too.",
)
@click.option(
    "--no-content",
    "no_content",
    is_flag=True,
    help="Do not keep file text in the content store. Content is addressed by "
    "hash, so unchanged files and vendored copies cost nothing to keep — but "
    "without it lexical search has no corpus to match against.",
)
@click.option(
    "--no-gitignore",
    "no_gitignore",
    is_flag=True,
    help="Index files git ignores. By default a git checkout contributes only "
    "the files git tracks or would show as untracked, which is the same set "
    "ripgrep walks — without this, build output lands in the graph and agents "
    "are handed generated code as if it were source.",
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
    repo_key: str,
    excludes: tuple[str, ...],
    no_content: bool,
    no_gitignore: bool,
):
    """Ingest a repository's code into the graph (AST + call graph)."""
    if monorepo:
        from navegador.monorepo import MonorepoIngester

        store = _get_store(db, target=repo_path)
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

    store = _get_store(db, target=repo_path)
    ingester = RepoIngester(
        store,
        redact=redact,
        exclude=list(excludes),
        respect_gitignore=not no_gitignore,
        store_content=not no_content,
    )

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
        stats = ingester.ingest(
            repo_path, clear=clear, incremental=incremental, repo_key=repo_key or None
        )
        click.echo(json.dumps(stats, indent=2))
    else:
        with console.status(f"[bold]Ingesting[/bold] {repo_path}..."):
            stats = ingester.ingest(
                repo_path, clear=clear, incremental=incremental, repo_key=repo_key or None
            )
        table = Table(title="Ingestion complete")
        table.add_column("Metric", style="cyan")
        table.add_column("Count", justify="right", style="green")
        for k, v in stats.items():
            table.add_row(k.capitalize(), str(v))
        console.print(table)
        if ingester.unavailable_grammars:
            console.print(
                f"[yellow]Skipped {stats.get('grammar_skipped', 0)} file(s) — "
                f"missing grammars: {', '.join(sorted(ingester.unavailable_grammars))}. "
                "Install with pip install 'navegador\\[languages,iac]' or the "
                "specific tree-sitter-<language> package.[/yellow]"
            )


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
@click.option("--file-path", "file_path", default="", help="File path to scope the code symbol.")
@click.option("--repo", default="", help="Repo name to scope the memory node lookup.")
@DB_OPTION
def annotate(
    code_name: str,
    code_label: str,
    concept: str,
    rule: str,
    memory: str,
    file_path: str,
    repo: str,
    db: str,
):
    """Link a code node to a concept, rule, or memory node."""
    from navegador.ingestion import KnowledgeIngester

    k = KnowledgeIngester(_get_store(db))
    k.annotate_code(
        code_name,
        code_label,
        concept=concept or None,
        rule=rule or None,
        memory=memory or None,
        file_path=file_path,
        repo=repo,
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
    """Ingest and query structured memory/ directories."""


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
    """Ingest a structured memory/ directory into the graph."""
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


@wiki.command("sync-local")
@click.option("--repo", required=True, help="GitHub repo (owner/repo).")
@click.option("--token", default="", envvar="GITHUB_TOKEN", help="GitHub token.")
@click.option("--dir", "local_dir", required=True, help="Local directory of markdown files.")
@click.option(
    "--cursor",
    "cursor_path",
    default="",
    help="Sync cursor file (default: .navegador/wiki-local-sync.json).",
)
def wiki_sync_local(repo: str, token: str, local_dir: str, cursor_path: str):
    """Bidirectional sync between a GitHub wiki and a local markdown directory.

    Pages changed on one side since the last sync are pushed to the other.
    Pages changed on both sides are flagged as conflicts and left untouched.
    """
    import subprocess

    from navegador.wiki_sync import GitHubWikiProvider, LocalMarkdownProvider, WikiSync

    if not cursor_path:
        from pathlib import Path

        cursor_path = str(Path(".navegador") / "wiki-local-sync.json")

    engine = WikiSync(GitHubWikiProvider(repo, token=token), LocalMarkdownProvider(local_dir))
    try:
        stats = engine.sync(cursor_path=cursor_path)
    except subprocess.CalledProcessError as exc:
        raise click.ClickException(f"git operation failed: {exc.stderr or exc}") from exc

    a = stats["pushed_to_b"]  # pushed to local dir
    b = stats["pushed_to_a"]  # pushed to github
    skipped = stats["skipped"]
    conflicts = stats["conflicts"]

    console.print(
        f"[green]GitHub wiki ↔ local sync:[/green] {a} → local, {b} → GitHub, {skipped} skipped"
    )
    if conflicts:
        console.print(
            f"[yellow]Conflicts ({len(conflicts)}) — both sides changed, skipped:[/yellow]"
        )
        for name in conflicts:
            console.print(f"  • {name}")


# ── Fossil SCM ───────────────────────────────────────────────────────────────


@main.group()
def fossil():
    """Ingest Fossil SCM wiki/tickets and sync with GitHub wiki."""


@fossil.command("wiki")
@click.option("--path", "repo_path", default=".", help="Path to Fossil checkout.")
@click.option("--repo", default="", help="Repository name stored on each node.")
@DB_OPTION
def fossil_wiki(repo_path: str, repo: str, db: str):
    """Ingest Fossil wiki pages into the knowledge graph."""
    from navegador.ingestion.fossil import FossilIngester
    from navegador.vcs import FossilAdapter

    adapter = FossilAdapter(repo_path)
    ingester = FossilIngester(_get_store(db), adapter, repo_name=repo)
    stats = ingester.ingest_wiki()
    console.print(
        f"[green]Fossil wiki ingested:[/green] {stats['pages']} pages, {stats['edges']} edges"
    )


@fossil.command("tickets")
@click.option("--path", "repo_path", default=".", help="Path to Fossil checkout.")
@click.option("--repo", default="", help="Repository name stored on each node.")
@click.option("--limit", default=200, show_default=True, help="Max tickets to ingest.")
@DB_OPTION
def fossil_tickets(repo_path: str, repo: str, limit: int, db: str):
    """Ingest Fossil tickets into the knowledge graph."""
    from navegador.ingestion.fossil import FossilIngester
    from navegador.vcs import FossilAdapter

    adapter = FossilAdapter(repo_path)
    ingester = FossilIngester(_get_store(db), adapter, repo_name=repo)
    stats = ingester.ingest_tickets(limit=limit)
    console.print(
        f"[green]Fossil tickets ingested:[/green] "
        f"{stats['tickets']} tickets, {stats['edges']} edges"
    )


@fossil.command("push-wiki")
@click.option("--repo", required=True, help="GitHub repo to push to (owner/repo).")
@click.option("--token", default="", envvar="GITHUB_TOKEN", help="GitHub token.")
@click.option("--path", "repo_path", default=".", help="Path to Fossil checkout.")
def fossil_push_wiki(repo: str, token: str, repo_path: str):
    """Push Fossil wiki pages to the GitHub wiki (Fossil → GitHub)."""
    import subprocess

    from navegador.ingestion.fossil import FossilWikiSync
    from navegador.vcs import FossilAdapter

    adapter = FossilAdapter(repo_path)
    sync = FossilWikiSync(adapter, repo, token=token)
    try:
        stats = sync.fossil_to_github()
    except subprocess.CalledProcessError as exc:
        raise click.ClickException(f"git operation failed: {exc.stderr or exc}") from exc
    console.print(
        f"[green]Fossil → GitHub:[/green] {stats['pages']} page(s) synced, "
        f"{stats['skipped']} skipped"
    )


@fossil.command("pull-wiki")
@click.option("--repo", required=True, help="GitHub repo to pull from (owner/repo).")
@click.option("--token", default="", envvar="GITHUB_TOKEN", help="GitHub token.")
@click.option("--path", "repo_path", default=".", help="Path to Fossil checkout.")
def fossil_pull_wiki(repo: str, token: str, repo_path: str):
    """Pull GitHub wiki pages into Fossil (GitHub → Fossil)."""
    import subprocess

    from navegador.ingestion.fossil import FossilWikiSync
    from navegador.vcs import FossilAdapter

    adapter = FossilAdapter(repo_path)
    sync = FossilWikiSync(adapter, repo, token=token)
    try:
        stats = sync.github_to_fossil()
    except subprocess.CalledProcessError as exc:
        raise click.ClickException(f"git operation failed: {exc.stderr or exc}") from exc
    console.print(
        f"[green]GitHub → Fossil:[/green] {stats['pages']} page(s) synced, "
        f"{stats['skipped']} skipped"
    )


@fossil.command("sync-wiki")
@click.option("--repo", required=True, help="GitHub repo (owner/repo).")
@click.option("--token", default="", envvar="GITHUB_TOKEN", help="GitHub token.")
@click.option("--path", "repo_path", default=".", help="Path to Fossil checkout.")
@click.option(
    "--cursor",
    "cursor_path",
    default="",
    help="Sync cursor file (default: .navegador/fossil-wiki-sync.json).",
)
def fossil_sync_wiki(repo: str, token: str, repo_path: str, cursor_path: str):
    """Bidirectional sync between Fossil and GitHub wiki.

    Pages changed on one side since the last sync are pushed to the other.
    Pages changed on both sides are flagged as conflicts and left untouched —
    resolve them with push-wiki or pull-wiki to force one direction.
    """
    import subprocess

    from navegador.ingestion.fossil import FossilWikiSync
    from navegador.vcs import FossilAdapter

    adapter = FossilAdapter(repo_path)
    sync = FossilWikiSync(adapter, repo, token=token)
    kwargs = {}
    if cursor_path:
        kwargs["cursor_path"] = cursor_path
    try:
        stats = sync.sync(**kwargs)
    except subprocess.CalledProcessError as exc:
        raise click.ClickException(f"git operation failed: {exc.stderr or exc}") from exc

    gh = stats["pushed_to_github"]
    fossil = stats["pushed_to_fossil"]
    skipped = stats["skipped"]
    conflicts = stats["conflicts"]

    console.print(f"[green]Wiki sync:[/green] {gh} → GitHub, {fossil} → Fossil, {skipped} skipped")
    if conflicts:
        console.print(
            f"[yellow]Conflicts ({len(conflicts)}) — both sides changed, skipped:[/yellow]"
        )
        for name in conflicts:
            console.print(f"  • {name}")
        console.print(
            "[dim]Run [bold]fossil push-wiki[/bold] or [bold]fossil pull-wiki[/bold] "
            "to force one direction for conflicting pages.[/dim]"
        )


@fossil.command("sync-local")
@click.option("--path", "repo_path", default=".", help="Path to Fossil checkout.")
@click.option("--dir", "local_dir", required=True, help="Local directory of markdown files.")
@click.option(
    "--cursor",
    "cursor_path",
    default="",
    help="Sync cursor file (default: .navegador/fossil-local-sync.json).",
)
def fossil_sync_local(repo_path: str, local_dir: str, cursor_path: str):
    """Bidirectional sync between Fossil wiki and a local markdown directory.

    Pages changed on one side since the last sync are pushed to the other.
    Pages changed on both sides are flagged as conflicts and left untouched.
    """
    from navegador.vcs import FossilAdapter
    from navegador.wiki_sync import FossilWikiProvider, LocalMarkdownProvider, WikiSync

    adapter = FossilAdapter(repo_path)
    if not cursor_path:
        cursor_path = str((adapter.repo_path / ".navegador" / "fossil-local-sync.json").resolve())

    engine = WikiSync(FossilWikiProvider(adapter), LocalMarkdownProvider(local_dir))
    stats = engine.sync(cursor_path=cursor_path)

    a = stats["pushed_to_b"]  # pushed to local dir
    b = stats["pushed_to_a"]  # pushed to fossil
    skipped = stats["skipped"]
    conflicts = stats["conflicts"]

    console.print(
        f"[green]Fossil ↔ local sync:[/green] {a} → local, {b} → Fossil, {skipped} skipped"
    )
    if conflicts:
        console.print(
            f"[yellow]Conflicts ({len(conflicts)}) — both sides changed, skipped:[/yellow]"
        )
        for name in conflicts:
            console.print(f"  • {name}")


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
      - A PlanOpticonExchange JSON file (exchange.json / interchange.json)
      - A batch manifest JSON
      - A planopticon output directory (auto-detects current PlanOpticon layouts)
    """
    from navegador.ingestion import PlanopticonIngester
    from navegador.ingestion.planopticon import resolve_planopticon_input

    try:
        input_type, p = resolve_planopticon_input(path, input_type=input_type)
    except FileNotFoundError as exc:
        raise click.UsageError(str(exc)) from exc

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
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["jsonl", "conflict-kg"]),
    default="jsonl",
    help="Export format: jsonl (legacy) or conflict-kg (canonical conflict-kg/v1; "
    "writes SQLite for .db/.sqlite outputs, JSON otherwise).",
)
@click.option(
    "--graph",
    "graph_name",
    default="",
    metavar="NAME",
    help="Named graph within the connected FalkorDB to export "
    "(e.g. navegador_myrepo from a federated workspace ingest). "
    "Defaults to the main 'navegador' graph.",
)
@click.option("--json", "as_json", is_flag=True, help="Output stats as JSON.")
def export_cmd(output: str, db: str, fmt: str, graph_name: str, as_json: bool):
    """Export the graph to a text-based JSONL file (git-friendly)."""
    store = _get_store(db)
    if graph_name:
        store = store.with_graph(graph_name)
    if fmt == "conflict-kg":
        from navegador.graph.interchange import export_conflict_kg

        stats = export_conflict_kg(store, output)
    else:
        from navegador.graph.export import export_graph

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
    """Import a graph from a JSONL or conflict-kg/v1 export file (auto-detected)."""
    from navegador.graph.interchange import (
        import_conflict_kg,
        is_conflict_kg_json,
        is_sqlite_file,
    )

    store = _get_store(db)
    if is_sqlite_file(input_path) or is_conflict_kg_json(input_path):
        stats = import_conflict_kg(store, input_path, clear=not no_clear)
    else:
        from navegador.graph.export import import_graph

        stats = import_graph(store, input_path, clear=not no_clear)

    if as_json:
        click.echo(json.dumps(stats, indent=2))
    else:
        console.print(
            f"[green]Imported[/green] {stats['nodes']} nodes, {stats['edges']} edges ← {input_path}"
        )


def _parse_repo_sources(args: tuple[str, ...]) -> dict[str, str]:
    """Parse [NAME=]PATH source arguments into {repo name: path}."""
    from navegador.federation import repo_name_from_path

    sources: dict[str, str] = {}
    for arg in args:
        name, _, path = arg.rpartition("=")
        if not name:
            path = arg
            name = repo_name_from_path(arg)
        sources[name] = path
    return sources


@main.command("aggregate")
@click.argument("repos", nargs=-1, required=True)
@DB_OPTION
@click.option(
    "--graph",
    "graph_name",
    default="",
    metavar="NAME",
    help="Target super-graph name within the connected FalkorDB "
    "(e.g. navegador_supergraph), so per-repo shards and the rollup "
    "coexist. Defaults to the main 'navegador' graph.",
)
@click.option("--clear", is_flag=True, help="Wipe the central graph before aggregating.")
@click.option("--json", "as_json", is_flag=True, help="Output stats as JSON.")
def aggregate_cmd(repos: tuple[str, ...], db: str, graph_name: str, clear: bool, as_json: bool):
    """
    Roll repo-local graphs up into a central super-graph.

    Each REPO is a repo root (containing .navegador/graph.db), a graph
    file, or the name of a graph already resident in the connected
    FalkorDB (<name> or navegador_<name>, as written by
    `workspace ingest --mode federated`), optionally prefixed NAME= to set
    the repo namespace (defaults to the directory basename). The --db (or
    --graph) graph is the central target.
    """
    from navegador.federation import SuperGraphAggregator

    sources = _parse_repo_sources(repos)
    store = _get_store(db)
    if graph_name:
        store = store.with_graph(graph_name)
    aggregator = SuperGraphAggregator(store)
    summary = aggregator.aggregate(sources, clear=clear)

    if as_json:
        click.echo(json.dumps(summary, indent=2))
    else:
        for name, stats in summary.items():
            if "error" in stats:
                console.print(f"[red]{name}[/red]: {stats['error']}")
            else:
                console.print(
                    f"[green]{name}[/green]: {stats['nodes']} nodes, "
                    f"{stats['edges']} edges ({stats['deduped']} knowledge nodes unified)"
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
@click.option(
    "--federate",
    "federate",
    multiple=True,
    metavar="[NAME=]PATH",
    help=(
        "Roll up a repo graph ([NAME=]PATH, repeatable) into the bound graph at "
        "startup and serve the federated super-graph."
    ),
)
def mcp(db: str, read_only: bool, federate: tuple[str, ...]):
    """Start the MCP server for AI agent integration (stdio)."""
    from mcp.server.stdio import stdio_server  # type: ignore[import]

    from navegador.mcp import create_mcp_server

    def _store_factory():
        store = _get_store(db)
        if federate:
            from navegador.federation import SuperGraphAggregator

            SuperGraphAggregator(store).aggregate(_parse_repo_sources(federate))
        return store

    server = create_mcp_server(_store_factory, read_only=read_only)
    mode = "read-only" if read_only else "read-write"
    federated = f", federated over {len(federate)} repos" if federate else ""
    console.print(f"[green]Navegador MCP server running[/green] (stdio, {mode}{federated})")

    import asyncio

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
    "--snapshot",
    is_flag=True,
    help="Use snapshot-backed graph diff (requires prior snapshots).",
)
@click.option(
    "--repo-path",
    default=".",
    type=click.Path(exists=True),
    help="Path to the git repo.",
)
@DB_OPTION
def diff_graph(base: str, head: str, as_json: bool, snapshot: bool, repo_path: str, db: str):
    """Structural diff — what graph changes did this branch introduce?

    Reports new/changed symbols, blast-radius summary, and affected knowledge
    nodes for all lines changed between BASE and HEAD.

    With --snapshot, uses true graph diff between two previously-snapshotted refs
    instead of the git-diff heuristic. Falls back to heuristic if snapshots are
    missing.

    \b
    Examples:
      navegador diff-graph                        # working tree vs HEAD
      navegador diff-graph --base main            # current branch vs main
      navegador diff-graph --base main --head HEAD
      navegador diff-graph --base v1.0 --head v2.0 --snapshot
    """
    from navegador.analysis.diffgraph import DiffGraphAnalyzer

    analyzer = DiffGraphAnalyzer(_get_store(db), repo_path)
    if snapshot:
        report = analyzer.diff_snapshots(base_ref=base, head_ref=head)
    elif base == "HEAD" and head == "working tree":
        report = analyzer.diff_working_tree()
    else:
        report = analyzer.diff_refs(base=base, head=head)

    if as_json:
        click.echo(report.to_json())
    else:
        console.print(report.to_markdown())


# ── ANALYSIS: rule-aware review comments ────────────────────────────────────


@main.command("review")
@click.option("--base", default="main", show_default=True, help="Base ref (branch, tag, SHA).")
@click.option("--head", default="HEAD", show_default=True, help="Head ref to compare.")
@click.option(
    "--min-confidence",
    default=0.5,
    show_default=True,
    type=float,
    help="Minimum confidence threshold for comments.",
)
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
@click.option(
    "--repo-path",
    default=".",
    type=click.Path(exists=True),
    help="Path to the git repo.",
)
@DB_OPTION
def review(base: str, head: str, min_confidence: float, as_json: bool, repo_path: str, db: str):
    """Generate rule-aware review comments for a diff.

    Analyses structural changes between BASE and HEAD, then queries the
    knowledge graph for governing rules, ADRs, and documentation links.
    Each finding is tied back to the exact Rule, Decision, or WikiPage.

    \b
    Examples:
      navegador review                              # main vs HEAD
      navegador review --base develop --head HEAD
      navegador review --min-confidence 0.7 --json
    """
    from navegador.analysis.diffgraph import DiffGraphAnalyzer
    from navegador.analysis.review import ReviewGenerator

    store = _get_store(db)
    analyzer = DiffGraphAnalyzer(store, repo_path)
    diff_report = analyzer.diff_refs(base=base, head=head)

    changed_symbols = [
        {"name": sc.symbol, "file_path": sc.file_path}
        for sc in diff_report.new_symbols + diff_report.changed_symbols
    ]

    gen = ReviewGenerator(store)
    report = gen.review_diff(
        changed_symbols=changed_symbols,
        changed_files=list(diff_report.affected_files),
    )

    # Filter by confidence
    report.comments = [c for c in report.comments if c.confidence >= min_confidence]

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


@repo.command("nodes")
@DB_OPTION
@click.option("--json", "as_json", is_flag=True)
def repo_nodes(db: str, as_json: bool):
    """List Repository nodes in the graph with how many files each owns.

    \b
    Useful for spotting phantom repositories — before repository identity was
    derived from the git remote, a worktree or a renamed clone created a second
    node indistinguishable from a real one (#167).
    """
    store = _get_store(db)
    rows = (
        store.query(
            "MATCH (r:Repository) "
            "OPTIONAL MATCH (f)-[:BELONGS_TO]->(r) "
            "RETURN r.path AS path, r.name AS name, count(f) AS files "
            "ORDER BY r.path"
        ).result_set
        or []
    )
    entries = [{"path": r[0], "name": r[1], "files": r[2]} for r in rows]

    if as_json:
        click.echo(json.dumps(entries, indent=2))
        return
    if not entries:
        console.print("No Repository nodes in this graph.")
        return

    table = Table(title=f"{len(entries)} Repository node(s)")
    table.add_column("Identity", style="cyan", overflow="fold")
    table.add_column("Name")
    table.add_column("Files", justify="right")
    for e in entries:
        table.add_row(str(e["path"]), str(e["name"]), str(e["files"]))
    console.print(table)

    names = [e["name"] for e in entries]
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        console.print(
            f"\n[yellow]{len(dupes)} name(s) held by more than one node[/yellow]: "
            f"{', '.join(dupes)}\n"
            "If these are the same repository under different checkout names, merge them:\n"
            "  [cyan]navegador repo merge <phantom-identity> <canonical-identity>[/cyan]"
        )


@repo.command("merge")
@click.argument("source")
@click.argument("target")
@DB_OPTION
@click.option("--json", "as_json", is_flag=True)
def repo_merge(source: str, target: str, db: str, as_json: bool):
    """Merge Repository node SOURCE into TARGET, then delete SOURCE.

    \b
    Repairs graphs that accumulated phantom repositories before identity was
    derived from the git remote. Every file owned by SOURCE is re-pointed at
    TARGET; files already owned by both simply lose the duplicate edge.

    \b
    Example:
      navegador repo merge myproj-worktree ExampleOrg/myproj
    """
    store = _get_store(db)

    def count(identity: str) -> int | None:
        rows = store.query(
            "MATCH (r:Repository {path: $p}) RETURN count(r)", {"p": identity}
        ).result_set
        return rows[0][0] if rows else 0

    if not count(source):
        raise click.ClickException(f"No Repository node with identity {source!r}.")
    if not count(target):
        raise click.ClickException(
            f"No Repository node with identity {target!r}. "
            f"Merging into a node that does not exist would lose the files instead."
        )
    if source == target:
        raise click.ClickException("SOURCE and TARGET are the same identity.")

    moved = (
        store.query(
            "MATCH (f)-[old:BELONGS_TO]->(:Repository {path: $src}), "
            "(t:Repository {path: $tgt}) "
            "DELETE old "
            "MERGE (f)-[:BELONGS_TO]->(t) "
            "RETURN count(f)",
            {"src": source, "tgt": target},
        ).result_set
        or [[0]]
    )[0][0]

    store.query("MATCH (r:Repository {path: $src}) DETACH DELETE r", {"src": source})

    result = {"source": source, "target": target, "files_moved": moved}
    if as_json:
        click.echo(json.dumps(result, indent=2))
    else:
        console.print(f"[green]Merged[/green] {source} → {target} ({moved} file(s) re-pointed)")


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
@click.option(
    "--no-comments",
    is_flag=True,
    help="Skip fetching issue comment threads (fetched by default).",
)
@click.option(
    "--extract-decisions",
    is_flag=True,
    help="After ingesting, surface Decision nodes from issue threads via LLM "
    "(requires the [llm] extra and provider credentials).",
)
@click.option("--llm-provider", default="anthropic", show_default=True)
@click.option("--llm-model", default="", help="Override the provider's default model.")
@DB_OPTION
@click.option("--json", "as_json", is_flag=True)
def pm_ingest(
    github_repo: str,
    token: str,
    state: str,
    limit: int,
    no_comments: bool,
    extract_decisions: bool,
    llm_provider: str,
    llm_model: str,
    db: str,
    as_json: bool,
):
    """Ingest tickets from a PM tool into the knowledge graph.

    \b
    Examples:
      navegador pm ingest --github owner/repo
      navegador pm ingest --github owner/repo --token ghp_...
      navegador pm ingest --github owner/repo --state all --limit 200
      navegador pm ingest --github owner/repo --extract-decisions
    """
    if not github_repo:
        raise click.UsageError(
            "Provide --github <owner/repo> (more backends coming in a future release)."
        )

    from navegador.pm import TicketIngester

    ing = TicketIngester(_get_store(db))
    stats = ing.ingest_github_issues(
        github_repo,
        token=token,
        state=state,
        limit=limit,
        include_comments=not no_comments,
    )

    if extract_decisions:
        domain = github_repo.split("/")[-1]
        stats.update(
            ing.extract_decisions(domain=domain, llm_provider=llm_provider, llm_model=llm_model)
        )

    if as_json:
        click.echo(json.dumps(stats, indent=2))
    else:
        table = Table(title=f"PM import: {github_repo}")
        table.add_column("Metric", style="cyan")
        table.add_column("Count", justify="right", style="green")
        for k, v in stats.items():
            table.add_row(k.capitalize(), str(v))
        console.print(table)


@pm.command("decisions")
@click.option(
    "--to-markdown",
    "memory_dir",
    default="",
    metavar="DIR",
    help="Write one project_<slug>.md per decision into DIR "
    "(frontmatter format readable by `navegador memory ingest`).",
)
@click.option(
    "--to-json",
    "json_path",
    default="",
    metavar="FILE",
    help="Write the decision list as JSON to FILE (e.g. app/decisions.json).",
)
@click.option("--domain", default="", help="Only export decisions from this domain.")
@DB_OPTION
@click.option("--json", "as_json", is_flag=True, help="Output stats as JSON.")
def pm_decisions(memory_dir: str, json_path: str, domain: str, db: str, as_json: bool):
    """Retrofit Decision nodes into a brain's memory store.

    \b
    Examples:
      navegador pm decisions --to-markdown memory/
      navegador pm decisions --to-json app/decisions.json --domain myrepo
    """
    if not memory_dir and not json_path:
        raise click.UsageError("Provide --to-markdown DIR and/or --to-json FILE.")

    from navegador.pm import retrofit_decisions

    stats = retrofit_decisions(
        _get_store(db),
        memory_dir=memory_dir or None,
        json_path=json_path or None,
        domain=domain,
    )

    if as_json:
        click.echo(json.dumps(stats, indent=2))
    else:
        markdown_note = (
            f" → {stats['markdown_files']} markdown file(s) in {memory_dir}" if memory_dir else ""
        )
        json_note = f" → {json_path}" if json_path else ""
        console.print(
            f"[green]Retrofitted[/green] {stats['decisions']} decision(s){markdown_note}{json_note}"
        )


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

    store, storage = _open_store(db, target=repo_path)
    ing = SubmoduleIngester(store)
    stats = ing.ingest_with_submodules(repo_path, clear=clear)

    if as_json:
        click.echo(json.dumps({**stats, "storage": storage.describe()}, indent=2))
    else:
        sub_names = list(stats.get("submodules", {}).keys())
        console.print(
            f"[green]Submodule ingestion complete[/green]: "
            f"{stats.get('total_files', 0)} total files, "
            f"{len(sub_names)} submodule(s)"
        )
        if sub_names:
            console.print("  Submodules: " + ", ".join(sub_names))
        console.print(f"  Wrote to: {storage.describe()}")


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
@click.argument("repos", nargs=-1, metavar="[NAME=]PATH ...")
@click.option(
    "--mode",
    type=click.Choice(["unified", "federated", "authored", "full"]),
    default="unified",
    show_default=True,
    help="unified (shared graph), federated (per-repo graphs), or the "
    "metarepo modes (imply --recursive + federated): authored skips each "
    "repo's vendored nested clones, full indexes them too.",
)
@click.option(
    "--recursive",
    is_flag=True,
    help="Discover nested git clones under each PATH and ingest each as its own repo.",
)
@click.option(
    "--exclude",
    "excludes",
    multiple=True,
    metavar="GLOB",
    help="Exclude paths matching GLOB in every repo (repeatable; "
    "per-repo .navignore files are honored too).",
)
@DB_OPTION
@click.option("--clear", is_flag=True)
@click.option("--json", "as_json", is_flag=True)
def workspace_ingest(
    repos: tuple,
    mode: str,
    recursive: bool,
    excludes: tuple[str, ...],
    db: str,
    clear: bool,
    as_json: bool,
):
    """Ingest multiple repositories as a workspace.

    \b
    REPOS is a list of [NAME=]PATH entries (NAME defaults to the directory
    basename), e.g.:
      navegador workspace ingest backend=/path/to/backend frontend=/path/to/frontend

    \b
    Examples:
      navegador workspace ingest backend=. frontend=../frontend --mode unified
      navegador workspace ingest api=./api worker=./worker --mode federated
      navegador workspace ingest /path/to/metarepo --recursive --mode authored
    """
    from navegador.multirepo import WorkspaceManager, WorkspaceMode, discover_nested_repos

    if not repos:
        raise click.UsageError("Provide at least one [NAME=]PATH repo.")

    metarepo = mode in ("authored", "full")
    recursive = recursive or metarepo
    storage_mode = WorkspaceMode.FEDERATED if metarepo else WorkspaceMode(mode)

    # Resolve storage against the first named repo: a workspace is usually
    # driven from a directory that has no config of its own, and the repos it
    # names do (#170).
    first_spec = repos[0]
    _, sep, first_path = first_spec.partition("=")
    target = (first_path if sep else first_spec).strip()

    wm = WorkspaceManager(
        _get_store(db, target=target),
        mode=storage_mode,
        exclude=list(excludes),
        include_nested_repos=(mode == "full"),
    )
    for repo_spec in repos:
        name, sep, path = repo_spec.partition("=")
        if not sep:
            path = repo_spec
            name = Path(path).resolve().name
        name, path = name.strip(), path.strip()
        wm.add_repo(name, path)
        if recursive:
            for nested_name, nested_path in discover_nested_repos(path):
                wm.add_repo(f"{name}-{nested_name}", nested_path)

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

    store = _get_store(db)
    provider = _get_llm(llm_provider, llm_model)
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

    store = _get_store(db)
    provider = _get_llm(llm_provider, llm_model)
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

    store = _get_store(db)
    provider = _get_llm(llm_provider, llm_model)
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
        console.print(f"[bold]{len(symbols)}[/bold] symbols at [bold]{ref}[/bold]\n")
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
    console.print(f"[green]Accepted[/green] {count} doc links (min_confidence={min_confidence})")


# ── Lenses ───────────────────────────────────────────────────────────────────


@main.group()
def lens():
    """Architecture lenses — reusable named graph views."""


@lens.command("list")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
@DB_OPTION
def lens_list(as_json: bool, db: str):
    """List available architecture lenses.

    \b
    Examples:
      navegador lens list
      navegador lens list --json
    """
    from navegador.lenses import LensEngine

    store = _get_store(db)
    engine = LensEngine(store)
    lenses = engine.list_lenses()

    if as_json:
        click.echo(json.dumps(lenses, indent=2))
        return

    table = Table(title="Architecture Lenses")
    table.add_column("Name", style="cyan bold")
    table.add_column("Built-in", justify="center")
    table.add_column("Description")
    for item in lenses:
        table.add_row(
            item["name"],
            "yes" if item["builtin"] else "no",
            item["description"],
        )
    console.print(table)


@lens.command("apply")
@click.argument("name")
@click.option("--symbol", default="", help="Symbol name filter.")
@click.option("--domain", default="", help="Domain name filter.")
@click.option("--file", "file_path", default="", help="File path filter.")
@click.option("--label", default="", help="Node label filter.")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
@DB_OPTION
def lens_apply(
    name: str,
    symbol: str,
    domain: str,
    file_path: str,
    label: str,
    as_json: bool,
    db: str,
):
    """Apply a named architecture lens.

    \b
    Built-in lenses: request_path, ownership_map, domain_boundaries,
    dependency_layers, framework_components.

    \b
    Examples:
      navegador lens apply request_path
      navegador lens apply ownership_map --domain billing
      navegador lens apply dependency_layers --file src/app.py --json
    """
    from navegador.lenses import LensEngine

    store = _get_store(db)
    engine = LensEngine(store)
    try:
        result = engine.apply(name, symbol=symbol, domain=domain, file_path=file_path, label=label)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc

    if as_json:
        click.echo(result.to_json())
    else:
        console.print(result.to_markdown())


# ── Central server: native FalkorDB lifecycle (#172) ─────────────────────────


@main.group()
def server():
    """Manage a native (non-Docker) FalkorDB server shared by every project."""


@server.command("install")
@click.option("--version", default="", help="FalkorDB module version. Defaults to the pinned one.")
@click.option("--port", default=6379, show_default=True, help="Port to serve on.")
@click.option("--bind", default="127.0.0.1", show_default=True, help="Address to bind.")
@click.option(
    "--maxmemory-mb",
    default=0,
    show_default=True,
    help="Redis keyspace memory cap in MB. 0 = unlimited (recommended: FalkorDB "
    "holds graph data in module memory a cap does not govern).",
)
@click.option("--sha256", "expected_sha256", default="", help="Expected module checksum.")
@click.option("--force", is_flag=True, help="Re-download the module even if present.")
@click.option("--start/--no-start", default=True, show_default=True, help="Start after installing.")
@click.option(
    "--set-default/--no-set-default",
    default=True,
    show_default=True,
    help="Write ~/.config/navegador/config.toml so every project uses this server "
    "unless it configures otherwise.",
)
@click.option("--json", "as_json", is_flag=True)
def server_install(
    version: str,
    port: int,
    bind: str,
    maxmemory_mb: int,
    expected_sha256: str,
    force: bool,
    start: bool,
    set_default: bool,
    as_json: bool,
):
    """Install and start a native FalkorDB server.

    \b
    Downloads the official prebuilt FalkorDB module for this platform, writes a
    tuned redis.conf, and registers a start-at-login service. No Docker, and
    nothing is compiled locally.

    \b
    Examples:
      navegador server install
      navegador server install --port 6390 --no-set-default
    """
    from navegador import server as srv

    if port_conflict := (start and srv.port_in_use(port)):
        existing = srv.probe(f"redis://{bind}:{port}")
        detail = (
            f"FalkorDB {existing.get('module_version', '?')} on Redis "
            f"{existing.get('redis_version', '?')}"
            if existing.get("graph_module")
            else "a server without the FalkorDB graph module"
        )
        raise click.ClickException(
            f"Port {port} is already serving {detail}.\n"
            f"  Stop it first, or install on another port with --port.\n"
            f"  If that is a Docker FalkorDB you are replacing, migrate it first:\n"
            f"    navegador storage migrate --from redis://{bind}:{port} --to redis://{bind}:<new-port>"
        )

    try:
        manifest = srv.install(
            version=version or srv.DEFAULT_FALKORDB_VERSION,
            port=port,
            bind=bind,
            maxmemory_mb=maxmemory_mb,
            force=force,
            expected_sha256=expected_sha256,
        )
    except srv.ServerError as e:
        raise click.ClickException(str(e)) from e

    url = f"redis://{bind}:{port}"
    if set_default:
        _write_user_default(url)
        manifest["user_config"] = str(_user_config_file())

    if start:
        try:
            manifest["service"] = srv.start_service()
        except srv.ServerError as e:
            raise click.ClickException(str(e)) from e
        manifest["probe"] = srv.wait_until_ready(url)

    manifest["url"] = url

    if as_json:
        click.echo(json.dumps(manifest, indent=2, default=str))
        return

    console.print("[green]FalkorDB installed[/green]")
    table = Table(show_header=False, box=None)
    table.add_column(style="cyan")
    table.add_column()
    table.add_row("Version", str(manifest.get("falkordb_version", "")))
    table.add_row("Module", str(manifest.get("module_asset", "")))
    table.add_row("redis-server", str(manifest.get("redis_server", "")))
    table.add_row("Config", str(manifest.get("config", "")))
    table.add_row("Data", str(manifest.get("data_dir", "")))
    table.add_row("URL", url)
    if set_default:
        table.add_row("Default for", "all projects (user config written)")
    console.print(table)

    probe = manifest.get("probe") or {}
    if start and not probe.get("graph_module"):
        # Do not soften this into "not reported in yet": the service manager
        # returning cleanly is not evidence that the server came up.
        console.print(
            f"[red]The server did not come up[/red] — "
            f"{probe.get('error', 'no graph module loaded')}\n"
            f"  Log: {srv.paths().logs / 'falkordb.log'}\n"
            f"  Retry with: [cyan]navegador server start[/cyan]"
        )
        raise SystemExit(1)
    elif start:
        console.print(f"[green]Running[/green] — FalkorDB {probe.get('module_version', '?')}")
    if not port_conflict and not start:
        console.print("Start it with: [cyan]navegador server start[/cyan]")


@server.command("start")
def server_start():
    """Start the installed FalkorDB service."""
    from navegador import server as srv

    try:
        console.print(f"[green]{srv.start_service()}[/green]")
    except srv.ServerError as e:
        raise click.ClickException(str(e)) from e


@server.command("stop")
def server_stop():
    """Stop the FalkorDB service."""
    from navegador import server as srv

    console.print(f"[yellow]{srv.stop_service()}[/yellow]")


@server.command("restart")
def server_restart():
    """Restart the FalkorDB service."""
    from navegador import server as srv

    srv.stop_service()
    try:
        srv.start_service()
    except srv.ServerError as e:
        raise click.ClickException(str(e)) from e

    manifest = srv.read_manifest(srv.paths())
    url = f"redis://{manifest.get('bind', '127.0.0.1')}:{manifest.get('port', 6379)}"
    # A large graph takes seconds to reload from AOF, during which the port is
    # not yet accepting connections. Reporting success before then would send
    # the user to a status check that contradicts it.
    info = srv.wait_until_ready(url, timeout=120)
    if not info.get("graph_module"):
        raise click.ClickException(
            f"Restarted, but {url} is not serving graphs — {info.get('error', 'no graph module')}\n"
            f"  Log: {srv.paths().logs / 'falkordb.log'}"
        )
    console.print(f"[green]restarted[/green] — {len(info.get('graphs', []))} graph(s) resident")


@server.command("status")
@click.option("--url", default="", help="Server URL to probe. Defaults to the installed one.")
@click.option("--json", "as_json", is_flag=True)
def server_status(url: str, as_json: bool):
    """Report whether the shared graph server is running and usable."""
    from navegador import server as srv

    paths = srv.paths()
    manifest = srv.read_manifest(paths)
    target = url or f"redis://{manifest.get('bind', '127.0.0.1')}:{manifest.get('port', 6379)}"
    info = srv.probe(target)
    info["installed"] = bool(manifest)
    info["home"] = str(paths.home)

    if as_json:
        click.echo(json.dumps(info, indent=2, default=str))
        return

    if not manifest:
        console.print("[yellow]No managed server installed.[/yellow]")
        console.print("Install one with: [cyan]navegador server install[/cyan]")

    if not info.get("reachable"):
        console.print(f"[red]Not reachable[/red] at {target}")
        if err := info.get("error"):
            console.print(f"  {err}")
        console.print("Start it with: [cyan]navegador server start[/cyan]")
        raise SystemExit(1)

    if not info.get("graph_module"):
        # A plain Redis answers PING and then fails every graph query, which is
        # the most confusing possible state to be in.
        console.print(f"[red]Reachable, but this is not a FalkorDB[/red] — {target}")
        console.print("  The graph module is not loaded; every query will fail.")
        console.print("  Install a proper one with: [cyan]navegador server install[/cyan]")
        raise SystemExit(1)

    console.print(f"[green]Running[/green] — {target}")
    table = Table(show_header=False, box=None)
    table.add_column(style="cyan")
    table.add_column()
    table.add_row("FalkorDB", str(info.get("module_version", "")))
    table.add_row("Redis", str(info.get("redis_version", "")))
    table.add_row("Memory", str(info.get("used_memory_human", "")))
    table.add_row("Uptime", f"{info.get('uptime_days', 0)} days")
    table.add_row("Graphs", str(len(info.get("graphs", []))))
    console.print(table)
    for name in info.get("graphs", []):
        console.print(f"  • {name}")


@server.command("uninstall")
@click.option("--remove-data", is_flag=True, help="Also delete the graph data directory.")
@click.confirmation_option(prompt="Stop and remove the managed FalkorDB service?")
def server_uninstall(remove_data: bool):
    """Stop the service and remove its definition (data is kept by default)."""
    from navegador import server as srv

    result = srv.uninstall(remove_data=remove_data)
    for path in result["removed"]:
        console.print(f"  removed {path}")
    if not remove_data:
        console.print(f"[green]Data kept[/green] at {srv.paths().data}")


def _user_config_file():
    from navegador.config import user_config_path

    return user_config_path()


def _write_user_default(redis_url: str) -> None:
    """Point every project at *redis_url* unless it configures otherwise."""
    path = _user_config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# Navegador user configuration\n"
        "# Written by: navegador server install\n"
        "# Applies to every project that has no [storage] of its own.\n"
        "\n"
        "[storage]\n"
        'backend = "redis"\n'
        f'redis_url = "{redis_url}"\n',
        encoding="utf-8",
    )


# ── Diagnostics: doctor + scan (#172) ────────────────────────────────────────


@main.command("doctor")
@click.option("--target", default=".", help="Project to diagnose.", type=click.Path())
@click.option("--json", "as_json", is_flag=True)
def doctor(target: str, as_json: bool):
    """Explain which graph backend this project resolves to, and whether it works.

    \b
    Reports the resolved backend, the config file that decided it, whether the
    store is reachable, and how much is actually in it — so a repo that reads
    as empty can be told apart from one that was never ingested.
    """
    from navegador import server as srv
    from navegador.config import open_store, resolve_storage
    from navegador.inventory import inspect_project

    config = resolve_storage(target=target)
    report: dict = {"target": str(Path(target).resolve()), "storage": config.describe()}

    record = inspect_project(target)
    report["declared_backend"] = record.declared_backend
    report["local_graph_bytes"] = record.db_bytes
    report["stranded"] = record.is_stranded

    if config.is_redis:
        info = srv.probe(config.redis_url)
        report["server"] = info

    problems: list[str] = []
    notes: list[str] = []
    served_nodes: int | None = None

    if config.is_redis:
        info = report["server"]
        if not info.get("reachable"):
            problems.append(
                f"The configured server {config.redis_url} is not reachable — "
                f"every query will fail. Run: navegador server status"
            )
        elif not info.get("graph_module"):
            problems.append(
                f"{config.redis_url} answers, but has no FalkorDB graph module. "
                f"This is a plain Redis; graph queries cannot work against it."
            )
        else:
            # What the project would actually read. A namespace that resolves
            # but holds nothing answers every question with a valid-looking
            # negative, which is worse than failing.
            try:
                store = open_store(config)
                served_nodes = store.node_count()
            except Exception:  # noqa: BLE001 — diagnosis must not itself fail
                served_nodes = None
            report["served_nodes"] = served_nodes
            if served_nodes == 0:
                problems.append(
                    f"The graph this project reads ({config.graph_name or 'navegador'}) "
                    f"is empty. Queries will return nothing, which is not the same as "
                    f"'not found'. Ingest it, or migrate an existing local graph: "
                    f"navegador storage migrate"
                )

    if record.is_stranded:
        # A local file alongside a populated server graph is leftover from a
        # completed migration, not an ingest writing where nothing reads.
        if served_nodes:
            notes.append(
                f"A local graph file is still present ({record.db_bytes / 1024 / 1024:.1f} MB) "
                f"but the server graph is populated, so nothing reads it. Remove it, or "
                f"re-run the migration with --prune."
            )
        else:
            problems.append(
                f"This project declares a Redis backend but holds "
                f"{record.db_bytes / 1024 / 1024:.1f} MB of local graph data — those "
                f"ingests are not visible to anything reading the server. "
                f"Migrate it: navegador storage migrate"
            )

    report["problems"] = problems
    report["notes"] = notes

    if as_json:
        click.echo(json.dumps(report, indent=2, default=str))
        raise SystemExit(1 if problems else 0)

    console.print(f"[bold]Storage[/bold]: {config.describe()}")
    if config.is_redis:
        info = report["server"]
        if info.get("reachable") and info.get("graph_module"):
            console.print(
                f"[green]Server OK[/green] — FalkorDB {info.get('module_version')}, "
                f"{len(info.get('graphs', []))} graph(s), {info.get('used_memory_human')}"
            )
        else:
            console.print(f"[red]Server unusable[/red] — {info.get('error', 'no graph module')}")
    if record.db_bytes:
        console.print(
            f"Local graph file: {record.db_bytes / 1024 / 1024:.1f} MB at {record.db_path}"
        )

    for note in notes:
        console.print(f"[dim]·[/dim] {note}")

    if problems:
        console.print()
        for problem in problems:
            console.print(f"[yellow]![/yellow] {problem}")
        raise SystemExit(1)
    console.print("[green]No problems found.[/green]")


@main.command("scan")
@click.argument("root", type=click.Path(exists=True), default=".")
@click.option("--depth", default=6, show_default=True, help="Maximum directory depth to search.")
@click.option("--json", "as_json", is_flag=True)
def scan_cmd(root: str, depth: int, as_json: bool):
    """Inventory every navegador project under ROOT and where its graph lives.

    \b
    Flags projects that declare a shared backend while holding a populated local
    graph — ingests that reported success but are invisible to the server.

    \b
    Examples:
      navegador scan ~/repos
      navegador scan ~/repos --json
    """
    from navegador.inventory import recommend_central_server, scan

    records = scan(root, max_depth=depth)
    recommended, reasons = recommend_central_server(records)

    if as_json:
        click.echo(
            json.dumps(
                {
                    "root": str(Path(root).resolve()),
                    "projects": [r.to_dict() for r in records],
                    "central_server_recommended": recommended,
                    "reasons": reasons,
                },
                indent=2,
                default=str,
            )
        )
        return

    if not records:
        console.print(f"No navegador projects found under {root}.")
        return

    table = Table(title=f"{len(records)} navegador project(s) under {root}")
    table.add_column("Project", style="cyan", overflow="fold")
    table.add_column("Declared")
    table.add_column("Local graph", justify="right")
    table.add_column("State")
    for record in sorted(records, key=lambda r: -r.db_bytes):
        size = f"{record.db_bytes / 1024 / 1024:.1f} MB" if record.db_bytes else "—"
        state = "[yellow]stranded[/yellow]" if record.is_stranded else ""
        table.add_row(str(record.root), record.declared_backend, size, state)
    console.print(table)

    stranded = [r for r in records if r.is_stranded]
    if stranded:
        console.print(
            f"\n[yellow]{len(stranded)} project(s) stranded[/yellow] — declared Redis, "
            f"data on disk. Migrate with: [cyan]navegador storage migrate --all --root "
            f"{root}[/cyan]"
        )

    if recommended:
        console.print("\n[bold]A shared graph server would help here:[/bold]")
        for reason in reasons:
            console.print(f"  • {reason}")
        console.print("\nSet one up with: [cyan]navegador server install[/cyan]")


# ── Migration: embedded → shared server (#172) ───────────────────────────────


@main.group()
def storage():
    """Inspect and move the graph data behind the [storage] configuration."""


@main.command("locate")
@click.argument("intent")
@click.option("--db", default="", help="Graph to search. Default: resolved storage.")
@click.option("--target", default=".", type=click.Path())
@click.option("-n", "--limit", default=10)
@click.option("--json", "as_json", is_flag=True)
def locate(intent: str, db: str, target: str, limit: int, as_json: bool):
    """
    Where to look for INTENT, ranked, with the reason for each place.

    Returns places to look, not an answer. Exact text matches, symbol names,
    documents and semantic similarity are fused on rank, so the top result is
    where several kinds of evidence agree.
    """
    from navegador.targeting import Targeting

    store = _get_store(db, target=target)
    candidates = Targeting(store).locate(intent, limit=limit)

    if as_json:
        click.echo(json.dumps([c.to_dict() for c in candidates], indent=2))
        return
    if not candidates:
        console.print("[dim]Nothing found. Has this repository been ingested?[/dim]")
        return

    table = Table(title=f"Where to look — {intent}")
    table.add_column("Where", style="cyan", overflow="fold")
    table.add_column("Score", justify="right", width=7)
    table.add_column("Why", style="dim", overflow="fold")
    for candidate in candidates:
        where = candidate.path + (f":{candidate.line}" if candidate.line else "")
        table.add_row(where, f"{candidate.score:.4f}", "; ".join(candidate.reasons)[:90])
    console.print(table)


@main.command("scope")
@click.argument("symbol")
@click.option("--db", default="", help="Graph to search. Default: resolved storage.")
@click.option("--target", default=".", type=click.Path())
@click.option("--depth", default=2, help="How far to follow calls, references and imports.")
@click.option("--pattern", default="", help="Search within the scope instead of listing it.")
@click.option("-n", "--limit", default=50)
@click.option("--json", "as_json", is_flag=True)
def scope(symbol: str, db: str, target: str, depth: int, pattern: str, limit: int, as_json: bool):
    """
    Files reachable from SYMBOL — the set worth searching.

    With --pattern, searches only those files. This is the reduction a flat
    text index cannot compute: it follows calls, references and imports.
    """
    from navegador.targeting import Targeting

    store = _get_store(db, target=target)
    targeting = Targeting(store)
    paths = targeting.scope_for(symbol, depth=depth)

    if pattern:
        matches = targeting.search_within(symbol, pattern, depth=depth, limit=limit)
        if as_json:
            click.echo(
                json.dumps(
                    {"symbol": symbol, "files": paths, "matches": [m.to_dict() for m in matches]},
                    indent=2,
                )
            )
            return
        console.print(f"[dim]{len(paths)} file(s) in scope[/dim]")
        for match in matches:
            console.print(
                f"[cyan]{match.path}[/cyan]:[green]{match.line}[/green]: {match.text.strip()}"
            )
        return

    if as_json:
        click.echo(json.dumps({"symbol": symbol, "files": paths}, indent=2))
        return
    if not paths:
        console.print(f"[dim]No scope found for {symbol}.[/dim]")
        return
    console.print(f"[bold]{len(paths)} file(s) reachable from {symbol}:[/bold]")
    for path in paths:
        console.print(f"  {path}")


@main.command("grep")
@click.argument("pattern")
@click.option("--db", default="", help="Graph to search. Default: resolved storage.")
@click.option("--target", default=".", type=click.Path(), help="Project the graph belongs to.")
@click.option("-e", "--regex", is_flag=True, help="Treat PATTERN as a regular expression.")
@click.option("-i", "--ignore-case", is_flag=True)
@click.option("-n", "--limit", default=100, help="Maximum matches.")
@click.option(
    "--reindex", is_flag=True, help="Index any stored content not yet in the trigram index."
)
@click.option("--json", "as_json", is_flag=True)
def grep(
    pattern: str,
    db: str,
    target: str,
    regex: bool,
    ignore_case: bool,
    limit: int,
    reindex: bool,
    as_json: bool,
):
    """
    Exact substring or regex search over indexed content.

    Trigrams narrow the candidate set inside the database and the real pattern
    then matches against stored text, so results are exact — verified against
    ripgrep on this package's own source. Cost scales with the number of
    matches rather than the size of the corpus, so a miss is nearly free.
    """
    from navegador.graph.trigram import TrigramIndex

    store = _get_store(db, target=target)
    index = TrigramIndex(store)
    if reindex:
        index.index_graph()

    matches = index.search(pattern, is_regex=regex, limit=limit, ignore_case=ignore_case)

    if as_json:
        click.echo(json.dumps([m.to_dict() for m in matches], indent=2))
        return

    if not matches:
        console.print(
            "[dim]No matches. If content has not been indexed yet, run with --reindex.[/dim]"
        )
        return
    for match in matches:
        console.print(
            f"[cyan]{match.path}[/cyan]:[green]{match.line}[/green]: {match.text.strip()}"
        )


def _audit_reports(server_url: str, root: str):
    """Audit every graph, checking against whatever checkouts we can find."""
    from navegador.graph.audit import audit_server
    from navegador.inventory import scan

    roots = {}
    for record in scan(root):
        if record.effective and record.effective.graph_name:
            roots[record.effective.graph_name] = record.root
    return audit_server(server_url, roots)


def _resolve_server_url(db: str) -> str:
    from navegador.config import resolve_storage

    storage_config = resolve_storage(db or None)
    if not storage_config.is_redis:
        raise click.ClickException(
            "This inspects a shared server; the resolved backend is "
            f"{storage_config.describe()}. Pass --db redis://... or configure [storage]."
        )
    return storage_config.redis_url


@storage.command("audit")
@click.option("--db", default="", help="Server to inspect. Default: resolved storage.")
@click.option(
    "--root",
    default=str(Path.home() / "repos"),
    type=click.Path(),
    help="Tree searched for the checkouts a graph is checked against. A graph "
    "with no checkout here is reported 'unknown', never assumed stale.",
)
@click.option("--json", "as_json", is_flag=True)
def storage_audit(db: str, root: str, as_json: bool):
    """
    Report graphs that have stopped describing anything real.

    Four verdicts matter: 'junk' (a name we would not have written), 'empty',
    'stale' (its file paths no longer exist on disk) and 'duplicate' (another
    graph covers the same repository). Nothing is deleted here.
    """
    server_url = _resolve_server_url(db)
    reports = _audit_reports(server_url, root)

    if as_json:
        click.echo(json.dumps([r.to_dict() for r in reports], indent=2))
        return

    table = Table(title=f"Graph audit — {server_url}")
    # Graph names are long and the interesting columns are the narrow ones, so
    # the name truncates rather than squeezing everything else to ellipses.
    table.add_column("Graph", style="cyan", max_width=42, overflow="ellipsis", no_wrap=True)
    table.add_column("Verdict", width=9)
    table.add_column("Nodes", justify="right", width=9)
    table.add_column("Size", justify="right", width=9)
    table.add_column("Why", style="dim", overflow="fold")
    colours = {
        "healthy": "green",
        "stale": "red",
        "junk": "red",
        "empty": "yellow",
        "duplicate": "yellow",
        "unknown": "dim",
    }
    for report in sorted(reports, key=lambda r: -r.size_bytes):
        verdict = report.verdict
        table.add_row(
            report.name,
            f"[{colours[verdict]}]{verdict}[/{colours[verdict]}]",
            f"{report.nodes:,}",
            f"{report.size_bytes / 1048576:.1f} MB",
            report.explain(),
        )
    console.print(table)

    reclaimable = [r for r in reports if r.reclaimable]
    if reclaimable:
        total = sum(r.size_bytes for r in reclaimable) / 1048576
        console.print(
            f"\n[yellow]{len(reclaimable)} graph(s) reclaimable, {total:.1f} MB[/yellow] — "
            "run [bold]navegador storage prune[/bold] to see what would go."
        )


@storage.command("reindex")
@click.option("--db", default="", help="Server to inspect. Default: resolved storage.")
@click.option(
    "--root",
    default=str(Path.home() / "repos"),
    type=click.Path(),
    help="Tree searched for the checkouts behind each graph.",
)
@click.option("--yes", is_flag=True, help="Actually re-ingest. Without it, this is a dry run.")
@click.option("--json", "as_json", is_flag=True)
def storage_reindex(db: str, root: str, yes: bool, as_json: bool):
    """
    Rebuild graphs holding files a current ingest would exclude.

    Ingest stopped indexing gitignored files, but graphs built before that
    still hold whatever the old fixed skip-list let through — one real graph
    was 99.98% build output. Those graphs are not stale and not duplicates, so
    `storage audit` does not flag them; this is the command that finds them.

    Dry run unless --yes.
    """
    from navegador.graph.audit import needs_reindex
    from navegador.ingestion import RepoIngester
    from navegador.inventory import scan

    server_url = _resolve_server_url(db)
    import falkordb
    import redis as redis_lib

    database = falkordb.FalkorDB.from_url(server_url)
    connection = redis_lib.from_url(server_url)

    affected = []
    for record in scan(root):
        config = record.effective
        if not config or not config.graph_name:
            continue
        checked, excluded = needs_reindex(database, connection, config.graph_name, record.root)
        if excluded:
            affected.append(
                {
                    "graph": config.graph_name,
                    "root": str(record.root),
                    "checked": checked,
                    "excluded": excluded,
                    "share": round(excluded / checked, 3) if checked else 0.0,
                }
            )

    if not affected:
        if as_json:
            click.echo(json.dumps({"reindexed": [], "dry_run": not yes}, indent=2))
        else:
            console.print("[green]No graph holds files a current ingest would exclude.[/green]")
        return

    if not yes:
        if as_json:
            click.echo(json.dumps({"would_reindex": affected, "dry_run": True}, indent=2))
            return
        table = Table(title="Graphs a current ingest would build differently")
        table.add_column("Graph", style="cyan", max_width=42, overflow="ellipsis")
        table.add_column("Sampled", justify="right")
        table.add_column("Now excluded", justify="right")
        table.add_column("Share", justify="right")
        for item in sorted(affected, key=lambda i: -i["share"]):
            table.add_row(
                item["graph"],
                str(item["checked"]),
                str(item["excluded"]),
                f"{item['share']:.0%}",
            )
        console.print(table)
        console.print("\nRe-run with [bold]--yes[/bold] to rebuild these.")
        return

    rebuilt = []
    for item in affected:
        console.print(f"[bold]Re-ingesting[/bold] {item['graph']} …")
        try:
            store = _get_store(db, target=item["root"])
            stats = RepoIngester(store).ingest(item["root"], clear=True)
            rebuilt.append({**item, "files": stats.get("files", 0)})
        except Exception as exc:  # noqa: BLE001 — one bad repo must not stop the sweep
            console.print(f"[red]  failed: {exc}[/red]")

    if as_json:
        click.echo(json.dumps({"reindexed": rebuilt, "dry_run": False}, indent=2))
    else:
        console.print(f"[green]Rebuilt {len(rebuilt)} graph(s).[/green]")


@storage.command("prune")
@click.option("--db", default="", help="Server to prune. Default: resolved storage.")
@click.option("--root", default=str(Path.home() / "repos"), type=click.Path())
@click.option(
    "--include-stale",
    is_flag=True,
    help="Also delete graphs whose files no longer exist. Off by default: a "
    "moved checkout and a deleted one look identical from here, and one of "
    "those is recoverable by re-ingesting while the other is not.",
)
@click.option("--yes", is_flag=True, help="Actually delete. Without it, this is a dry run.")
@click.option("--json", "as_json", is_flag=True)
def storage_prune(db: str, root: str, include_stale: bool, yes: bool, as_json: bool):
    """Delete junk and empty graphs. Dry run unless --yes is given."""
    from navegador.graph.audit import prune as prune_graphs

    server_url = _resolve_server_url(db)
    reports = _audit_reports(server_url, root)
    doomed = [
        r
        for r in reports
        if r.verdict in {"junk", "empty"} or (include_stale and r.verdict == "stale")
    ]

    if not doomed:
        if as_json:
            click.echo(json.dumps({"removed": [], "dry_run": not yes}, indent=2))
        else:
            console.print("[green]Nothing to prune.[/green]")
        return

    reclaimed = sum(r.size_bytes for r in doomed) / 1048576
    if not yes:
        if as_json:
            click.echo(
                json.dumps(
                    {"would_remove": [r.to_dict() for r in doomed], "dry_run": True}, indent=2
                )
            )
            return
        console.print(f"[bold]Would delete {len(doomed)} graph(s), {reclaimed:.1f} MB:[/bold]")
        for report in sorted(doomed, key=lambda r: -r.size_bytes):
            console.print(f"  {report.name}  [dim]{report.explain()}[/dim]")
        stale_held = [r for r in reports if r.verdict == "stale" and not include_stale]
        if stale_held:
            console.print(
                f"\n[dim]{len(stale_held)} stale graph(s) held back; "
                "--include-stale to delete them too.[/dim]"
            )
        console.print("\nRe-run with [bold]--yes[/bold] to delete.")
        return

    removed = prune_graphs(server_url, doomed, include_stale=include_stale)
    if as_json:
        click.echo(json.dumps({"removed": removed, "dry_run": False}, indent=2))
    else:
        console.print(f"[green]Deleted {len(removed)} graph(s), {reclaimed:.1f} MB.[/green]")


@storage.command("migrate")
@click.option("--target", default=".", type=click.Path(), help="Project to migrate.")
@click.option("--to", "dest_url", default="", help="Destination server URL.")
@click.option(
    "--from", "source_url", default="", help="Migrate from this server instead of a file."
)
@click.option("--all", "migrate_all", is_flag=True, help="Migrate every project under --root.")
@click.option("--root", default=".", type=click.Path(), help="Tree to search with --all.")
@click.option(
    "--graph", "graph_name", default="", help="Destination graph name. Default: per-repo."
)
@click.option(
    "--default-as",
    "default_as",
    default="",
    metavar="NAME",
    help="With --from: destination name for the source server's unnamespaced "
    "'navegador' graph, so it does not collide with the destination's own.",
)
@click.option(
    "--overwrite", is_flag=True, help="Replace destination graphs that already hold data."
)
@click.option(
    "--write-config",
    is_flag=True,
    help="Point each migrated project's .navegador/config.toml at the shared "
    "server and the graph it was copied into. Configs tracked by git are left "
    "alone — a committed [storage] is a decision the repo makes for everyone.",
)
@click.option("--dry-run", is_flag=True, help="Report what would move without writing.")
@click.option("--prune", is_flag=True, help="Delete the local graph file after a verified copy.")
@click.option("--json", "as_json", is_flag=True)
def storage_migrate(
    target: str,
    dest_url: str,
    source_url: str,
    migrate_all: bool,
    root: str,
    graph_name: str,
    default_as: str,
    overwrite: bool,
    write_config: bool,
    dry_run: bool,
    prune: bool,
    as_json: bool,
):
    """Copy local graphs into a shared FalkorDB server.

    \b
    The copy is verified: node and edge counts must match on both sides or the
    command fails rather than reporting success. The source is never modified
    unless --prune is passed, and never when verification fails.

    \b
    Examples:
      navegador storage migrate                       # this project → its server
      navegador storage migrate --all --root ~/repos  # every project under a tree
      navegador storage migrate --from redis://localhost:6380 --to redis://localhost:6379
      navegador storage migrate --all --root ~/repos --dry-run
    """
    from navegador.config import DEFAULT_REDIS_URL, resolve_storage

    if source_url:
        results = [
            _migrate_server(
                source_url,
                dest_url or DEFAULT_REDIS_URL,
                dry_run,
                default_as=default_as,
                overwrite=overwrite,
            )
        ]
    elif migrate_all:
        from navegador.inventory import scan

        # Select on the graph file existing, not on its size: the size threshold
        # is a heuristic for spotting stranded ingests, and a small repo's
        # legitimate graph must not be silently left behind. Genuinely empty
        # sources are reported as skipped once opened.
        records = [r for r in scan(root) if r.db_path]
        if not records:
            console.print(f"No projects with a local graph found under {root}.")
            return
        results = [
            _migrate_project(
                r.root,
                dest_url,
                graph_name,
                dry_run=dry_run,
                prune=prune,
                overwrite=overwrite,
                write_config=write_config,
            )
            for r in records
        ]
    else:
        resolved = resolve_storage(target=target)
        if not dest_url and not resolved.is_redis:
            raise click.UsageError(
                "No destination server. Either configure one for this project "
                "(navegador init --storage redis) or pass --to redis://host:port."
            )
        results = [
            _migrate_project(
                Path(target),
                dest_url,
                graph_name,
                dry_run=dry_run,
                prune=prune,
                overwrite=overwrite,
                write_config=write_config,
            )
        ]

    failed = [r for r in results if r.get("status") == "failed"]

    if as_json:
        click.echo(json.dumps({"results": results}, indent=2, default=str))
        raise SystemExit(1 if failed else 0)

    table = Table(title="Dry run — nothing written" if dry_run else "Migration")
    table.add_column("Source", style="cyan", overflow="fold")
    table.add_column("Destination graph", overflow="fold")
    table.add_column("Nodes", justify="right")
    table.add_column("Edges", justify="right")
    table.add_column("Result")
    for result in results:
        status = result.get("status", "")
        colour = {"ok": "green", "failed": "red", "planned": "yellow"}.get(status, "")
        table.add_row(
            str(result.get("source", "")),
            str(result.get("graph", "")),
            str(result.get("nodes", "")),
            str(result.get("edges", "")),
            f"[{colour}]{status}[/{colour}]" if colour else status,
        )
    console.print(table)

    for result in failed:
        console.print(f"[red]{result.get('source')}[/red]: {result.get('error')}")
    if failed:
        raise SystemExit(1)


def _plan_graph_names(source_client, default_as: str) -> dict[str, str]:
    """
    Map each source graph name to its destination name.

    A store's unnamespaced ``navegador`` graph is the one that collides when
    stores are consolidated — every store has one. Giving it a namespace on the
    way in is what makes many sources fit in one server.
    """
    from navegador.graph.store import GraphStore

    return {
        name: (default_as if name == GraphStore.GRAPH_NAME and default_as else name)
        for name in sorted(source_client.list_graphs())
    }


def _copy_all_graphs(
    source_client,
    dest_url: str,
    default_as: str,
    dry_run: bool,
    overwrite: bool,
) -> dict:
    """
    Copy every named graph from one store to another, refusing silent clobbers.

    A store holds more than one graph whenever a federated or workspace ingest
    has run against it, so copying only the default graph would quietly leave
    most of the data behind.
    """
    from navegador.graph import GraphStore
    from navegador.graph.transfer import copy_graph

    plan = _plan_graph_names(source_client, default_as)
    if not plan:
        return {"status": "skipped", "error": "no graphs in source", "nodes": 0, "edges": 0}

    source_total = sum(source_client.with_graph(src).node_count() for src in plan)
    if not source_total:
        return {"status": "skipped", "error": "source graphs are empty", "nodes": 0, "edges": 0}

    # A dry run is precisely what you reach for before the destination is up, so
    # an unreachable one downgrades the clash check to a warning instead of
    # failing the report the user asked for.
    dest_client = None
    reachable = False
    existing: set[str] = set()
    try:
        dest_client = GraphStore.redis(dest_url)
        existing = set(dest_client.list_graphs())
        reachable = True
    except Exception as e:  # noqa: BLE001
        if not dry_run:
            raise
        progress.print(
            f"  [yellow]destination not reachable, cannot check for clashes:[/yellow] {e}"
        )

    if not overwrite and reachable:
        clashes = [
            f"{src} → {dst}"
            for src, dst in plan.items()
            if dst in existing and dest_client.with_graph(dst).node_count() > 0
        ]
        if clashes:
            return {
                "status": "failed",
                "error": (
                    "destination already holds data for: "
                    + ", ".join(clashes)
                    + ". Rename the source's default graph with --default-as NAME, "
                    "or pass --overwrite to replace them."
                ),
            }

    if dry_run:
        nodes = edges = 0
        for src, dst in plan.items():
            counts = source_client.with_graph(src)
            n, e = counts.node_count(), counts.edge_count()
            nodes += n
            edges += e
            progress.print(f"  {src} → {dst}: {n} nodes, {e} edges")
        return {"status": "planned", "nodes": nodes, "edges": edges, "graphs": len(plan)}

    nodes = edges = 0
    for src, dst in plan.items():
        stats = copy_graph(source_client.with_graph(src), dest_client.with_graph(dst))
        nodes += stats["nodes"]
        edges += stats["edges"]
        progress.print(f"  {src} → {dst}: {stats['nodes']} nodes, {stats['edges']} edges")

    return {"status": "ok", "nodes": nodes, "edges": edges, "graphs": len(plan)}


def _migrate_project(
    root: Path,
    dest_url: str,
    graph_name: str,
    *,
    dry_run: bool = False,
    prune: bool = False,
    overwrite: bool = False,
    write_config: bool = False,
) -> dict:
    """Copy a project's embedded graphs — all of them — into the shared server."""
    from navegador.config import DEFAULT_REDIS_URL, resolve_storage
    from navegador.federation import repo_name_from_path
    from navegador.graph import GraphStore
    from navegador.graph.transfer import TransferError

    root = Path(root).resolve()
    db_file = root / ".navegador" / "graph.db"
    resolved = resolve_storage(target=root)
    url = dest_url or (resolved.redis_url if resolved.is_redis else DEFAULT_REDIS_URL)
    default_as = graph_name or f"navegador_{repo_name_from_path(root)}"

    result: dict = {"source": str(db_file), "graph": default_as, "url": url}

    if not db_file.is_file():
        return {**result, "status": "skipped", "error": "no local graph file"}

    source = None
    try:
        source = GraphStore.sqlite(db_file)
        outcome = _copy_all_graphs(source, url, default_as, dry_run, overwrite)
        result.update(outcome)
    except TransferError as e:
        return {**result, "status": "failed", "error": str(e)}
    except Exception as e:  # noqa: BLE001 — one bad repo must not abort the batch
        return {**result, "status": "failed", "error": str(e)}
    finally:
        if source is not None:
            source.close()

    if result.get("status") == "ok" and write_config:
        # Only after a verified copy: repointing a project at a graph that was
        # not fully written would be worse than leaving it on the local file.
        if _config_is_tracked(root):
            result["config_skipped"] = "tracked by git"
        else:
            _point_config_at(root, url, default_as)
            result["config_updated"] = True

    if prune and result.get("status") == "ok":
        # Only ever reached after copy_graph verified the counts matched.
        db_file.unlink()
        result["pruned"] = True

    return result


def _config_is_tracked(root: Path) -> bool:
    """
    True when the project's config.toml is committed to git.

    A tracked ``[storage]`` is a decision the repository makes for everyone who
    clones it. Repointing it at one developer's machine-local server would break
    every teammate who has no such server, so --write-config leaves it alone.
    """
    import subprocess

    try:
        result = subprocess.run(
            ["git", "ls-files", "--error-unmatch", ".navegador/config.toml"],
            cwd=root,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _point_config_at(root: Path, redis_url: str, graph_name: str) -> None:
    """Rewrite a project's [storage] table to use the shared server."""
    from navegador.config import init_project, read_config

    existing = read_config(root / ".navegador" / "config.toml")
    llm = existing.get("llm", {}) if isinstance(existing.get("llm"), dict) else {}
    cluster = existing.get("cluster", {}) if isinstance(existing.get("cluster"), dict) else {}
    init_project(
        root,
        storage="redis",
        redis_url=redis_url,
        graph_name=graph_name,
        llm_provider=str(llm.get("provider", "")),
        llm_model=str(llm.get("model", "")),
        cluster=bool(cluster.get("enabled", False)),
        commit_graph=True,  # never re-touch .gitignore on an existing project
    )


def _migrate_server(
    source_url: str,
    dest_url: str,
    dry_run: bool,
    default_as: str = "",
    overwrite: bool = False,
) -> dict:
    """Copy every named graph from one FalkorDB server to another."""
    from navegador.graph import GraphStore
    from navegador.graph.transfer import TransferError

    result: dict = {"source": source_url, "graph": default_as or "(all)", "url": dest_url}
    try:
        source_client = GraphStore.redis(source_url)
        result.update(_copy_all_graphs(source_client, dest_url, default_as, dry_run, overwrite))
    except TransferError as e:
        return {**result, "status": "failed", "error": str(e)}
    except Exception as e:  # noqa: BLE001
        return {**result, "status": "failed", "error": str(e)}
    return result


# ── Supergraph contract v1.0 (#158) ──────────────────────────────────────────


@main.group()
def contract():
    """Supergraph interop contract — the code realm's output boundary."""


@contract.command("resolve")
@click.argument("address")
@DB_OPTION
@click.option("--json", "as_json", is_flag=True)
def contract_resolve(address: str, db: str, as_json: bool):
    """Resolve a contract ADDRESS into the code graph.

    \b
    This is the brain-to-code hop: given the target of an `implemented_in` join
    edge, it returns the node and the callers/callees traversal continues to.

    \b
    Examples:
      navegador contract resolve "code:src/auth.py#validate_token"
      navegador contract resolve "myrepo/code:src/auth.py"
    """
    from navegador.contract import AddressError, resolve

    store = _get_store(db)
    try:
        resolved = resolve(store, address)
    except AddressError as e:
        raise click.ClickException(str(e)) from e

    if as_json:
        click.echo(json.dumps(resolved.to_dict(), indent=2, default=str))
        raise SystemExit(0 if resolved.found else 1)

    if not resolved.found:
        console.print(f"[yellow]No node at[/yellow] {resolved.address}")
        console.print(
            "  The repo may not be ingested, or its paths may be recorded "
            "relative to a different root. Check: [cyan]navegador repo nodes[/cyan]"
        )
        raise SystemExit(1)

    console.print(f"[green]{resolved.label}[/green] {resolved.name}")
    console.print(f"  address: {resolved.address}")
    console.print(f"  path:    {resolved.path}")


@contract.command("propose")
@click.option("--repo", default="", help="Federation namespace to qualify targets with.")
@click.option(
    "--min-confidence",
    default=0.5,
    show_default=True,
    help="Drop proposals scoring below this.",
)
@DB_OPTION
@click.option("--json", "as_json", is_flag=True)
def contract_propose(repo: str, min_confidence: float, db: str, as_json: bool):
    """Propose join edges from inferred documentation-to-code affinity.

    \b
    Emits contract-format `implemented_in` proposals with confidence and
    evidence. Navegador proposes; the brain reviews and decides what to commit.

    \b
    Examples:
      navegador contract propose --repo myrepo --json
      navegador contract propose --min-confidence 0.8
    """
    from navegador.contract import propose_join_edges

    payload = propose_join_edges(_get_store(db), repo=repo, min_confidence=min_confidence)

    if as_json:
        click.echo(json.dumps(payload, indent=2, default=str))
        return

    proposals = payload["proposals"]
    if not proposals:
        console.print("No join edges to propose above that confidence.")
        return

    table = Table(title=f"{len(proposals)} join-edge proposal(s), contract {payload['contract']}")
    table.add_column("Source", style="cyan", overflow="fold")
    table.add_column("Edge")
    table.add_column("Target address", overflow="fold")
    table.add_column("Conf.", justify="right")
    for item in proposals:
        table.add_row(
            f"{item['source']['kind']}:{item['source']['name']}",
            item["edge"],
            item["target"],
            f"{item['confidence']:.2f}",
        )
    console.print(table)
    console.print("\nReview and commit these in the brain — navegador only proposes.")


# ── Manual: documentation packaged with the CLI (#172) ───────────────────────


@main.command("manual")
@click.argument("page", required=False, default="")
@click.option("--search", "query", default="", metavar="TERM", help="Search all pages for TERM.")
@click.option("--list", "list_only", is_flag=True, help="List available pages and exit.")
@click.option("--raw", is_flag=True, help="Emit raw markdown instead of rendered output.")
@click.option("--json", "as_json", is_flag=True)
def manual(page: str, query: str, list_only: bool, raw: bool, as_json: bool):
    """Read navegador's own documentation, offline.

    \b
    The docs ship inside the package — no network, no mkdocs install. PAGE is a
    slug such as 'guide/mcp-integration', or any unambiguous fragment of one.

    \b
    Examples:
      navegador manual                            # list every page
      navegador manual quickstart                 # read one page
      navegador manual guide/mcp-integration
      navegador manual --search "redis"
    """
    from navegador.manual import ManualError, find_page, list_pages, search

    try:
        if query:
            hits = search(query)
            if as_json:
                click.echo(json.dumps({"query": query, "results": hits}, indent=2))
                return
            if not hits:
                console.print(f"No documentation matches [cyan]{query}[/cyan].")
                return
            for hit in hits:
                console.print(
                    f"[cyan]{hit['slug']}[/cyan] — {hit['title']} ({hit['matches']} hits)"
                )
                for line in hit["context"]:
                    console.print(f"    {line[:120]}")
            return

        if page and not list_only:
            doc = find_page(page)
            if as_json:
                click.echo(json.dumps({**doc.to_dict(), "content": doc.read()}, indent=2))
            elif raw:
                click.echo(doc.read())
            else:
                from rich.markdown import Markdown

                console.print(Markdown(doc.read()))
            return

        pages = list_pages()
        if as_json:
            click.echo(json.dumps([p.to_dict() for p in pages], indent=2))
            return

        table = Table(title=f"navegador documentation ({len(pages)} pages)")
        table.add_column("Page", style="cyan")
        table.add_column("Title")
        for doc in pages:
            table.add_row(doc.slug, doc.title)
        console.print(table)
        console.print("\nRead one with: [cyan]navegador manual <page>[/cyan]")
    except ManualError as e:
        raise click.ClickException(str(e)) from e


if __name__ == "__main__":
    # PyInstaller builds the standalone binaries by targeting this file, which
    # runs it as __main__ rather than going through the `navegador` console
    # script. Without this guard the module only defines the command group and
    # exits 0 without doing anything.
    main()
