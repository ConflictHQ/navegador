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
from navegador.graph.schema import EdgeType, NodeLabel
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


def _relative_path(path: Path, root: Path) -> str | None:
    """
    Path relative to *root*, or None when it lies outside.

    The raw path is tried first so ordinary files keep their literal recorded
    path; resolution is a fallback for the case where one side has already been
    resolved through a symlink and the other has not (on macOS, /var against
    /private/var). Returning None rather than raising keeps a stray path from
    aborting an entire ingest.
    """
    try:
        return str(path.relative_to(root))
    except ValueError:
        pass
    try:
        return str(path.resolve().relative_to(root))
    except ValueError:
        logger.debug("Skipping %s: not under %s", path, root)
        return None


def repo_identity(repo_path: Path) -> str:
    """
    Stable graph identity for a checkout.

    The directory basename is not an identity: a worktree, a renamed clone, or
    ``git clone <url> <otherdir>`` all produce a different basename for the same
    repository, and each one used to create an additional Repository node
    indistinguishable from a real one (#167). The git remote is the only thing
    that stays constant across all three, so it is preferred; the basename
    remains the fallback for a checkout with no remote.
    """
    from navegador.vcs import GitAdapter

    try:
        identity = GitAdapter(repo_path).remote_identity()
    except Exception:  # noqa: BLE001 — identity must never fail an ingest
        identity = ""
    return identity or repo_path.name


