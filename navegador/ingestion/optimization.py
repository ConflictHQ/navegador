"""
AST optimization utilities for navegador ingestion.

Provides four independent classes that can be composed to accelerate
re-ingestion of large repositories:

  TreeCache         — LRU cache for parsed tree-sitter trees (#42)
  IncrementalParser — Wraps tree-sitter parse() with old_tree support (#43)
  GraphDiffer       — Node-level diffing to skip unchanged writes (#44)
  ParallelIngester  — ThreadPoolExecutor wrapper around RepoIngester (#45)
"""

from __future__ import annotations

import concurrent.futures
import logging
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ── #42 — LRU cache for parsed trees ─────────────────────────────────────────


class TreeCache:
    """
    Thread-safe LRU cache that maps ``(path, content_hash)`` to a parsed
    tree-sitter tree.

    Args:
        max_size: Maximum number of trees to hold in memory.  When the cache
                  is full the least-recently-used entry is evicted.
    """

    def __init__(self, max_size: int = 256) -> None:
        if max_size < 1:
            raise ValueError("max_size must be >= 1")
        self._max_size = max_size
        # OrderedDict used as an LRU store: most-recently used is at the end.
        self._cache: OrderedDict[tuple[str, str], Any] = OrderedDict()
        self._hits = 0
        self._misses = 0
        self._lock = threading.Lock()

    # ── public API ────────────────────────────────────────────────────────────

    def get(self, path: str, content_hash: str) -> Any | None:
        """Return the cached tree or ``None`` on a cache miss."""
        key = (path, content_hash)
        with self._lock:
            if key in self._cache:
                self._hits += 1
                # Move to the end (most recently used).
                self._cache.move_to_end(key)
                return self._cache[key]
            self._misses += 1
            return None

    def put(self, path: str, content_hash: str, tree: Any) -> None:
        """Insert (or update) a tree in the cache, evicting LRU entry if full."""
        key = (path, content_hash)
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                self._cache[key] = tree
            else:
                if len(self._cache) >= self._max_size:
                    # Evict oldest entry (front of the OrderedDict).
                    self._cache.popitem(last=False)
                self._cache[key] = tree

    def clear(self) -> None:
        """Remove all cached trees and reset statistics."""
        with self._lock:
            self._cache.clear()
            self._hits = 0
            self._misses = 0

    def stats(self) -> dict[str, int]:
        """Return a snapshot of cache statistics."""
        with self._lock:
            return {
                "hits": self._hits,
                "misses": self._misses,
                "size": len(self._cache),
                "max_size": self._max_size,
            }

    # ── dunder helpers ────────────────────────────────────────────────────────

    def __len__(self) -> int:
        with self._lock:
            return len(self._cache)


# ── #43 — Incremental re-parsing via tree-sitter old_tree API ────────────────


class IncrementalParser:
    """
    Wraps tree-sitter's ``parser.parse()`` to pass ``old_tree`` when a
    previously-parsed tree for the same path is available.

    Parsed trees are stored in a :class:`TreeCache` so subsequent calls for
    an unchanged file return instantly without hitting the tree-sitter C
    extension.

    Args:
        cache: A :class:`TreeCache` instance.  A default cache is created if
               none is provided.
    """

    def __init__(self, cache: TreeCache | None = None) -> None:
        self._cache = cache if cache is not None else TreeCache()

    def parse(
        self,
        source_bytes: bytes,
        language: Any,
        path: str,
        content_hash: str,
    ) -> Any:
        """
        Parse *source_bytes* using *language* and return the tree.

        If a cached tree exists for *(path, content_hash)* it is returned
        immediately.  Otherwise the most recent tree for *path* (with a
        different hash, if any) is retrieved from the cache and passed as
        ``old_tree`` to ``language.parser.parse()`` to enable incremental
        parsing, then the new tree is stored.

        Args:
            source_bytes: Raw UTF-8 source code.
            language: A tree-sitter ``Language`` object (or mock in tests).
            path: Repository-relative path, used as cache key.
            content_hash: Content hash, used as cache key.

        Returns:
            A parsed tree-sitter ``Tree``.
        """
        # Fast path: tree for this exact content is already cached.
        cached = self._cache.get(path, content_hash)
        if cached is not None:
            return cached

        # Look for a stale tree for this path to use as old_tree.
        old_tree = self._get_stale_tree(path)

        # Build a parser using the language object.  tree-sitter parsers are
        # instantiated differently depending on version; we support both the
        # legacy API (tree_sitter.Parser) and the new Language.parser attribute.
        try:
            import tree_sitter  # type: ignore[import]

            parser = tree_sitter.Parser()
            parser.set_language(language)
        except Exception:
            # Fallback: language might already be a parser-like object.
            parser = language

        if old_tree is not None:
            tree = parser.parse(source_bytes, old_tree)
        else:
            tree = parser.parse(source_bytes)

        self._cache.put(path, content_hash, tree)
        return tree

    # ── internal helpers ──────────────────────────────────────────────────────

    def _get_stale_tree(self, path: str) -> Any | None:
        """Return any cached tree for *path* regardless of hash, or ``None``."""
        with self._cache._lock:
            for (cached_path, _), tree in self._cache._cache.items():
                if cached_path == path:
                    return tree
        return None

    @property
    def cache(self) -> TreeCache:
        return self._cache


