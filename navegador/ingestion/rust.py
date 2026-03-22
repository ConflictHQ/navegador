"""
Rust AST parser — extracts functions, methods (from impl blocks),
structs/enums/traits, use declarations, and call edges from .rs files.
"""

import logging
from pathlib import Path

from navegador.graph.schema import EdgeType, NodeLabel
from navegador.graph.store import GraphStore
from navegador.ingestion.parser import LanguageParser

logger = logging.getLogger(__name__)


def _get_rust_language():
    try:
        import tree_sitter_rust as tsrust  # type: ignore[import]
        from tree_sitter import Language
        return Language(tsrust.language())
    except ImportError as e:
        raise ImportError("Install tree-sitter-rust: pip install tree-sitter-rust") from e


def _node_text(node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _doc_comment(node, source: bytes) -> str:
    """Collect preceding /// doc-comment lines from siblings."""
    parent = node.parent
    if not parent:
        return ""
    siblings = list(parent.children)
    idx = next((i for i, c in enumerate(siblings) if c.id == node.id), -1)
    if idx <= 0:
        return ""
    lines = []
    for i in range(idx - 1, -1, -1):
        sib = siblings[i]
        raw = _node_text(sib, source)
        if sib.type == "line_comment" and raw.startswith("///"):
            lines.insert(0, raw.lstrip("/").strip())
        else:
            break
    return " ".join(lines)


class RustParser(LanguageParser):
    """Parses Rust source files into the navegador graph."""

    def __init__(self) -> None:
        from tree_sitter import Parser  # type: ignore[import]
        self._parser = Parser(_get_rust_language())

    def parse_file(self, path: Path, repo_root: Path, store: GraphStore) -> dict[str, int]:
        source = path.read_bytes()
        tree = self._parser.parse(source)
        rel_path = str(path.relative_to(repo_root))

        store.create_node(NodeLabel.File, {
            "name": path.name,
            "path": rel_path,
            "language": "rust",
            "line_count": source.count(b"\n"),
        })

        stats = {"functions": 0, "classes": 0, "edges": 0}
        self._walk(tree.root_node, source, rel_path, store, stats, impl_type=None)
        return stats

    # ── AST walker ────────────────────────────────────────────────────────────

    def _walk(self, node, source: bytes, file_path: str,
              store: GraphStore, stats: dict, impl_type: str | None) -> None:
        if node.type == "function_item":
            self._handle_function(node, source, file_path, store, stats, impl_type)
            return
        if node.type == "impl_item":
            self._handle_impl(node, source, file_path, store, stats)
            return
        if node.type in ("struct_item", "enum_item", "trait_item"):
            self._handle_type(node, source, file_path, store, stats)
            return
        if node.type == "use_declaration":
            self._handle_use(node, source, file_path, store, stats)
            return
        for child in node.children:
            self._walk(child, source, file_path, store, stats, impl_type)

    # ── Handlers ──────────────────────────────────────────────────────────────

    def _handle_function(self, node, source: bytes, file_path: str,
                         store: GraphStore, stats: dict,
                         impl_type: str | None) -> None:
        name_node = node.child_by_field_name("name")
        if not name_node:
            return
        name = _node_text(name_node, source)
        docstring = _doc_comment(node, source)
        label = NodeLabel.Method if impl_type else NodeLabel.Function

        store.create_node(label, {
            "name": name,
            "file_path": file_path,
            "line_start": node.start_point[0] + 1,
            "line_end": node.end_point[0] + 1,
            "docstring": docstring,
            "class_name": impl_type or "",
        })

        container_label = NodeLabel.Class if impl_type else NodeLabel.File
        container_key = (
            {"name": impl_type, "file_path": file_path}
            if impl_type else {"path": file_path}
        )
        store.create_edge(
            container_label, container_key,
            EdgeType.CONTAINS,
            label, {"name": name, "file_path": file_path},
        )
        stats["functions"] += 1
        stats["edges"] += 1

        self._extract_calls(node, source, file_path, name, label, store, stats)

    def _handle_impl(self, node, source: bytes, file_path: str,
                     store: GraphStore, stats: dict) -> None:
        type_node = node.child_by_field_name("type")
        impl_type_name = _node_text(type_node, source) if type_node else ""

        body = node.child_by_field_name("body")
        if body:
            for child in body.children:
                if child.type == "function_item":
                    self._handle_function(child, source, file_path, store, stats,
                                          impl_type=impl_type_name or None)

    def _handle_type(self, node, source: bytes, file_path: str,
                     store: GraphStore, stats: dict) -> None:
        name_node = node.child_by_field_name("name")
        if not name_node:
            return
        name = _node_text(name_node, source)
        kind = {"struct_item": "struct", "enum_item": "enum",
                "trait_item": "trait"}.get(node.type, "")
        docstring = _doc_comment(node, source)

        store.create_node(NodeLabel.Class, {
            "name": name,
            "file_path": file_path,
            "line_start": node.start_point[0] + 1,
            "line_end": node.end_point[0] + 1,
            "docstring": f"{kind}: {docstring}".strip(": ") if kind else docstring,
        })
        store.create_edge(
            NodeLabel.File, {"path": file_path},
            EdgeType.CONTAINS,
            NodeLabel.Class, {"name": name, "file_path": file_path},
        )
        stats["classes"] += 1
        stats["edges"] += 1

    def _handle_use(self, node, source: bytes, file_path: str,
                    store: GraphStore, stats: dict) -> None:
        raw = _node_text(node, source)
        module = raw.removeprefix("use ").removesuffix(";").strip()
        store.create_node(NodeLabel.Import, {
            "name": module,
            "file_path": file_path,
            "line_start": node.start_point[0] + 1,
            "module": module,
        })
        store.create_edge(
            NodeLabel.File, {"path": file_path},
            EdgeType.IMPORTS,
            NodeLabel.Import, {"name": module, "file_path": file_path},
        )
        stats["edges"] += 1

    def _extract_calls(self, fn_node, source: bytes, file_path: str,
                       fn_name: str, fn_label: str,
                       store: GraphStore, stats: dict) -> None:
        def walk(node):
            if node.type == "call_expression":
                func = node.child_by_field_name("function")
                if func:
                    text = _node_text(func, source)
                    # Handle Foo::bar() and obj.method()
                    callee = text.replace("::", ".").split(".")[-1]
                    store.create_edge(
                        fn_label, {"name": fn_name, "file_path": file_path},
                        EdgeType.CALLS,
                        NodeLabel.Function, {"name": callee, "file_path": file_path},
                    )
                    stats["edges"] += 1
            for child in node.children:
                walk(child)

        body = fn_node.child_by_field_name("body")
        if body:
            walk(body)