def repo_display_name(repo_key: str) -> str:
    """Human-facing name for a repo key — the segment after any owner prefix."""
    return repo_key.rsplit("/", 1)[-1] if repo_key else ""


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
        respect_gitignore: bool = True,
        store_content: bool = True,
    ) -> None:
        self.store = store
        self.redact = redact
        # Keep each file's text in the content store so lexical search has
        # something to match against (#183). Content-addressed, so unchanged
        # files and vendored copies cost nothing.
        self.store_content = store_content
        self._content_store = None
        self._prose_index = None
        # When True (default), a git checkout contributes only the files git
        # does not ignore. Without this the walk pruned on a fixed directory
        # list and indexed any build output whose directory name happened to
        # fall outside it (#180).
        self.respect_gitignore = respect_gitignore
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
        repo_key: str | None = None,
        rel_root: str | Path | None = None,
    ) -> dict[str, int]:
        """
        Ingest a repository into the graph.

        Args:
            repo_path: Path to the repository root.
            clear: If True, wipe the graph before ingesting.
            incremental: If True, skip files whose content hash hasn't changed.
            repo_key: Portable graph identity for the Repository node
                (defaults to the ``owner/repo`` of the git remote, falling back
                to the directory name when there is none). Workspace ingesters
                pass the workspace-relative path here so nested repos with
                the same basename stay distinct.
            rel_root: Ancestor directory that node paths are recorded
                relative to (defaults to repo_path). Workspace ingesters pass
                the workspace root so a nested repo's nodes carry a
                repo-prefixed path (``libs/core/main.tf``) — otherwise two
                repos owning the same relative path collide on id (#144).

        Returns:
            Dict with counts: files, functions, classes, edges, skipped.
        """
        repo_path = Path(repo_path).resolve()
        if not repo_path.exists():
            raise FileNotFoundError(f"Repository not found: {repo_path}")

        rel_root = Path(rel_root).resolve() if rel_root else repo_path
        repo_path.relative_to(rel_root)  # raises ValueError unless an ancestor

        if clear:
            self.store.clear()

        # Proxy the store for this pass: create_edge calls whose endpoint
        # nodes don't exist yet (forward references — callee parsed later)
        # are queued and replayed once after the walk, when the node set is
        # final, so a single pass reaches the call-graph fixpoint (#143).
        original_store = self.store
        proxy = _EdgeDeferringStore(original_store)
        self.store = proxy
        try:
            stats = self._ingest_walk(repo_path, incremental, repo_key, rel_root)
        finally:
            self.store = original_store
        stats["edges_resolved"] = self._resolve_deferred_edges(proxy.deferred)
        return stats

    def _ingest_walk(
        self,
        repo_path: Path,
        incremental: bool,
        repo_key: str | None = None,
        rel_root: Path | None = None,
    ) -> dict[str, int]:
        rel_root = rel_root or repo_path
        repo_key = repo_key or repo_identity(repo_path)
        # Create repository node. Keyed by a portable identity, not the
        # absolute checkout path — exports are committed/shared, and machine
        # paths churned ids across machines and leaked local layout (#145).
        # `name` is derived from the key rather than the directory, or a
        # worktree's directory name silently clobbers the display name of the
        # node its files are attached to (#167).
        self.store.create_node(
            NodeLabel.Repository,
            {
                "name": repo_display_name(repo_key),
                "path": repo_key,
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
            "removed": 0,
            "content_stored": 0,
            "prose_indexed": 0,
        }

        # Every path this pass saw on disk, recorded before any decision about
        # whether it can be parsed. Pruning against the parsed set instead would
        # delete files whose optional grammar merely isn't installed.
        present: set[str] = set()

        for source_file in self._iter_source_files(repo_path):
            rel_path = _relative_path(source_file, rel_root)
            if rel_path is None:
                continue
            present.add(rel_path)

            language = LANGUAGE_MAP.get(source_file.suffix)
            if not language:
                continue

            parser = self._get_parser(language)
            if parser is None:
                stats["grammar_skipped"] += 1
                continue

            content_hash = _file_hash(source_file)

            if incremental and self._file_unchanged(rel_path, content_hash):
                stats["skipped"] += 1
                continue

            if incremental:
                self._clear_file_subgraph(rel_path)

            parse_path, effective_root = self._maybe_redact_to_tmp(source_file, rel_root)
            try:
                file_stats = parser.parse_file(parse_path, effective_root, self.store)
                stats["files"] += 1
                stats["functions"] += file_stats.get("functions", 0)
                stats["classes"] += file_stats.get("classes", 0)
                stats["edges"] += file_stats.get("edges", 0)

                self._store_file_hash(rel_path, content_hash)
                self._link_file_to_repo(rel_path, repo_key)
                stats["edges"] += 1
                if self._store_content(source_file, content_hash):
                    stats["content_stored"] += 1
                if self._index_prose(rel_path, source_file):
                    stats["prose_indexed"] += 1
                if stats["files"] % 1000 == 0:
                    logger.info(
                        "Ingest progress %s: %d files parsed", repo_path.name, stats["files"]
                    )
            except Exception:
                logger.exception("Failed to parse %s", source_file)
            finally:
                # Remove the temporary redacted directory if one was created
                if effective_root is not rel_root:
                    import shutil

                    shutil.rmtree(effective_root, ignore_errors=True)

        # Ansible pass — heuristically detect and parse Ansible YAML files
        self._ingest_ansible(repo_path, stats, incremental, rel_root, repo_key, present)

        # Anything the repository no longer contains is removed. Without this a
        # maintained graph only ever grows: deleted files keep their File node,
        # every symbol they contained, and all their edges, and those ghosts are
        # still returned by queries and cited in impact answers (#168).
        stats["removed"] = self._prune_deleted_files(present, repo_key)

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

    def _resolve_deferred_edges(self, deferred: list[tuple[tuple, dict]]) -> int:
        """
        Replay edge creations that missed an endpoint during the walk.

        By now every node from the pass exists, so one replay reaches the
        fixpoint — edges never create nodes. Entries whose endpoint is
        genuinely absent from the repo (library calls, builtins) stay
        unresolved and are dropped, exactly as before.
        """
        resolved = 0
        seen: set[str] = set()
        for args, kwargs in deferred:
            key = repr((args, kwargs))
            if key in seen:
                continue
            seen.add(key)
            try:
                if self.store.create_edge(*args, **kwargs):
                    resolved += 1
            except Exception:
                logger.exception("Failed to replay deferred edge: %r", args)
        if resolved:
            logger.info("Resolution sweep created %d forward-reference edges", resolved)
        return resolved

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

    @property
    def _content(self):
        """
        Lazily opened content store, or None when content is not being kept.

        Opened once per ingester rather than per file: it holds a Redis
        connection, and building one for each of 50,000 files would dominate
        the ingest.
        """
        if not self.store_content:
            return None
        if self._content_store is None:
            from navegador.graph.content import ContentStore

            self._content_store = ContentStore(self.store)
        return self._content_store

    def _store_content(self, source_file: Path, content_hash: str) -> bool:
        """
        Keep the file's text so lexical search has something to match against.

        Returns True only when the blob was newly written; an unchanged file
        or one already stored by another repository returns False, which is
        what makes re-ingest and vendored copies free.

        Redaction is deliberately honoured here: if content is being redacted
        for the graph, storing the unredacted original beside it would put the
        secret back on the server through another door.
        """
        content = self._content
        if content is None:
            return False
        try:
            text = source_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False
        if self._detector is not None:
            text = self._detector.redact(text)
        return content.put(content_hash, text)

    def _index_prose(self, rel_path: str, source_file: Path) -> bool:
        """
        Index the literals, comments and identifiers parsing discards (#185).

        Separate from content storage because the two answer different
        questions: content backs exact matching, this backs ranked "what is
        about this" search.
        """
        if not self.store_content:
            return False
        if self._prose_index is None:
            from navegador.graph.prose import ProseIndex

            self._prose_index = ProseIndex(self.store)
        try:
            text = source_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False
        if self._detector is not None:
            text = self._detector.redact(text)
        return self._prose_index.index_file(rel_path, text)

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

    def _prune_deleted_files(self, present: set[str], repo_key: str) -> int:
        """
        Remove File/Document nodes for paths this repo no longer contains.

        Scoped by BELONGS_TO so a shared or federated graph only loses the
        entries of the repository actually being ingested. Returns how many
        paths were removed.
        """
        try:
            rows = self.store.query(queries.REPO_FILE_PATHS, {"repo": repo_key}).result_set or []
        except Exception:
            logger.exception("Could not list known files for %s; skipping prune", repo_key)
            return 0

        stale = [row[0] for row in rows if row and row[0] and row[0] not in present]
        for path in stale:
            self.store.query(queries.DELETE_FILE_CHILDREN, {"path": path})
            self.store.query(queries.DELETE_FILE_IMPORTS, {"path": path})
            self.store.query(queries.DELETE_FILE_NODE, {"path": path})
            # The prose node is keyed by path like File is, and is just as
            # stale once the file is gone. Missing it reintroduced #168 for a
            # node type introduced after that fix: an incrementally maintained
            # graph stopped matching a clean rebuild.
            self.store.query(queries.DELETE_FILE_TEXT, {"path": path})

        if stale:
            logger.info(
                "Removed %d file(s) no longer present in %s: %s",
                len(stale),
                repo_key,
                ", ".join(sorted(stale)[:10]) + ("…" if len(stale) > 10 else ""),
            )
        return len(stale)

    def _clear_file_subgraph(self, rel_path: str) -> None:
        suffix = Path(rel_path).suffix.lower()
        if suffix in self._DOCUMENT_EXTENSIONS:
            self.store.query(queries.CLEAR_DOCUMENT_REFERENCES, {"path": rel_path})
        else:
            self.store.query(queries.DELETE_FILE_SUBGRAPH, {"path": rel_path})

    def _link_file_to_repo(self, rel_path: str, repo_key: str) -> None:
        """
        BELONGS_TO edge from a parsed File/Document to its Repository (#144),
        so consumers can answer "which repo is this node from" in one hop
        instead of guessing by path prefix.
        """
        suffix = Path(rel_path).suffix.lower()
        label = NodeLabel.Document if suffix in self._DOCUMENT_EXTENSIONS else NodeLabel.File
        self.store.create_edge(
            label,
            {"path": rel_path},
            EdgeType.BELONGS_TO,
            NodeLabel.Repository,
            {"path": repo_key},
        )

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
        visible_files, visible_dirs = self._git_visible(repo_path)
        for dirpath, dirnames, filenames in os.walk(repo_path):
            current = Path(dirpath)
            kept = []
            for d in dirnames:
                if d in self._SKIP_DIRS:
                    continue
                child = current / d
                # Prune ignored directories rather than filtering their files
                # afterwards, so a large ignored tree is never descended into.
                if visible_dirs is not None and child not in visible_dirs:
                    continue
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
                if visible_files is not None and path not in visible_files:
                    continue
                if patterns and self._matches_exclusion(
                    path.relative_to(repo_path).as_posix(), patterns
                ):
                    continue
                if path.is_file():  # excludes broken symlinks, FIFOs, sockets
                    yield path

    def _git_visible(self, repo_path: Path) -> tuple[set[Path] | None, set[Path] | None]:
        """
        The files git does not ignore, plus every directory leading to one.

        Returns ``(None, None)`` when the answer is "no opinion" — gitignore
        respect is off, the path is not a git checkout, or git returned
        nothing — and the caller then walks everything as before.

        The directory set exists so ignored trees can be pruned during the
        walk instead of being descended into and filtered file by file; a
        repository with 54k files of ignored build output should cost nothing
        to skip.
        """
        if not self.respect_gitignore:
            return None, None
        from navegador.vcs import GitAdapter

        adapter = GitAdapter(repo_path)
        if not adapter.is_repo():
            return None, None
        relative = adapter.visible_files()
        if not relative:
            return None, None
        files = {repo_path / rel for rel in relative}
        directories: set[Path] = set()
        for path in files:
            for parent in path.parents:
                if parent in directories:
                    break  # this chain is already recorded
                if parent == repo_path:
                    break
                directories.add(parent)
        return files, directories

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

    def _ingest_ansible(
        self,
        repo_path: Path,
        stats: dict[str, int],
        incremental: bool,
        rel_root: Path | None = None,
        repo_key: str | None = None,
        present: set[str] | None = None,
    ) -> None:
        """Detect and parse Ansible YAML files (playbooks, roles, tasks)."""
        from navegador.ingestion.ansible import AnsibleParser

        rel_root = rel_root or repo_path
        repo_key = repo_key or repo_identity(repo_path)
        is_ansible_file = AnsibleParser.is_ansible_file

        ansible_parser: AnsibleParser | None = None

        for path in self._walk_files(repo_path):
            if path.suffix not in (".yml", ".yaml"):
                continue
            if not is_ansible_file(path, repo_path):
                continue

            rel_path = _relative_path(path, rel_root)
            if rel_path is None:
                continue
            # Recorded before the unchanged/skip check: an Ansible file that did
            # not need re-parsing this pass is still present on disk and must
            # not be pruned.
            if present is not None:
                present.add(rel_path)
            content_hash = _file_hash(path)

            if incremental and self._file_unchanged(rel_path, content_hash):
                stats["skipped"] += 1
                continue

            if incremental:
                self._clear_file_subgraph(rel_path)

            if ansible_parser is None:
                ansible_parser = AnsibleParser()
            try:
                file_stats = ansible_parser.parse_file(path, rel_root, self.store)
                stats["files"] += 1
                stats["functions"] += file_stats.get("functions", 0)
                stats["classes"] += file_stats.get("classes", 0)
                stats["edges"] += file_stats.get("edges", 0)
                self._store_file_hash(rel_path, content_hash)
                self._link_file_to_repo(rel_path, repo_key)
                stats["edges"] += 1
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


class _EdgeDeferringStore:
    """
    Proxy over a GraphStore for the duration of one ingest pass.

    ``create_edge`` calls whose endpoints don't both exist yet (forward
    references — the callee's node is created by a file parsed later) are
    recorded in ``deferred`` instead of being silently dropped;
    :meth:`RepoIngester._resolve_deferred_edges` replays them once after the
    walk (#143). Every other attribute passes through to the real store.
    """

    def __init__(self, store: GraphStore) -> None:
        self._store = store
        self.deferred: list[tuple[tuple, dict]] = []

    def __getattr__(self, name):
        return getattr(self._store, name)

    def create_edge(self, *args, **kwargs) -> bool:
        matched = self._store.create_edge(*args, **kwargs)
        if not matched:
            self.deferred.append((args, kwargs))
        return matched


def _file_hash(path: Path) -> str:
    """SHA-256 content hash for a file."""
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


class LanguageParser:
    """Base class for language-specific AST parsers."""

    def parse_file(self, path: Path, repo_root: Path, store: GraphStore) -> dict[str, int]:
        raise NotImplementedError
