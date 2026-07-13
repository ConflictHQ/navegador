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

Infrastructure-as-Code:
  HCL         .tf .hcl        (Terraform / OpenTofu)
  Puppet      .pp
  Bash        .sh .bash .zsh
  Ansible     .yml .yaml      (detected heuristically, not via extension)
"""

import fnmatch
import hashlib
import logging
import os
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
    ".tf": "hcl",
    ".hcl": "hcl",
    ".pp": "puppet",
    ".sh": "bash",
    ".bash": "bash",
    ".zsh": "bash",
    ".md": "markdown",
    ".markdown": "markdown",
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

    def __init__(
        self,
        store: GraphStore,
        redact: bool = False,
        exclude: list[str] | None = None,
        include_nested_repos: bool = False,
    ) -> None:
        self.store = store
        self.redact = redact
        # Glob patterns excluded from the walk, merged with the repo's
        # .navignore. Matching directories are pruned before descent.
        self.exclude = list(exclude or [])
        # When True, nested git clones are walked instead of boundary-stopped
        # (metarepo "full" mode — index vendored cores too).
        self.include_nested_repos = include_nested_repos
        self._parsers: dict[str, "LanguageParser | None"] = {}
        # language → install hint, populated when an optional grammar is missing
        self.unavailable_grammars: dict[str, str] = {}
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
                "file_path": "",
            },
        )

        stats: dict[str, int] = {
            "files": 0,
            "functions": 0,
            "classes": 0,
            "edges": 0,
            "skipped": 0,
            "grammar_skipped": 0,
        }

        for source_file in self._iter_source_files(repo_path):
            language = LANGUAGE_MAP.get(source_file.suffix)
            if not language:
                continue

            parser = self._get_parser(language)
            if parser is None:
                stats["grammar_skipped"] += 1
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
                file_stats = parser.parse_file(parse_path, effective_root, self.store)
                stats["files"] += 1
                stats["functions"] += file_stats.get("functions", 0)
                stats["classes"] += file_stats.get("classes", 0)
                stats["edges"] += file_stats.get("edges", 0)

                self._store_file_hash(rel_path, content_hash)
                if stats["files"] % 1000 == 0:
                    logger.info(
                        "Ingest progress %s: %d files parsed", repo_path.name, stats["files"]
                    )
            except Exception:
                logger.exception("Failed to parse %s", source_file)
            finally:
                # Remove the temporary redacted directory if one was created
                if effective_root is not repo_path:
                    import shutil

                    shutil.rmtree(effective_root, ignore_errors=True)

        # Ansible pass — heuristically detect and parse Ansible YAML files
        self._ingest_ansible(repo_path, stats, incremental)

        # Fossil mirror pass — if the repo is also a Fossil checkout (e.g. a
        # Git repo mirrored to/from Fossil), ingest wiki pages and tickets.
        self._ingest_fossil_mirror(repo_path, stats)

        if self.unavailable_grammars:
            logger.warning(
                "Skipped %d file(s) with missing optional grammars: %s "
                "(pip install 'navegador[languages,iac]' to parse everything)",
                stats["grammar_skipped"],
                ", ".join(sorted(self.unavailable_grammars)),
            )

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

    # Extensions handled by MarkdownParser — these produce Document nodes, not File nodes.
    _DOCUMENT_EXTENSIONS = frozenset({".md", ".markdown"})

    def _file_unchanged(self, rel_path: str, content_hash: str) -> bool:
        suffix = Path(rel_path).suffix.lower()
        if suffix in self._DOCUMENT_EXTENSIONS:
            q = queries.DOCUMENT_HASH
        else:
            q = queries.FILE_HASH
        result = self.store.query(q, {"path": rel_path})
        rows = result.result_set or []
        if not rows or rows[0][0] is None:
            return False
        return rows[0][0] == content_hash

    def _clear_file_subgraph(self, rel_path: str) -> None:
        suffix = Path(rel_path).suffix.lower()
        if suffix in self._DOCUMENT_EXTENSIONS:
            self.store.query(queries.CLEAR_DOCUMENT_REFERENCES, {"path": rel_path})
        else:
            self.store.query(queries.DELETE_FILE_SUBGRAPH, {"path": rel_path})

    def _store_file_hash(self, rel_path: str, content_hash: str) -> None:
        suffix = Path(rel_path).suffix.lower()
        if suffix in self._DOCUMENT_EXTENSIONS:
            self.store.query(
                "MATCH (d:Document {path: $path}) SET d.content_hash = $hash",
                {"path": rel_path, "hash": content_hash},
            )
        else:
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

    # Directories never entered during the repo walk.
    _SKIP_DIRS = frozenset(
        {
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
    )

    def _walk_files(self, repo_path: Path):
        """
        Walk *repo_path* yielding files, pruning skipped directories BEFORE
        descending into them (rglob enumerated everything first, which made
        metarepo roots with huge vendored trees appear to hang — #128).

        A subdirectory containing ``.git`` (directory, or file for worktrees
        and submodule pointers) is another repository: it is a boundary and
        is never entered — unless ``include_nested_repos`` is set. Exclusion
        patterns (``exclude`` + the repo's ``.navignore``) prune matching
        directories the same way (#130).
        """
        patterns = self._exclusion_patterns(repo_path)
        for dirpath, dirnames, filenames in os.walk(repo_path):
            current = Path(dirpath)
            kept = []
            for d in dirnames:
                if d in self._SKIP_DIRS:
                    continue
                child = current / d
                if patterns and self._matches_exclusion(
                    child.relative_to(repo_path).as_posix(), patterns
                ):
                    logger.info("Excluding %s (exclusion pattern)", child)
                    continue
                if not self.include_nested_repos and (child / ".git").exists():
                    logger.info("Skipping nested git repository: %s", child)
                    continue
                kept.append(d)
            dirnames[:] = kept
            for fname in filenames:
                path = current / fname
                if patterns and self._matches_exclusion(
                    path.relative_to(repo_path).as_posix(), patterns
                ):
                    continue
                if path.is_file():  # excludes broken symlinks, FIFOs, sockets
                    yield path

    def _exclusion_patterns(self, repo_path: Path) -> list[str]:
        """Explicit exclude patterns plus the repo's .navignore entries."""
        patterns = list(self.exclude)
        navignore = repo_path / ".navignore"
        if navignore.is_file():
            for line in navignore.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    patterns.append(line)
        return patterns

    @staticmethod
    def _matches_exclusion(rel_posix: str, patterns: list[str]) -> bool:
        """
        True when the repo-relative POSIX path matches any pattern.

        A pattern matches the whole relative path (``docs/generated/*``) or,
        gitignore-style, any single path component (``*.gen.py``, ``vscode``).
        Trailing slashes (directory markers) are ignored.
        """
        parts = rel_posix.split("/")
        for pattern in patterns:
            pattern = pattern.rstrip("/")
            if not pattern:
                continue
            if fnmatch.fnmatch(rel_posix, pattern) or any(
                fnmatch.fnmatch(part, pattern) for part in parts
            ):
                return True
        return False

    def _iter_source_files(self, repo_path: Path):
        for path in self._walk_files(repo_path):
            if path.suffix in LANGUAGE_MAP:
                yield path

    def _ingest_ansible(self, repo_path: Path, stats: dict[str, int], incremental: bool) -> None:
        """Detect and parse Ansible YAML files (playbooks, roles, tasks)."""
        from navegador.ingestion.ansible import AnsibleParser

        is_ansible_file = AnsibleParser.is_ansible_file

        ansible_parser: AnsibleParser | None = None

        for path in self._walk_files(repo_path):
            if path.suffix not in (".yml", ".yaml"):
                continue
            if not is_ansible_file(path, repo_path):
                continue

            rel_path = str(path.relative_to(repo_path))
            content_hash = _file_hash(path)

            if incremental and self._file_unchanged(rel_path, content_hash):
                stats["skipped"] += 1
                continue

            if incremental:
                self._clear_file_subgraph(rel_path)

            if ansible_parser is None:
                ansible_parser = AnsibleParser()
            try:
                file_stats = ansible_parser.parse_file(path, repo_path, self.store)
                stats["files"] += 1
                stats["functions"] += file_stats.get("functions", 0)
                stats["classes"] += file_stats.get("classes", 0)
                stats["edges"] += file_stats.get("edges", 0)
                self._store_file_hash(rel_path, content_hash)
            except Exception:
                logger.exception("Failed to parse Ansible file %s", path)

    def _ingest_fossil_mirror(self, repo_path: Path, stats: dict[str, int]) -> None:
        """
        Ingest wiki pages and tickets from a co-located Fossil mirror, if present.

        A Fossil-mirrored Git repo has both a ``.git`` directory and a
        ``.fslckout`` / ``_FOSSIL_`` marker.  When detected, wiki pages and
        tickets are ingested automatically alongside the Git-based code graph.
        """
        from navegador.vcs import detect_fossil

        fossil_adapter = detect_fossil(repo_path)
        if fossil_adapter is None:
            return

        logger.info("Fossil mirror detected at %s — ingesting wiki and tickets", repo_path)
        from navegador.ingestion.fossil import FossilIngester

        ingester = FossilIngester(self.store, fossil_adapter)
        wiki_stats = ingester.ingest_wiki()
        ticket_stats = ingester.ingest_tickets()

        stats["wiki_pages"] = wiki_stats["pages"]
        stats["tickets"] = ticket_stats["tickets"]
        stats["edges"] += wiki_stats["edges"] + ticket_stats["edges"]

    def _get_parser(self, language: str) -> "LanguageParser | None":
        """
        Return the parser for *language*, or None when its optional
        tree-sitter grammar is not installed. A missing grammar is recorded
        in ``unavailable_grammars`` and warned about once — never raised, so
        one absent grammar cannot abort a whole ingest.
        """
        if language not in self._parsers:
            try:
                self._parsers[language] = self._build_parser(language)
            except ImportError as e:
                self._parsers[language] = None
                self.unavailable_grammars[language] = str(e)
                logger.warning("Skipping %s files — %s", language, e)
        return self._parsers[language]

    def _build_parser(self, language: str) -> "LanguageParser":
        if language == "python":
            from navegador.ingestion.python import PythonParser

            return PythonParser()
        elif language in ("typescript", "javascript"):
            from navegador.ingestion.typescript import TypeScriptParser

            return TypeScriptParser(language)
        elif language == "go":
            from navegador.ingestion.go import GoParser

            return GoParser()
        elif language == "rust":
            from navegador.ingestion.rust import RustParser

            return RustParser()
        elif language == "java":
            from navegador.ingestion.java import JavaParser

            return JavaParser()
        elif language == "kotlin":
            from navegador.ingestion.kotlin import KotlinParser

            return KotlinParser()
        elif language == "csharp":
            from navegador.ingestion.csharp import CSharpParser

            return CSharpParser()
        elif language == "php":
            from navegador.ingestion.php import PHPParser

            return PHPParser()
        elif language == "ruby":
            from navegador.ingestion.ruby import RubyParser

            return RubyParser()
        elif language == "swift":
            from navegador.ingestion.swift import SwiftParser

            return SwiftParser()
        elif language == "c":
            from navegador.ingestion.c import CParser

            return CParser()
        elif language == "cpp":
            from navegador.ingestion.cpp import CppParser

            return CppParser()
        elif language == "hcl":
            from navegador.ingestion.hcl import HCLParser

            return HCLParser()
        elif language == "puppet":
            from navegador.ingestion.puppet import PuppetParser

            return PuppetParser()
        elif language == "bash":
            from navegador.ingestion.bash import BashParser

            return BashParser()
        elif language == "markdown":
            from navegador.ingestion.markdown import MarkdownParser

            return MarkdownParser()
        raise ValueError(f"Unsupported language: {language}")


def _file_hash(path: Path) -> str:
    """SHA-256 content hash for a file."""
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


class LanguageParser:
    """Base class for language-specific AST parsers."""

    def parse_file(self, path: Path, repo_root: Path, store: GraphStore) -> dict[str, int]:
        raise NotImplementedError
