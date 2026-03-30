"""Tests for navegador.ingestion.puppet — PuppetParser internal methods."""

from unittest.mock import MagicMock, patch

import pytest

from navegador.graph.schema import NodeLabel


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
    from navegador.ingestion.puppet import PuppetParser

    parser = PuppetParser.__new__(PuppetParser)
    parser._parser = MagicMock()
    return parser


class TestPuppetGetLanguage:
    def test_raises_when_not_installed(self):
        from navegador.ingestion.puppet import _get_puppet_language

        with patch.dict(
            "sys.modules",
            {
                "tree_sitter_puppet": None,
                "tree_sitter": None,
            },
        ):
            with pytest.raises(ImportError, match="tree-sitter-puppet"):
                _get_puppet_language()

    def test_returns_language_object(self):
        from navegador.ingestion.puppet import _get_puppet_language

        mock_tspuppet = MagicMock()
        mock_ts = MagicMock()
        with patch.dict(
            "sys.modules",
            {
                "tree_sitter_puppet": mock_tspuppet,
                "tree_sitter": mock_ts,
            },
        ):
            result = _get_puppet_language()
        assert result is mock_ts.Language.return_value


class TestPuppetHandleClass:
    def test_creates_class_with_puppet_class_semantic_type(self):
        parser = _make_parser()
        store = _make_store()
        source = b"nginx"
        class_ident = MockNode(
            "class_identifier",
            children=[
                MockNode(
                    "identifier",
                    start_byte=0,
                    end_byte=5,
                ),
            ],
            start_byte=0,
            end_byte=5,
        )
        node = MockNode(
            "class_definition",
            children=[class_ident],
            start_point=(0, 0),
            end_point=(5, 1),
        )
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_class(node, source, "nginx.pp", store, stats)
        assert stats["classes"] == 1
        assert stats["edges"] == 1
        label = store.create_node.call_args[0][0]
        props = store.create_node.call_args[0][1]
        assert label == NodeLabel.Class
        assert props["name"] == "nginx"
        assert props["semantic_type"] == "puppet_class"

    def test_skips_when_no_class_identifier(self):
        parser = _make_parser()
        store = _make_store()
        node = MockNode(
            "class_definition",
            children=[],
            start_point=(0, 0),
            end_point=(0, 5),
        )
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_class(node, b"", "test.pp", store, stats)
        assert stats["classes"] == 0
        store.create_node.assert_not_called()


class TestPuppetHandleDefinedType:
    def test_creates_class_with_puppet_defined_type(self):
        parser = _make_parser()
        store = _make_store()
        source = b"nginx::vhost"
        class_ident = MockNode(
            "class_identifier",
            children=[
                MockNode(
                    "identifier",
                    start_byte=0,
                    end_byte=5,
                ),
                MockNode(
                    "identifier",
                    start_byte=7,
                    end_byte=12,
                ),
            ],
            start_byte=0,
            end_byte=12,
        )
        node = MockNode(
            "defined_resource_type",
            children=[class_ident],
            start_point=(0, 0),
            end_point=(3, 1),
        )
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_defined_type(node, source, "vhost.pp", store, stats)
        assert stats["classes"] == 1
        label = store.create_node.call_args[0][0]
        props = store.create_node.call_args[0][1]
        assert label == NodeLabel.Class
        assert props["name"] == "nginx::vhost"
        assert props["semantic_type"] == "puppet_defined_type"


