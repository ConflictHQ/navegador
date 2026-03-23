"""
Schema versioning and migrations for the navegador graph.

The schema version is stored as a property on a singleton :Meta node.
On store open, the current version is checked and any pending migrations
are applied sequentially.

Migration functions take a GraphStore and upgrade from version N to N+1.
"""

import logging

from navegador.graph.store import GraphStore

logger = logging.getLogger(__name__)

CURRENT_SCHEMA_VERSION = 2

# ── Migration registry ───────────────────────────────────────────────────────

_migrations: dict[int, callable] = {}


def migration(from_version: int):
    """Decorator to register a migration from `from_version` to `from_version + 1`."""

    def decorator(fn):
        _migrations[from_version] = fn
        return fn

    return decorator


# ── Core API ─────────────────────────────────────────────────────────────────


def get_schema_version(store: GraphStore) -> int:
    """Read the current schema version from the graph (0 if unset)."""
    result = store.query("MATCH (m:Meta {name: 'schema'}) RETURN m.version")
    rows = result.result_set or []
    if not rows or rows[0][0] is None:
        return 0
    return int(rows[0][0])


def set_schema_version(store: GraphStore, version: int) -> None:
    """Write the schema version to the graph."""
    store.query(
        "MERGE (m:Meta {name: 'schema'}) SET m.version = $version",
        {"version": version},
    )


def migrate(store: GraphStore) -> list[int]:
    """
    Apply all pending migrations to bring the graph up to CURRENT_SCHEMA_VERSION.

    Returns:
        List of versions that were applied.
    """
    current = get_schema_version(store)
    applied: list[int] = []

    while current < CURRENT_SCHEMA_VERSION:
        fn = _migrations.get(current)
        if fn is None:
            raise RuntimeError(f"No migration registered for version {current} -> {current + 1}")
        logger.info("Applying migration %d -> %d", current, current + 1)
        fn(store)
        current += 1
        set_schema_version(store, current)
        applied.append(current)

    return applied


def needs_migration(store: GraphStore) -> bool:
    """Check if the graph needs migration."""
    return get_schema_version(store) < CURRENT_SCHEMA_VERSION


# ── Migrations ───────────────────────────────────────────────────────────────


@migration(0)
def _migrate_0_to_1(store: GraphStore) -> None:
    """Initial schema — set version on existing graphs."""
    # No structural changes needed — this just stamps the version.
    logger.info("Stamping initial schema version")


@migration(1)
def _migrate_1_to_2(store: GraphStore) -> None:
    """Add content_hash property to File nodes for incremental ingestion."""
    # Set content_hash to empty string on existing File nodes that lack it.
    store.query("MATCH (f:File) WHERE f.content_hash IS NULL SET f.content_hash = ''")
    logger.info("Added content_hash to File nodes")
