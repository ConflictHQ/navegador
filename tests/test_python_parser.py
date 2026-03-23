"""Tests for navegador.ingestion.python — PythonParser internal methods."""

from unittest.mock import MagicMock, patch

import pytest

from navegador.graph.schema import NodeLabel

# ── Mock tree-sitter node ──────────────────────────────────────────────────────

class MockNode:
    """Minimal mock of a tree-sitter Node."""
    def __init__(self, type_: str, text: bytes = b"", children: list = None,
                 start_byte: int = 0, end_byte: int = 0,
                 start_point: tuple = (0, 0), end_point: tuple = (0, 0)):
        self.type = type_
        self._text = text
        self.children = children or []
        self.start_byte = start_byte
        self.end_byte = end_byte
        self.start_point = start_point
        self.end_point = end_point


def _text_node(text: bytes, type_: str = "identifier") -> MockNode:
    return MockNode(type_, text, start_byte=0, end_byte=len(text))


def _make_store():
    store = MagicMock()
    store.query.return_value = MagicMock(result_set=[])
    return store


# ── _node_text ────────────────────────────────────────────────────────────────

class TestNodeText:
    def test_extracts_text_from_source(self):
        from navegador.ingestion.python import _node_text
        source = b"hello world"
        node = MockNode("identifier", start_byte=6, end_byte=11)
        assert _node_text(node, source) == "world"

    def test_full_source(self):
        from navegador.ingestion.python import _node_text
        source = b"foo_bar"
        node = MockNode("identifier", start_byte=0, end_byte=7)
        assert _node_text(node, source) == "foo_bar"

    def test_handles_utf8(self):
        from navegador.ingestion.python import _node_text
        source = "héllo".encode("utf-8")
        node = MockNode("identifier", start_byte=0, end_byte=len(source))
        assert "llo" in _node_text(node, source)


# ── _get_docstring ────────────────────────────────────────────────────────────

class TestGetDocstring:
    def test_returns_none_when_no_block(self):
        from navegador.ingestion.python import _get_docstring
        node = MockNode("function_definition", children=[
            MockNode("identifier")
        ])
        assert _get_docstring(node, b"def foo(): pass") is None

    def test_returns_none_when_no_expression_stmt(self):
        from navegador.ingestion.python import _get_docstring
        block = MockNode("block", children=[
            MockNode("return_statement")
        ])
        fn = MockNode("function_definition", children=[block])
        assert _get_docstring(fn, b"") is None

    def test_returns_none_when_no_string_in_expr(self):
        from navegador.ingestion.python import _get_docstring
        expr_stmt = MockNode("expression_statement", children=[
            MockNode("assignment")
        ])
        block = MockNode("block", children=[expr_stmt])
        fn = MockNode("function_definition", children=[block])
        assert _get_docstring(fn, b"") is None

    def test_extracts_docstring(self):
        from navegador.ingestion.python import _get_docstring
        source = b'"""My docstring."""'
        string_node = MockNode("string", start_byte=0, end_byte=len(source))
        expr_stmt = MockNode("expression_statement", children=[string_node])
        block = MockNode("block", children=[expr_stmt])
        fn = MockNode("function_definition", children=[block])
        result = _get_docstring(fn, source)
        assert "My docstring." in result


# ── _get_python_language error ─────────────────────────────────────────────────

class TestGetPythonLanguage:
    def test_raises_import_error_when_not_installed(self):
        from navegador.ingestion.python import _get_python_language
        with patch.dict("sys.modules", {"tree_sitter_python": None, "tree_sitter": None}):
            with pytest.raises(ImportError, match="tree-sitter-python"):
                _get_python_language()


# ── PythonParser with mocked parser ──────────────────────────────────────────

