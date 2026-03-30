"""
Puppet manifest parser — extracts classes, defined types, node definitions,
resource declarations, includes, and parameters from .pp files using tree-sitter.
"""

import logging
from pathlib import Path

from navegador.graph.schema import EdgeType, NodeLabel
from navegador.graph.store import GraphStore
from navegador.ingestion.parser import LanguageParser

logger = logging.getLogger(__name__)


def _get_puppet_language():
    try:
        import tree_sitter_puppet as tspuppet  # type: ignore[import]
        from tree_sitter import Language

        return Language(tspuppet.language())
    except ImportError as e:
        raise ImportError("Install tree-sitter-puppet: pip install tree-sitter-puppet") from e


def _node_text(node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _class_identifier_text(node, source: bytes) -> str:
    """Join identifier children of a class_identifier with '::'."""
    parts = [_node_text(child, source) for child in node.children if child.type == "identifier"]
    return "::".join(parts) if parts else _node_text(node, source)


class PuppetParser(LanguageParser):
    """Parses Puppet manifest files into the navegador graph."""

    def __init__(self) -> None:
        from tree_sitter import Parser  # type: ignore[import]

        self._parser = Parser(_get_puppet_language())

    def parse_file(self, path: Path, repo_root: Path, store: GraphStore) -> dict[str, int]:
        source = path.read_bytes()
        tree = self._parser.parse(source)
        rel_path = str(path.relative_to(repo_root))

        store.create_node(
            NodeLabel.File,
            {
                "name": path.name,
                "path": rel_path,
                "language": "puppet",
                "line_count": source.count(b"\n"),
            },
        )

        stats = {"functions": 0, "classes": 0, "edges": 0}
        self._walk(tree.root_node, source, rel_path, store, stats)
        return stats

    # ── AST walker ────────────────────────────────────────────────────────────

    def _walk(self, node, source: bytes, file_path: str, store: GraphStore, stats: dict) -> None:
        if node.type == "class_definition":
            self._handle_class(node, source, file_path, store, stats)
            return
        if node.type == "defined_resource_type":
            self._handle_defined_type(node, source, file_path, store, stats)
            return
        if node.type == "node_definition":
            self._handle_node(node, source, file_path, store, stats)
            return
        if node.type == "include_statement":
            self._handle_include(node, source, file_path, store, stats)
            return
        for child in node.children:
            self._walk(child, source, file_path, store, stats)

    # ── Handlers ──────────────────────────────────────────────────────────────

    def _handle_class(
        self, node, source: bytes, file_path: str, store: GraphStore, stats: dict
    ) -> None:
        name = self._extract_class_identifier(node, source)
        if not name:
            return

        store.create_node(
            NodeLabel.Class,
            {
                "name": name,
                "file_path": file_path,
                "line_start": node.start_point[0] + 1,
                "line_end": node.end_point[0] + 1,
                "docstring": "",
                "semantic_type": "puppet_class",
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

        self._extract_parameters(node, source, file_path, name, store, stats)
        self._extract_resources(node, source, file_path, name, store, stats)

    def _handle_defined_type(
        self, node, source: bytes, file_path: str, store: GraphStore, stats: dict
    ) -> None:
        name = self._extract_class_identifier(node, source)
        if not name:
            return

        store.create_node(
            NodeLabel.Class,
            {
                "name": name,
                "file_path": file_path,
                "line_start": node.start_point[0] + 1,
                "line_end": node.end_point[0] + 1,
                "docstring": "",
                "semantic_type": "puppet_defined_type",
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

        self._extract_parameters(node, source, file_path, name, store, stats)
        self._extract_resources(node, source, file_path, name, store, stats)

    def _handle_node(
        self, node, source: bytes, file_path: str, store: GraphStore, stats: dict
    ) -> None:
        name = self._extract_node_name(node, source)
        if not name:
            return

        store.create_node(
            NodeLabel.Class,
            {
                "name": name,
                "file_path": file_path,
                "line_start": node.start_point[0] + 1,
                "line_end": node.end_point[0] + 1,
                "docstring": "",
                "semantic_type": "puppet_node",
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

        self._extract_resources(node, source, file_path, name, store, stats)

    def _handle_include(
        self, node, source: bytes, file_path: str, store: GraphStore, stats: dict
    ) -> None:
        ident_node = None
        for child in node.children:
            if child.type == "class_identifier":
                ident_node = child
                break
        if not ident_node:
            return

        module = _class_identifier_text(ident_node, source)
        store.create_node(
            NodeLabel.Import,
            {
                "name": module,
                "file_path": file_path,
                "line_start": node.start_point[0] + 1,
                "module": module,
                "semantic_type": "puppet_include",
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

    # ── Extractors ────────────────────────────────────────────────────────────

    def _extract_class_identifier(self, node, source: bytes) -> str | None:
        """Find and return the class_identifier text from a class/define node."""
        for child in node.children:
            if child.type == "class_identifier":
                return _class_identifier_text(child, source)
        return None

    def _extract_node_name(self, node, source: bytes) -> str | None:
        """Extract the node name from a node_definition (string child of node_name)."""
        for child in node.children:
            if child.type == "node_name":
                for grandchild in child.children:
                    if grandchild.type == "string":
                        return _node_text(grandchild, source).strip("'\"")
                return _node_text(child, source).strip("'\"")
        return None

    def _extract_parameters(
        self,
        node,
        source: bytes,
        file_path: str,
        class_name: str,
        store: GraphStore,
        stats: dict,
    ) -> None:
        """Extract parameters from a parameter_list inside a class/define."""
        for child in node.children:
            if child.type != "parameter_list":
                continue
            for param in child.children:
                if param.type != "parameter":
                    continue
                var_node = None
                for pc in param.children:
                    if pc.type == "variable":
                        var_node = pc
                        break
                if not var_node:
                    continue
                var_name = _node_text(var_node, source).lstrip("$")
                store.create_node(
                    NodeLabel.Variable,
                    {
                        "name": var_name,
                        "file_path": file_path,
                        "line_start": param.start_point[0] + 1,
                        "semantic_type": "puppet_parameter",
                    },
                )
                store.create_edge(
                    NodeLabel.Class,
                    {"name": class_name, "file_path": file_path},
                    EdgeType.CONTAINS,
                    NodeLabel.Variable,
                    {"name": var_name, "file_path": file_path},
                )
                stats["edges"] += 1

    def _extract_resources(
        self,
        node,
        source: bytes,
        file_path: str,
        class_name: str,
        store: GraphStore,
        stats: dict,
    ) -> None:
        """Walk the block of a class/define/node to find resource declarations."""
        for child in node.children:
            if child.type == "block":
                self._walk_block_for_resources(child, source, file_path, class_name, store, stats)
                break

    def _walk_block_for_resources(
        self,
        node,
        source: bytes,
        file_path: str,
        class_name: str,
        store: GraphStore,
        stats: dict,
    ) -> None:
        """Recursively find resource_declaration nodes inside a block."""
        if node.type == "resource_declaration":
            self._handle_resource(node, source, file_path, class_name, store, stats)
            return
        for child in node.children:
            self._walk_block_for_resources(child, source, file_path, class_name, store, stats)

    def _handle_resource(
        self,
        node,
        source: bytes,
        file_path: str,
        class_name: str,
        store: GraphStore,
        stats: dict,
    ) -> None:
        """Handle a resource_declaration: first identifier = type, first string = title."""
        res_type = None
        res_title = None
        for child in node.children:
            if child.type == "identifier" and res_type is None:
                res_type = _node_text(child, source)
            if child.type == "string" and res_title is None:
                res_title = _node_text(child, source).strip("'\"")
        if not res_type:
            return

        name = f"{res_type}[{res_title}]" if res_title else res_type
        store.create_node(
            NodeLabel.Function,
            {
                "name": name,
                "file_path": file_path,
                "line_start": node.start_point[0] + 1,
                "line_end": node.end_point[0] + 1,
                "docstring": "",
                "class_name": class_name,
                "semantic_type": "puppet_resource",
            },
        )
        store.create_edge(
            NodeLabel.Class,
            {"name": class_name, "file_path": file_path},
            EdgeType.CONTAINS,
            NodeLabel.Function,
            {"name": name, "file_path": file_path},
        )
        stats["functions"] += 1
        stats["edges"] += 1
