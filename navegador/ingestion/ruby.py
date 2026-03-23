"""
Ruby AST parser — extracts classes, modules, methods, and requires/includes
from .rb files using tree-sitter.
"""

import logging
from pathlib import Path

from navegador.graph.schema import EdgeType, NodeLabel
from navegador.graph.store import GraphStore
from navegador.ingestion.parser import LanguageParser

logger = logging.getLogger(__name__)


def _get_ruby_language():
    try:
        import tree_sitter_ruby as tsruby  # type: ignore[import]
        from tree_sitter import Language

        return Language(tsruby.language())
    except ImportError as e:
        raise ImportError("Install tree-sitter-ruby: pip install tree-sitter-ruby") from e


def _node_text(node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


class RubyParser(LanguageParser):
    """Parses Ruby source files into the navegador graph."""

    def __init__(self) -> None:
        from tree_sitter import Parser  # type: ignore[import]

        self._parser = Parser(_get_ruby_language())

    def parse_file(self, path: Path, repo_root: Path, store: GraphStore) -> dict[str, int]:
        source = path.read_bytes()
        tree = self._parser.parse(source)
        rel_path = str(path.relative_to(repo_root))

        store.create_node(
            NodeLabel.File,
            {
                "name": path.name,
                "path": rel_path,
                "language": "ruby",
                "line_count": source.count(b"\n"),
            },
        )

        stats = {"functions": 0, "classes": 0, "edges": 0}
        self._walk(tree.root_node, source, rel_path, store, stats, class_name=None)
        return stats

    # ── AST walker ────────────────────────────────────────────────────────────

    def _walk(
        self,
        node,
        source: bytes,
        file_path: str,
        store: GraphStore,
        stats: dict,
        class_name: str | None,
    ) -> None:
        if node.type == "class":
            self._handle_class(node, source, file_path, store, stats)
            return
        if node.type == "module":
            self._handle_module(node, source, file_path, store, stats)
            return
        if node.type == "method":
            self._handle_method(node, source, file_path, store, stats, class_name)
            return
        if node.type == "singleton_method":
            self._handle_method(node, source, file_path, store, stats, class_name)
            return
        if node.type == "call":
            # require / require_relative / include
            self._maybe_handle_require(node, source, file_path, store, stats)
        for child in node.children:
            self._walk(child, source, file_path, store, stats, class_name)

    # ── Handlers ──────────────────────────────────────────────────────────────

    def _handle_class(
        self, node, source: bytes, file_path: str, store: GraphStore, stats: dict
    ) -> None:
        name_node = node.child_by_field_name("name")
        if not name_node:
            name_node = next(
                (c for c in node.children if c.type in ("constant", "scope_resolution")), None
            )
        if not name_node:
            return
        name = _node_text(name_node, source)

        store.create_node(
            NodeLabel.Class,
            {
                "name": name,
                "file_path": file_path,
                "line_start": node.start_point[0] + 1,
                "line_end": node.end_point[0] + 1,
                "docstring": "",
            },
        )
        store.create_edge(
            NodeLabel.File,
            {"path": file_path},
            EdgeType.CONTAINS,
            NodeLabel.Class,
            {"name": name, "file_path": file_path},
        )
        stats["classes"] += 1
        stats["edges"] += 1

        # Superclass
        superclass = node.child_by_field_name("superclass")
        if superclass:
            parent_name = _node_text(superclass, source).lstrip("<").strip()
            store.create_edge(
                NodeLabel.Class,
                {"name": name, "file_path": file_path},
                EdgeType.INHERITS,
                NodeLabel.Class,
                {"name": parent_name, "file_path": file_path},
            )
            stats["edges"] += 1

        # Walk body for methods
        body = node.child_by_field_name("body")
        if not body:
            body = next((c for c in node.children if c.type == "body_statement"), None)
        if body:
            for child in body.children:
                if child.type in ("method", "singleton_method"):
                    self._handle_method(child, source, file_path, store, stats, class_name=name)

    def _handle_module(
        self, node, source: bytes, file_path: str, store: GraphStore, stats: dict
    ) -> None:
        name_node = node.child_by_field_name("name")
        if not name_node:
            name_node = next(
                (c for c in node.children if c.type in ("constant", "scope_resolution")), None
            )
        if not name_node:
            return
        name = _node_text(name_node, source)

        store.create_node(
            NodeLabel.Class,
            {
                "name": name,
                "file_path": file_path,
                "line_start": node.start_point[0] + 1,
                "line_end": node.end_point[0] + 1,
                "docstring": "module",
            },
        )
        store.create_edge(
            NodeLabel.File,
            {"path": file_path},
            EdgeType.CONTAINS,
            NodeLabel.Class,
            {"name": name, "file_path": file_path},
        )
        stats["classes"] += 1
        stats["edges"] += 1

        body = node.child_by_field_name("body")
        if not body:
            body = next((c for c in node.children if c.type == "body_statement"), None)
        if body:
            for child in body.children:
                if child.type in ("method", "singleton_method"):
                    self._handle_method(child, source, file_path, store, stats, class_name=name)

    def _handle_method(
        self,
        node,
        source: bytes,
        file_path: str,
        store: GraphStore,
        stats: dict,
        class_name: str | None,
    ) -> None:
        name_node = node.child_by_field_name("name")
        if not name_node:
            name_node = next((c for c in node.children if c.type == "identifier"), None)
        if not name_node:
            return
        name = _node_text(name_node, source)

        label = NodeLabel.Method if class_name else NodeLabel.Function
        store.create_node(
            label,
            {
                "name": name,
                "file_path": file_path,
                "line_start": node.start_point[0] + 1,
                "line_end": node.end_point[0] + 1,
                "docstring": "",
                "class_name": class_name or "",
            },
        )

        container_label = NodeLabel.Class if class_name else NodeLabel.File
        container_key = (
            {"name": class_name, "file_path": file_path} if class_name else {"path": file_path}
        )
        store.create_edge(
            container_label,
            container_key,
            EdgeType.CONTAINS,
            label,
            {"name": name, "file_path": file_path},
        )
        stats["functions"] += 1
        stats["edges"] += 1

        self._extract_calls(node, source, file_path, name, label, store, stats)

    def _maybe_handle_require(
        self, node, source: bytes, file_path: str, store: GraphStore, stats: dict
    ) -> None:
        method_node = node.child_by_field_name("method")
        if not method_node:
            method_node = next((c for c in node.children if c.type == "identifier"), None)
        if not method_node:
            return
        method_name = _node_text(method_node, source)
        if method_name not in ("require", "require_relative", "load"):
            return

        args = node.child_by_field_name("arguments")
        if not args:
            return
        for child in args.children:
            if child.type in ("string", "simple_symbol"):
                module = _node_text(child, source).strip("'\":")
                store.create_node(
                    NodeLabel.Import,
                    {
                        "name": module,
                        "file_path": file_path,
                        "line_start": node.start_point[0] + 1,
                        "module": module,
                    },
                )
                store.create_edge(
                    NodeLabel.File,
                    {"path": file_path},
                    EdgeType.IMPORTS,
                    NodeLabel.Import,
                    {"name": module, "file_path": file_path},
                )
                stats["edges"] += 1
                break

    def _extract_calls(
        self,
        fn_node,
        source: bytes,
        file_path: str,
        fn_name: str,
        fn_label: str,
        store: GraphStore,
        stats: dict,
    ) -> None:
        def walk(node):
            if node.type == "call":
                method_node = node.child_by_field_name("method")
                if not method_node:
                    method_node = next((c for c in node.children if c.type == "identifier"), None)
                if method_node:
                    callee = _node_text(method_node, source)
                    if callee not in ("require", "require_relative", "load"):
                        store.create_edge(
                            fn_label,
                            {"name": fn_name, "file_path": file_path},
                            EdgeType.CALLS,
                            NodeLabel.Function,
                            {"name": callee, "file_path": file_path},
                        )
                        stats["edges"] += 1
            for child in node.children:
                walk(child)

        body = fn_node.child_by_field_name("body")
        if not body:
            body = next((c for c in fn_node.children if c.type == "body_statement"), None)
        if body:
            walk(body)
