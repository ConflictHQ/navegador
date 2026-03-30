"""
HCL/Terraform parser — extracts resources, data sources, providers,
variables, outputs, modules, and locals from .tf files using tree-sitter.
"""

import logging
import re
from pathlib import Path

from navegador.graph.schema import EdgeType, NodeLabel
from navegador.graph.store import GraphStore
from navegador.ingestion.parser import LanguageParser

logger = logging.getLogger(__name__)

# Patterns for reference extraction from expression text
_VAR_REF = re.compile(r"\bvar\.(\w+)")
_LOCAL_REF = re.compile(r"\blocal\.(\w+)")
_MODULE_REF = re.compile(r"\bmodule\.(\w+)")
_DATA_REF = re.compile(r"\bdata\.(\w+)\.(\w+)")
_RESOURCE_REF = re.compile(
    r"(?<!\bdata\.)"  # exclude data.resource_type references (handled by _DATA_REF)
    r"\b(aws_\w+|google_\w+|azurerm_\w+|azuread_\w+|oci_\w+|digitalocean_\w+"
    r"|cloudflare_\w+|helm_\w+|kubernetes_\w+|null_\w+|random_\w+"
    r"|local_\w+|tls_\w+|template_\w+|archive_\w+|external_\w+)\.(\w+)"
)


def _get_hcl_language():
    try:
        import tree_sitter_hcl as tshcl  # type: ignore[import]
        from tree_sitter import Language

        return Language(tshcl.language())
    except ImportError as e:
        raise ImportError("Install tree-sitter-hcl: pip install tree-sitter-hcl") from e


