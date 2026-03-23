"""Tests for navegador.ingestion.typescript — TypeScriptParser internal methods."""

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


def _make_parser(language: str = "typescript"):
    from navegador.ingestion.typescript import TypeScriptParser
    parser = TypeScriptParser.__new__(TypeScriptParser)
    parser._parser = MagicMock()
    parser._language = language
    return parser


class TestTsGetLanguage:
    def test_raises_when_ts_not_installed(self):
        from navegador.ingestion.typescript import _get_ts_language
        with patch.dict("sys.modules", {"tree_sitter_typescript": None, "tree_sitter": None}):
            with pytest.raises(ImportError, match="tree-sitter-typescript"):
                _get_ts_language("typescript")

    def test_raises_when_js_not_installed(self):
        from navegador.ingestion.typescript import _get_ts_language
        with patch.dict("sys.modules", {"tree_sitter_javascript": None, "tree_sitter": None}):
            with pytest.raises(ImportError, match="tree-sitter-javascript"):
                _get_ts_language("javascript")


class TestTsNodeText:
    def test_extracts_text(self):
        from navegador.ingestion.typescript import _node_text
        source = b"class Foo {}"
        node = MockNode("type_identifier", start_byte=6, end_byte=9)
        assert _node_text(node, source) == "Foo"


class TestTsJsdoc:
    def test_extracts_jsdoc(self):
        from navegador.ingestion.typescript import _jsdoc
        source = b"/** My class */\nclass Foo {}"
        comment = MockNode("comment", start_byte=0, end_byte=15)
        cls_node = MockNode("class_declaration", start_byte=16, end_byte=28)
        MockNode("program", children=[comment, cls_node])
        result = _jsdoc(cls_node, source)
        assert "My class" in result

    def test_ignores_single_line_comment(self):
        from navegador.ingestion.typescript import _jsdoc
        source = b"// not jsdoc\nclass Foo {}"
        comment = MockNode("comment", start_byte=0, end_byte=12)
        cls_node = MockNode("class_declaration", start_byte=13, end_byte=25)
        MockNode("program", children=[comment, cls_node])
        result = _jsdoc(cls_node, source)
        assert result == ""


class TestTsHandleClass:
    def test_creates_class_node(self):
        parser = _make_parser()
        store = _make_store()
        source = b"class Foo {}"
        name_node = _text_node(b"Foo", "type_identifier")
        body = MockNode("class_body")
        node = MockNode("class_declaration",
                        children=[name_node, body],
                        start_point=(0, 0), end_point=(0, 11))
        MockNode("program", children=[node])
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_class(node, source, "app.ts", store, stats)
        assert stats["classes"] == 1
        label = store.create_node.call_args[0][0]
        assert label == NodeLabel.Class

    def test_creates_inherits_edge(self):
        parser = _make_parser()
        store = _make_store()
        source = b"class Child extends Parent {}"
        name_node = _text_node(b"Child", "type_identifier")
        parent_id = _text_node(b"Parent")
        extends = MockNode("extends_clause", children=[parent_id])
        heritage = MockNode("class_heritage", children=[extends])
        body = MockNode("class_body")
        node = MockNode("class_declaration",
                        children=[name_node, heritage, body],
                        start_point=(0, 0), end_point=(0, 28))
        MockNode("program", children=[node])
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_class(node, source, "app.ts", store, stats)
        assert stats["edges"] == 2  # CONTAINS + INHERITS

    def test_ingests_methods_in_body(self):
        parser = _make_parser()
        store = _make_store()
        source = b"class Foo { bar() {} }"
        name_node = _text_node(b"Foo", "type_identifier")
        method_name = _text_node(b"bar", "property_identifier")
        method_body = MockNode("statement_block")
        method = MockNode("method_definition",
                          children=[method_name, method_body],
                          start_point=(1, 2), end_point=(1, 9))
        body = MockNode("class_body", children=[method])
        node = MockNode("class_declaration",
                        children=[name_node, body],
                        start_point=(0, 0), end_point=(0, 21))
        MockNode("program", children=[node])
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_class(node, source, "app.ts", store, stats)
        assert stats["functions"] == 1

    def test_skips_if_no_name(self):
        parser = _make_parser()
        store = _make_store()
        node = MockNode("class_declaration", children=[])
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_class(node, b"", "app.ts", store, stats)
        assert stats["classes"] == 0


