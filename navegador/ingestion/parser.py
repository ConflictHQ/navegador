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
"""

import logging
from pathlib import Path

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
}


class RepoIngester:
    """
    Parses a local code repository and populates a GraphStore.

    Usage:
        store = GraphStore.sqlite(".navegador/graph.db")
        ingester = RepoIngester(store)
        stats = ingester.ingest("/path/to/repo")
    """

    def __init__(self, store: GraphStore) -> None:
        self.store = store
        self._parsers: dict[str, "LanguageParser"] = {}

    def ingest(self, repo_path: str | Path, clear: bool = False) -> dict[str, int]:
        """
        Ingest a repository into the graph.

        Args:
            repo_path: Path to the repository root.
            clear: If True, wipe the graph before ingesting.

        Returns:
            Dict with counts: files, functions, classes, edges.
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

        stats: dict[str, int] = {"files": 0, "functions": 0, "classes": 0, "edges": 0}

        for source_file in self._iter_source_files(repo_path):
            language = LANGUAGE_MAP.get(source_file.suffix)
            if not language:
                continue
            try:
                parser = self._get_parser(language)
                file_stats = parser.parse_file(source_file, repo_path, self.store)
                stats["files"] += 1
                stats["functions"] += file_stats.get("functions", 0)
                stats["classes"] += file_stats.get("classes", 0)
                stats["edges"] += file_stats.get("edges", 0)
            except Exception:
                logger.exception("Failed to parse %s", source_file)

        logger.info(
            "Ingested %s: %d files, %d functions, %d classes",
            repo_path.name,
            stats["files"],
            stats["functions"],
            stats["classes"],
        )
        return stats

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
            else:
                raise ValueError(f"Unsupported language: {language}")
        return self._parsers[language]


class LanguageParser:
    """Base class for language-specific AST parsers."""

    def parse_file(self, path: Path, repo_root: Path, store: GraphStore) -> dict[str, int]:
        raise NotImplementedError
