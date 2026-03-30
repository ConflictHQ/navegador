"""Tests for navegador.ingestion.hcl — HCLParser internal methods."""

from unittest.mock import MagicMock, patch

import pytest

from navegador.graph.schema import EdgeType, NodeLabel


class MockNode:
    _id_counter = 0

    def __init__(
        self,
        type_: str,
        text: bytes = b"",
        children: list = None,
        start_byte: int = 0,
        end_byte: int = 0,
        start_point: tuple = (0, 0),
        end_point: tuple = (0, 0),
        parent=None,
    ):
        MockNode._id_counter += 1
        self.id = MockNode._id_counter
        self.type = type_
        self._text = text
        self.children = children or []
        self.start_byte = start_byte
        self.end_byte = end_byte
        self.start_point = start_point
        self.end_point = end_point
        self.parent = parent
        self._fields: dict = {}
        for child in self.children:
            child.parent = self

    def child_by_field_name(self, name: str):
        return self._fields.get(name)

    def set_field(self, name: str, node):
        self._fields[name] = node
        node.parent = self
        return self


def _text_node(text: bytes, type_: str = "identifier") -> MockNode:
    return MockNode(type_, text, start_byte=0, end_byte=len(text))


def _make_store():
    store = MagicMock()
    store.query.return_value = MagicMock(result_set=[])
    return store


def _make_parser():
    from navegador.ingestion.hcl import HCLParser

    parser = HCLParser.__new__(HCLParser)
    parser._parser = MagicMock()
    return parser


class TestHCLGetLanguage:
    def test_raises_when_not_installed(self):
        from navegador.ingestion.hcl import _get_hcl_language

        with patch.dict(
            "sys.modules",
            {
                "tree_sitter_hcl": None,
                "tree_sitter": None,
            },
        ):
            with pytest.raises(ImportError, match="tree-sitter-hcl"):
                _get_hcl_language()

    def test_returns_language_object(self):
        from navegador.ingestion.hcl import _get_hcl_language

        mock_tshcl = MagicMock()
        mock_ts = MagicMock()
        with patch.dict(
            "sys.modules",
            {
                "tree_sitter_hcl": mock_tshcl,
                "tree_sitter": mock_ts,
            },
        ):
            result = _get_hcl_language()
        assert result is mock_ts.Language.return_value


class TestHCLNodeText:
    def test_extracts_bytes(self):
        from navegador.ingestion.hcl import _node_text

        source = b'resource "aws_instance" "web" {}'
        node = MockNode("identifier", start_byte=10, end_byte=22)
        assert _node_text(node, source) == "aws_instance"


class TestHCLHandleResource:
    def test_creates_class_node_with_semantic_type(self):
        parser = _make_parser()
        store = _make_store()
        source = b'resource "aws_instance" "web" {}'
        node = MockNode(
            "block",
            start_point=(0, 0),
            end_point=(0, 30),
        )
        labels = ["aws_instance", "web"]
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_resource(node, source, "main.tf", store, stats, labels, None)
        assert stats["classes"] == 1
        assert stats["edges"] == 1
        store.create_node.assert_called_once()
        label = store.create_node.call_args[0][0]
        props = store.create_node.call_args[0][1]
        assert label == NodeLabel.Class
        assert props["name"] == "aws_instance.web"
        assert props["semantic_type"] == "terraform_resource"

    def test_extracts_references_from_body(self):
        parser = _make_parser()
        store = _make_store()
        source = b"var.region"
        body = MockNode("body", start_byte=0, end_byte=10)
        node = MockNode(
            "block",
            start_point=(0, 0),
            end_point=(0, 30),
        )
        labels = ["aws_instance", "web"]
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_resource(node, source, "main.tf", store, stats, labels, body)
        # 1 CONTAINS edge + 1 REFERENCES edge from var.region
        assert stats["edges"] == 2


class TestHCLHandleVariable:
    def test_creates_variable_node(self):
        parser = _make_parser()
        store = _make_store()
        source = b'variable "region" {}'
        node = MockNode(
            "block",
            start_point=(0, 0),
            end_point=(0, 19),
        )
        labels = ["region"]
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_variable(node, source, "vars.tf", store, stats, labels, None)
        assert stats["functions"] == 1
        assert stats["edges"] == 1
        label = store.create_node.call_args[0][0]
        props = store.create_node.call_args[0][1]
        assert label == NodeLabel.Variable
        assert props["name"] == "region"
        assert props["semantic_type"] == "terraform_variable"