class TestTsHandleInterface:
    def test_creates_interface_node(self):
        parser = _make_parser()
        store = _make_store()
        source = b"interface IFoo {}"
        name_node = _text_node(b"IFoo", "type_identifier")
        node = MockNode("interface_declaration",
                        children=[name_node],
                        start_point=(0, 0), end_point=(0, 16))
        MockNode("program", children=[node])
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_interface(node, source, "app.ts", store, stats)
        assert stats["classes"] == 1
        props = store.create_node.call_args[0][1]
        assert "interface" in props.get("docstring", "")

    def test_skips_if_no_name(self):
        parser = _make_parser()
        store = _make_store()
        node = MockNode("interface_declaration", children=[])
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_interface(node, b"", "app.ts", store, stats)
        assert stats["classes"] == 0


class TestTsHandleFunction:
    def test_creates_function_node(self):
        parser = _make_parser()
        store = _make_store()
        source = b"function foo() {}"
        name_node = _text_node(b"foo")
        body = MockNode("statement_block")
        node = MockNode("function_declaration",
                        children=[name_node, body],
                        start_point=(0, 0), end_point=(0, 16))
        MockNode("program", children=[node])
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_function(node, source, "app.ts", store, stats, class_name=None)
        assert stats["functions"] == 1
        label = store.create_node.call_args[0][0]
        assert label == NodeLabel.Function

    def test_creates_method_when_class_name_given(self):
        parser = _make_parser()
        store = _make_store()
        source = b"foo() {}"
        name_node = _text_node(b"foo", "property_identifier")
        body = MockNode("statement_block")
        node = MockNode("method_definition",
                        children=[name_node, body],
                        start_point=(0, 0), end_point=(0, 7))
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_function(node, source, "app.ts", store, stats, class_name="Bar")
        label = store.create_node.call_args[0][0]
        assert label == NodeLabel.Method

    def test_skips_if_no_name(self):
        parser = _make_parser()
        store = _make_store()
        node = MockNode("function_declaration", children=[])
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_function(node, b"", "app.ts", store, stats, class_name=None)
        assert stats["functions"] == 0


class TestTsHandleLexical:
    def test_ingests_const_arrow_function(self):
        parser = _make_parser()
        store = _make_store()
        source = b"const foo = () => {}"
        name_node = _text_node(b"foo")
        arrow = MockNode("arrow_function", start_point=(1, 0), end_point=(1, 7))
        declarator = MockNode("variable_declarator")
        declarator.set_field("name", name_node)
        declarator.set_field("value", arrow)
        node = MockNode("lexical_declaration",
                        children=[declarator],
                        start_point=(0, 0), end_point=(0, 19))
        MockNode("program", children=[node])
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_lexical(node, source, "app.ts", store, stats)
        assert stats["functions"] == 1

    def test_ingests_const_function_expression(self):
        parser = _make_parser()
        store = _make_store()
        source = b"const bar = function() {}"
        name_node = _text_node(b"bar")
        fn_expr = MockNode("function_expression")
        declarator = MockNode("variable_declarator")
        declarator.set_field("name", name_node)
        declarator.set_field("value", fn_expr)
        node = MockNode("lexical_declaration",
                        children=[declarator],
                        start_point=(0, 0), end_point=(0, 24))
        MockNode("program", children=[node])
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_lexical(node, source, "app.ts", store, stats)
        assert stats["functions"] == 1

    def test_skips_non_function_declarations(self):
        parser = _make_parser()
        store = _make_store()
        source = b"const x = 42"
        name_node = _text_node(b"x")
        value = MockNode("number")
        declarator = MockNode("variable_declarator")
        declarator.set_field("name", name_node)
        declarator.set_field("value", value)
        node = MockNode("lexical_declaration", children=[declarator])
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_lexical(node, source, "app.ts", store, stats)
        assert stats["functions"] == 0


class TestTsHandleImport:
    def test_ingests_import_statement(self):
        parser = _make_parser()
        store = _make_store()
        source = b"import { foo } from 'bar'"
        string_node = MockNode("string", start_byte=20, end_byte=25)
        node = MockNode("import_statement",
                        children=[string_node],
                        start_point=(0, 0))
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_import(node, source, "app.ts", store, stats)
        assert stats["edges"] == 1


class TestTsExtractCalls:
    def test_extracts_call(self):
        parser = _make_parser()
        store = _make_store()
        source = b"bar"
        callee = _text_node(b"bar")
        call_node = MockNode("call_expression")
        call_node.set_field("function", callee)
        body = MockNode("statement_block", children=[call_node])
        fn_node = MockNode("function_declaration", children=[body])
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._extract_calls(fn_node, source, "app.ts", "foo",
                              NodeLabel.Function, store, stats)
        assert stats["edges"] == 1
        edge_call = store.create_edge.call_args[0]
        assert edge_call[4]["name"] == "bar"

    def test_no_calls_in_empty_body(self):
        parser = _make_parser()
        store = _make_store()
        fn_node = MockNode("function_declaration",
                           children=[MockNode("statement_block")])
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._extract_calls(fn_node, b"", "app.ts", "foo",
                              NodeLabel.Function, store, stats)
        assert stats["edges"] == 0


