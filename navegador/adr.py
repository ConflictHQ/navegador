"""
ADR ingestion — structured Architecture Decision Records as Decision nodes.

Parses ADR markdown files in standard MADR format (or a relaxed variant)
and creates Decision nodes in the navegador knowledge graph.

Usage:
    from navegador.adr import ADRIngester

    ingester = ADRIngester(store)
    stats = ingester.ingest("/path/to/docs/decisions")
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from navegador.graph.schema import NodeLabel
from navegador.graph.store import GraphStore

logger = logging.getLogger(__name__)

# ── Regex helpers ─────────────────────────────────────────────────────────────

_H1 = re.compile(r"^#\s+(.+)$", re.MULTILINE)
_STATUS = re.compile(
    r"^#{1,3}\s+Status\s*\n+(.+?)(?=\n#{1,3}\s|\Z)", re.MULTILINE | re.DOTALL
)
_RATIONALE = re.compile(
    r"^#{1,3}\s+Rationale\s*\n+(.+?)(?=\n#{1,3}\s|\Z)",
    re.MULTILINE | re.DOTALL,
)
_RATIONALE_FALLBACK = re.compile(
    r"^#{1,3}\s+(?:Decision Outcome|Decision)\s*\n+(.+?)(?=\n#{1,3}\s|\Z)",
    re.MULTILINE | re.DOTALL,
)
_DATE = re.compile(r"(?:Date|date)[:\s]+(\d{4}-\d{2}-\d{2})", re.MULTILINE)
_ADR_NUMBER = re.compile(r"(\d+)", re.ASCII)


class ADRIngester:
    """
    Ingest ADR markdown files from a directory.

    Expected file naming convention:  NNNN-short-title.md  (e.g. 0001-use-falkordb.md)
    Each file is parsed for title, status, rationale, and date, then stored
    as a Decision node.
    """

    def __init__(self, store: GraphStore) -> None:
        self.store = store

    # ── Public API ────────────────────────────────────────────────────────────

    def ingest(self, adr_dir: str | Path) -> dict[str, Any]:
        """
        Parse all ``*.md`` files in *adr_dir* and upsert Decision nodes.

        Returns a stats dict: decisions, skipped.
        """
        adr_dir = Path(adr_dir)
        if not adr_dir.is_dir():
            logger.warning("ADRIngester: %s is not a directory", adr_dir)
            return {"decisions": 0, "skipped": 0}

        md_files = sorted(adr_dir.glob("*.md"))
        decisions = 0
        skipped = 0

        for md_file in md_files:
            try:
                parsed = self._parse_adr(md_file)
                if parsed is None:
                    skipped += 1
                    continue
                self.store.create_node(NodeLabel.Decision, parsed)
                decisions += 1
                logger.debug("ADR: %s", parsed["name"])
            except Exception as exc:  # noqa: BLE001
                logger.warning("ADRIngester: failed to parse %s: %s", md_file, exc)
                skipped += 1

        stats = {"decisions": decisions, "skipped": skipped}
        logger.info("ADRIngester: %s", stats)
        return stats

    # ── Parsing ───────────────────────────────────────────────────────────────

    def _parse_adr(self, path: Path) -> dict[str, Any] | None:
        """
        Parse a single ADR markdown file.

        Returns a dict suitable for create_node(NodeLabel.Decision, ...) or
        None if the file does not look like an ADR.
        """
        text = path.read_text(encoding="utf-8", errors="replace")

        # Title from first H1 heading
        h1_match = _H1.search(text)
        if h1_match is None:
            return None
        title = h1_match.group(1).strip()

        # Strip leading ADR number from title if present (e.g. "1. Use FalkorDB")
        title = re.sub(r"^\d+[.\s]+", "", title).strip()

        # Status
        status = "accepted"
        st_match = _STATUS.search(text)
        if st_match:
            raw_status = st_match.group(1).strip().lower().splitlines()[0]
            for known in ("proposed", "accepted", "deprecated", "superseded", "rejected"):
                if known in raw_status:
                    status = known
                    break

        # Rationale — prefer explicit ## Rationale section, fall back to ## Decision
        rationale = ""
        rat_match = _RATIONALE.search(text) or _RATIONALE_FALLBACK.search(text)
        if rat_match:
            rationale = rat_match.group(1).strip()

        # Date
        date = ""
        date_match = _DATE.search(text)
        if date_match:
            date = date_match.group(1)

        # Use the filename stem as the canonical name (stable, unique)
        name = path.stem

        return {
            "name": name,
            "description": title,
            "domain": "",
            "status": status,
            "rationale": rationale,
            "alternatives": "",
            "date": date,
            "file_path": str(path),
        }
