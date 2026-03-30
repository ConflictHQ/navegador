"""Tests for navegador.ingestion.bash — BashParser internal methods."""

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
    from navegador.ingestion.bash import BashParser

    parser = BashParser.__new__(BashParser)
    parser._parser = MagicMock()
    return parser


class TestBashGetLanguage:
    def test_raises_when_not_installed(self):
        from navegador.ingestion.bash import _get_bash_language

        with patch.dict(
            "sys.modules",
            {
                "tree_sitter_bash": None,
                "tree_sitter": None,
            },
        ):
            with pytest.raises(ImportError, match="tree-sitter-bash"):
                _get_bash_language()

    def test_returns_language_object(self):
        from navegador.ingestion.bash import _get_bash_language

        mock_tsbash = MagicMock()
        mock_ts = MagicMock()
        with patch.dict(
            "sys.modules",
            {
                "tree_sitter_bash": mock_tsbash,
                "tree_sitter": mock_ts,
            },
        ):
            result = _get_bash_language()
        assert result is mock_ts.Language.return_value


class TestBashNodeText:
    def test_extracts_bytes(self):
        from navegador.ingestion.bash import _node_text

        source = b"#!/bin/bash\nmy_func() {"
        node = MockNode(
            "identifier",
            start_byte=12,
            end_byte=19,
        )
        assert _node_text(node, source) == "my_func"


class TestBashHandleFunction:
    def test_creates_function_node(self):
        parser = _make_parser()
        store = _make_store()
        source = b"deploy"
        name_node = MockNode(
            "word",
            start_byte=0,
            end_byte=6,
        )
        node = MockNode(
            "function_definition",
            start_point=(0, 0),
            end_point=(5, 1),
        )
        node.set_field("name", name_node)
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_function(node, source, "deploy.sh", store, stats)
        assert stats["functions"] == 1
        assert stats["edges"] == 1
        label = store.create_node.call_args[0][0]
        props = store.create_node.call_args[0][1]
        assert label == NodeLabel.Function
        assert props["name"] == "deploy"
        assert props["semantic_type"] == "shell_function"

    def test_skips_if_no_name_node(self):
        parser = _make_parser()
        store = _make_store()
        node = MockNode(
            "function_definition",
            start_point=(0, 0),
            end_point=(0, 5),
        )
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_function(node, b"", "test.sh", store, stats)
        assert stats["functions"] == 0
        store.create_node.assert_not_called()

    def test_extracts_calls_from_body(self):
        parser = _make_parser()
        store = _make_store()
        source = b"deploy helper"
        name_node = MockNode(
            "word",
            start_byte=0,
            end_byte=6,
        )
        callee_name = MockNode(
            "word",
            start_byte=7,
            end_byte=13,
        )
        cmd = MockNode("command")
        cmd.set_field("name", callee_name)
        body = MockNode(
            "compound_statement",
            children=[cmd],
        )
        node = MockNode(
            "function_definition",
            start_point=(0, 0),
            end_point=(5, 1),
        )
        node.set_field("name", name_node)
        node.set_field("body", body)
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_function(node, source, "deploy.sh", store, stats)
        # 1 CONTAINS edge + 1 CALLS edge
        assert stats["edges"] == 2


class TestBashHandleVariable:
    def test_creates_variable_node_for_top_level(self):
        parser = _make_parser()
        store = _make_store()
        source = b'VERSION="1.0"'
        name_node = MockNode(
            "variable_name",
            start_byte=0,
            end_byte=7,
        )
        value_node = MockNode(
            "string",
            start_byte=8,
            end_byte=13,
        )
        program = MockNode("program")
        node = MockNode(
            "variable_assignment",
            start_point=(0, 0),
            end_point=(0, 13),
            parent=program,
        )
        node.set_field("name", name_node)
        node.set_field("value", value_node)
        # Re-set parent after construction since constructor
        # overwrites it
        node.parent = program
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_variable(node, source, "env.sh", store, stats)
        assert stats["edges"] == 1
        label = store.create_node.call_args[0][0]
        props = store.create_node.call_args[0][1]
        assert label == NodeLabel.Variable
        assert props["name"] == "VERSION"
        assert props["semantic_type"] == "shell_variable"

    def test_skips_non_top_level_variable(self):
        parser = _make_parser()
        store = _make_store()
        source = b"x=1"
        name_node = MockNode(
            "variable_name",
            start_byte=0,
            end_byte=1,
        )
        func_parent = MockNode("function_definition")
        node = MockNode(
            "variable_assignment",
            start_point=(0, 0),
            end_point=(0, 3),
            parent=func_parent,
        )
        node.set_field("name", name_node)
        node.parent = func_parent
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_variable(node, source, "test.sh", store, stats)
        assert stats["edges"] == 0
        store.create_node.assert_not_called()

    def test_skips_variable_without_name(self):
        parser = _make_parser()
        store = _make_store()
        program = MockNode("program")
        node = MockNode(
            "variable_assignment",
            start_point=(0, 0),
            end_point=(0, 3),
            parent=program,
        )
        node.parent = program
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_variable(node, b"", "test.sh", store, stats)
        store.create_node.assert_not_called()