# ── _get_ts_language happy paths ──────────────────────────────────────────────

class TestTsGetLanguageHappyPath:
    def test_returns_typescript_language(self):
        from navegador.ingestion.typescript import _get_ts_language
        mock_tsts = MagicMock()
        mock_ts = MagicMock()
        with patch.dict("sys.modules", {
            "tree_sitter_typescript": mock_tsts,
            "tree_sitter": mock_ts,
        }):
            result = _get_ts_language("typescript")
        assert result is mock_ts.Language.return_value

    def test_returns_javascript_language(self):
        from navegador.ingestion.typescript import _get_ts_language
        mock_tsjs = MagicMock()
        mock_ts = MagicMock()
        with patch.dict("sys.modules", {
            "tree_sitter_javascript": mock_tsjs,
            "tree_sitter": mock_ts,
        }):
            result = _get_ts_language("javascript")
        assert result is mock_ts.Language.return_value


# ── TypeScriptParser init and parse_file ─────────────────────────────────────

class TestTsParserInit:
    def test_init_creates_parser(self):
        mock_tsts = MagicMock()
        mock_ts = MagicMock()
        with patch.dict("sys.modules", {
            "tree_sitter_typescript": mock_tsts,
            "tree_sitter": mock_ts,
        }):
            from navegador.ingestion.typescript import TypeScriptParser
            parser = TypeScriptParser("typescript")
        assert parser._parser is mock_ts.Parser.return_value
        assert parser._language == "typescript"

    def test_parse_file_creates_file_node(self):
        import tempfile
        from pathlib import Path

        from navegador.graph.schema import NodeLabel
        parser = _make_parser("typescript")
        store = _make_store()
        mock_tree = MagicMock()
        mock_tree.root_node.type = "program"
        mock_tree.root_node.children = []
        parser._parser.parse.return_value = mock_tree
        with tempfile.NamedTemporaryFile(suffix=".ts", delete=False) as f:
            f.write(b"const x = 1;\n")
            fpath = Path(f.name)
        try:
            parser.parse_file(fpath, fpath.parent, store)
            store.create_node.assert_called_once()
            assert store.create_node.call_args[0][0] == NodeLabel.File
            assert store.create_node.call_args[0][1]["language"] == "typescript"
        finally:
            fpath.unlink()


# ── _walk dispatch ────────────────────────────────────────────────────────────

class TestTsWalkDispatch:
    def test_walk_handles_class_declaration(self):
        parser = _make_parser()
        store = _make_store()
        source = b"MyClass"
        name = MockNode("type_identifier", start_byte=0, end_byte=7)
        body = MockNode("class_body", children=[])
        node = MockNode("class_declaration", start_point=(0, 0), end_point=(0, 20))
        node.children = [name, body]
        root = MockNode("program", children=[node])
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._walk(root, source, "app.ts", store, stats, class_name=None)
        assert stats["classes"] == 1

    def test_walk_handles_interface_declaration(self):
        parser = _make_parser()
        store = _make_store()
        source = b"MyInterface"
        name = MockNode("type_identifier", start_byte=0, end_byte=11)
        node = MockNode("interface_declaration", start_point=(0, 0), end_point=(0, 25))
        node.children = [name]
        root = MockNode("program", children=[node])
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._walk(root, source, "app.ts", store, stats, class_name=None)
        assert stats["classes"] == 1

    def test_walk_handles_function_declaration(self):
        parser = _make_parser()
        store = _make_store()
        source = b"myFn"
        name = MockNode("identifier", start_byte=0, end_byte=4)
        node = MockNode("function_declaration", start_point=(0, 0), end_point=(0, 20))
        node.children = [name]
        root = MockNode("program", children=[node])
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._walk(root, source, "app.ts", store, stats, class_name=None)
        assert stats["functions"] == 1

    def test_walk_handles_lexical_declaration(self):
        parser = _make_parser()
        store = _make_store()
        source = b"arrowFn"
        name_node = MockNode("identifier", start_byte=0, end_byte=7)
        arrow = MockNode("arrow_function")
        declarator = MockNode("variable_declarator")
        declarator.set_field("name", name_node)
        declarator.set_field("value", arrow)
        node = MockNode("lexical_declaration", start_point=(0, 0), end_point=(0, 30))
        node.children = [declarator]
        root = MockNode("program", children=[node])
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._walk(root, source, "app.ts", store, stats, class_name=None)
        assert stats["functions"] == 1

    def test_walk_handles_import_statement(self):
        parser = _make_parser()
        store = _make_store()
        source = b'"./utils"'
        str_node = MockNode("string", start_byte=0, end_byte=9)
        node = MockNode("import_statement", start_point=(0, 0), end_point=(0, 25))
        node.children = [str_node]
        root = MockNode("program", children=[node])
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._walk(root, source, "app.ts", store, stats, class_name=None)
        assert stats["edges"] == 1

    def test_walk_handles_export_statement(self):
        parser = _make_parser()
        store = _make_store()
        source = b"myFn"
        name = MockNode("identifier", start_byte=0, end_byte=4)
        fn_decl = MockNode("function_declaration", start_point=(0, 0), end_point=(0, 20))
        fn_decl.children = [name]
        export_node = MockNode("export_statement")
        export_node.children = [MockNode("export"), fn_decl]
        root = MockNode("program", children=[export_node])
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._walk(root, source, "app.ts", store, stats, class_name=None)
        assert stats["functions"] == 1

    def test_walk_recurses_into_children(self):
        parser = _make_parser()
        store = _make_store()
        source = b"myFn"
        name = MockNode("identifier", start_byte=0, end_byte=4)
        fn = MockNode("function_declaration", start_point=(0, 0), end_point=(0, 20))
        fn.children = [name]
        wrapper = MockNode("unknown_node", children=[fn])
        root = MockNode("program", children=[wrapper])
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._walk(root, source, "app.ts", store, stats, class_name=None)
        assert stats["functions"] == 1


