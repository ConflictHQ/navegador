"""Tests for navegador.ingestion.java — JavaParser internal methods."""

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
    from navegador.ingestion.java import JavaParser
    parser = JavaParser.__new__(JavaParser)
    parser._parser = MagicMock()
    return parser


class TestJavaGetLanguage:
    def test_raises_when_not_installed(self):
        from navegador.ingestion.java import _get_java_language
        with patch.dict("sys.modules", {"tree_sitter_java": None, "tree_sitter": None}):
            with pytest.raises(ImportError, match="tree-sitter-java"):
                _get_java_language()


class TestJavaNodeText:
    def test_extracts_bytes(self):
        from navegador.ingestion.java import _node_text
        source = b"class Foo {}"
        node = MockNode("identifier", start_byte=6, end_byte=9)
        assert _node_text(node, source) == "Foo"


class TestJavadoc:
    def test_extracts_javadoc(self):
        from navegador.ingestion.java import _javadoc
        source = b"/** My class */\nclass Foo {}"
        comment = MockNode("block_comment", start_byte=0, end_byte=15)
        cls_node = MockNode("class_declaration", start_byte=16, end_byte=28)
        _parent = MockNode("program", children=[comment, cls_node])
        result = _javadoc(cls_node, source)
        assert "My class" in result

    def test_ignores_regular_block_comment(self):
        from navegador.ingestion.java import _javadoc
        source = b"/* regular */\nclass Foo {}"
        comment = MockNode("block_comment", start_byte=0, end_byte=13)
        cls_node = MockNode("class_declaration", start_byte=14, end_byte=26)
        MockNode("program", children=[comment, cls_node])
        result = _javadoc(cls_node, source)
        assert result == ""


class TestJavaHandleClass:
    def test_creates_class_node(self):
        parser = _make_parser()
        store = _make_store()
        source = b"class Foo {}"
        name_node = _text_node(b"Foo")
        body = MockNode("class_body")
        node = MockNode("class_declaration", start_point=(0, 0), end_point=(0, 11))
        node.set_field("name", name_node)
        node.set_field("body", body)
        _parent = MockNode("program", children=[node])
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_class(node, source, "Foo.java", store, stats)
        assert stats["classes"] == 1
        label = store.create_node.call_args[0][0]
        assert label == NodeLabel.Class

    def test_creates_inherits_edge(self):
        parser = _make_parser()
        store = _make_store()
        source = b"class Child extends Parent {}"
        name_node = _text_node(b"Child")
        parent_id = _text_node(b"Parent", "type_identifier")
        superclass = MockNode("superclass", children=[parent_id])
        body = MockNode("class_body")
        node = MockNode("class_declaration", start_point=(0, 0), end_point=(0, 28))
        node.set_field("name", name_node)
        node.set_field("superclass", superclass)
        node.set_field("body", body)
        MockNode("program", children=[node])
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_class(node, source, "Child.java", store, stats)
        # Should have CONTAINS edge + INHERITS edge
        assert stats["edges"] == 2

    def test_ingests_methods_in_body(self):
        parser = _make_parser()
        store = _make_store()
        source = b"class Foo { void bar() {} }"
        name_node = _text_node(b"Foo")
        method_name = _text_node(b"bar")
        method_body = MockNode("block")
        method = MockNode("method_declaration", start_point=(1, 2), end_point=(1, 14))
        method.set_field("name", method_name)
        method.set_field("body", method_body)
        body = MockNode("class_body", children=[method])
        node = MockNode("class_declaration", start_point=(0, 0), end_point=(0, 26))
        node.set_field("name", name_node)
        node.set_field("body", body)
        MockNode("program", children=[node])
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_class(node, source, "Foo.java", store, stats)
        assert stats["functions"] == 1

    def test_skips_if_no_name(self):
        parser = _make_parser()
        store = _make_store()
        node = MockNode("class_declaration")
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_class(node, b"", "X.java", store, stats)
        assert stats["classes"] == 0


class TestJavaHandleInterface:
    def test_creates_interface_node(self):
        parser = _make_parser()
        store = _make_store()
        source = b"interface Saveable {}"
        name_node = _text_node(b"Saveable")
        body = MockNode("interface_body")
        node = MockNode("interface_declaration", start_point=(0, 0), end_point=(0, 20))
        node.set_field("name", name_node)
        node.set_field("body", body)
        MockNode("program", children=[node])
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_interface(node, source, "Saveable.java", store, stats)
        assert stats["classes"] == 1
        props = store.create_node.call_args[0][1]
        assert "interface" in props.get("docstring", "")

    def test_skips_if_no_name(self):
        parser = _make_parser()
        store = _make_store()
        node = MockNode("interface_declaration")
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_interface(node, b"", "X.java", store, stats)
        assert stats["classes"] == 0


class TestJavaHandleMethod:
    def test_creates_method_node(self):
        parser = _make_parser()
        store = _make_store()
        source = b"void save() {}"
        name_node = _text_node(b"save")
        body = MockNode("block")
        node = MockNode("method_declaration", start_point=(0, 0), end_point=(0, 13))
        node.set_field("name", name_node)
        node.set_field("body", body)
        MockNode("class_body", children=[node])
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_method(node, source, "Foo.java", store, stats, class_name="Foo")
        assert stats["functions"] == 1
        label = store.create_node.call_args[0][0]
        assert label == NodeLabel.Method

    def test_skips_if_no_name(self):
        parser = _make_parser()
        store = _make_store()
        node = MockNode("method_declaration")
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_method(node, b"", "X.java", store, stats, class_name="X")
        assert stats["functions"] == 0


class TestJavaHandleImport:
    def test_ingests_import(self):
        parser = _make_parser()
        store = _make_store()
        source = b"import java.util.List;"
        node = MockNode("import_declaration", start_byte=0, end_byte=22,
                        start_point=(0, 0))
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_import(node, source, "Foo.java", store, stats)
        assert stats["edges"] == 1
        props = store.create_node.call_args[0][1]
        assert "java.util.List" in props["name"]

    def test_handles_static_import(self):
        parser = _make_parser()
        store = _make_store()
        source = b"import static java.util.Collections.sort;"
        node = MockNode("import_declaration", start_byte=0, end_byte=41,
                        start_point=(0, 0))
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_import(node, source, "Foo.java", store, stats)
        assert stats["edges"] == 1


class TestJavaExtractCalls:
    def test_extracts_method_invocation(self):
        parser = _make_parser()
        store = _make_store()
        source = b"bar"
        callee = _text_node(b"bar")
        invocation = MockNode("method_invocation")
        invocation.set_field("name", callee)
        body = MockNode("block", children=[invocation])
        node = MockNode("method_declaration")
        node.set_field("body", body)
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._extract_calls(node, source, "Foo.java", "foo", store, stats)
        assert stats["edges"] == 1
        edge_call = store.create_edge.call_args[0]
        assert edge_call[4]["name"] == "bar"

    def test_no_calls_in_empty_body(self):
        parser = _make_parser()
        store = _make_store()
        node = MockNode("method_declaration")
        node.set_field("body", MockNode("block"))
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._extract_calls(node, b"", "X.java", "foo", store, stats)
        assert stats["edges"] == 0
