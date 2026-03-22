"""
TypeScript/JavaScript AST parser — placeholder for tree-sitter-typescript.
Full implementation follows the same pattern as python.py.
"""

import logging
from pathlib import Path

from navegador.graph.schema import EdgeType, NodeLabel
from navegador.graph.store import GraphStore
from navegador.ingestion.parser import LanguageParser

logger = logging.getLogger(__name__)


def _get_ts_language(language: str):
    try:
        if language == "typescript":
            import tree_sitter_typescript as tsts  # type: ignore[import]
            from tree_sitter import Language
            return Language(tsts.language_typescript())
        else:
            import tree_sitter_javascript as tsjs  # type: ignore[import]
            from tree_sitter import Language
            return Language(tsjs.language())
    except ImportError as e:
        raise ImportError(
            f"Install tree-sitter-{language}: pip install tree-sitter-{language}"
        ) from e


class TypeScriptParser(LanguageParser):
    def __init__(self, language: str = "typescript") -> None:
        from tree_sitter import Parser  # type: ignore[import]
        self._parser = Parser(_get_ts_language(language))
        self._language = language

    def parse_file(self, path: Path, repo_root: Path, store: GraphStore) -> dict[str, int]:
        source = path.read_bytes()
        tree = self._parser.parse(source)
        rel_path = str(path.relative_to(repo_root))

        store.create_node(NodeLabel.File, {
            "name": path.name,
            "path": rel_path,
            "language": self._language,
            "line_count": source.count(b"\n"),
        })

        stats = {"functions": 0, "classes": 0, "edges": 0}
        self._walk(tree.root_node, source, rel_path, store, stats, class_name=None)
        return stats

    def _walk(self, node, source: bytes, file_path: str, store: GraphStore,
              stats: dict, class_name: str | None) -> None:
        if node.type in ("class_declaration", "abstract_class_declaration"):
            self._handle_class(node, source, file_path, store, stats)
            return
        if node.type in (
            "function_declaration", "arrow_function", "method_definition",
            "function_expression",
        ):
            self._handle_function(node, source, file_path, store, stats, class_name)
            return
        if node.type in ("import_statement", "import_declaration"):
            self._handle_import(node, source, file_path, store, stats)

        for child in node.children:
            self._walk(child, source, file_path, store, stats, class_name)

    def _handle_import(self, node, source: bytes, file_path: str,
                       store: GraphStore, stats: dict) -> None:
        # Extract "from '...'" module path
        for child in node.children:
            if child.type == "string":
                module = source[child.start_byte:child.end_byte].decode().strip("'\"")
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
                break

    def _handle_class(self, node, source: bytes, file_path: str,
                      store: GraphStore, stats: dict) -> None:
        name_node = next((c for c in node.children if c.type == "type_identifier"), None)
        if not name_node:
            return
        name = source[name_node.start_byte:name_node.end_byte].decode()

        store.create_node(NodeLabel.Class, {
            "name": name,
            "file_path": file_path,
            "line_start": node.start_point[0] + 1,
            "line_end": node.end_point[0] + 1,
            "docstring": "",
        })
        store.create_edge(
            NodeLabel.File, {"path": file_path},
            EdgeType.CONTAINS,
            NodeLabel.Class, {"name": name, "file_path": file_path},
        )
        stats["classes"] += 1
        stats["edges"] += 1

        body = next((c for c in node.children if c.type == "class_body"), None)
        if body:
            for child in body.children:
                if child.type == "method_definition":
                    self._handle_function(child, source, file_path, store, stats, class_name=name)

    def _handle_function(self, node, source: bytes, file_path: str,
                         store: GraphStore, stats: dict, class_name: str | None) -> None:
        name_node = next(
            (c for c in node.children if c.type in ("identifier", "property_identifier")), None
        )
        if not name_node:
            return
        name = source[name_node.start_byte:name_node.end_byte].decode()

        label = NodeLabel.Method if class_name else NodeLabel.Function
        store.create_node(label, {
            "name": name,
            "file_path": file_path,
            "line_start": node.start_point[0] + 1,
            "line_end": node.end_point[0] + 1,
            "docstring": "",
            "class_name": class_name or "",
        })

        container_label = NodeLabel.Class if class_name else NodeLabel.File
        container_key = (
            {"name": class_name, "file_path": file_path}
            if class_name
            else {"path": file_path}
        )
        store.create_edge(
            container_label, container_key, EdgeType.CONTAINS, label,
            {"name": name, "file_path": file_path},
        )
        stats["functions"] += 1
        stats["edges"] += 1