class TestBashHandleSource:
    def test_creates_import_for_source_command(self):
        parser = _make_parser()
        store = _make_store()
        source = b"source ./lib.sh"
        name_node = MockNode(
            "word",
            start_byte=0,
            end_byte=6,
        )
        arg_node = MockNode(
            "word",
            start_byte=7,
            end_byte=15,
        )
        node = MockNode(
            "command",
            children=[name_node, arg_node],
            start_point=(0, 0),
            end_point=(0, 15),
        )
        node.set_field("name", name_node)
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_command(node, source, "main.sh", store, stats)
        assert stats["edges"] == 1
        label = store.create_node.call_args[0][0]
        props = store.create_node.call_args[0][1]
        assert label == NodeLabel.Import
        assert props["name"] == "./lib.sh"
        assert props["semantic_type"] == "shell_source"

    def test_creates_import_for_dot_command(self):
        parser = _make_parser()
        store = _make_store()
        source = b". /etc/profile"
        name_node = MockNode(
            "word",
            start_byte=0,
            end_byte=1,
        )
        arg_node = MockNode(
            "word",
            start_byte=2,
            end_byte=14,
        )
        node = MockNode(
            "command",
            children=[name_node, arg_node],
            start_point=(0, 0),
            end_point=(0, 14),
        )
        node.set_field("name", name_node)
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_command(node, source, "main.sh", store, stats)
        assert stats["edges"] == 1
        props = store.create_node.call_args[0][1]
        assert props["name"] == "/etc/profile"

    def test_ignores_non_source_commands(self):
        parser = _make_parser()
        store = _make_store()
        source = b"echo hello"
        name_node = MockNode(
            "word",
            start_byte=0,
            end_byte=4,
        )
        node = MockNode(
            "command",
            children=[name_node],
            start_point=(0, 0),
            end_point=(0, 10),
        )
        node.set_field("name", name_node)
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_command(node, source, "main.sh", store, stats)
        assert stats["edges"] == 0
        store.create_node.assert_not_called()

    def test_skips_source_without_arguments(self):
        parser = _make_parser()
        store = _make_store()
        source = b"source"
        name_node = MockNode(
            "word",
            start_byte=0,
            end_byte=6,
        )
        node = MockNode(
            "command",
            children=[name_node],
            start_point=(0, 0),
            end_point=(0, 6),
        )
        node.set_field("name", name_node)
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_command(node, source, "main.sh", store, stats)
        assert stats["edges"] == 0
        store.create_node.assert_not_called()


