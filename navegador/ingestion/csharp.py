"""
C# AST parser — extracts classes, interfaces, structs, methods, and
imports (using directives) from .cs files using tree-sitter.
"""

import logging
from pathlib import Path

from navegador.graph.schema import EdgeType, NodeLabel
from navegador.graph.store import GraphStore
from navegador.ingestion.parser import LanguageParser

logger = logging.getLogger(__name__)


def _get_csharp_language():
    try:
        import tree_sitter_c_sharp as tscsharp  # type: ignore[import]
        from tree_sitter import Language

        return Language(tscsharp.language())
    except ImportError as e:
        raise ImportError("Install tree-sitter-c-sharp: pip install tree-sitter-c-sharp") from e


def _node_text(node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


class CSharpParser(LanguageParser):
    """Parses C# source files into the navegador graph."""

    def __init__(self) -> None:
        from tree_sitter import Parser  # type: ignore[import]

        self._parser = Parser(_get_csharp_language())

    def parse_file(self, path: Path, repo_root: Path, store: GraphStore) -> dict[str, int]:
        source = path.read_bytes()
        tree = self._parser.parse(source)
        rel_path = str(path.relative_to(repo_root))

        store.create_node(
            NodeLabel.File,
            {
                "name": path.name,
                "path": rel_path,
                "language": "csharp",
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
        if node.type in (
            "class_declaration",
            "interface_declaration",
            "struct_declaration",
            "record_declaration",
        ):
            self._handle_class(node, source, file_path, store, stats)
            return
        if node.type in ("method_declaration", "constructor_declaration"):
            self._handle_method(node, source, file_path, store, stats, class_name)
            return
        if node.type == "using_directive":
            self._handle_using(node, source, file_path, store, stats)
            return
        for child in node.children:
            self._walk(child, source, file_path, store, stats, class_name)

    # ── Handlers ──────────────────────────────────────────────────────────────

    def _handle_class(
        self, node, source: bytes, file_path: str, store: GraphStore, stats: dict
    ) -> None:
        name_node = node.child_by_field_name("name")
        if not name_node:
            name_node = next((c for c in node.children if c.type == "identifier"), None)
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

        # Superclass / interfaces
        bases = node.child_by_field_name("bases")
        if bases:
            for child in bases.children:
                if child.type == "identifier":
                    parent_name = _node_text(child, source)
                    store.create_edge(
                        NodeLabel.Class,
                        {"name": name, "file_path": file_path},
                        EdgeType.INHERITS,
                        NodeLabel.Class,
                        {"name": parent_name, "file_path": file_path},
                    )
                    stats["edges"] += 1

        # Walk class body for methods
        body = node.child_by_field_name("body")
        if not body:
            body = next((c for c in node.children if c.type == "declaration_list"), None)
        if body:
            for child in body.children:
                if child.type in ("method_declaration", "constructor_declaration"):
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

    def _handle_using(
        self, node, source: bytes, file_path: str, store: GraphStore, stats: dict
    ) -> None:
        raw = _node_text(node, source).strip()
        # "using System.Collections.Generic;" or "using static ..."
        module = raw.removeprefix("using").removeprefix(" static").removesuffix(";").strip()
        if not module:
            return
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
            if node.type == "invocation_expression":
                func = node.child_by_field_name("function")
                if not func:
                    func = next(
                        (
                            c
                            for c in node.children
                            if c.type in ("identifier", "member_access_expression")
                        ),
                        None,
                    )
                if func:
                    callee = _node_text(func, source).split(".")[-1]
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
            body = next((c for c in fn_node.children if c.type == "block"), None)
        if body:
            walk(body)
