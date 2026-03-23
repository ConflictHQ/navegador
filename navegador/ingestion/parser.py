"""
RepoIngester — walks a repository, parses source files with tree-sitter,
and writes nodes + edges into the GraphStore.

Supported languages (all via tree-sitter):
  Python      .py
  TypeScript  .ts .tsx
  JavaScript  .js .jsx
  Go          .go
  Rust        .rs
  Java        .java
  Kotlin      .kt .kts
  C#          .cs
  PHP         .php
  Ruby        .rb
  Swift       .swift
  C           .c .h
  C++         .cpp .hpp .cc .cxx
"""

import hashlib
import logging
import time
from pathlib import Path

from navegador.graph import queries
from navegador.graph.schema import NodeLabel
from navegador.graph.store import GraphStore

logger = logging.getLogger(__name__)

# File extensions → language key
LANGUAGE_MAP: dict[str, str] = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".cs": "csharp",
    ".php": "php",
    ".rb": "ruby",
    ".swift": "swift",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
}


class RepoIngester:
    """
    Parses a local code repository and populates a GraphStore.

    Usage:
        store = GraphStore.sqlite(".navegador/graph.db")
        ingester = RepoIngester(store)
        stats = ingester.ingest("/path/to/repo")

    Args:
        store: The graph store to write nodes and edges into.
        redact: When True, file contents are scanned for sensitive patterns
                (API keys, passwords, tokens, …) and any matches are replaced
                with ``[REDACTED]`` before the content is stored in graph nodes.
    """

    def __init__(self, store: GraphStore, redact: bool = False) -> None:
        self.store = store
        self.redact = redact
        self._parsers: dict[str, "LanguageParser"] = {}
        if redact:
            from navegador.security import SensitiveContentDetector

            self._detector = SensitiveContentDetector()
        else:
            self._detector = None  # type: ignore[assignment]

    def ingest(
        self,
        repo_path: str | Path,
        clear: bool = False,
        incremental: bool = False,
    ) -> dict[str, int]:
        """
        Ingest a repository into the graph.

        Args:
            repo_path: Path to the repository root.
            clear: If True, wipe the graph before ingesting.
            incremental: If True, skip files whose content hash hasn't changed.

        Returns:
            Dict with counts: files, functions, classes, edges, skipped.
        """
        repo_path = Path(repo_path).resolve()
        if not repo_path.exists():
            raise FileNotFoundError(f"Repository not found: {repo_path}")

        if clear:
            self.store.clear()

        # Create repository node
        self.store.create_node(
            NodeLabel.Repository,
            {
                "name": repo_path.name,
                "path": str(repo_path),
            },
        )

        stats: dict[str, int] = {
            "files": 0,
            "functions": 0,
            "classes": 0,
            "edges": 0,
            "skipped": 0,
        }

        for source_file in self._iter_source_files(repo_path):
            language = LANGUAGE_MAP.get(source_file.suffix)
            if not language:
                continue

            rel_path = str(source_file.relative_to(repo_path))
            content_hash = _file_hash(source_file)

            if incremental and self._file_unchanged(rel_path, content_hash):
                stats["skipped"] += 1
                continue

            if incremental:
                self._clear_file_subgraph(rel_path)

            parse_path, effective_root = self._maybe_redact_to_tmp(source_file, repo_path)
            try:
                parser = self._get_parser(language)
                file_stats = parser.parse_file(parse_path, effective_root, self.store)
                stats["files"] += 1
                stats["functions"] += file_stats.get("functions", 0)
                stats["classes"] += file_stats.get("classes", 0)
                stats["edges"] += file_stats.get("edges", 0)

                self._store_file_hash(rel_path, content_hash)
            except Exception:
                logger.exception("Failed to parse %s", source_file)
            finally:
                # Remove the temporary redacted directory if one was created
                if effective_root is not repo_path:
                    import shutil
                    shutil.rmtree(effective_root, ignore_errors=True)

        logger.info(
            "Ingested %s: %d files, %d functions, %d classes, %d skipped",
            repo_path.name,
            stats["files"],
            stats["functions"],
            stats["classes"],
            stats["skipped"],
        )
        return stats

    def watch(
        self,
        repo_path: str | Path,
        interval: float = 2.0,
        callback=None,
    ) -> None:
        """
        Watch a repo for changes and re-ingest incrementally.

        Args:
            repo_path: Path to the repository root.
            interval: Seconds between polls.
            callback: Optional callable receiving stats dict after each cycle.
                      If callback returns False, the watch loop stops.
        """
        repo_path = Path(repo_path).resolve()
        if not repo_path.exists():
            raise FileNotFoundError(f"Repository not found: {repo_path}")

        # Initial full ingest
        stats = self.ingest(repo_path, incremental=True)
        if callback and callback(stats) is False:
            return

        while True:
            time.sleep(interval)
            stats = self.ingest(repo_path, incremental=True)
            if callback and callback(stats) is False:
                return

    def _file_unchanged(self, rel_path: str, content_hash: str) -> bool:
        result = self.store.query(queries.FILE_HASH, {"path": rel_path})
        rows = result.result_set or []
        if not rows or rows[0][0] is None:
            return False
        return rows[0][0] == content_hash

    def _clear_file_subgraph(self, rel_path: str) -> None:
        self.store.query(queries.DELETE_FILE_SUBGRAPH, {"path": rel_path})

    def _store_file_hash(self, rel_path: str, content_hash: str) -> None:
        self.store.query(
            "MATCH (f:File {path: $path}) SET f.content_hash = $hash",
            {"path": rel_path, "hash": content_hash},
        )

    def _maybe_redact_to_tmp(self, source_file: Path, repo_root: Path) -> tuple[Path, Path]:
        """
        If redaction is enabled, return a *(parse_path, effective_repo_root)*
        tuple where *parse_path* can be passed to ``parser.parse_file`` and
        ``parse_path.relative_to(effective_repo_root)`` still yields the
        correct relative path for graph node naming.

        When redaction is disabled or the file has no sensitive content, both
        returned values are the originals unchanged.

        The caller is responsible for deleting the temp directory when it is
        no longer needed.
        """
        if not self.redact or self._detector is None:
            return source_file, repo_root

        try:
            original = source_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return source_file, repo_root

        redacted = self._detector.redact(original)
        if redacted == original:
            return source_file, repo_root

        # Mirror the file at the same relative path inside a temp directory so
        # that parse_path.relative_to(tmp_root) == source_file.relative_to(repo_root).
        import tempfile

        rel = source_file.relative_to(repo_root)
        tmp_root = Path(tempfile.mkdtemp())
        tmp_file = tmp_root / rel
        tmp_file.parent.mkdir(parents=True, exist_ok=True)
        tmp_file.write_text(redacted, encoding="utf-8")
        return tmp_file, tmp_root

    def _iter_source_files(self, repo_path: Path):
        skip_dirs = {
            ".git",
            ".venv",
            "venv",
            "node_modules",
            "__pycache__",
            "dist",
            "build",
            ".next",
            "target",  # Rust / Java (Maven/Gradle)
            "vendor",  # Go modules cache
            ".gradle",  # Gradle cache
        }
        for path in repo_path.rglob("*"):
            if path.is_file() and path.suffix in LANGUAGE_MAP:
                if not any(part in skip_dirs for part in path.parts):
                    yield path

    def _get_parser(self, language: str) -> "LanguageParser":
        if language not in self._parsers:
            if language == "python":
                from navegador.ingestion.python import PythonParser

                self._parsers[language] = PythonParser()
            elif language in ("typescript", "javascript"):
                from navegador.ingestion.typescript import TypeScriptParser

                self._parsers[language] = TypeScriptParser(language)
            elif language == "go":
                from navegador.ingestion.go import GoParser

                self._parsers[language] = GoParser()
            elif language == "rust":
                from navegador.ingestion.rust import RustParser

                self._parsers[language] = RustParser()
            elif language == "java":
                from navegador.ingestion.java import JavaParser

                self._parsers[language] = JavaParser()
            elif language == "kotlin":
                from navegador.ingestion.kotlin import KotlinParser

                self._parsers[language] = KotlinParser()
            elif language == "csharp":
                from navegador.ingestion.csharp import CSharpParser

                self._parsers[language] = CSharpParser()
            elif language == "php":
                from navegador.ingestion.php import PHPParser

                self._parsers[language] = PHPParser()
            elif language == "ruby":
                from navegador.ingestion.ruby import RubyParser

                self._parsers[language] = RubyParser()
            elif language == "swift":
                from navegador.ingestion.swift import SwiftParser

                self._parsers[language] = SwiftParser()
            elif language == "c":
                from navegador.ingestion.c import CParser

                self._parsers[language] = CParser()
            elif language == "cpp":
                from navegador.ingestion.cpp import CppParser

                self._parsers[language] = CppParser()
            else:
                raise ValueError(f"Unsupported language: {language}")
        return self._parsers[language]


def _file_hash(path: Path) -> str:
    """SHA-256 content hash for a file."""
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


class LanguageParser:
    """Base class for language-specific AST parsers."""

    def parse_file(self, path: Path, repo_root: Path, store: GraphStore) -> dict[str, int]:
        raise NotImplementedError
