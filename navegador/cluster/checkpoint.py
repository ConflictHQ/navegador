"""
Checkpoint and rollback for swarm state recovery.

Snapshots the current graph to a JSONL file (navegador.graph.export format)
and can restore to any previous checkpoint.  Checkpoints are stored in a
local directory and tracked via a JSON index file.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from navegador.graph.export import export_graph, import_graph

if TYPE_CHECKING:
    from navegador.graph.store import GraphStore

logger = logging.getLogger(__name__)

_INDEX_FILE = "checkpoints.json"


class CheckpointManager:
    """
    Manage graph checkpoints for swarm state recovery.

    Checkpoints are stored as JSONL files in *checkpoint_dir* using the same
    format as :func:`navegador.graph.export.export_graph`.  An index file
    (``checkpoints.json``) tracks metadata for all checkpoints.

    Args:
        store: GraphStore instance to snapshot / restore.
        checkpoint_dir: Directory where checkpoint files will be written.
    """

    def __init__(self, store: "GraphStore", checkpoint_dir: str | Path) -> None:
        self._store = store
        self._dir = Path(checkpoint_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._index_path = self._dir / _INDEX_FILE

    # ── Index helpers ─────────────────────────────────────────────────────────

    def _load_index(self) -> list[dict]:
        if not self._index_path.exists():
            return []
        with self._index_path.open("r", encoding="utf-8") as f:
            return json.load(f)

    def _save_index(self, index: list[dict]) -> None:
        with self._index_path.open("w", encoding="utf-8") as f:
            json.dump(index, f, indent=2)

    # ── Public API ────────────────────────────────────────────────────────────

    def create(self, label: str = "") -> str:
        """
        Snapshot the current graph state.

        Args:
            label: Human-readable description for this checkpoint.

        Returns:
            Unique checkpoint ID (UUID4 string).
        """
        checkpoint_id = str(uuid.uuid4())
        filename = f"{checkpoint_id}.jsonl"
        filepath = self._dir / filename

        counts = export_graph(self._store, filepath)

        index = self._load_index()
        entry = {
            "id": checkpoint_id,
            "label": label,
            "timestamp": time.time(),
            "file": filename,
            "node_count": counts["nodes"],
            "edge_count": counts["edges"],
        }
        index.append(entry)
        self._save_index(index)

        logger.info(
            "Checkpoint created: %s ('%s') — %d nodes, %d edges",
            checkpoint_id,
            label,
            counts["nodes"],
            counts["edges"],
        )
        return checkpoint_id

    def restore(self, checkpoint_id: str) -> None:
        """
        Roll back the graph to a previous checkpoint.

        This clears the current graph and re-imports the checkpoint data.

        Args:
            checkpoint_id: ID returned by :meth:`create`.

        Raises:
            KeyError: If *checkpoint_id* is not found in the index.
            FileNotFoundError: If the checkpoint JSONL file is missing.
        """
        index = self._load_index()
        entry = next((e for e in index if e["id"] == checkpoint_id), None)
        if entry is None:
            raise KeyError(f"Checkpoint not found: {checkpoint_id}")

        filepath = self._dir / entry["file"]
        if not filepath.exists():
            raise FileNotFoundError(f"Checkpoint file missing: {filepath}")

        counts = import_graph(self._store, filepath, clear=True)
        logger.info(
            "Checkpoint restored: %s — %d nodes, %d edges",
            checkpoint_id,
            counts["nodes"],
            counts["edges"],
        )

    def list_checkpoints(self) -> list[dict]:
        """
        Return metadata for all checkpoints, oldest first.

        Each entry contains: id, label, timestamp, node_count.
        """
        index = self._load_index()
        return [
            {
                "id": e["id"],
                "label": e.get("label", ""),
                "timestamp": e["timestamp"],
                "node_count": e.get("node_count", 0),
            }
            for e in index
        ]

    def delete(self, checkpoint_id: str) -> None:
        """
        Remove a checkpoint and its JSONL file.

        Args:
            checkpoint_id: ID to delete.

        Raises:
            KeyError: If *checkpoint_id* is not found.
        """
        index = self._load_index()
        entry = next((e for e in index if e["id"] == checkpoint_id), None)
        if entry is None:
            raise KeyError(f"Checkpoint not found: {checkpoint_id}")

        filepath = self._dir / entry["file"]
        if filepath.exists():
            filepath.unlink()

        index = [e for e in index if e["id"] != checkpoint_id]
        self._save_index(index)
        logger.info("Checkpoint deleted: %s", checkpoint_id)
