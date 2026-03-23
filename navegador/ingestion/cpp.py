"""
C++ AST parser — extracts classes, structs, namespaces, functions, methods,
and #include directives from .cpp, .hpp, .cc, and .cxx files using tree-sitter.
"""

import logging
from pathlib import Path

from navegador.graph.schema import EdgeType, NodeLabel
from navegador.graph.store import GraphStore
from navegador.ingestion.parser import LanguageParser

logger = logging.getLogger(__name__)


def _get_cpp_language():
    try:
        import tree_sitter_cpp as tscpp  # type: ignore[import]
        from tree_sitter import Language

        return Language(tscpp.language())
    except ImportError as e:
        raise ImportError("Install tree-sitter-cpp: pip install tree-sitter-cpp") from e


def _node_text(node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


class CppParser(LanguageParser):
    """Parses C++ source files into the navegador graph."""

    def __init__(self) -> None:
        from tree_sitter import Parser  # type: ignore[import]

        self._parser = Parser(_get_cpp_language())

    def parse_file(self, path: Path, repo_root: Path, store: GraphStore) -> dict[str, int]:
        source = path.read_bytes()
        tree = self._parser.parse(source)
        rel_path = str(path.relative_to(repo_root))

        store.create_node(
            NodeLabel.File,
            {
                "name": path.name,
                "path": rel_path,
                "language": "cpp",
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
        if node.type in ("class_specifier", "struct_specifier"):
            self._handle_class(node, source, file_path, store, stats)
            return
        if node.type == "function_definition":
            self._handle_function(node, source, file_path, store, stats, class_name)
            return
        if node.type == "preproc_include":
            self._handle_include(node, source, file_path, store, stats)
            return
        if node.type == "namespace_definition":
            # Recurse into namespace body
            body = node.child_by_field_name("body")
            if not body:
                body = next((c for c in node.children if c.type == "declaration_list"), None)
            if body:
                for child in body.children:
                    self._walk(child, source, file_path, store, stats, class_name)
            return
        for child in node.children:
            self._walk(child, source, file_path, store, stats, class_name)

    # ── Handlers ──────────────────────────────────────────────────────────────

    def _handle_class(
        self, node, source: bytes, file_path: str, store: GraphStore, stats: dict
    ) -> None:
        name_node = node.child_by_field_name("name")
        if not name_node:
            name_node = next((c for c in node.children if c.type == "type_identifier"), None)
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

        # Base classes
        base_clause = node.child_by_field_name("base_clause")
        if not base_clause:
            base_clause = next((c for c in node.children if c.type == "base_class_clause"), None)
        if base_clause:
            for child in base_clause.children:
                if child.type == "type_identifier":
                    parent_name = _node_text(child, source)
                    store.create_edge(
                        NodeLabel.Class,
                        {"name": name, "file_path": file_path},
                        EdgeType.INHERITS,
                        NodeLabel.Class,
                        {"name": parent_name, "file_path": file_path},
                    )
                    stats["edges"] += 1

        # Walk class body for member functions
        body = node.child_by_field_name("body")
        if not body:
            body = next((c for c in node.children if c.type == "field_declaration_list"), None)
        if body:
            for child in body.children:
                if child.type == "function_definition":
                    self._handle_function(child, source, file_path, store, stats, class_name=name)

    def _handle_function(
        self,
        node,
        source: bytes,
        file_path: str,
        store: GraphStore,
        stats: dict,
        class_name: str | None,
    ) -> None:
        declarator = node.child_by_field_name("declarator")
        name = self._extract_function_name(declarator, source)
        if not name:
            return

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

    def _extract_function_name(self, declarator, source: bytes) -> str | None:
        """Recursively dig through C++ declarator nodes to find the function name."""
        if declarator is None:
            return None
        if declarator.type == "identifier":
            return _node_text(declarator, source)
        if declarator.type == "qualified_identifier":
            # MyClass::method — take the last component
            name_node = declarator.child_by_field_name("name")
            if name_node:
                return _node_text(name_node, source)
            # fallback: last identifier child
            ids = [c for c in declarator.children if c.type == "identifier"]
            if ids:
                return _node_text(ids[-1], source)
        if declarator.type in ("function_declarator", "pointer_declarator", "reference_declarator"):
            inner = declarator.child_by_field_name("declarator")
            return self._extract_function_name(inner, source)
        # Fallback: first identifier child
        for child in declarator.children:
            if child.type == "identifier":
                return _node_text(child, source)
        return None

    def _handle_include(
        self, node, source: bytes, file_path: str, store: GraphStore, stats: dict
    ) -> None:
        path_node = node.child_by_field_name("path")
        if not path_node:
            path_node = next(
                (c for c in node.children if c.type in ("string_literal", "system_lib_string")),
                None,
            )
        if not path_node:
            return
        module = _node_text(path_node, source).strip('<>"')
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
            if node.type == "call_expression":
                func = node.child_by_field_name("function")
                if not func:
                    func = next(
                        (
                            c
                            for c in node.children
                            if c.type in ("identifier", "qualified_identifier", "field_expression")
                        ),
                        None,
                    )
                if func:
                    callee = _node_text(func, source).split("::")[-1].split(".")[-1].split("->")[-1]
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
            body = next((c for c in fn_node.children if c.type == "compound_statement"), None)
        if body:
            walk(body)
