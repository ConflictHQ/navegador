"""
Bash/Shell script parser — extracts functions, top-level variables,
source/. imports, and call edges from .sh/.bash files using tree-sitter.
"""

import logging
from pathlib import Path

from navegador.graph.schema import EdgeType, NodeLabel
from navegador.graph.store import GraphStore
from navegador.ingestion.parser import LanguageParser

logger = logging.getLogger(__name__)


def _get_bash_language():
    try:
        import tree_sitter_bash as tsbash  # type: ignore[import]
        from tree_sitter import Language

        return Language(tsbash.language())
    except ImportError as e:
        raise ImportError("Install tree-sitter-bash: pip install tree-sitter-bash") from e


def _node_text(node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


class BashParser(LanguageParser):
    """Parses Bash/Shell script files into the navegador graph."""

    def __init__(self) -> None:
        from tree_sitter import Parser  # type: ignore[import]

        self._parser = Parser(_get_bash_language())

    def parse_file(self, path: Path, repo_root: Path, store: GraphStore) -> dict[str, int]:
        source = path.read_bytes()
        tree = self._parser.parse(source)
        rel_path = str(path.relative_to(repo_root))

        store.create_node(
            NodeLabel.File,
            {
                "name": path.name,
                "path": rel_path,
                "language": "bash",
                "line_count": source.count(b"\n"),
            },
        )

        stats = {"functions": 0, "classes": 0, "edges": 0}
        self._walk(tree.root_node, source, rel_path, store, stats)
        return stats

    # ── AST walker ────────────────────────────────────────────────────────────

    def _walk(self, node, source: bytes, file_path: str, store: GraphStore, stats: dict) -> None:
        if node.type == "function_definition":
            self._handle_function(node, source, file_path, store, stats)
            return
        if node.type == "variable_assignment":
            self._handle_variable(node, source, file_path, store, stats)
            return
        if node.type == "command":
            self._handle_command(node, source, file_path, store, stats)
            return
        for child in node.children:
            self._walk(child, source, file_path, store, stats)

    # ── Handlers ──────────────────────────────────────────────────────────────

    def _handle_function(
        self,
        node,
        source: bytes,
        file_path: str,
        store: GraphStore,
        stats: dict,
    ) -> None:
        name_node = node.child_by_field_name("name")
        if not name_node:
            return
        name = _node_text(name_node, source)

        store.create_node(
            NodeLabel.Function,
            {
                "name": name,
                "file_path": file_path,
                "line_start": node.start_point[0] + 1,
                "line_end": node.end_point[0] + 1,
                "docstring": "",
                "semantic_type": "shell_function",
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

    def _handle_variable(
        self,
        node,
        source: bytes,
        file_path: str,
        store: GraphStore,
        stats: dict,
    ) -> None:
        # Only track top-level variable assignments (parent is program)
        if node.parent is None or node.parent.type not in ("program", "source_file"):
            return

        name_node = node.child_by_field_name("name")
        if not name_node:
            return
        name = _node_text(name_node, source)

        value_node = node.child_by_field_name("value")
        value = _node_text(value_node, source) if value_node else ""

        store.create_node(
            NodeLabel.Variable,
            {
                "name": name,
                "file_path": file_path,
                "line_start": node.start_point[0] + 1,
                "line_end": node.end_point[0] + 1,
                "semantic_type": "shell_variable",
                "value": value,
            },
        )

        store.create_edge(
            NodeLabel.File,
            {"path": file_path},
            EdgeType.CONTAINS,
            NodeLabel.Variable,
            {"name": name, "file_path": file_path},
        )
        stats["edges"] += 1

    def _handle_command(
        self,
        node,
        source: bytes,
        file_path: str,
        store: GraphStore,
        stats: dict,
    ) -> None:
        """Handle source/. commands as imports."""
        name_node = node.child_by_field_name("name")
        if not name_node:
            return
        cmd_name = _node_text(name_node, source)

        # Only handle source and . (dot-source) commands
        if cmd_name not in ("source", "."):
            return

        # The sourced file path is the first argument
        arg_types = ("word", "string", "raw_string", "concatenation")
        args = [child for child in node.children if child != name_node and child.type in arg_types]
        if not args:
            return
        sourced_path = _node_text(args[0], source).strip("'\"")

        store.create_node(
            NodeLabel.Import,
            {
                "name": sourced_path,
                "file_path": file_path,
                "line_start": node.start_point[0] + 1,
                "module": sourced_path,
                "semantic_type": "shell_source",
            },
        )

        store.create_edge(
            NodeLabel.File,
            {"path": file_path},
            EdgeType.IMPORTS,
            NodeLabel.Import,
            {"name": sourced_path, "file_path": file_path},
        )
        stats["edges"] += 1

    # ── Call extraction ───────────────────────────────────────────────────────

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
            if node.type == "command":
                name_node = node.child_by_field_name("name")
                if name_node:
                    callee = _node_text(name_node, source)
                    # Skip builtins and source commands — only track function calls
                    if callee not in (
                        "source",
                        ".",
                        "echo",
                        "printf",
                        "cd",
                        "exit",
                        "return",
                        "export",
                        "local",
                        "readonly",
                        "declare",
                        "typeset",
                        "unset",
                        "shift",
                        "set",
                        "eval",
                        "exec",
                        "test",
                        "[",
                        "[[",
                        "true",
                        "false",
                        ":",
                        "read",
                        "if",
                        "then",
                        "else",
                        "fi",
                        "for",
                        "while",
                        "do",
                        "done",
                        "case",
                        "esac",
                    ):
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
        if body:
            walk(body)