def _node_text(node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _string_lit_text(node, source: bytes) -> str:
    """Extract the inner text from a string_lit node (strips quotes)."""
    for child in node.children:
        if child.type == "template_literal":
            return _node_text(child, source)
    # Fallback: strip surrounding quotes from the full text
    text = _node_text(node, source)
    return text.strip('"').strip("'")


class HCLParser(LanguageParser):
    """Parses HCL/Terraform files into the navegador graph."""

    def __init__(self) -> None:
        from tree_sitter import Parser  # type: ignore[import]

        self._parser = Parser(_get_hcl_language())

    def parse_file(self, path: Path, repo_root: Path, store: GraphStore) -> dict[str, int]:
        source = path.read_bytes()
        tree = self._parser.parse(source)
        rel_path = str(path.relative_to(repo_root))

        store.create_node(
            NodeLabel.File,
            {
                "name": path.name,
                "path": rel_path,
                "language": "hcl",
                "line_count": source.count(b"\n"),
            },
        )

        stats = {"functions": 0, "classes": 0, "edges": 0}
        self._walk(tree.root_node, source, rel_path, store, stats)
        return stats

    # ── AST walker ────────────────────────────────────────────────────────────

    def _walk(self, node, source: bytes, file_path: str, store: GraphStore, stats: dict) -> None:
        """Walk the top-level body looking for block nodes."""
        for child in node.children:
            if child.type == "body":
                for body_child in child.children:
                    if body_child.type == "block":
                        self._handle_block(body_child, source, file_path, store, stats)
            elif child.type == "block":
                self._handle_block(child, source, file_path, store, stats)

    def _handle_block(
        self, node, source: bytes, file_path: str, store: GraphStore, stats: dict
    ) -> None:
        """Dispatch a block based on its block-type identifier."""
        block_type = None
        labels: list[str] = []
        body_node = None

        for child in node.children:
            if child.type == "identifier" and block_type is None:
                block_type = _node_text(child, source)
            elif child.type == "string_lit":
                labels.append(_string_lit_text(child, source))
            elif child.type == "body":
                body_node = child

        if not block_type:
            return

        if block_type == "resource" and len(labels) >= 2:
            self._handle_resource(node, source, file_path, store, stats, labels, body_node)
        elif block_type == "data" and len(labels) >= 2:
            self._handle_data(node, source, file_path, store, stats, labels, body_node)
        elif block_type == "provider" and len(labels) >= 1:
            self._handle_provider(node, source, file_path, store, stats, labels, body_node)
        elif block_type == "variable" and len(labels) >= 1:
            self._handle_variable(node, source, file_path, store, stats, labels, body_node)
        elif block_type == "output" and len(labels) >= 1:
            self._handle_output(node, source, file_path, store, stats, labels, body_node)
        elif block_type == "module" and len(labels) >= 1:
            self._handle_module(node, source, file_path, store, stats, labels, body_node)
        elif block_type == "locals":
            self._handle_locals(node, source, file_path, store, stats, body_node)
        elif block_type == "terraform":
            pass  # Configuration block, skip
        else:
            logger.debug("Skipping unknown HCL block type: %s", block_type)

    # ── Handlers ──────────────────────────────────────────────────────────────

    def _handle_resource(
        self,
        node,
        source: bytes,
        file_path: str,
        store: GraphStore,
        stats: dict,
        labels: list[str],
        body_node,
    ) -> None:
        name = f"{labels[0]}.{labels[1]}"
        store.create_node(
            NodeLabel.Class,
            {
                "name": name,
                "file_path": file_path,
                "line_start": node.start_point[0] + 1,
                "line_end": node.end_point[0] + 1,
                "docstring": "",
                "semantic_type": "terraform_resource",
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

        if body_node:
            self._extract_references(
                body_node, source, file_path, name, NodeLabel.Class, store, stats
            )

    def _handle_data(
        self,
        node,
        source: bytes,
        file_path: str,
        store: GraphStore,
        stats: dict,
        labels: list[str],
        body_node,
    ) -> None:
        name = f"{labels[0]}.{labels[1]}"
        store.create_node(
            NodeLabel.Class,
            {
                "name": name,
                "file_path": file_path,
                "line_start": node.start_point[0] + 1,
                "line_end": node.end_point[0] + 1,
                "docstring": "",
                "semantic_type": "terraform_data",
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

        if body_node:
            self._extract_references(
                body_node, source, file_path, name, NodeLabel.Class, store, stats
            )

    def _handle_provider(
        self,
        node,
        source: bytes,
        file_path: str,
        store: GraphStore,
        stats: dict,
        labels: list[str],
        body_node,
    ) -> None:
        name = labels[0]
        store.create_node(
            NodeLabel.Class,
            {
                "name": name,
                "file_path": file_path,
                "line_start": node.start_point[0] + 1,
                "line_end": node.end_point[0] + 1,
                "docstring": "",
                "semantic_type": "terraform_provider",
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

        if body_node:
            self._extract_references(
                body_node, source, file_path, name, NodeLabel.Class, store, stats
            )

    def _handle_variable(
        self,
        node,
        source: bytes,
        file_path: str,
        store: GraphStore,
        stats: dict,
        labels: list[str],
        body_node,
    ) -> None:
        name = labels[0]
        store.create_node(
            NodeLabel.Variable,
            {
                "name": name,
                "file_path": file_path,
                "line_start": node.start_point[0] + 1,
                "line_end": node.end_point[0] + 1,
                "semantic_type": "terraform_variable",
            },
        )
        store.create_edge(
            NodeLabel.File,
            {"path": file_path},
            EdgeType.CONTAINS,
            NodeLabel.Variable,
            {"name": name, "file_path": file_path},
        )
        stats["functions"] += 1
        stats["edges"] += 1

    def _handle_output(
        self,
        node,
        source: bytes,
        file_path: str,
        store: GraphStore,
        stats: dict,
        labels: list[str],
        body_node,
    ) -> None:
        name = labels[0]
        store.create_node(
            NodeLabel.Variable,
            {
                "name": name,
                "file_path": file_path,
                "line_start": node.start_point[0] + 1,
                "line_end": node.end_point[0] + 1,
                "semantic_type": "terraform_output",
            },
        )
        store.create_edge(
            NodeLabel.File,
            {"path": file_path},
            EdgeType.CONTAINS,
            NodeLabel.Variable,
            {"name": name, "file_path": file_path},
        )
        stats["functions"] += 1
        stats["edges"] += 1

        if body_node:
            self._extract_references(
                body_node, source, file_path, name, NodeLabel.Variable, store, stats
            )

    def _handle_module(
        self,
        node,
        source: bytes,
        file_path: str,
        store: GraphStore,
        stats: dict,
        labels: list[str],
        body_node,
    ) -> None:
        name = labels[0]
        source_attr = ""
        if body_node:
            source_attr = self._get_attribute_value(body_node, "source", source)

        store.create_node(
            NodeLabel.Module,
            {
                "name": name,
                "file_path": file_path,
                "line_start": node.start_point[0] + 1,
                "line_end": node.end_point[0] + 1,
                "semantic_type": "terraform_module",
                "source": source_attr,
            },
        )
        store.create_edge(
            NodeLabel.File,
            {"path": file_path},
            EdgeType.CONTAINS,
            NodeLabel.Module,
            {"name": name, "file_path": file_path},
        )
        stats["classes"] += 1
        stats["edges"] += 1

        if body_node:
            self._extract_references(
                body_node, source, file_path, name, NodeLabel.Module, store, stats
            )

    def _handle_locals(
        self,
        node,
        source: bytes,
        file_path: str,
        store: GraphStore,
        stats: dict,
        body_node,
    ) -> None:
        if not body_node:
            return

        for child in body_node.children:
            if child.type == "attribute":
                attr_name = None
                for attr_child in child.children:
                    if attr_child.type == "identifier":
                        attr_name = _node_text(attr_child, source)
                        break

                if not attr_name:
                    continue

                store.create_node(
                    NodeLabel.Variable,
                    {
                        "name": attr_name,
                        "file_path": file_path,
                        "line_start": child.start_point[0] + 1,
                        "line_end": child.end_point[0] + 1,
                        "semantic_type": "terraform_local",
                    },
                )
                store.create_edge(
                    NodeLabel.File,
                    {"path": file_path},
                    EdgeType.CONTAINS,
                    NodeLabel.Variable,
                    {"name": attr_name, "file_path": file_path},
                )
                stats["functions"] += 1
                stats["edges"] += 1

                # Extract references from the attribute expression
                self._extract_references(
                    child, source, file_path, attr_name, NodeLabel.Variable, store, stats
                )

    # ── Reference extraction ──────────────────────────────────────────────────

    def _extract_references(
        self,
        node,
        source: bytes,
        file_path: str,
        from_name: str,
        from_label: str,
        store: GraphStore,
        stats: dict,
    ) -> None:
        """Scan expression text for var.X, local.X, module.X, data.T.N, and resource references."""
        text = _node_text(node, source)

        # var.xxx → REFERENCES edge to terraform_variable
        for match in _VAR_REF.finditer(text):
            var_name = match.group(1)
            store.create_edge(
                from_label,
                {"name": from_name, "file_path": file_path},
                EdgeType.REFERENCES,
                NodeLabel.Variable,
                {"name": var_name, "file_path": file_path},
            )
            stats["edges"] += 1

        # local.xxx → REFERENCES edge to terraform_local
        for match in _LOCAL_REF.finditer(text):
            local_name = match.group(1)
            store.create_edge(
                from_label,
                {"name": from_name, "file_path": file_path},
                EdgeType.REFERENCES,
                NodeLabel.Variable,
                {"name": local_name, "file_path": file_path},
            )
            stats["edges"] += 1

        # module.xxx → REFERENCES edge to terraform_module
        for match in _MODULE_REF.finditer(text):
            mod_name = match.group(1)
            store.create_edge(
                from_label,
                {"name": from_name, "file_path": file_path},
                EdgeType.REFERENCES,
                NodeLabel.Module,
                {"name": mod_name, "file_path": file_path},
            )
            stats["edges"] += 1

        # data.type.name → DEPENDS_ON edge to terraform_data
        for match in _DATA_REF.finditer(text):
            data_name = f"{match.group(1)}.{match.group(2)}"
            store.create_edge(
                from_label,
                {"name": from_name, "file_path": file_path},
                EdgeType.DEPENDS_ON,
                NodeLabel.Class,
                {"name": data_name, "file_path": file_path},
            )
            stats["edges"] += 1

        # resource_type.resource_name → DEPENDS_ON edge to terraform_resource
        for match in _RESOURCE_REF.finditer(text):
            resource_name = f"{match.group(1)}.{match.group(2)}"
            store.create_edge(
                from_label,
                {"name": from_name, "file_path": file_path},
                EdgeType.DEPENDS_ON,
                NodeLabel.Class,
                {"name": resource_name, "file_path": file_path},
            )
            stats["edges"] += 1

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _get_attribute_value(self, body_node, attr_name: str, source: bytes) -> str:
        """Extract the string value of a named attribute from a body node."""
        for child in body_node.children:
            if child.type == "attribute":
                ident = None
                expr = None
                for attr_child in child.children:
                    if attr_child.type == "identifier":
                        ident = _node_text(attr_child, source)
                    elif attr_child.type == "expression" or attr_child.is_named:
                        expr = attr_child
                if ident == attr_name and expr is not None:
                    text = _node_text(expr, source).strip().strip('"').strip("'")
                    return text
        return ""