class TestPythonParserHandlers:
    def _make_parser(self):
        """Create PythonParser bypassing tree-sitter init."""
        from navegador.ingestion.python import PythonParser
        with patch("navegador.ingestion.python._get_parser") as mock_get:
            mock_get.return_value = MagicMock()
            parser = PythonParser()
        return parser

    def test_handle_import(self):
        parser = self._make_parser()
        store = _make_store()
        source = b"import os.path"

        dotted = _text_node(b"os.path", "dotted_name")
        import_node = MockNode("import_statement", children=[dotted],
                               start_point=(0, 0))
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_import(import_node, source, "app.py", store, stats)
        store.create_node.assert_called_once()
        store.create_edge.assert_called_once()
        assert stats["edges"] == 1

    def test_handle_import_no_dotted_name(self):
        parser = self._make_parser()
        store = _make_store()
        import_node = MockNode("import_statement", children=[
            MockNode("keyword", b"import")
        ], start_point=(0, 0))
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_import(import_node, b"import x", "app.py", store, stats)
        store.create_node.assert_not_called()

    def test_handle_class(self):
        parser = self._make_parser()
        store = _make_store()
        source = b"class MyClass: pass"
        name_node = _text_node(b"MyClass")
        class_node = MockNode("class_definition",
                              children=[name_node],
                              start_point=(0, 0), end_point=(0, 18))
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_class(class_node, source, "app.py", store, stats)
        assert stats["classes"] == 1
        assert stats["edges"] == 1
        store.create_node.assert_called()

    def test_handle_class_no_identifier(self):
        parser = self._make_parser()
        store = _make_store()
        class_node = MockNode("class_definition", children=[
            MockNode("keyword", b"class")
        ], start_point=(0, 0), end_point=(0, 0))
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_class(class_node, b"class: pass", "app.py", store, stats)
        assert stats["classes"] == 0

    def test_handle_class_with_inheritance(self):
        parser = self._make_parser()
        store = _make_store()
        source = b"class Child(Parent): pass"
        name_node = _text_node(b"Child")
        parent_id = _text_node(b"Parent")
        arg_list = MockNode("argument_list", children=[parent_id])
        class_node = MockNode("class_definition",
                              children=[name_node, arg_list],
                              start_point=(0, 0), end_point=(0, 24))
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_class(class_node, source, "app.py", store, stats)
        # Should create class node + CONTAINS edge + INHERITS edge
        assert stats["edges"] == 2

    def test_handle_function(self):
        parser = self._make_parser()
        store = _make_store()
        source = b"def foo(): pass"
        name_node = _text_node(b"foo")
        fn_node = MockNode("function_definition", children=[name_node],
                           start_point=(0, 0), end_point=(0, 14))
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_function(fn_node, source, "app.py", store, stats, class_name=None)
        assert stats["functions"] == 1
        assert stats["edges"] == 1
        store.create_node.assert_called_once()
        label = store.create_node.call_args[0][0]
        assert label == NodeLabel.Function

    def test_handle_method(self):
        parser = self._make_parser()
        store = _make_store()
        source = b"def my_method(self): pass"
        name_node = _text_node(b"my_method")
        fn_node = MockNode("function_definition", children=[name_node],
                           start_point=(0, 0), end_point=(0, 24))
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_function(fn_node, source, "app.py", store, stats, class_name="MyClass")
        label = store.create_node.call_args[0][0]
        assert label == NodeLabel.Method

    def test_handle_function_no_identifier(self):
        parser = self._make_parser()
        store = _make_store()
        fn_node = MockNode("function_definition", children=[
            MockNode("keyword", b"def")
        ], start_point=(0, 0), end_point=(0, 0))
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_function(fn_node, b"def", "app.py", store, stats, class_name=None)
        assert stats["functions"] == 0

    def test_extract_calls(self):
        parser = self._make_parser()
        store = _make_store()
        source = b"def foo():\n    bar()\n"

        callee = _text_node(b"bar")
        call_node = MockNode("call", children=[callee])
        block = MockNode("block", children=[call_node])
        fn_node = MockNode("function_definition", children=[block])

        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._extract_calls(fn_node, source, "app.py", "foo", NodeLabel.Function, store, stats)
        store.create_edge.assert_called_once()
        assert stats["edges"] == 1

    def test_extract_calls_no_block(self):
        parser = self._make_parser()
        store = _make_store()
        fn_node = MockNode("function_definition", children=[])
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._extract_calls(fn_node, b"", "app.py", "foo", NodeLabel.Function, store, stats)
        store.create_edge.assert_not_called()

    def test_walk_dispatches_import(self):
        parser = self._make_parser()
        store = _make_store()
        dotted = _text_node(b"sys", "dotted_name")
        import_node = MockNode("import_statement", children=[dotted], start_point=(0, 0))
        root = MockNode("module", children=[import_node])
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._walk(root, b"import sys", "app.py", store, stats, class_name=None)
        assert stats["edges"] == 1

    def test_walk_dispatches_class(self):
        parser = self._make_parser()
        store = _make_store()
        name_node = _text_node(b"MyClass")
        class_node = MockNode("class_definition", children=[name_node],
                              start_point=(0, 0), end_point=(0, 0))
        root = MockNode("module", children=[class_node])
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._walk(root, b"class MyClass: pass", "app.py", store, stats, class_name=None)
        assert stats["classes"] == 1

    def test_walk_dispatches_function(self):
        parser = self._make_parser()
        store = _make_store()
        name_node = _text_node(b"my_fn")
        fn_node = MockNode("function_definition", children=[name_node],
                           start_point=(0, 0), end_point=(0, 0))
        root = MockNode("module", children=[fn_node])
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._walk(root, b"def my_fn(): pass", "app.py", store, stats, class_name=None)
        assert stats["functions"] == 1


# ── _get_python_language happy path ──────────────────────────────────────────

