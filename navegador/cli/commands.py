"""
Navegador CLI — ingest repos, load context, serve MCP.

All commands support --format json for clean stdout output suitable for
piping to agents or other tools without MCP overhead.
"""

import asyncio
import logging

import click
from rich.console import Console
from rich.table import Table

console = Console()

DB_OPTION = click.option(
    "--db", default=".navegador/graph.db", show_default=True, help="Graph DB path."
)
FMT_OPTION = click.option(
    "--format", "fmt",
    type=click.Choice(["markdown", "json"]),
    default="markdown",
    show_default=True,
    help="Output format. Use json for agent/pipe consumption.",
)


def _get_store(db: str):
    from navegador.graph import GraphStore
    return GraphStore.sqlite(db)


def _emit(text: str, fmt: str) -> None:
    """Print text — raw to stdout for json, rich for markdown."""
    if fmt == "json":
        click.echo(text)
    else:
        console.print(text)


@click.group()
@click.version_option(package_name="navegador")
def main():
    """Navegador — AST + knowledge graph context engine for AI coding agents."""
    logging.basicConfig(level=logging.WARNING)


@main.command()
@click.argument("repo_path", type=click.Path(exists=True))
@DB_OPTION
@click.option("--clear", is_flag=True, help="Clear existing graph before ingesting.")
@click.option("--json", "as_json", is_flag=True, help="Output stats as JSON.")
def ingest(repo_path: str, db: str, clear: bool, as_json: bool):
    """Ingest a repository into the navegador graph."""
    import json

    from navegador.ingestion import RepoIngester

    store = _get_store(db)
    ingester = RepoIngester(store)

    if as_json:
        stats = ingester.ingest(repo_path, clear=clear)
        click.echo(json.dumps(stats, indent=2))
    else:
        with console.status(f"[bold]Ingesting[/bold] {repo_path}..."):
            stats = ingester.ingest(repo_path, clear=clear)
        table = Table(title="Ingestion complete", show_header=True)
        table.add_column("Metric", style="cyan")
        table.add_column("Count", justify="right", style="green")
        for k, v in stats.items():
            table.add_row(k.capitalize(), str(v))
        console.print(table)


@main.command()
@click.argument("file_path")
@DB_OPTION
@FMT_OPTION
def context(file_path: str, db: str, fmt: str):
    """Load context for a file — all symbols and their relationships."""
    from navegador.context import ContextLoader

    loader = ContextLoader(_get_store(db))
    bundle = loader.load_file(file_path)
    _emit(bundle.to_json() if fmt == "json" else bundle.to_markdown(), fmt)


@main.command()
@click.argument("name")
@click.option("--file", "file_path", default="", help="File path to narrow the search.")
@click.option("--depth", default=2, show_default=True, help="Call graph traversal depth.")
@DB_OPTION
@FMT_OPTION
def function(name: str, file_path: str, db: str, depth: int, fmt: str):
    """Load context for a function — callers, callees, signature."""
    from navegador.context import ContextLoader

    loader = ContextLoader(_get_store(db))
    bundle = loader.load_function(name, file_path=file_path, depth=depth)
    _emit(bundle.to_json() if fmt == "json" else bundle.to_markdown(), fmt)


@main.command("class")
@click.argument("name")
@click.option("--file", "file_path", default="", help="File path to narrow the search.")
@DB_OPTION
@FMT_OPTION
def class_(name: str, file_path: str, db: str, fmt: str):
    """Load context for a class — methods, inheritance, subclasses."""
    from navegador.context import ContextLoader

    loader = ContextLoader(_get_store(db))
    bundle = loader.load_class(name, file_path=file_path)
    _emit(bundle.to_json() if fmt == "json" else bundle.to_markdown(), fmt)


@main.command()
@click.argument("query")
@DB_OPTION
@click.option("--limit", default=20, show_default=True)
@FMT_OPTION
def search(query: str, db: str, limit: int, fmt: str):
    """Search for symbols (functions, classes, methods) by name."""
    import json

    from navegador.context import ContextLoader

    loader = ContextLoader(_get_store(db))
    results = loader.search(query, limit=limit)

    if fmt == "json":
        click.echo(json.dumps([
            {"type": r.type, "name": r.name, "file_path": r.file_path,
             "line_start": r.line_start, "docstring": r.docstring}
            for r in results
        ], indent=2))
        return

    if not results:
        console.print("[yellow]No results.[/yellow]")
        return

    table = Table(title=f"Search: {query!r}", show_header=True)
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
    """Run a raw Cypher query and print results as JSON."""
    import json

    store = _get_store(db)
    result = store.query(cypher)
    rows = result.result_set or []
    click.echo(json.dumps(rows, default=str, indent=2))


@main.command()
@DB_OPTION
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def stats(db: str, as_json: bool):
    """Show graph statistics."""
    import json

    store = _get_store(db)
    data = {"nodes": store.node_count(), "edges": store.edge_count()}

    if as_json:
        click.echo(json.dumps(data, indent=2))
    else:
        table = Table(title="Graph stats", show_header=True)
        table.add_column("Metric", style="cyan")
        table.add_column("Count", justify="right", style="green")
        for k, v in data.items():
            table.add_row(k.capitalize(), str(v))
        console.print(table)


@main.command()
@DB_OPTION
def mcp(db: str):
    """Start the MCP server for AI agent integration (stdio)."""
    from mcp.server.stdio import stdio_server  # type: ignore[import]

    from navegador.mcp import create_mcp_server

    server = create_mcp_server(lambda: _get_store(db))
    console.print("[green]Navegador MCP server running[/green] (stdio)")

    async def _run():
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())

    asyncio.run(_run())
