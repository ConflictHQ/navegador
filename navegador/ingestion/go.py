"""
Go AST parser — extracts functions, methods, struct/interface types,
imports, and call edges from .go files using tree-sitter.
"""

import logging
from pathlib import Path

from navegador.graph.schema import EdgeType, NodeLabel
from navegador.graph.store import GraphStore
from navegador.ingestion.parser import LanguageParser

logger = logging.getLogger(__name__)


def _get_go_language():
    try:
        import tree_sitter_go as tsgo  # type: ignore[import]
        from tree_sitter import Language
        return Language(tsgo.language())
    except ImportError as e:
        raise ImportError("Install tree-sitter-go: pip install tree-sitter-go") from e


def _node_text(node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


class GoParser(LanguageParser):
    """Parses Go source files into the navegador graph."""

    def __init__(self) -> None:
        from tree_sitter import Parser  # type: ignore[import]
        self._parser = Parser(_get_go_language())

    def parse_file(self, path: Path, repo_root: Path, store: GraphStore) -> dict[str, int]:
        source = path.read_bytes()
        tree = self._parser.parse(source)
        rel_path = str(path.relative_to(repo_root))

        store.create_node(NodeLabel.File, {
            "name": path.name,
            "path": rel_path,
            "language": "go",
            "line_count": source.count(b"\n"),
        })

        stats = {"functions": 0, "classes": 0, "edges": 0}
        self._walk(tree.root_node, source, rel_path, store, stats)
        return stats

    # ── AST walker ────────────────────────────────────────────────────────────

    def _walk(self, node, source: bytes, file_path: str,
              store: GraphStore, stats: dict) -> None:
        if node.type == "function_declaration":
            self._handle_function(node, source, file_path, store, stats, receiver=None)
            return
        if node.type == "method_declaration":
            self._handle_method(node, source, file_path, store, stats)
            return
        if node.type == "type_declaration":
            self._handle_type(node, source, file_path, store, stats)
            return
        if node.type == "import_declaration":
            self._handle_import(node, source, file_path, store, stats)
            return
        for child in node.children:
            self._walk(child, source, file_path, store, stats)

    # ── Handlers ──────────────────────────────────────────────────────────────

    def _handle_function(self, node, source: bytes, file_path: str,
                         store: GraphStore, stats: dict,
                         receiver: str | None) -> None:
        name_node = node.child_by_field_name("name")
        if not name_node:
            return
        name = _node_text(name_node, source)
        label = NodeLabel.Method if receiver else NodeLabel.Function

        store.create_node(label, {
            "name": name,
            "file_path": file_path,
            "line_start": node.start_point[0] + 1,
            "line_end": node.end_point[0] + 1,
            "docstring": "",
            "class_name": receiver or "",
        })

        container_label = NodeLabel.Class if receiver else NodeLabel.File
        container_key = (
            {"name": receiver, "file_path": file_path}
            if receiver else {"path": file_path}
        )
        store.create_edge(
            container_label, container_key,
            EdgeType.CONTAINS,
            label, {"name": name, "file_path": file_path},
        )
        stats["functions"] += 1
        stats["edges"] += 1

        self._extract_calls(node, source, file_path, name, label, store, stats)

    def _handle_method(self, node, source: bytes, file_path: str,
                       store: GraphStore, stats: dict) -> None:
        receiver_type = ""
        recv_node = node.child_by_field_name("receiver")
        if recv_node:
            for child in recv_node.children:
                if child.type == "parameter_declaration":
                    for c in child.children:
                        if c.type in ("type_identifier", "pointer_type"):
                            receiver_type = _node_text(c, source).lstrip("*").strip()
                            break
        self._handle_function(node, source, file_path, store, stats,
                              receiver=receiver_type or None)

    def _handle_type(self, node, source: bytes, file_path: str,
                     store: GraphStore, stats: dict) -> None:
        for child in node.children:
            if child.type != "type_spec":
                continue
            name_node = child.child_by_field_name("name")
            type_node = child.child_by_field_name("type")
            if not name_node or not type_node:
                continue
            if type_node.type not in ("struct_type", "interface_type"):
                continue
            name = _node_text(name_node, source)
            kind = "struct" if type_node.type == "struct_type" else "interface"
            store.create_node(NodeLabel.Class, {
                "name": name,
                "file_path": file_path,
                "line_start": node.start_point[0] + 1,
                "line_end": node.end_point[0] + 1,
                "docstring": kind,
            })
            store.create_edge(
                NodeLabel.File, {"path": file_path},
                EdgeType.CONTAINS,
                NodeLabel.Class, {"name": name, "file_path": file_path},
            )
            stats["classes"] += 1
            stats["edges"] += 1

    def _handle_import(self, node, source: bytes, file_path: str,
                       store: GraphStore, stats: dict) -> None:
        line_start = node.start_point[0] + 1
        for child in node.children:
            if child.type == "import_spec":
                self._ingest_import_spec(child, source, file_path, line_start, store, stats)
            elif child.type == "import_spec_list":
                for spec in child.children:
                    if spec.type == "import_spec":
                        self._ingest_import_spec(spec, source, file_path, line_start,
                                                 store, stats)

    def _ingest_import_spec(self, spec, source: bytes, file_path: str,
                            line_start: int, store: GraphStore, stats: dict) -> None:
        path_node = spec.child_by_field_name("path")
        if not path_node:
            return
        module = _node_text(path_node, source).strip('"')
        store.create_node(NodeLabel.Import, {
            "name": module,
            "file_path": file_path,
            "line_start": line_start,
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
                    callee = _node_text(func, source).split(".")[-1]
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