class TestHCLHandleModule:
    def test_creates_module_node(self):
        parser = _make_parser()
        store = _make_store()
        source = b'module "vpc" {}'
        node = MockNode(
            "block",
            start_point=(0, 0),
            end_point=(0, 14),
        )
        labels = ["vpc"]
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_module(node, source, "main.tf", store, stats, labels, None)
        assert stats["classes"] == 1
        assert stats["edges"] == 1
        label = store.create_node.call_args[0][0]
        props = store.create_node.call_args[0][1]
        assert label == NodeLabel.Module
        assert props["name"] == "vpc"
        assert props["semantic_type"] == "terraform_module"

    def test_extracts_source_attribute(self):
        parser = _make_parser()
        store = _make_store()
        full_src = b"source./modules/vpc"
        ident_node = MockNode(
            "identifier",
            start_byte=0,
            end_byte=6,
        )
        expr_node = MockNode(
            "expression",
            start_byte=6,
            end_byte=19,
        )
        expr_node.is_named = True
        attr_node = MockNode(
            "attribute",
            children=[ident_node, expr_node],
        )
        body_node = MockNode("body", children=[attr_node])
        node = MockNode(
            "block",
            start_point=(0, 0),
            end_point=(0, 30),
        )
        labels = ["vpc"]
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_module(node, full_src, "main.tf", store, stats, labels, body_node)
        props = store.create_node.call_args[0][1]
        assert props["source"] == "./modules/vpc"


class TestHCLHandleOutput:
    def test_creates_variable_node(self):
        parser = _make_parser()
        store = _make_store()
        source = b'output "vpc_id" {}'
        node = MockNode(
            "block",
            start_point=(0, 0),
            end_point=(0, 17),
        )
        labels = ["vpc_id"]
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_output(node, source, "outputs.tf", store, stats, labels, None)
        assert stats["functions"] == 1
        assert stats["edges"] == 1
        label = store.create_node.call_args[0][0]
        props = store.create_node.call_args[0][1]
        assert label == NodeLabel.Variable
        assert props["semantic_type"] == "terraform_output"

    def test_extracts_references_from_body(self):
        parser = _make_parser()
        store = _make_store()
        source = b"module.vpc"
        body = MockNode("body", start_byte=0, end_byte=10)
        node = MockNode(
            "block",
            start_point=(0, 0),
            end_point=(0, 17),
        )
        labels = ["vpc_id"]
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_output(node, source, "outputs.tf", store, stats, labels, body)
        # 1 CONTAINS + 1 REFERENCES (module.vpc)
        assert stats["edges"] == 2


class TestHCLHandleProvider:
    def test_creates_class_node(self):
        parser = _make_parser()
        store = _make_store()
        source = b'provider "aws" {}'
        node = MockNode(
            "block",
            start_point=(0, 0),
            end_point=(0, 16),
        )
        labels = ["aws"]
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_provider(node, source, "provider.tf", store, stats, labels, None)
        assert stats["classes"] == 1
        assert stats["edges"] == 1
        label = store.create_node.call_args[0][0]
        props = store.create_node.call_args[0][1]
        assert label == NodeLabel.Class
        assert props["name"] == "aws"
        assert props["semantic_type"] == "terraform_provider"


class TestHCLHandleLocals:
    def test_creates_variable_nodes(self):
        parser = _make_parser()
        store = _make_store()
        source = b"region"
        ident = MockNode(
            "identifier",
            start_byte=0,
            end_byte=6,
        )
        attr = MockNode(
            "attribute",
            children=[ident],
            start_point=(1, 0),
            end_point=(1, 20),
        )
        body = MockNode("body", children=[attr])
        node = MockNode(
            "block",
            start_point=(0, 0),
            end_point=(2, 1),
        )
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_locals(node, source, "locals.tf", store, stats, body)
        assert stats["functions"] == 1
        assert stats["edges"] >= 1
        label = store.create_node.call_args[0][0]
        props = store.create_node.call_args[0][1]
        assert label == NodeLabel.Variable
        assert props["semantic_type"] == "terraform_local"

    def test_skips_when_no_body(self):
        parser = _make_parser()
        store = _make_store()
        node = MockNode("block", start_point=(0, 0), end_point=(0, 5))
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_locals(node, b"", "locals.tf", store, stats, None)
        assert stats["functions"] == 0
        store.create_node.assert_not_called()


