"""
Java AST parser — extracts classes, interfaces, methods, constructors,
imports, inheritance, and call edges from .java files using tree-sitter.
"""

import logging
from pathlib import Path

from navegador.graph.schema import EdgeType, NodeLabel
from navegador.graph.store import GraphStore
from navegador.ingestion.parser import LanguageParser

logger = logging.getLogger(__name__)


def _get_java_language():
    try:
        import tree_sitter_java as tsjava  # type: ignore[import]
        from tree_sitter import Language

        return Language(tsjava.language())
    except ImportError as e:
        raise ImportError("Install tree-sitter-java: pip install tree-sitter-java") from e


def _node_text(node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _javadoc(node, source: bytes) -> str:
    """Return the Javadoc (/** ... */) comment preceding a node, if any."""
    parent = node.parent
    if not parent:
        return ""
    siblings = list(parent.children)
    idx = next((i for i, c in enumerate(siblings) if c.id == node.id), -1)
    if idx <= 0:
        return ""
    prev = siblings[idx - 1]
    if prev.type == "block_comment":
        raw = _node_text(prev, source).strip()
        if raw.startswith("/**"):
            return raw.strip("/**").strip("*/").strip()
    return ""


class JavaParser(LanguageParser):
    """Parses Java source files into the navegador graph."""

    def __init__(self) -> None:
        from tree_sitter import Parser  # type: ignore[import]

        self._parser = Parser(_get_java_language())

    def parse_file(self, path: Path, repo_root: Path, store: GraphStore) -> dict[str, int]:
        source = path.read_bytes()
        tree = self._parser.parse(source)
        rel_path = str(path.relative_to(repo_root))

        store.create_node(
            NodeLabel.File,
            {
                "name": path.name,
                "path": rel_path,
                "language": "java",
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
        if node.type in ("class_declaration", "record_declaration"):
            self._handle_class(node, source, file_path, store, stats)
            return
        if node.type == "interface_declaration":
            self._handle_interface(node, source, file_path, store, stats)
            return
        if node.type == "import_declaration":
            self._handle_import(node, source, file_path, store, stats)
            return
        for child in node.children:
            self._walk(child, source, file_path, store, stats, class_name)

    # ── Handlers ──────────────────────────────────────────────────────────────

    def _handle_class(
        self, node, source: bytes, file_path: str, store: GraphStore, stats: dict
    ) -> None:
        name_node = node.child_by_field_name("name")
        if not name_node:
            return
        name = _node_text(name_node, source)
        docstring = _javadoc(node, source)

        store.create_node(
            NodeLabel.Class,
            {
                "name": name,
                "file_path": file_path,
                "line_start": node.start_point[0] + 1,
                "line_end": node.end_point[0] + 1,
                "docstring": docstring,
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

        # Superclass → INHERITS edge
        superclass = node.child_by_field_name("superclass")
        if superclass:
            for child in superclass.children:
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
                    break

        # Walk class body for methods and constructors
        body = node.child_by_field_name("body")
        if body:
            for child in body.children:
                if child.type in ("method_declaration", "constructor_declaration"):
                    self._handle_method(child, source, file_path, store, stats, class_name=name)
                elif child.type in (
                    "class_declaration",
                    "record_declaration",
                    "interface_declaration",
                ):
                    # Nested class — register but don't recurse into methods
                    inner_name_node = child.child_by_field_name("name")
                    if inner_name_node:
                        inner_name = _node_text(inner_name_node, source)
                        store.create_node(
                            NodeLabel.Class,
                            {
                                "name": inner_name,
                                "file_path": file_path,
                                "line_start": child.start_point[0] + 1,
                                "line_end": child.end_point[0] + 1,
                                "docstring": "",
                            },
                        )
                        store.create_edge(
                            NodeLabel.Class,
                            {"name": name, "file_path": file_path},
                            EdgeType.CONTAINS,
                            NodeLabel.Class,
                            {"name": inner_name, "file_path": file_path},
                        )
                        stats["classes"] += 1
                        stats["edges"] += 1

    def _handle_interface(
        self, node, source: bytes, file_path: str, store: GraphStore, stats: dict
    ) -> None:
        name_node = node.child_by_field_name("name")
        if not name_node:
            return
        name = _node_text(name_node, source)
        docstring = _javadoc(node, source)

        store.create_node(
            NodeLabel.Class,
            {
                "name": name,
                "file_path": file_path,
                "line_start": node.start_point[0] + 1,
                "line_end": node.end_point[0] + 1,
                "docstring": f"interface: {docstring}".strip(": "),
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

        # Walk interface body for method signatures
        body = node.child_by_field_name("body")
        if body:
            for child in body.children:
                if child.type == "method_declaration":
                    self._handle_method(child, source, file_path, store, stats, class_name=name)

    def _handle_method(
        self, node, source: bytes, file_path: str, store: GraphStore, stats: dict, class_name: str
    ) -> None:
        name_node = node.child_by_field_name("name")
        if not name_node:
            return
        name = _node_text(name_node, source)
        docstring = _javadoc(node, source)

        store.create_node(
            NodeLabel.Method,
            {
                "name": name,
                "file_path": file_path,
                "line_start": node.start_point[0] + 1,
                "line_end": node.end_point[0] + 1,
                "docstring": docstring,
                "class_name": class_name,
            },
        )
        store.create_edge(
            NodeLabel.Class,
            {"name": class_name, "file_path": file_path},
            EdgeType.CONTAINS,
            NodeLabel.Method,
            {"name": name, "file_path": file_path},
        )
        stats["functions"] += 1
        stats["edges"] += 1

        self._extract_calls(node, source, file_path, name, store, stats)

    def _handle_import(
        self, node, source: bytes, file_path: str, store: GraphStore, stats: dict
    ) -> None:
        # import java.util.List; → strip keyword + semicolon
        raw = _node_text(node, source)
        module = raw.removeprefix("import").removeprefix(" static").removesuffix(";").strip()
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
        method_node,
        source: bytes,
        file_path: str,
        method_name: str,
        store: GraphStore,
        stats: dict,
    ) -> None:
        def walk(node):
            if node.type == "method_invocation":
                name_node = node.child_by_field_name("name")
                if name_node:
                    callee = _node_text(name_node, source)
                    store.create_edge(
                        NodeLabel.Method,
                        {"name": method_name, "file_path": file_path},
                        EdgeType.CALLS,
                        NodeLabel.Function,
                        {"name": callee, "file_path": file_path},
                    )
                    stats["edges"] += 1
            for child in node.children:
                walk(child)

        body = method_node.child_by_field_name("body")
        if body:
            walk(body)
