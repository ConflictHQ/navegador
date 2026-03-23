"""
Fossil live integration — ATTACH DATABASE for zero-copy cross-DB queries.

Attaches a Fossil SCM SQLite database to FalkorDB's SQLite connection so that
Cypher-side and raw-SQL-side queries can share the same connection without
copying data.  Falls back gracefully when the underlying database is not
SQLite-backed.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from navegador.graph.store import GraphStore

logger = logging.getLogger(__name__)

# Label used when importing Fossil timeline events into the navegador graph
_FOSSIL_COMMIT_LABEL = "FossilCommit"
_FOSSIL_TICKET_LABEL = "FossilTicket"


class FossilLiveAdapter:
    """
    Bridge between a Fossil SCM repository database and navegador's graph.

    The preferred approach is ``ATTACH DATABASE`` — attaching the Fossil
    SQLite file to the same SQLite connection used by FalkorDB's SQLite
    backend (falkordblite) so cross-DB queries work with zero data copying.

    When a direct SQLite connection is not available (e.g. Redis-backed
    FalkorDB), the adapter falls back to opening its own ``sqlite3``
    connection to the Fossil DB.

    Args:
        fossil_db_path: Path to the Fossil ``.fossil`` or ``.db`` repository
            file (the SQLite database Fossil uses internally).
    """

    def __init__(self, fossil_db_path: str | Path, _sqlite_conn: Any = None) -> None:
        self._fossil_path = Path(fossil_db_path)
        self._conn: Any = _sqlite_conn  # injected in tests; opened lazily otherwise
        self._attached = False

    # ── Internal ──────────────────────────────────────────────────────────────

    def _get_conn(self) -> Any:
        """Return a sqlite3 connection to the Fossil DB."""
        if self._conn is None:
            import sqlite3

            self._conn = sqlite3.connect(str(self._fossil_path))
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def _extract_sqlite_conn(self, store: "GraphStore") -> Any | None:
        """
        Try to pull the underlying sqlite3 connection out of a falkordblite
        GraphStore so we can run ATTACH DATABASE on it.

        Returns None when the store is Redis-backed.
        """
        try:
            # falkordblite wraps redislite; the raw sqlite3 connection is buried
            # several layers deep — we try the most common attribute paths.
            client = store._client  # type: ignore[attr-defined]
            for attr in ("_db", "connection", "_connection", "db"):
                conn = getattr(client, attr, None)
                if conn is not None:
                    import sqlite3
                    if isinstance(conn, sqlite3.Connection):
                        return conn
        except Exception:
            pass
        return None

    # ── Public API ────────────────────────────────────────────────────────────

    def attach(self, store: "GraphStore") -> None:
        """
        Attach the Fossil SQLite DB to FalkorDB's SQLite connection.

        If the underlying connection cannot be retrieved (Redis backend), the
        method logs a warning and falls back to a standalone connection.

        Args:
            store: The GraphStore whose SQLite connection to attach into.
        """
        if self._attached:
            return

        native_conn = self._extract_sqlite_conn(store)
        if native_conn is not None:
            native_conn.execute(
                f"ATTACH DATABASE ? AS fossil", (str(self._fossil_path),)
            )
            self._conn = native_conn
            self._attached = True
            logger.info("Fossil DB attached to FalkorDB SQLite: %s", self._fossil_path)
        else:
            logger.warning(
                "Could not attach Fossil DB to FalkorDB connection (not SQLite-backed); "
                "falling back to standalone connection."
            )
            # Open a standalone connection so queries still work
            self._get_conn()

    def query_timeline(self, limit: int = 50) -> list[dict]:
        """
        Query the Fossil event (timeline) table.

        Fossil stores timeline events in a table called ``event``.  Each row
        represents a commit, wiki edit, ticket change, etc.

        Args:
            limit: Maximum number of events to return (newest first).

        Returns:
            List of dicts with keys: ``type``, ``mtime``, ``objid``,
            ``tagid``, ``uid``, ``bgcolor``, ``euser``, ``user``,
            ``ecomment``, ``comment``, ``brief``.
        """
        conn = self._get_conn()
        prefix = "fossil." if self._attached else ""
        sql = f"""
            SELECT type, mtime, objid, uid, user,
                   euser, comment, ecomment
            FROM {prefix}event
            ORDER BY mtime DESC
            LIMIT ?
        """
        cursor = conn.execute(sql, (limit,))
        rows = cursor.fetchall()
        result = []
        for row in rows:
            if hasattr(row, "keys"):
                result.append(dict(row))
            else:
                result.append({
                    "type": row[0],
                    "mtime": row[1],
                    "objid": row[2],
                    "uid": row[3],
                    "user": row[4],
                    "euser": row[5],
                    "comment": row[6],
                    "ecomment": row[7],
                })
        return result

    def query_tickets(self) -> list[dict]:
        """
        Query Fossil tickets.

        Fossil stores tickets in the ``ticket`` table.  The schema can vary
        per-repository; we return the full row as a dict.

        Returns:
            List of dicts representing ticket rows.
        """
        conn = self._get_conn()
        prefix = "fossil." if self._attached else ""
        try:
            sql = f"SELECT * FROM {prefix}ticket ORDER BY tkt_mtime DESC"
            cursor = conn.execute(sql)
            cols = [description[0] for description in cursor.description]
            rows = cursor.fetchall()
            return [dict(zip(cols, row)) for row in rows]
        except Exception as exc:
            logger.warning("Could not query Fossil tickets: %s", exc)
            return []

    def sync_to_graph(self, store: "GraphStore") -> dict:
        """
        Import Fossil timeline and ticket data into the navegador graph.

        Creates :class:`FossilCommit` and :class:`FossilTicket` nodes.

        Args:
            store: Target GraphStore.

        Returns:
            Dict with keys: ``commits``, ``tickets`` (counts of imported items).
        """
        commit_count = 0
        ticket_count = 0

        # Import timeline events as FossilCommit nodes
        events = self.query_timeline(limit=200)
        for event in events:
            if event.get("type") not in ("ci", "c"):  # "ci" = check-in
                continue
            props = {
                "name": str(event.get("objid", "")),
                "file_path": "",
                "user": str(event.get("user") or event.get("euser") or ""),
                "comment": str(event.get("comment") or event.get("ecomment") or ""),
                "mtime": str(event.get("mtime", "")),
                "source": "fossil",
            }
            prop_str = ", ".join(f"n.{k} = ${k}" for k in props)
            store.query(
                f"MERGE (n:{_FOSSIL_COMMIT_LABEL} {{name: $name, file_path: $file_path}}) "
                f"SET {prop_str}",
                props,
            )
            commit_count += 1

        # Import tickets as FossilTicket nodes
        tickets = self.query_tickets()
        for ticket in tickets:
            tkt_id = str(ticket.get("tkt_uuid") or ticket.get("tkt_id") or "")
            if not tkt_id:
                continue
            props = {
                "name": tkt_id,
                "file_path": "",
                "title": str(ticket.get("title") or ""),
                "status": str(ticket.get("status") or ""),
                "type": str(ticket.get("type") or ""),
                "mtime": str(ticket.get("tkt_mtime") or ""),
                "source": "fossil",
            }
            prop_str = ", ".join(f"n.{k} = ${k}" for k in props)
            store.query(
                f"MERGE (n:{_FOSSIL_TICKET_LABEL} {{name: $name, file_path: $file_path}}) "
                f"SET {prop_str}",
                props,
            )
            ticket_count += 1

        logger.info(
            "Fossil sync complete: %d commits, %d tickets", commit_count, ticket_count
        )
        return {"commits": commit_count, "tickets": ticket_count}