# ── #44 — Graph node diffing ──────────────────────────────────────────────────


@dataclass
class DiffResult:
    """Summary of a node-level diff for one file."""

    added: int = 0
    modified: int = 0
    unchanged: int = 0
    removed: int = 0

    @property
    def total_changes(self) -> int:
        return self.added + self.modified + self.removed


@dataclass
class NodeDescriptor:
    """Minimal, hashable description of a graph node used for comparison."""

    label: str
    name: str
    line_start: int
    # Extra properties that contribute to the "modified" check.
    extra: dict[str, Any] = field(default_factory=dict)

    def identity_key(self) -> tuple[str, str, int]:
        return (self.label, self.name, self.line_start)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, NodeDescriptor):
            return NotImplemented
        return self.identity_key() == other.identity_key() and self.extra == other.extra


class GraphDiffer:
    """
    Compares newly-parsed nodes against what is already stored in the graph
    so that only genuinely changed nodes need to be written.

    Args:
        store: A :class:`~navegador.graph.store.GraphStore` instance.
    """

    def __init__(self, store: Any) -> None:
        self._store = store

    # ── public API ────────────────────────────────────────────────────────────

    def diff_file(
        self,
        file_path: str,
        new_nodes: list[NodeDescriptor],
    ) -> DiffResult:
        """
        Compare *new_nodes* against graph nodes currently stored for
        *file_path*.

        Args:
            file_path: Repository-relative path of the file being re-parsed.
            new_nodes: Nodes produced by the latest parse pass.

        Returns:
            A :class:`DiffResult` with counts of added / modified / unchanged /
            removed nodes.
        """
        existing_nodes = self._fetch_existing_nodes(file_path)

        existing_by_key: dict[tuple[str, str, int], NodeDescriptor] = {
            n.identity_key(): n for n in existing_nodes
        }
        new_by_key: dict[tuple[str, str, int], NodeDescriptor] = {
            n.identity_key(): n for n in new_nodes
        }

        result = DiffResult()

        for key, new_node in new_by_key.items():
            if key not in existing_by_key:
                result.added += 1
            elif new_node != existing_by_key[key]:
                result.modified += 1
            else:
                result.unchanged += 1

        for key in existing_by_key:
            if key not in new_by_key:
                result.removed += 1

        return result

    # ── internal helpers ──────────────────────────────────────────────────────

    def _fetch_existing_nodes(self, file_path: str) -> list[NodeDescriptor]:
        """
        Query the graph for all nodes associated with *file_path* and return
        them as :class:`NodeDescriptor` objects.

        The query returns rows of ``[label, name, line_start]``.  Any row
        where *name* or *line_start* is ``None`` is skipped.
        """
        cypher = (
            "MATCH (n {file_path: $file_path}) "
            "RETURN labels(n)[0] AS label, n.name AS name, n.line_start AS line_start"
        )
        result = self._store.query(cypher, {"file_path": file_path})
        rows = result.result_set or []
        nodes: list[NodeDescriptor] = []
        for row in rows:
            label, name, line_start = row[0], row[1], row[2]
            if name is None or line_start is None:
                continue
            nodes.append(
                NodeDescriptor(label=str(label), name=str(name), line_start=int(line_start))
            )
        return nodes