# ── _handle_function keyword name with no follow-up identifier ────────────────

class TestTsHandleFunctionKeywordThenNone:
    def test_skips_when_keyword_name_has_no_second_identifier(self):
        parser = _make_parser()
        store = _make_store()
        source = b"constructor"
        # Node whose only identifier child is the keyword "constructor"
        keyword_name = MockNode("property_identifier", start_byte=0, end_byte=11)
        node = MockNode("method_definition", start_point=(0, 0), end_point=(0, 20))
        node.children = [keyword_name]
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_function(node, source, "app.ts", store, stats, class_name=None)
        # "constructor" is a keyword — looks for next identifier, finds none → skips
        assert stats["functions"] == 0


class TestTsHandleFunctionKeywordWithSuccessor:
    def test_uses_second_identifier_when_first_is_keyword(self):
        parser = _make_parser()
        store = _make_store()
        source = b"get foo"
        keyword_name = MockNode("identifier", start_byte=0, end_byte=3)
        real_name = MockNode("identifier", start_byte=4, end_byte=7)
        node = MockNode("method_definition", start_point=(0, 0), end_point=(0, 10))
        node.children = [keyword_name, real_name]
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_function(node, source, "app.ts", store, stats, class_name=None)
        # "get" is a keyword → picks next identifier "foo"
        assert stats["functions"] == 1
        props = store.create_node.call_args[0][1]
        assert props["name"] == "foo"


class TestTsWalkMethodDefinition:
    def test_walk_dispatches_method_definition(self):
        parser = _make_parser()
        store = _make_store()
        source = b"get foo"
        keyword_name = MockNode("identifier", start_byte=0, end_byte=3)
        real_name = MockNode("identifier", start_byte=4, end_byte=7)
        node = MockNode("method_definition", start_point=(0, 0), end_point=(0, 10))
        node.children = [keyword_name, real_name]
        root = MockNode("program", children=[node])
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._walk(root, source, "app.ts", store, stats, class_name=None)
        assert stats["functions"] == 1


class TestTsHandleLexicalContinueBranches:
    def test_skips_non_variable_declarator_children(self):
        parser = _make_parser()
        store = _make_store()
        source = b"const"
        # Child is not a variable_declarator
        other_child = MockNode("identifier", start_byte=0, end_byte=5)
        node = MockNode("lexical_declaration", start_point=(0, 0), end_point=(0, 10))
        node.children = [other_child]
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_lexical(node, source, "app.ts", store, stats)
        assert stats["functions"] == 0

    def test_skips_declarator_without_value_node(self):
        parser = _make_parser()
        store = _make_store()
        source = b"x"
        name_node = MockNode("identifier", start_byte=0, end_byte=1)
        # declarator with a name but no value field
        declarator = MockNode("variable_declarator")
        declarator._fields["name"] = name_node
        # no "value" field set
        node = MockNode("lexical_declaration", start_point=(0, 0), end_point=(0, 5))
        node.children = [declarator]
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_lexical(node, source, "app.ts", store, stats)
        assert stats["functions"] == 0