class TestBashExtractCalls:
    def test_finds_command_calls(self):
        parser = _make_parser()
        store = _make_store()
        source = b"build_app"
        callee = MockNode(
            "word",
            start_byte=0,
            end_byte=9,
        )
        cmd = MockNode("command")
        cmd.set_field("name", callee)
        body = MockNode(
            "compound_statement",
            children=[cmd],
        )
        fn_node = MockNode("function_definition")
        fn_node.set_field("body", body)
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._extract_calls(fn_node, source, "deploy.sh", "deploy", store, stats)
        assert stats["edges"] == 1
        edge_call = store.create_edge.call_args[0]
        assert edge_call[2] == EdgeType.CALLS
        assert edge_call[4]["name"] == "build_app"

    def test_skips_builtins(self):
        parser = _make_parser()
        store = _make_store()
        source = b"echo"
        callee = MockNode(
            "word",
            start_byte=0,
            end_byte=4,
        )
        cmd = MockNode("command")
        cmd.set_field("name", callee)
        body = MockNode(
            "compound_statement",
            children=[cmd],
        )
        fn_node = MockNode("function_definition")
        fn_node.set_field("body", body)
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._extract_calls(fn_node, source, "test.sh", "myfunc", store, stats)
        assert stats["edges"] == 0

    def test_no_calls_in_empty_body(self):
        parser = _make_parser()
        store = _make_store()
        fn_node = MockNode("function_definition")
        fn_node.set_field("body", MockNode("compound_statement"))
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._extract_calls(fn_node, b"", "test.sh", "myfunc", store, stats)
        assert stats["edges"] == 0

    def test_no_body_means_no_calls(self):
        parser = _make_parser()
        store = _make_store()
        fn_node = MockNode("function_definition")
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._extract_calls(fn_node, b"", "test.sh", "myfunc", store, stats)
        assert stats["edges"] == 0


class TestBashWalkDispatch:
    def test_walk_handles_function_definition(self):
        parser = _make_parser()
        store = _make_store()
        source = b"deploy"
        name_node = MockNode(
            "word",
            start_byte=0,
            end_byte=6,
        )
        fn = MockNode(
            "function_definition",
            start_point=(0, 0),
            end_point=(5, 1),
        )
        fn.set_field("name", name_node)
        root = MockNode("program", children=[fn])
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._walk(root, source, "deploy.sh", store, stats)
        assert stats["functions"] == 1

    def test_walk_handles_variable_assignment(self):
        parser = _make_parser()
        store = _make_store()
        source = b"VERSION"
        name_node = MockNode(
            "variable_name",
            start_byte=0,
            end_byte=7,
        )
        program = MockNode("program")
        var = MockNode(
            "variable_assignment",
            start_point=(0, 0),
            end_point=(0, 13),
        )
        var.set_field("name", name_node)
        program.children = [var]
        for child in program.children:
            child.parent = program
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._walk(program, source, "env.sh", store, stats)
        assert stats["edges"] == 1

    def test_walk_handles_source_command(self):
        parser = _make_parser()
        store = _make_store()
        source = b"source ./lib.sh"
        name_node = MockNode(
            "word",
            start_byte=0,
            end_byte=6,
        )
        arg_node = MockNode(
            "word",
            start_byte=7,
            end_byte=15,
        )
        cmd = MockNode(
            "command",
            children=[name_node, arg_node],
            start_point=(0, 0),
            end_point=(0, 15),
        )
        cmd.set_field("name", name_node)
        root = MockNode("program", children=[cmd])
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._walk(root, source, "main.sh", store, stats)
        assert stats["edges"] == 1

    def test_walk_recurses_into_children(self):
        parser = _make_parser()
        store = _make_store()
        source = b"deploy"
        name_node = MockNode(
            "word",
            start_byte=0,
            end_byte=6,
        )
        fn = MockNode(
            "function_definition",
            start_point=(0, 0),
            end_point=(5, 1),
        )
        fn.set_field("name", name_node)
        wrapper = MockNode("if_statement", children=[fn])
        root = MockNode("program", children=[wrapper])
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._walk(root, source, "deploy.sh", store, stats)
        assert stats["functions"] == 1


class TestBashParseFile:
    def test_creates_file_node(self):
        import tempfile
        from pathlib import Path

        parser = _make_parser()
        store = _make_store()
        mock_tree = MagicMock()
        mock_tree.root_node.type = "program"
        mock_tree.root_node.children = []
        parser._parser.parse.return_value = mock_tree
        with tempfile.NamedTemporaryFile(suffix=".sh", delete=False) as f:
            f.write(b"#!/bin/bash\necho hello\n")
            fpath = Path(f.name)
        try:
            parser.parse_file(fpath, fpath.parent, store)
            store.create_node.assert_called_once()
            label = store.create_node.call_args[0][0]
            props = store.create_node.call_args[0][1]
            assert label == NodeLabel.File
            assert props["language"] == "bash"
        finally:
            fpath.unlink()
