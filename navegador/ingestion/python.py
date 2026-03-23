"""
Python AST parser — extracts classes, functions, imports, calls, and
their relationships from .py files using tree-sitter.
"""

import logging
from pathlib import Path

from navegador.graph.schema import EdgeType, NodeLabel
from navegador.graph.store import GraphStore
from navegador.ingestion.parser import LanguageParser

logger = logging.getLogger(__name__)


def _get_python_language():
    try:
        import tree_sitter_python as tspython  # type: ignore[import]
        from tree_sitter import Language

        return Language(tspython.language())
    except ImportError as e:
        raise ImportError("Install tree-sitter-python: pip install tree-sitter-python") from e


def _get_parser():
    from tree_sitter import Parser  # type: ignore[import]

    parser = Parser(_get_python_language())
    return parser


def _node_text(node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _get_docstring(node, source: bytes) -> str | None:
    """Extract the first string literal from a function/class body as docstring."""
    body = next((c for c in node.children if c.type == "block"), None)
    if not body:
        return None
    first_stmt = next((c for c in body.children if c.type == "expression_statement"), None)
    if not first_stmt:
        return None
    string_node = next(
        (c for c in first_stmt.children if c.type in ("string", "string_content")), None
    )
    if string_node:
        raw = _node_text(string_node, source)
        return raw.strip('"""').strip("'''").strip('"').strip("'").strip()
    return None


class PythonParser(LanguageParser):
    def __init__(self) -> None:
        self._parser = _get_parser()

    def parse_file(self, path: Path, repo_root: Path, store: GraphStore) -> dict[str, int]:
        source = path.read_bytes()
        tree = self._parser.parse(source)
        rel_path = str(path.relative_to(repo_root))

        stats = {"functions": 0, "classes": 0, "edges": 0}

        # File node
        store.create_node(
            NodeLabel.File,
            {
                "name": path.name,
                "path": rel_path,
                "language": "python",
                "line_count": source.count(b"\n"),
            },
        )

        self._walk(tree.root_node, source, rel_path, store, stats, class_name=None)
        return stats

    def _walk(
        self,
        node,
        source: bytes,
        file_path: str,
        store: GraphStore,
        stats: dict,
        class_name: str | None,
    ) -> None:
        if node.type == "import_statement":
            self._handle_import(node, source, file_path, store, stats)

        elif node.type == "import_from_statement":
            self._handle_import_from(node, source, file_path, store, stats)

        elif node.type == "class_definition":
            self._handle_class(node, source, file_path, store, stats)
            return  # class walker handles children

        elif node.type == "function_definition":
            self._handle_function(node, source, file_path, store, stats, class_name)
            return  # function walker handles children

        for child in node.children:
            self._walk(child, source, file_path, store, stats, class_name)

    def _handle_import(
        self, node, source: bytes, file_path: str, store: GraphStore, stats: dict
    ) -> None:
        for child in node.children:
            if child.type == "dotted_name":
                name = _node_text(child, source)
                store.create_node(
                    NodeLabel.Import,
                    {
                        "name": name,
                        "file_path": file_path,
                        "line_start": node.start_point[0] + 1,
                        "module": name,
                    },
                )
                store.create_edge(
                    NodeLabel.File,
                    {"path": file_path},
                    EdgeType.IMPORTS,
                    NodeLabel.Import,
                    {"name": name, "file_path": file_path},
                )
                stats["edges"] += 1

    def _handle_import_from(
        self, node, source: bytes, file_path: str, store: GraphStore, stats: dict
    ) -> None:
        module = ""
        for child in node.children:
            if child.type in ("dotted_name", "relative_import"):
                module = _node_text(child, source)
                break
        for child in node.children:
            if child.type == "import_from_member":
                name = _node_text(child, source)
                store.create_node(
                    NodeLabel.Import,
                    {
                        "name": name,
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
                    {"name": name, "file_path": file_path},
                )
                stats["edges"] += 1

    def _handle_class(
        self, node, source: bytes, file_path: str, store: GraphStore, stats: dict
    ) -> None:
        name_node = next((c for c in node.children if c.type == "identifier"), None)
        if not name_node:
            return
        name = _node_text(name_node, source)
        docstring = _get_docstring(node, source)

        store.create_node(
            NodeLabel.Class,
            {
                "name": name,
                "file_path": file_path,
                "line_start": node.start_point[0] + 1,
                "line_end": node.end_point[0] + 1,
                "docstring": docstring or "",
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

        # Inheritance
        for child in node.children:
            if child.type == "argument_list":
                for arg in child.children:
                    if arg.type == "identifier":
                        parent_name = _node_text(arg, source)
                        store.create_edge(
                            NodeLabel.Class,
                            {"name": name, "file_path": file_path},
                            EdgeType.INHERITS,
                            NodeLabel.Class,
                            {"name": parent_name, "file_path": file_path},
                        )
                        stats["edges"] += 1

        # Walk class body for methods
        body = next((c for c in node.children if c.type == "block"), None)
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
        name_node = next((c for c in node.children if c.type == "identifier"), None)
        if not name_node:
            return
        name = _node_text(name_node, source)
        docstring = _get_docstring(node, source)

        label = NodeLabel.Method if class_name else NodeLabel.Function
        props = {
            "name": name,
            "file_path": file_path,
            "line_start": node.start_point[0] + 1,
            "line_end": node.end_point[0] + 1,
            "docstring": docstring or "",
            "class_name": class_name or "",
        }
        store.create_node(label, props)

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

        # Call edges — find all call expressions in the body
        self._extract_calls(node, source, file_path, name, label, store, stats)

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
        def walk_calls(node):
            if node.type == "call":
                func = next(
                    (c for c in node.children if c.type in ("identifier", "attribute")), None
                )
                if func:
                    callee_name = _node_text(func, source).split(".")[-1]
                    store.create_edge(
                        fn_label,
                        {"name": fn_name, "file_path": file_path},
                        EdgeType.CALLS,
                        NodeLabel.Function,
                        {"name": callee_name, "file_path": file_path},
                    )
                    stats["edges"] += 1
            for child in node.children:
                walk_calls(child)

        body = next((c for c in fn_node.children if c.type == "block"), None)
        if body:
            walk_calls(body)
