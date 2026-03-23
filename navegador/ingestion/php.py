"""
PHP AST parser — extracts classes, interfaces, traits, functions, methods,
and use statements from .php files using tree-sitter.
"""

import logging
from pathlib import Path

from navegador.graph.schema import EdgeType, NodeLabel
from navegador.graph.store import GraphStore
from navegador.ingestion.parser import LanguageParser

logger = logging.getLogger(__name__)


def _get_php_language():
    try:
        import tree_sitter_php as tsphp  # type: ignore[import]
        from tree_sitter import Language

        # tree-sitter-php exposes language_php() or language()
        lang_fn = getattr(tsphp, "language_php", None) or getattr(tsphp, "language", None)
        if lang_fn is None:
            raise ImportError("tree_sitter_php has no language() or language_php() callable")
        return Language(lang_fn())
    except ImportError as e:
        raise ImportError("Install tree-sitter-php: pip install tree-sitter-php") from e


def _node_text(node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


class PHPParser(LanguageParser):
    """Parses PHP source files into the navegador graph."""

    def __init__(self) -> None:
        from tree_sitter import Parser  # type: ignore[import]

        self._parser = Parser(_get_php_language())

    def parse_file(self, path: Path, repo_root: Path, store: GraphStore) -> dict[str, int]:
        source = path.read_bytes()
        tree = self._parser.parse(source)
        rel_path = str(path.relative_to(repo_root))

        store.create_node(
            NodeLabel.File,
            {
                "name": path.name,
                "path": rel_path,
                "language": "php",
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
            "trait_declaration",
        ):
            self._handle_class(node, source, file_path, store, stats)
            return
        if node.type == "function_definition":
            self._handle_function(node, source, file_path, store, stats, class_name)
            return
        if node.type == "use_declaration":
            self._handle_use(node, source, file_path, store, stats)
            return
        for child in node.children:
            self._walk(child, source, file_path, store, stats, class_name)

    # ── Handlers ──────────────────────────────────────────────────────────────

    def _handle_class(
        self, node, source: bytes, file_path: str, store: GraphStore, stats: dict
    ) -> None:
        name_node = node.child_by_field_name("name")
        if not name_node:
            name_node = next((c for c in node.children if c.type == "name"), None)
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
        base_clause = node.child_by_field_name("base_clause")
        if base_clause:
            for child in base_clause.children:
                if child.type in ("qualified_name", "name"):
                    parent_name = _node_text(child, source)
                    store.create_edge(
                        NodeLabel.Class,
                        {"name": name, "file_path": file_path},
                        EdgeType.INHERITS,
                        NodeLabel.Class,
                        {"name": parent_name, "file_path": file_path},
                    )
                    stats["edges"] += 1
                    break

        # Walk class body for methods
        body = node.child_by_field_name("body")
        if not body:
            body = next((c for c in node.children if c.type == "declaration_list"), None)
        if body:
            for child in body.children:
                if child.type == "method_declaration":
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
        name_node = node.child_by_field_name("name")
        if not name_node:
            name_node = next((c for c in node.children if c.type == "name"), None)
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

    def _handle_use(
        self, node, source: bytes, file_path: str, store: GraphStore, stats: dict
    ) -> None:
        raw = _node_text(node, source).strip()
        module = raw.removeprefix("use").removesuffix(";").strip()
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
            if node.type == "function_call_expression":
                func = node.child_by_field_name("function")
                if not func:
                    func = next(
                        (c for c in node.children if c.type in ("name", "qualified_name")), None
                    )
                if func:
                    callee = _node_text(func, source).split("\\")[-1]
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