class TestHCLWalkDispatch:
    def test_walk_dispatches_block_in_body(self):
        parser = _make_parser()
        store = _make_store()
        # Build: root > body > block(variable "region")
        source = b'variable "region" {}'
        ident = MockNode(
            "identifier",
            start_byte=0,
            end_byte=8,
        )
        string_lit_inner = MockNode(
            "template_literal",
            start_byte=10,
            end_byte=16,
        )
        string_lit = MockNode(
            "string_lit",
            children=[string_lit_inner],
            start_byte=9,
            end_byte=17,
        )
        block = MockNode(
            "block",
            children=[ident, string_lit],
            start_point=(0, 0),
            end_point=(0, 19),
        )
        body = MockNode("body", children=[block])
        root = MockNode("config_file", children=[body])
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._walk(root, source, "vars.tf", store, stats)
        assert stats["functions"] == 1

    def test_walk_dispatches_top_level_block(self):
        parser = _make_parser()
        store = _make_store()
        source = b'provider "aws" {}'
        ident = MockNode(
            "identifier",
            start_byte=0,
            end_byte=8,
        )
        string_lit_inner = MockNode(
            "template_literal",
            start_byte=10,
            end_byte=13,
        )
        string_lit = MockNode(
            "string_lit",
            children=[string_lit_inner],
            start_byte=9,
            end_byte=14,
        )
        block = MockNode(
            "block",
            children=[ident, string_lit],
            start_point=(0, 0),
            end_point=(0, 16),
        )
        root = MockNode("config_file", children=[block])
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._walk(root, source, "main.tf", store, stats)
        assert stats["classes"] == 1


class TestHCLExtractReferences:
    def test_finds_var_reference(self):
        parser = _make_parser()
        store = _make_store()
        source = b"var.region"
        node = MockNode("body", start_byte=0, end_byte=10)
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._extract_references(
            node,
            source,
            "main.tf",
            "aws_instance.web",
            NodeLabel.Class,
            store,
            stats,
        )
        assert stats["edges"] == 1
        edge_call = store.create_edge.call_args[0]
        assert edge_call[2] == EdgeType.REFERENCES
        assert edge_call[4]["name"] == "region"

    def test_finds_resource_reference(self):
        parser = _make_parser()
        store = _make_store()
        source = b"aws_security_group.default"
        node = MockNode("body", start_byte=0, end_byte=25)
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._extract_references(
            node,
            source,
            "main.tf",
            "aws_instance.web",
            NodeLabel.Class,
            store,
            stats,
        )
        assert stats["edges"] == 1
        edge_call = store.create_edge.call_args[0]
        assert edge_call[2] == EdgeType.DEPENDS_ON

    def test_finds_local_reference(self):
        parser = _make_parser()
        store = _make_store()
        source = b"local.common_tags"
        node = MockNode("body", start_byte=0, end_byte=17)
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._extract_references(
            node,
            source,
            "main.tf",
            "aws_instance.web",
            NodeLabel.Class,
            store,
            stats,
        )
        assert stats["edges"] == 1

    def test_finds_module_reference(self):
        parser = _make_parser()
        store = _make_store()
        source = b"module.vpc"
        node = MockNode("body", start_byte=0, end_byte=10)
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._extract_references(
            node,
            source,
            "main.tf",
            "output_vpc",
            NodeLabel.Variable,
            store,
            stats,
        )
        assert stats["edges"] == 1
        edge_call = store.create_edge.call_args[0]
        assert edge_call[3] == NodeLabel.Module

    def test_finds_data_reference(self):
        parser = _make_parser()
        store = _make_store()
        source = b"data.http.myip"
        node = MockNode("body", start_byte=0, end_byte=14)
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._extract_references(
            node,
            source,
            "main.tf",
            "aws_instance.web",
            NodeLabel.Class,
            store,
            stats,
        )
        assert stats["edges"] == 1
        edge_call = store.create_edge.call_args[0]
        assert edge_call[2] == EdgeType.DEPENDS_ON
        assert edge_call[4]["name"] == "http.myip"


class TestHCLParseFile:
    def test_creates_file_node(self):
        import tempfile
        from pathlib import Path

        parser = _make_parser()
        store = _make_store()
        mock_tree = MagicMock()
        mock_tree.root_node.type = "config_file"
        mock_tree.root_node.children = []
        parser._parser.parse.return_value = mock_tree
        with tempfile.NamedTemporaryFile(suffix=".tf", delete=False) as f:
            f.write(b'resource "aws_instance" "web" {}\n')
            fpath = Path(f.name)
        try:
            parser.parse_file(fpath, fpath.parent, store)
            store.create_node.assert_called_once()
            label = store.create_node.call_args[0][0]
            props = store.create_node.call_args[0][1]
            assert label == NodeLabel.File
            assert props["language"] == "hcl"
        finally:
            fpath.unlink()