class TestGetPythonLanguageHappyPath:
    def test_returns_language_object(self):
        from navegador.ingestion.python import _get_python_language
        mock_tspy = MagicMock()
        mock_ts = MagicMock()
        with patch.dict("sys.modules", {
            "tree_sitter_python": mock_tspy,
            "tree_sitter": mock_ts,
        }):
            result = _get_python_language()
        assert result is mock_ts.Language.return_value


# ── _get_parser ───────────────────────────────────────────────────────────────

class TestGetParserHappyPath:
    def test_returns_parser(self):
        from navegador.ingestion.python import _get_parser
        mock_tspy = MagicMock()
        mock_ts = MagicMock()
        with patch.dict("sys.modules", {
            "tree_sitter_python": mock_tspy,
            "tree_sitter": mock_ts,
        }):
            result = _get_parser()
        assert result is mock_ts.Parser.return_value


# ── parse_file ────────────────────────────────────────────────────────────────

class TestPythonParseFile:
    def _make_parser(self):
        from navegador.ingestion.python import PythonParser
        with patch("navegador.ingestion.python._get_parser") as mock_get:
            mock_get.return_value = MagicMock()
            parser = PythonParser()
        return parser

    def test_parse_file_creates_file_node(self):
        import tempfile
        from pathlib import Path
        parser = self._make_parser()
        store = MagicMock()
        store.query.return_value = MagicMock(result_set=[])
        mock_tree = MagicMock()
        mock_tree.root_node.type = "module"
        mock_tree.root_node.children = []
        parser._parser.parse.return_value = mock_tree
        with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as f:
            f.write(b"x = 1\n")
            fpath = Path(f.name)
        try:
            stats = parser.parse_file(fpath, fpath.parent, store)
            store.create_node.assert_called_once()
            call = store.create_node.call_args[0]
            from navegador.graph.schema import NodeLabel
            assert call[0] == NodeLabel.File
            assert call[1]["language"] == "python"
            assert isinstance(stats, dict)
        finally:
            fpath.unlink()


# ── _handle_import_from ───────────────────────────────────────────────────────

class TestHandleImportFrom:
    def _make_parser(self):
        from navegador.ingestion.python import PythonParser
        with patch("navegador.ingestion.python._get_parser") as mock_get:
            mock_get.return_value = MagicMock()
            parser = PythonParser()
        return parser

    def test_handle_import_from_with_member(self):
        parser = self._make_parser()
        store = MagicMock()
        stats = {"functions": 0, "classes": 0, "edges": 0}
        combined = b"os.pathjoin"
        module_node3 = MockNode("dotted_name", start_byte=0, end_byte=7)
        member_node3 = MockNode("import_from_member", start_byte=7, end_byte=11)
        node3 = MockNode("import_from_statement",
                         children=[module_node3, member_node3],
                         start_point=(0, 0))
        parser._handle_import_from(node3, combined, "app.py", store, stats)
        store.create_node.assert_called_once()
        store.create_edge.assert_called_once()
        assert stats["edges"] == 1

    def test_handle_import_from_no_member(self):
        parser = self._make_parser()
        store = MagicMock()
        # No import_from_member children — nothing should be created
        module_node = MockNode("dotted_name", start_byte=0, end_byte=7)
        node = MockNode("import_from_statement",
                        children=[module_node],
                        start_point=(0, 0))
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_import_from(node, b"os.path", "app.py", store, stats)
        store.create_node.assert_not_called()
        assert stats["edges"] == 0

    def test_walk_dispatches_import_from(self):
        parser = self._make_parser()
        store = MagicMock()
        source = b"os.pathjoin"
        module_node = MockNode("dotted_name", start_byte=0, end_byte=7)
        member_node = MockNode("import_from_member", start_byte=7, end_byte=11)
        import_from = MockNode("import_from_statement",
                               children=[module_node, member_node],
                               start_point=(0, 0))
        root = MockNode("module", children=[import_from])
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._walk(root, source, "app.py", store, stats, class_name=None)
        assert stats["edges"] == 1


# ── _handle_class with body ───────────────────────────────────────────────────

class TestHandleClassWithBody:
    def _make_parser(self):
        from navegador.ingestion.python import PythonParser
        with patch("navegador.ingestion.python._get_parser") as mock_get:
            mock_get.return_value = MagicMock()
            parser = PythonParser()
        return parser

    def test_handle_class_with_method_in_body(self):
        parser = self._make_parser()
        store = MagicMock()
        source = b"method"
        name_node = MockNode("identifier", start_byte=0, end_byte=5)
        # Method inside the class body
        method_name = MockNode("identifier", start_byte=0, end_byte=6)
        fn_node = MockNode("function_definition",
                           children=[method_name],
                           start_point=(1, 4), end_point=(1, 20))
        body = MockNode("block", children=[fn_node])
        class_node = MockNode("class_definition",
                              children=[name_node, body],
                              start_point=(0, 0), end_point=(2, 0))
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_class(class_node, source, "app.py", store, stats)
        # class node + method node both created
        assert stats["classes"] == 1
        assert stats["functions"] == 1
