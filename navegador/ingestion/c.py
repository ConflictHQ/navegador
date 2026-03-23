"""
C AST parser — extracts functions, structs, typedefs, and #include directives
from .c and .h files using tree-sitter.
"""

import logging
from pathlib import Path

from navegador.graph.schema import EdgeType, NodeLabel
from navegador.graph.store import GraphStore
from navegador.ingestion.parser import LanguageParser

logger = logging.getLogger(__name__)


def _get_c_language():
    try:
        import tree_sitter_c as tsc  # type: ignore[import]
        from tree_sitter import Language

        return Language(tsc.language())
    except ImportError as e:
        raise ImportError(
            "Install tree-sitter-c: pip install tree-sitter-c"
        ) from e


def _node_text(node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


class CParser(LanguageParser):
    """Parses C source files into the navegador graph."""

    def __init__(self) -> None:
        from tree_sitter import Parser  # type: ignore[import]

        self._parser = Parser(_get_c_language())

    def parse_file(self, path: Path, repo_root: Path, store: GraphStore) -> dict[str, int]:
        source = path.read_bytes()
        tree = self._parser.parse(source)
        rel_path = str(path.relative_to(repo_root))

        store.create_node(
            NodeLabel.File,
            {
                "name": path.name,
                "path": rel_path,
                "language": "c",
                "line_count": source.count(b"\n"),
            },
        )

        stats = {"functions": 0, "classes": 0, "edges": 0}
        self._walk(tree.root_node, source, rel_path, store, stats)
        return stats

    # ── AST walker ────────────────────────────────────────────────────────────

    def _walk(
        self,
        node,
        source: bytes,
        file_path: str,
        store: GraphStore,
        stats: dict,
    ) -> None:
        if node.type == "function_definition":
            self._handle_function(node, source, file_path, store, stats)
            return
        if node.type in ("struct_specifier", "union_specifier", "enum_specifier"):
            self._handle_struct(node, source, file_path, store, stats)
            return
        if node.type == "preproc_include":
            self._handle_include(node, source, file_path, store, stats)
            return
        for child in node.children:
            self._walk(child, source, file_path, store, stats)

    # ── Handlers ──────────────────────────────────────────────────────────────

    def _handle_function(
        self, node, source: bytes, file_path: str, store: GraphStore, stats: dict
    ) -> None:
        # function_definition: type declarator body
        # declarator may be a function_declarator with a name field
        declarator = node.child_by_field_name("declarator")
        name = self._extract_function_name(declarator, source)
        if not name:
            return

        store.create_node(
            NodeLabel.Function,
            {
                "name": name,
                "file_path": file_path,
                "line_start": node.start_point[0] + 1,
                "line_end": node.end_point[0] + 1,
                "docstring": "",
                "class_name": "",
            },
        )
        store.create_edge(
            NodeLabel.File,
            {"path": file_path},
            EdgeType.CONTAINS,
            NodeLabel.Function,
            {"name": name, "file_path": file_path},
        )
        stats["functions"] += 1
        stats["edges"] += 1

        self._extract_calls(node, source, file_path, name, store, stats)

    def _extract_function_name(self, declarator, source: bytes) -> str | None:
        """Recursively dig through declarator nodes to find the function name."""
        if declarator is None:
            return None
        if declarator.type == "identifier":
            return _node_text(declarator, source)
        if declarator.type == "function_declarator":
            inner = declarator.child_by_field_name("declarator")
            return self._extract_function_name(inner, source)
        if declarator.type == "pointer_declarator":
            inner = declarator.child_by_field_name("declarator")
            return self._extract_function_name(inner, source)
        # Fallback: look for identifier child
        for child in declarator.children:
            if child.type == "identifier":
                return _node_text(child, source)
        return None

    def _handle_struct(
        self, node, source: bytes, file_path: str, store: GraphStore, stats: dict
    ) -> None:
        name_node = node.child_by_field_name("name")
        if not name_node:
            name_node = next((c for c in node.children if c.type == "type_identifier"), None)
        if not name_node:
            return
        name = _node_text(name_node, source)

        kind = "struct" if node.type == "struct_specifier" else (
            "union" if node.type == "union_specifier" else "enum"
        )
        store.create_node(
            NodeLabel.Class,
            {
                "name": name,
                "file_path": file_path,
                "line_start": node.start_point[0] + 1,
                "line_end": node.end_point[0] + 1,
                "docstring": kind,
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
        store: GraphStore,
        stats: dict,
    ) -> None:
        def walk(node):
            if node.type == "call_expression":
                func = node.child_by_field_name("function")
                if not func:
                    func = next(
                        (c for c in node.children if c.type == "identifier"), None
                    )
                if func:
                    callee = _node_text(func, source)
                    store.create_edge(
                        NodeLabel.Function,
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
