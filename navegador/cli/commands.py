"""
Navegador CLI — ingest repos, load context, serve MCP.
"""

import asyncio
import logging

import click
from rich.console import Console
from rich.table import Table

console = Console()


def _get_store(db: str):
    from navegador.graph import GraphStore
    return GraphStore.sqlite(db)


@click.group()
@click.version_option(package_name="navegador")
def main():
    """Navegador — AST + knowledge graph context engine for AI coding agents."""
    logging.basicConfig(level=logging.WARNING)


@main.command()
@click.argument("repo_path", type=click.Path(exists=True))
@click.option("--db", default=".navegador/graph.db", show_default=True, help="Graph DB path.")
@click.option("--clear", is_flag=True, help="Clear existing graph before ingesting.")
def ingest(repo_path: str, db: str, clear: bool):
    """Ingest a repository into the navegador graph."""
    from navegador.ingestion import RepoIngester

    store = _get_store(db)
    ingester = RepoIngester(store)

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
@click.option("--db", default=".navegador/graph.db", show_default=True)
@click.option("--format", "fmt", type=click.Choice(["markdown", "json"]), default="markdown")
def context(file_path: str, db: str, fmt: str):
    """Load and print context for a file."""
    from navegador.context import ContextLoader

    store = _get_store(db)
    loader = ContextLoader(store)
    bundle = loader.load_file(file_path)
    output = bundle.to_markdown() if fmt == "markdown" else bundle.to_json()
    console.print(output)


@main.command()
@click.argument("query")
@click.option("--db", default=".navegador/graph.db", show_default=True)
@click.option("--limit", default=20, show_default=True)
def search(query: str, db: str, limit: int):
    """Search for symbols (functions, classes) by name."""
    from navegador.context import ContextLoader

    store = _get_store(db)
    loader = ContextLoader(store)
    results = loader.search(query, limit=limit)

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
@click.option("--db", default=".navegador/graph.db", show_default=True)
def stats(db: str):
    """Show graph statistics."""
    store = _get_store(db)
    table = Table(title="Graph stats", show_header=True)
    table.add_column("Metric", style="cyan")
    table.add_column("Count", justify="right", style="green")
    table.add_row("Nodes", str(store.node_count()))
    table.add_row("Edges", str(store.edge_count()))
    console.print(table)


@main.command()
@click.option("--db", default=".navegador/graph.db", show_default=True)
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", default=8765, show_default=True)
def mcp(db: str, host: str, port: int):
    """Start the MCP server for AI agent integration."""
    from mcp.server.stdio import stdio_server  # type: ignore[import]

    from navegador.mcp import create_mcp_server

    def store_factory():
        return _get_store(db)

    server = create_mcp_server(store_factory)
    console.print("[green]Navegador MCP server running[/green] (stdio)")

    async def _run():
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())

    asyncio.run(_run())
