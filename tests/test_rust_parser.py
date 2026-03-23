"""Tests for navegador.ingestion.rust — RustParser internal methods."""

from unittest.mock import MagicMock, patch

import pytest

from navegador.graph.schema import NodeLabel


class MockNode:
    _id_counter = 0

    def __init__(self, type_: str, text: bytes = b"", children: list = None,
                 start_byte: int = 0, end_byte: int = 0,
                 start_point: tuple = (0, 0), end_point: tuple = (0, 0),
                 parent=None):
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
    from navegador.ingestion.rust import RustParser
    parser = RustParser.__new__(RustParser)
    parser._parser = MagicMock()
    return parser


class TestRustGetLanguage:
    def test_raises_when_not_installed(self):
        from navegador.ingestion.rust import _get_rust_language
        with patch.dict("sys.modules", {"tree_sitter_rust": None, "tree_sitter": None}):
            with pytest.raises(ImportError, match="tree-sitter-rust"):
                _get_rust_language()


class TestRustNodeText:
    def test_extracts_bytes(self):
        from navegador.ingestion.rust import _node_text
        source = b"fn main() {}"
        node = MockNode("identifier", start_byte=3, end_byte=7)
        assert _node_text(node, source) == "main"


class TestRustDocComment:
    def test_collects_triple_slash_comments(self):
        from navegador.ingestion.rust import _doc_comment
        source = b"/// Docs line 1\n/// Docs line 2\nfn foo() {}"
        doc1 = MockNode("line_comment", start_byte=0, end_byte=15)
        doc2 = MockNode("line_comment", start_byte=16, end_byte=31)
        fn_node = MockNode("function_item", start_byte=32, end_byte=44)
        _parent = MockNode("source_file", children=[doc1, doc2, fn_node])
        result = _doc_comment(fn_node, source)
        assert "Docs line 1" in result
        assert "Docs line 2" in result

    def test_ignores_non_doc_comments(self):
        from navegador.ingestion.rust import _doc_comment
        source = b"// regular comment\nfn foo() {}"
        comment = MockNode("line_comment", start_byte=0, end_byte=18)
        fn_node = MockNode("function_item", start_byte=19, end_byte=30)
        MockNode("source_file", children=[comment, fn_node])
        result = _doc_comment(fn_node, source)
        assert result == ""

    def test_no_parent(self):
        from navegador.ingestion.rust import _doc_comment
        fn_node = MockNode("function_item")
        assert _doc_comment(fn_node, b"") == ""


class TestRustHandleFunction:
    def test_creates_function_node(self):
        parser = _make_parser()
        store = _make_store()
        source = b"fn foo() {}"
        name = _text_node(b"foo")
        node = MockNode("function_item", start_point=(0, 0), end_point=(0, 10))
        node.set_field("name", name)
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_function(node, source, "lib.rs", store, stats, impl_type=None)
        assert stats["functions"] == 1
        label = store.create_node.call_args[0][0]
        assert label == NodeLabel.Function

    def test_creates_method_when_impl_type_given(self):
        parser = _make_parser()
        store = _make_store()
        source = b"fn save(&self) {}"
        name = _text_node(b"save")
        node = MockNode("function_item", start_point=(0, 0), end_point=(0, 16))
        node.set_field("name", name)
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_function(node, source, "lib.rs", store, stats, impl_type="Repo")
        label = store.create_node.call_args[0][0]
        assert label == NodeLabel.Method

    def test_skips_if_no_name(self):
        parser = _make_parser()
        store = _make_store()
        node = MockNode("function_item", start_point=(0, 0), end_point=(0, 0))
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_function(node, b"", "lib.rs", store, stats, impl_type=None)
        assert stats["functions"] == 0


