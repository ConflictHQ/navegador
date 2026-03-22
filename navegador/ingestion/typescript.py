"""
TypeScript/JavaScript AST parser — extracts classes, interfaces, functions
(including const arrow functions), methods, imports, and call edges.

Supports:
  - .ts / .tsx  →  tree-sitter-typescript (TypeScript grammar)
  - .js / .jsx  →  tree-sitter-javascript (JavaScript grammar)
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


def _node_text(node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _jsdoc(node, source: bytes) -> str:
    """Return the JSDoc comment (/** ... */) preceding a node, if any."""
    parent = node.parent
    if not parent:
        return ""
    siblings = list(parent.children)
    idx = next((i for i, c in enumerate(siblings) if c.id == node.id), -1)
    if idx <= 0:
        return ""
    prev = siblings[idx - 1]
    if prev.type == "comment":
        raw = _node_text(prev, source).strip()
        if raw.startswith("/**"):
            return raw.strip("/**").strip("*/").strip()
    return ""


class TypeScriptParser(LanguageParser):
    """Parses TypeScript/JavaScript source files into the navegador graph."""

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

    # ── AST walker ────────────────────────────────────────────────────────────

    def _walk(self, node, source: bytes, file_path: str,
              store: GraphStore, stats: dict, class_name: str | None) -> None:
        if node.type in ("class_declaration", "abstract_class_declaration"):
            self._handle_class(node, source, file_path, store, stats)
            return
        if node.type in ("interface_declaration", "type_alias_declaration"):
            self._handle_interface(node, source, file_path, store, stats)
            return
        if node.type == "function_declaration":
            self._handle_function(node, source, file_path, store, stats, class_name)
            return
        if node.type == "method_definition":
            self._handle_function(node, source, file_path, store, stats, class_name)
            return
        if node.type in ("lexical_declaration", "variable_declaration"):
            self._handle_lexical(node, source, file_path, store, stats)
            return
        if node.type in ("import_statement", "import_declaration"):
            self._handle_import(node, source, file_path, store, stats)
            return
        if node.type == "export_statement":
            # Recurse into exported declarations
            for child in node.children:
                if child.type not in ("export", "default", "from"):
                    self._walk(child, source, file_path, store, stats, class_name)
            return

        for child in node.children:
            self._walk(child, source, file_path, store, stats, class_name)

    # ── Handlers ──────────────────────────────────────────────────────────────

    def _handle_class(self, node, source: bytes, file_path: str,
                      store: GraphStore, stats: dict) -> None:
        name_node = next(
            (c for c in node.children if c.type == "type_identifier"), None
        )
        if not name_node:
            return
        name = _node_text(name_node, source)
        docstring = _jsdoc(node, source)

        store.create_node(NodeLabel.Class, {
            "name": name,
            "file_path": file_path,
            "line_start": node.start_point[0] + 1,
            "line_end": node.end_point[0] + 1,
            "docstring": docstring,
        })
        store.create_edge(
            NodeLabel.File, {"path": file_path},
            EdgeType.CONTAINS,
            NodeLabel.Class, {"name": name, "file_path": file_path},
        )
        stats["classes"] += 1
        stats["edges"] += 1

        # Inheritance: extends clause
        heritage = next((c for c in node.children if c.type == "class_heritage"), None)
        if heritage:
            for child in heritage.children:
                if child.type == "extends_clause":
                    for c in child.children:
                        if c.type == "identifier":
                            parent_name = _node_text(c, source)
                            store.create_edge(
                                NodeLabel.Class, {"name": name, "file_path": file_path},
                                EdgeType.INHERITS,
                                NodeLabel.Class, {"name": parent_name, "file_path": file_path},
                            )
                            stats["edges"] += 1

        body = next((c for c in node.children if c.type == "class_body"), None)
        if body:
            for child in body.children:
                if child.type == "method_definition":
                    self._handle_function(child, source, file_path, store, stats,
                                          class_name=name)

    def _handle_interface(self, node, source: bytes, file_path: str,
                          store: GraphStore, stats: dict) -> None:
        name_node = next(
            (c for c in node.children if c.type == "type_identifier"), None
        )
        if not name_node:
            return
        name = _node_text(name_node, source)
        docstring = _jsdoc(node, source)
        kind = "interface" if node.type == "interface_declaration" else "type"

        store.create_node(NodeLabel.Class, {
            "name": name,
            "file_path": file_path,
            "line_start": node.start_point[0] + 1,
            "line_end": node.end_point[0] + 1,
            "docstring": f"{kind}: {docstring}".strip(": "),
        })
        store.create_edge(
            NodeLabel.File, {"path": file_path},
            EdgeType.CONTAINS,
            NodeLabel.Class, {"name": name, "file_path": file_path},
        )
        stats["classes"] += 1
        stats["edges"] += 1

    def _handle_function(self, node, source: bytes, file_path: str,
                         store: GraphStore, stats: dict,
                         class_name: str | None) -> None:
        name_node = next(
            (c for c in node.children
             if c.type in ("identifier", "property_identifier")), None
        )
        if not name_node:
            return
        name = _node_text(name_node, source)
        if name in ("constructor", "get", "set", "static", "async"):
            # These are keywords, not useful names — look for next identifier
            name_node = next(
                (c for c in node.children
                 if c.type in ("identifier", "property_identifier") and c != name_node),
                None,
            )
            if not name_node:
                return
            name = _node_text(name_node, source)

        docstring = _jsdoc(node, source)
        label = NodeLabel.Method if class_name else NodeLabel.Function

        store.create_node(label, {
            "name": name,
            "file_path": file_path,
            "line_start": node.start_point[0] + 1,
            "line_end": node.end_point[0] + 1,
            "docstring": docstring,
            "class_name": class_name or "",
        })

        container_label = NodeLabel.Class if class_name else NodeLabel.File
        container_key = (
            {"name": class_name, "file_path": file_path}
            if class_name else {"path": file_path}
        )
        store.create_edge(
            container_label, container_key,
            EdgeType.CONTAINS,
            label, {"name": name, "file_path": file_path},
        )
        stats["functions"] += 1
        stats["edges"] += 1

        self._extract_calls(node, source, file_path, name, label, store, stats)

    def _handle_lexical(self, node, source: bytes, file_path: str,
                        store: GraphStore, stats: dict) -> None:
        """Handle: const foo = () => {} and const bar = function() {}"""
        for child in node.children:
            if child.type != "variable_declarator":
                continue
            name_node = child.child_by_field_name("name")
            value_node = child.child_by_field_name("value")
            if not name_node or not value_node:
                continue
            if value_node.type not in ("arrow_function", "function_expression",
                                       "function"):
                continue
            name = _node_text(name_node, source)
            docstring = _jsdoc(node, source)

            store.create_node(NodeLabel.Function, {
                "name": name,
                "file_path": file_path,
                "line_start": node.start_point[0] + 1,
                "line_end": node.end_point[0] + 1,
                "docstring": docstring,
                "class_name": "",
            })
            store.create_edge(
                NodeLabel.File, {"path": file_path},
                EdgeType.CONTAINS,
                NodeLabel.Function, {"name": name, "file_path": file_path},
            )
            stats["functions"] += 1
            stats["edges"] += 1

            self._extract_calls(value_node, source, file_path, name,
                                 NodeLabel.Function, store, stats)

    def _handle_import(self, node, source: bytes, file_path: str,
                       store: GraphStore, stats: dict) -> None:
        for child in node.children:
            if child.type == "string":
                module = _node_text(child, source).strip("'\"")
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

    def _extract_calls(self, fn_node, source: bytes, file_path: str,
                       fn_name: str, fn_label: str,
                       store: GraphStore, stats: dict) -> None:
        def walk(node):
            if node.type == "call_expression":
                func = node.child_by_field_name("function")
                if func:
                    text = _node_text(func, source)
                    callee = text.split(".")[-1]
                    store.create_edge(
                        fn_label, {"name": fn_name, "file_path": file_path},
                        EdgeType.CALLS,
                        NodeLabel.Function, {"name": callee, "file_path": file_path},
                    )
                    stats["edges"] += 1
            for child in node.children:
                walk(child)

        # Body is statement_block for functions, expression for arrow fns
        body = next(
            (c for c in fn_node.children
             if c.type in ("statement_block", "expression_statement")),
            None,
        )
        if body:
            walk(body)