class TestPuppetHandleNode:
    def test_creates_class_with_puppet_node(self):
        parser = _make_parser()
        store = _make_store()
        source = b"'webserver'"
        string_node = MockNode(
            "string",
            start_byte=0,
            end_byte=11,
        )
        node_name = MockNode(
            "node_name",
            children=[string_node],
        )
        node = MockNode(
            "node_definition",
            children=[node_name],
            start_point=(0, 0),
            end_point=(3, 1),
        )
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_node(node, source, "nodes.pp", store, stats)
        assert stats["classes"] == 1
        label = store.create_node.call_args[0][0]
        props = store.create_node.call_args[0][1]
        assert label == NodeLabel.Class
        assert props["name"] == "webserver"
        assert props["semantic_type"] == "puppet_node"

    def test_skips_when_no_node_name(self):
        parser = _make_parser()
        store = _make_store()
        node = MockNode(
            "node_definition",
            children=[],
            start_point=(0, 0),
            end_point=(0, 5),
        )
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_node(node, b"", "nodes.pp", store, stats)
        assert stats["classes"] == 0


class TestPuppetHandleResource:
    def test_creates_function_with_puppet_resource(self):
        parser = _make_parser()
        store = _make_store()
        source = b"package 'nginx'"
        ident = MockNode(
            "identifier",
            start_byte=0,
            end_byte=7,
        )
        title = MockNode(
            "string",
            start_byte=8,
            end_byte=15,
        )
        node = MockNode(
            "resource_declaration",
            children=[ident, title],
            start_point=(1, 0),
            end_point=(3, 1),
        )
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_resource(node, source, "nginx.pp", "nginx", store, stats)
        assert stats["functions"] == 1
        assert stats["edges"] == 1
        label = store.create_node.call_args[0][0]
        props = store.create_node.call_args[0][1]
        assert label == NodeLabel.Function
        assert props["name"] == "package[nginx]"
        assert props["semantic_type"] == "puppet_resource"

    def test_skips_when_no_type_identifier(self):
        parser = _make_parser()
        store = _make_store()
        node = MockNode(
            "resource_declaration",
            children=[],
            start_point=(0, 0),
            end_point=(0, 5),
        )
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_resource(node, b"", "test.pp", "myclass", store, stats)
        assert stats["functions"] == 0


class TestPuppetHandleInclude:
    def test_creates_import_node(self):
        parser = _make_parser()
        store = _make_store()
        source = b"stdlib"
        class_ident = MockNode(
            "class_identifier",
            children=[
                MockNode(
                    "identifier",
                    start_byte=0,
                    end_byte=6,
                ),
            ],
        )
        node = MockNode(
            "include_statement",
            children=[class_ident],
            start_point=(0, 0),
            end_point=(0, 14),
        )
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_include(node, source, "init.pp", store, stats)
        assert stats["edges"] == 1
        label = store.create_node.call_args[0][0]
        props = store.create_node.call_args[0][1]
        assert label == NodeLabel.Import
        assert props["name"] == "stdlib"
        assert props["semantic_type"] == "puppet_include"

    def test_skips_when_no_class_identifier(self):
        parser = _make_parser()
        store = _make_store()
        node = MockNode(
            "include_statement",
            children=[],
            start_point=(0, 0),
            end_point=(0, 7),
        )
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_include(node, b"", "init.pp", store, stats)
        assert stats["edges"] == 0
        store.create_node.assert_not_called()


class TestPuppetHandleParameters:
    def test_creates_variable_nodes(self):
        parser = _make_parser()
        store = _make_store()
        source = b"$port"
        var_node = MockNode(
            "variable",
            start_byte=0,
            end_byte=5,
        )
        param = MockNode(
            "parameter",
            children=[var_node],
            start_point=(1, 2),
            end_point=(1, 7),
        )
        param_list = MockNode(
            "parameter_list",
            children=[param],
        )
        class_ident = MockNode(
            "class_identifier",
            children=[
                MockNode(
                    "identifier",
                    start_byte=0,
                    end_byte=5,
                ),
            ],
            start_byte=0,
            end_byte=5,
        )
        node = MockNode(
            "class_definition",
            children=[class_ident, param_list],
            start_point=(0, 0),
            end_point=(5, 1),
        )
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._extract_parameters(node, source, "nginx.pp", "nginx", store, stats)
        store.create_node.assert_called_once()
        label = store.create_node.call_args[0][0]
        props = store.create_node.call_args[0][1]
        assert label == NodeLabel.Variable
        assert props["name"] == "port"
        assert props["semantic_type"] == "puppet_parameter"
        assert stats["edges"] == 1

    def test_skips_param_without_variable(self):
        parser = _make_parser()
        store = _make_store()
        param = MockNode(
            "parameter",
            children=[MockNode("type")],
            start_point=(1, 2),
            end_point=(1, 7),
        )
        param_list = MockNode(
            "parameter_list",
            children=[param],
        )
        node = MockNode(
            "class_definition",
            children=[param_list],
            start_point=(0, 0),
            end_point=(5, 1),
        )
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._extract_parameters(node, b"", "test.pp", "myclass", store, stats)
        store.create_node.assert_not_called()