class TestRustHandleImpl:
    def test_handles_impl_block(self):
        parser = _make_parser()
        store = _make_store()
        source = b"impl Repo { fn save(&self) {} }"
        type_node = _text_node(b"Repo", "type_identifier")
        name_node = _text_node(b"save")
        fn_item = MockNode("function_item", start_point=(0, 12), end_point=(0, 28))
        fn_item.set_field("name", name_node)
        body = MockNode("declaration_list", children=[fn_item])
        impl = MockNode("impl_item", start_point=(0, 0), end_point=(0, 30))
        impl.set_field("type", type_node)
        impl.set_field("body", body)
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_impl(impl, source, "lib.rs", store, stats)
        assert stats["functions"] == 1

    def test_handles_impl_with_no_body(self):
        parser = _make_parser()
        store = _make_store()
        impl = MockNode("impl_item")
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_impl(impl, b"", "lib.rs", store, stats)
        assert stats["functions"] == 0


class TestRustHandleType:
    def test_ingests_struct(self):
        parser = _make_parser()
        store = _make_store()
        source = b"struct Foo {}"
        name = _text_node(b"Foo", "type_identifier")
        node = MockNode("struct_item", start_point=(0, 0), end_point=(0, 12))
        node.set_field("name", name)
        _parent = MockNode("source_file", children=[node])
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_type(node, source, "lib.rs", store, stats)
        assert stats["classes"] == 1
        props = store.create_node.call_args[0][1]
        assert "struct" in props["docstring"]

    def test_ingests_enum(self):
        parser = _make_parser()
        store = _make_store()
        source = b"enum Color { Red, Green }"
        name = _text_node(b"Color", "type_identifier")
        node = MockNode("enum_item", start_point=(0, 0), end_point=(0, 24))
        node.set_field("name", name)
        MockNode("source_file", children=[node])
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_type(node, source, "lib.rs", store, stats)
        assert stats["classes"] == 1

    def test_ingests_trait(self):
        parser = _make_parser()
        store = _make_store()
        source = b"trait Saveable {}"
        name = _text_node(b"Saveable", "type_identifier")
        node = MockNode("trait_item", start_point=(0, 0), end_point=(0, 16))
        node.set_field("name", name)
        MockNode("source_file", children=[node])
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_type(node, source, "lib.rs", store, stats)
        assert stats["classes"] == 1

    def test_skips_if_no_name(self):
        parser = _make_parser()
        store = _make_store()
        node = MockNode("struct_item")
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_type(node, b"", "lib.rs", store, stats)
        assert stats["classes"] == 0


class TestRustHandleUse:
    def test_ingests_use_statement(self):
        parser = _make_parser()
        store = _make_store()
        source = b"use std::collections::HashMap;"
        node = MockNode("use_declaration", start_byte=0, end_byte=30,
                        start_point=(0, 0))
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_use(node, source, "lib.rs", store, stats)
        assert stats["edges"] == 1
        store.create_node.assert_called_once()
        props = store.create_node.call_args[0][1]
        assert "HashMap" in props["name"] or "std" in props["name"]


class TestRustExtractCalls:
    def test_extracts_call(self):
        parser = _make_parser()
        store = _make_store()
        source = b"bar"
        callee = _text_node(b"bar")
        call_node = MockNode("call_expression")
        call_node.set_field("function", callee)
        body = MockNode("block", children=[call_node])
        fn_node = MockNode("function_item")
        fn_node.set_field("body", body)
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._extract_calls(fn_node, source, "lib.rs", "foo",
                              NodeLabel.Function, store, stats)
        assert stats["edges"] == 1
        edge_call = store.create_edge.call_args[0]
        assert edge_call[4]["name"] == "bar"

    def test_handles_method_call_syntax(self):
        parser = _make_parser()
        store = _make_store()
        source = b"Repo::save"
        callee = _text_node(b"Repo::save")
        call_node = MockNode("call_expression")
        call_node.set_field("function", callee)
        body = MockNode("block", children=[call_node])
        fn_node = MockNode("function_item")
        fn_node.set_field("body", body)
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._extract_calls(fn_node, source, "lib.rs", "foo",
                              NodeLabel.Function, store, stats)
        # "Repo::save" → callee = "save"
        edge_call = store.create_edge.call_args[0]
        assert edge_call[4]["name"] == "save"


# ── _get_rust_language happy path ─────────────────────────────────────────────