# ── #45 — Parallel ingestion with worker pool ─────────────────────────────────


class ParallelIngester:
    """
    Processes repository files concurrently using a
    :class:`concurrent.futures.ThreadPoolExecutor`.

    The class wraps a :class:`~navegador.ingestion.parser.RepoIngester` and
    overrides its sequential file-iteration loop with a parallel one while
    reusing all of its parsing, hashing and graph-writing logic.

    Args:
        store: Graph store passed through to the underlying
               :class:`~navegador.ingestion.parser.RepoIngester`.
        redact: Enable sensitive-content redaction.
    """

    def __init__(self, store: Any, redact: bool = False) -> None:
        from navegador.ingestion.parser import RepoIngester

        self._store = store
        self._redact = redact
        self._ingester = RepoIngester(store, redact=redact)

    def ingest_parallel(
        self,
        repo_path: str | Path,
        max_workers: int | None = None,
        clear: bool = False,
        incremental: bool = False,
    ) -> dict[str, int]:
        """
        Ingest a repository, parsing files concurrently.

        Args:
            repo_path: Path to the repository root.
            max_workers: Number of worker threads.  Defaults to
                         ``min(32, cpu_count + 4)`` via
                         :class:`~concurrent.futures.ThreadPoolExecutor`.
            clear: Wipe the graph before ingesting.
            incremental: Skip files whose content hash hasn't changed.

        Returns:
            Aggregated stats dict with keys: files, functions, classes,
            edges, skipped, errors.
        """
        from navegador.graph.schema import NodeLabel
        from navegador.ingestion.parser import LANGUAGE_MAP, _file_hash

        repo_path = Path(repo_path).resolve()
        if not repo_path.exists():
            raise FileNotFoundError(f"Repository not found: {repo_path}")

        if clear:
            self._store.clear()

        # Repository node (same as RepoIngester.ingest).
        self._store.create_node(
            NodeLabel.Repository,
            {
                "name": repo_path.name,
                "path": str(repo_path),
            },
        )

        # Collect all candidate files up-front (fast, single-threaded).
        candidate_files = [
            f for f in self._ingester._iter_source_files(repo_path) if LANGUAGE_MAP.get(f.suffix)
        ]

        aggregated: dict[str, int] = {
            "files": 0,
            "functions": 0,
            "classes": 0,
            "edges": 0,
            "skipped": 0,
            "errors": 0,
        }
        lock = threading.Lock()

        def _process_file(source_file: Path) -> None:
            language = LANGUAGE_MAP[source_file.suffix]
            rel_path = str(source_file.relative_to(repo_path))
            content_hash = _file_hash(source_file)

            if incremental and self._ingester._file_unchanged(rel_path, content_hash):
                with lock:
                    aggregated["skipped"] += 1
                return

            if incremental:
                self._ingester._clear_file_subgraph(rel_path)

            parse_path, effective_root = self._ingester._maybe_redact_to_tmp(source_file, repo_path)
            try:
                parser = self._ingester._get_parser(language)
                file_stats = parser.parse_file(parse_path, effective_root, self._store)
                self._ingester._store_file_hash(rel_path, content_hash)
                with lock:
                    aggregated["files"] += 1
                    aggregated["functions"] += file_stats.get("functions", 0)
                    aggregated["classes"] += file_stats.get("classes", 0)
                    aggregated["edges"] += file_stats.get("edges", 0)
            except Exception:
                logger.exception("Failed to parse %s", source_file)
                with lock:
                    aggregated["errors"] += 1
            finally:
                import shutil

                if effective_root is not repo_path:
                    shutil.rmtree(effective_root, ignore_errors=True)

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(_process_file, f): f for f in candidate_files}
            for future in concurrent.futures.as_completed(futures):
                # Exceptions are already caught inside _process_file; this
                # re-raises any unexpected ones that slipped through.
                future.result()

        logger.info(
            "ParallelIngester finished %s: %d files, %d functions, %d skipped, %d errors",
            repo_path.name,
            aggregated["files"],
            aggregated["functions"],
            aggregated["skipped"],
            aggregated["errors"],
        )
        return aggregated