class TestPuppetWalkDispatch:
    def test_walk_dispatches_class_definition(self):
        parser = _make_parser()
        store = _make_store()
        source = b"nginx"
        class_ident = MockNode(
            "class_identifier",
            children=[
                MockNode(
                    "identifier",
                    start_byte=0,
                    end_byte=5,
                ),
            ],
        )
        class_def = MockNode(
            "class_definition",
            children=[class_ident],
            start_point=(0, 0),
            end_point=(5, 1),
        )
        root = MockNode("program", children=[class_def])
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._walk(root, source, "nginx.pp", store, stats)
        assert stats["classes"] == 1

    def test_walk_dispatches_defined_resource_type(self):
        parser = _make_parser()
        store = _make_store()
        source = b"vhost"
        class_ident = MockNode(
            "class_identifier",
            children=[
                MockNode(
                    "identifier",
                    start_byte=0,
                    end_byte=5,
                ),
            ],
        )
        define_node = MockNode(
            "defined_resource_type",
            children=[class_ident],
            start_point=(0, 0),
            end_point=(3, 1),
        )
        root = MockNode("program", children=[define_node])
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._walk(root, source, "vhost.pp", store, stats)
        assert stats["classes"] == 1

    def test_walk_dispatches_node_definition(self):
        parser = _make_parser()
        store = _make_store()
        source = b"'webserver'"
        string_node = MockNode(
            "string",
            start_byte=0,
            end_byte=11,
        )
        node_name = MockNode(
            "node_name",
            children=[string_node],
        )
        node_def = MockNode(
            "node_definition",
            children=[node_name],
            start_point=(0, 0),
            end_point=(3, 1),
        )
        root = MockNode("program", children=[node_def])
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._walk(root, source, "nodes.pp", store, stats)
        assert stats["classes"] == 1

    def test_walk_dispatches_include_statement(self):
        parser = _make_parser()
        store = _make_store()
        source = b"stdlib"
        class_ident = MockNode(
            "class_identifier",
            children=[
                MockNode(
                    "identifier",
                    start_byte=0,
                    end_byte=6,
                ),
            ],
        )
        include = MockNode(
            "include_statement",
            children=[class_ident],
            start_point=(0, 0),
            end_point=(0, 14),
        )
        root = MockNode("program", children=[include])
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._walk(root, source, "init.pp", store, stats)
        assert stats["edges"] == 1


class TestPuppetParseFile:
    def test_creates_file_node(self):
        import tempfile
        from pathlib import Path

        parser = _make_parser()
        store = _make_store()
        mock_tree = MagicMock()
        mock_tree.root_node.type = "program"
        mock_tree.root_node.children = []
        parser._parser.parse.return_value = mock_tree
        with tempfile.NamedTemporaryFile(suffix=".pp", delete=False) as f:
            f.write(b"class nginx {}\n")
            fpath = Path(f.name)
        try:
            parser.parse_file(fpath, fpath.parent, store)
            store.create_node.assert_called_once()
            label = store.create_node.call_args[0][0]
            props = store.create_node.call_args[0][1]
            assert label == NodeLabel.File
            assert props["language"] == "puppet"
        finally:
            fpath.unlink()