class TestRustGetLanguageHappyPath:
    def test_returns_language_object(self):
        from navegador.ingestion.rust import _get_rust_language
        mock_tsrust = MagicMock()
        mock_ts = MagicMock()
        with patch.dict("sys.modules", {
            "tree_sitter_rust": mock_tsrust,
            "tree_sitter": mock_ts,
        }):
            result = _get_rust_language()
        assert result is mock_ts.Language.return_value


# ── RustParser init and parse_file ───────────────────────────────────────────

class TestRustParserInit:
    def test_init_creates_parser(self):
        mock_tsrust = MagicMock()
        mock_ts = MagicMock()
        with patch.dict("sys.modules", {
            "tree_sitter_rust": mock_tsrust,
            "tree_sitter": mock_ts,
        }):
            from navegador.ingestion.rust import RustParser
            parser = RustParser()
        assert parser._parser is mock_ts.Parser.return_value

    def test_parse_file_creates_file_node(self):
        import tempfile
        from pathlib import Path

        from navegador.graph.schema import NodeLabel
        parser = _make_parser()
        store = _make_store()
        mock_tree = MagicMock()
        mock_tree.root_node.type = "source_file"
        mock_tree.root_node.children = []
        parser._parser.parse.return_value = mock_tree
        with tempfile.NamedTemporaryFile(suffix=".rs", delete=False) as f:
            f.write(b"fn main() {}\n")
            fpath = Path(f.name)
        try:
            parser.parse_file(fpath, fpath.parent, store)
            store.create_node.assert_called_once()
            assert store.create_node.call_args[0][0] == NodeLabel.File
            assert store.create_node.call_args[0][1]["language"] == "rust"
        finally:
            fpath.unlink()


# ── _walk dispatch ────────────────────────────────────────────────────────────

class TestRustWalkDispatch:
    def test_walk_handles_function_item(self):
        parser = _make_parser()
        store = _make_store()
        source = b"foo"
        name = _text_node(b"foo")
        fn_node = MockNode("function_item", start_point=(0, 0), end_point=(0, 10))
        fn_node.set_field("name", name)
        root = MockNode("source_file", children=[fn_node])
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._walk(root, source, "lib.rs", store, stats, impl_type=None)
        assert stats["functions"] == 1

    def test_walk_handles_impl_item(self):
        parser = _make_parser()
        store = _make_store()
        source = b"MyStruct"
        type_node = MockNode("type_identifier", start_byte=0, end_byte=8)
        body = MockNode("declaration_list", children=[])
        impl_node = MockNode("impl_item")
        impl_node.set_field("type", type_node)
        impl_node.set_field("body", body)
        root = MockNode("source_file", children=[impl_node])
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._walk(root, source, "lib.rs", store, stats, impl_type=None)
        # No functions in body, just verifies dispatch doesn't crash
        assert stats["functions"] == 0

    def test_walk_handles_struct_item(self):
        parser = _make_parser()
        store = _make_store()
        source = b"Foo"
        name = _text_node(b"Foo", "type_identifier")
        node = MockNode("struct_item", start_point=(0, 0), end_point=(0, 10))
        node.set_field("name", name)
        _parent = MockNode("source_file", children=[node])
        root = MockNode("source_file", children=[node])
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._walk(root, source, "lib.rs", store, stats, impl_type=None)
        assert stats["classes"] == 1

    def test_walk_handles_use_declaration(self):
        parser = _make_parser()
        store = _make_store()
        source = b"use std::io;"
        use_node = MockNode("use_declaration", start_byte=0, end_byte=12)
        root = MockNode("source_file", children=[use_node])
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._walk(root, source, "lib.rs", store, stats, impl_type=None)
        assert stats["edges"] == 1

    def test_walk_recurses_into_unknown_nodes(self):
        parser = _make_parser()
        store = _make_store()
        source = b"foo"
        name = _text_node(b"foo")
        fn_node = MockNode("function_item", start_point=(0, 0), end_point=(0, 10))
        fn_node.set_field("name", name)
        wrapper = MockNode("mod_item", children=[fn_node])
        root = MockNode("source_file", children=[wrapper])
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._walk(root, source, "lib.rs", store, stats, impl_type=None)
        assert stats["functions"] == 1
