"""
Navegador Python SDK — high-level programmatic API.

Wraps the internal graph, ingestion, and context modules into a single
clean interface suitable for building tools on top of navegador.

Usage::

    from navegador import Navegador

    # SQLite (local, zero-infra)
    nav = Navegador.sqlite(".navegador/graph.db")

    # Redis (production)
    nav = Navegador.redis("redis://localhost:6379")

    # Ingest a codebase
    stats = nav.ingest("/path/to/repo")

    # Query context
    bundle = nav.function_context("validate_token")
    bundle = nav.class_context("AuthService")
    bundle = nav.file_context("src/auth.py")

    # Knowledge graph
    nav.add_domain("auth", description="Authentication layer")
    nav.add_concept("JWT", domain="auth")
    nav.annotate("validate_token", "Function", concept="JWT")

    # Search
    results = nav.search("validate")
    results = nav.search_all("JWT")
    results = nav.search_knowledge("token")

    # Raw Cypher
    result = nav.query("MATCH (n:Function) RETURN n.name LIMIT 10")

    # Graph admin
    counts = nav.stats()
    nav.export(".navegador/graph.jsonl")
    nav.import_graph(".navegador/graph.jsonl")
    nav.clear()
"""

from __future__ import annotations

from typing import Any


class Navegador:
    """High-level Python SDK for programmatic use of navegador."""

    def __init__(self, store: Any) -> None:
        """
        Initialise with an existing GraphStore instance.

        Prefer the class methods :meth:`sqlite` and :meth:`redis` for
        typical usage.
        """
        self._store = store

    # ── Constructors ──────────────────────────────────────────────────────────

    @classmethod
    def sqlite(cls, db_path: str = ".navegador/graph.db") -> "Navegador":
        """Create a Navegador instance backed by SQLite (zero-infra, local)."""
        from navegador.graph.store import GraphStore

        return cls(GraphStore.sqlite(db_path))

    @classmethod
    def redis(cls, url: str = "redis://localhost:6379") -> "Navegador":
        """Create a Navegador instance backed by Redis FalkorDB (production)."""
        from navegador.graph.store import GraphStore

        return cls(GraphStore.redis(url))

    # ── Ingestion ─────────────────────────────────────────────────────────────

    def ingest(
        self,
        repo_path: str,
        clear: bool = False,
        incremental: bool = False,
    ) -> dict[str, int]:
        """
        Parse and ingest a repository into the graph.

        Args:
            repo_path: Path to the repository root.
            clear: Wipe the graph before ingesting.
            incremental: Skip files whose content hash has not changed.

        Returns:
            Dict with counts: files, functions, classes, edges, skipped.
        """
        from navegador.ingestion import RepoIngester

        return RepoIngester(self._store).ingest(repo_path, clear=clear, incremental=incremental)

    # ── Context loading ───────────────────────────────────────────────────────

    def file_context(self, file_path: str) -> Any:
        """
        Return a ContextBundle with all symbols in *file_path*.

        Args:
            file_path: Relative or absolute path to the source file.

        Returns:
            :class:`~navegador.context.loader.ContextBundle`
        """
        from navegador.context.loader import ContextLoader

        return ContextLoader(self._store).load_file(file_path)

    def function_context(self, name: str, file_path: str = "", depth: int = 2) -> Any:
        """
        Return a ContextBundle for a function — callers, callees, decorators.

        Args:
            name: Function name.
            file_path: Optional file path to narrow the match.
            depth: Call-graph traversal depth.

        Returns:
            :class:`~navegador.context.loader.ContextBundle`
        """
        from navegador.context.loader import ContextLoader

        return ContextLoader(self._store).load_function(name, file_path=file_path, depth=depth)

    def class_context(self, name: str, file_path: str = "") -> Any:
        """
        Return a ContextBundle for a class — methods, inheritance, references.

        Args:
            name: Class name.
            file_path: Optional file path to narrow the match.

        Returns:
            :class:`~navegador.context.loader.ContextBundle`
        """
        from navegador.context.loader import ContextLoader

        return ContextLoader(self._store).load_class(name, file_path=file_path)

    def explain(self, name: str, file_path: str = "") -> Any:
        """
        Full picture: all inbound and outbound relationships for any node.

        Spans both code and knowledge layers.

        Args:
            name: Node name.
            file_path: Optional file path to narrow the match.

        Returns:
            :class:`~navegador.context.loader.ContextBundle`
        """
        from navegador.context.loader import ContextLoader

        return ContextLoader(self._store).explain(name, file_path=file_path)

    # ── Knowledge ─────────────────────────────────────────────────────────────

    def add_concept(self, name: str, **kwargs: Any) -> None:
        """
        Add a business concept node to the knowledge graph.

        Keyword arguments are forwarded to
        :meth:`~navegador.ingestion.KnowledgeIngester.add_concept`.
        Common kwargs: ``description``, ``domain``, ``status``.
        """
        from navegador.ingestion import KnowledgeIngester

        KnowledgeIngester(self._store).add_concept(name, **kwargs)

    def add_rule(self, name: str, **kwargs: Any) -> None:
        """
        Add a business rule node to the knowledge graph.

        Common kwargs: ``description``, ``domain``, ``severity``, ``rationale``.
        """
        from navegador.ingestion import KnowledgeIngester

        KnowledgeIngester(self._store).add_rule(name, **kwargs)

    def add_decision(self, name: str, **kwargs: Any) -> None:
        """
        Add an architectural or product decision node.

        Common kwargs: ``description``, ``domain``, ``status``,
        ``rationale``, ``alternatives``, ``date``.
        """
        from navegador.ingestion import KnowledgeIngester

        KnowledgeIngester(self._store).add_decision(name, **kwargs)

    def add_person(self, name: str, **kwargs: Any) -> None:
        """
        Add a person (contributor, owner, stakeholder) node.

        Common kwargs: ``email``, ``role``, ``team``.
        """
        from navegador.ingestion import KnowledgeIngester

        KnowledgeIngester(self._store).add_person(name, **kwargs)

    def add_domain(self, name: str, **kwargs: Any) -> None:
        """
        Add a business domain node (e.g. ``auth``, ``billing``).

        Common kwargs: ``description``.
        """
        from navegador.ingestion import KnowledgeIngester

        KnowledgeIngester(self._store).add_domain(name, **kwargs)

    def annotate(
        self,
        code_name: str,
        code_label: str,
        concept: str | None = None,
        rule: str | None = None,
    ) -> None:
        """
        Link a code node to a concept or rule via ``ANNOTATES``.

        Args:
            code_name: Name of the code symbol.
            code_label: Node label — one of ``Function``, ``Class``,
                ``Method``, ``File``, ``Module``.
            concept: Concept name to link to (optional).
            rule: Rule name to link to (optional).
        """
        from navegador.ingestion import KnowledgeIngester

        KnowledgeIngester(self._store).annotate_code(
            code_name, code_label, concept=concept, rule=rule
        )

    def concept(self, name: str) -> Any:
        """
        Load a concept context bundle — rules, related concepts, code.

        Returns:
            :class:`~navegador.context.loader.ContextBundle`
        """
        from navegador.context.loader import ContextLoader

        return ContextLoader(self._store).load_concept(name)

    def domain(self, name: str) -> Any:
        """
        Load a domain context bundle — everything belonging to that domain.

        Returns:
            :class:`~navegador.context.loader.ContextBundle`
        """
        from navegador.context.loader import ContextLoader

        return ContextLoader(self._store).load_domain(name)

    def decision(self, name: str) -> Any:
        """
        Load a decision rationale bundle — alternatives, status, related nodes.

        Returns:
            :class:`~navegador.context.loader.ContextBundle`
        """
        from navegador.context.loader import ContextLoader

        return ContextLoader(self._store).load_decision(name)

    # ── Search ────────────────────────────────────────────────────────────────

    def search(self, query: str, limit: int = 20) -> list[Any]:
        """
        Search code symbols by name.

        Returns:
            List of :class:`~navegador.context.loader.ContextNode`.
        """
        from navegador.context.loader import ContextLoader

        return ContextLoader(self._store).search(query, limit=limit)

    def search_all(self, query: str, limit: int = 20) -> list[Any]:
        """
        Search everything — code symbols, concepts, rules, decisions, wiki.

        Returns:
            List of :class:`~navegador.context.loader.ContextNode`.
        """
        from navegador.context.loader import ContextLoader

        return ContextLoader(self._store).search_all(query, limit=limit)

    def search_knowledge(self, query: str, limit: int = 20) -> list[Any]:
        """
        Search the knowledge layer — concepts, rules, decisions, wiki pages.

        Returns:
            List of :class:`~navegador.context.loader.ContextNode`.
        """
        from navegador.context.loader import ContextLoader

        return ContextLoader(self._store).search_knowledge(query, limit=limit)

    # ── Graph ─────────────────────────────────────────────────────────────────

    def query(self, cypher: str, params: dict[str, Any] | None = None) -> Any:
        """
        Execute a raw Cypher query against the graph.

        Args:
            cypher: Cypher query string.
            params: Optional parameter dict.

        Returns:
            FalkorDB result object (has ``.result_set`` attribute).
        """
        return self._store.query(cypher, params)

    def stats(self) -> dict[str, Any]:
        """
        Return graph statistics broken down by node and edge type.

        Returns:
            Dict with keys ``total_nodes``, ``total_edges``, ``nodes``
            (label → count), ``edges`` (type → count).
        """
        from navegador.graph import queries as q

        node_rows = self._store.query(q.NODE_TYPE_COUNTS).result_set or []
        edge_rows = self._store.query(q.EDGE_TYPE_COUNTS).result_set or []

        return {
            "total_nodes": sum(r[1] for r in node_rows),
            "total_edges": sum(r[1] for r in edge_rows),
            "nodes": {r[0]: r[1] for r in node_rows},
            "edges": {r[0]: r[1] for r in edge_rows},
        }

    def export(self, output_path: str) -> dict[str, int]:
        """
        Export the full graph to a JSONL file.

        Args:
            output_path: Destination file path.

        Returns:
            Dict with counts: ``nodes``, ``edges``.
        """
        from navegador.graph.export import export_graph

        return export_graph(self._store, output_path)

    def import_graph(self, input_path: str, clear: bool = True) -> dict[str, int]:
        """
        Import a graph from a JSONL export file.

        Args:
            input_path: Source JSONL file path.
            clear: Wipe the graph before importing (default ``True``).

        Returns:
            Dict with counts: ``nodes``, ``edges``.
        """
        from navegador.graph.export import import_graph

        return import_graph(self._store, input_path, clear=clear)

    def clear(self) -> None:
        """Delete all nodes and edges in the graph."""
        self._store.clear()

    # ── Owners ────────────────────────────────────────────────────────────────

    def find_owners(self, name: str, file_path: str = "") -> list[Any]:
        """
        Find people assigned as owners of a named node.

        Args:
            name: Node name.
            file_path: Optional file path to narrow the match.

        Returns:
            List of :class:`~navegador.context.loader.ContextNode` with
            ``type="Person"``.
        """
        from navegador.context.loader import ContextLoader

        return ContextLoader(self._store).find_owners(name, file_path=file_path)
