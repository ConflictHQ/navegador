"""
Tests for the 7 new language parsers:
  KotlinParser, CSharpParser, PHPParser, RubyParser, SwiftParser, CParser, CppParser

All tree-sitter grammar imports are mocked so no grammars need to be installed.
"""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from navegador.graph.schema import NodeLabel
from navegador.ingestion.parser import LANGUAGE_MAP, RepoIngester


# ── Shared helpers ────────────────────────────────────────────────────────────


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


def _make_mock_tree(root_node: MockNode):
    tree = MagicMock()
    tree.root_node = root_node
    return tree


def _mock_ts_modules(lang_module_name: str):
    """Return a patch.dict context that mocks tree_sitter and the given grammar module."""
    mock_lang_module = MagicMock()
    mock_ts = MagicMock()
    return patch.dict("sys.modules", {lang_module_name: mock_lang_module, "tree_sitter": mock_ts})


# ── LANGUAGE_MAP coverage ─────────────────────────────────────────────────────


class TestLanguageMapExtensions:
    def test_kotlin_kt(self):
        assert LANGUAGE_MAP[".kt"] == "kotlin"

    def test_kotlin_kts(self):
        assert LANGUAGE_MAP[".kts"] == "kotlin"

    def test_csharp_cs(self):
        assert LANGUAGE_MAP[".cs"] == "csharp"

    def test_php(self):
        assert LANGUAGE_MAP[".php"] == "php"

    def test_ruby_rb(self):
        assert LANGUAGE_MAP[".rb"] == "ruby"

    def test_swift(self):
        assert LANGUAGE_MAP[".swift"] == "swift"

    def test_c_c(self):
        assert LANGUAGE_MAP[".c"] == "c"

    def test_c_h(self):
        assert LANGUAGE_MAP[".h"] == "c"

    def test_cpp_cpp(self):
        assert LANGUAGE_MAP[".cpp"] == "cpp"

    def test_cpp_hpp(self):
        assert LANGUAGE_MAP[".hpp"] == "cpp"

    def test_cpp_cc(self):
        assert LANGUAGE_MAP[".cc"] == "cpp"

    def test_cpp_cxx(self):
        assert LANGUAGE_MAP[".cxx"] == "cpp"


# ── _get_parser dispatch ──────────────────────────────────────────────────────


class TestGetParserDispatch:
    def _make_ingester(self):
        store = _make_store()
        ingester = RepoIngester.__new__(RepoIngester)
        ingester.store = store
        ingester.redact = False
        ingester._detector = None
        ingester._parsers = {}
        return ingester

    def _test_parser_type(self, language: str, grammar_module: str, parser_cls_name: str):
        ingester = self._make_ingester()
        with _mock_ts_modules(grammar_module):
            # Also need to force re-import of the parser module
            import sys
            mod_name = f"navegador.ingestion.{language}"
            if mod_name in sys.modules:
                del sys.modules[mod_name]
            parser = ingester._get_parser(language)
        assert type(parser).__name__ == parser_cls_name

    def test_kotlin_parser(self):
        self._test_parser_type("kotlin", "tree_sitter_kotlin", "KotlinParser")

    def test_csharp_parser(self):
        self._test_parser_type("csharp", "tree_sitter_c_sharp", "CSharpParser")

    def test_php_parser(self):
        self._test_parser_type("php", "tree_sitter_php", "PHPParser")

    def test_ruby_parser(self):
        self._test_parser_type("ruby", "tree_sitter_ruby", "RubyParser")

    def test_swift_parser(self):
        self._test_parser_type("swift", "tree_sitter_swift", "SwiftParser")

    def test_c_parser(self):
        self._test_parser_type("c", "tree_sitter_c", "CParser")

    def test_cpp_parser(self):
        self._test_parser_type("cpp", "tree_sitter_cpp", "CppParser")


# ── _get_*_language ImportError ────────────────────────────────────────────────


class TestMissingGrammars:
    def _assert_import_error(self, module_path: str, fn_name: str, grammar_pkg: str, grammar_module: str):
        import importlib
        import sys
        # Remove cached module if present
        if module_path in sys.modules:
            del sys.modules[module_path]
        with patch.dict("sys.modules", {grammar_module: None, "tree_sitter": None}):
            mod = importlib.import_module(module_path)
            fn = getattr(mod, fn_name)
            with pytest.raises(ImportError, match=grammar_pkg):
                fn()

    def test_kotlin_missing(self):
        self._assert_import_error(
            "navegador.ingestion.kotlin", "_get_kotlin_language",
            "tree-sitter-kotlin", "tree_sitter_kotlin",
        )

    def test_csharp_missing(self):
        self._assert_import_error(
            "navegador.ingestion.csharp", "_get_csharp_language",
            "tree-sitter-c-sharp", "tree_sitter_c_sharp",
        )

    def test_php_missing(self):
        self._assert_import_error(
            "navegador.ingestion.php", "_get_php_language",
            "tree-sitter-php", "tree_sitter_php",
        )

    def test_ruby_missing(self):
        self._assert_import_error(
            "navegador.ingestion.ruby", "_get_ruby_language",
            "tree-sitter-ruby", "tree_sitter_ruby",
        )

    def test_swift_missing(self):
        self._assert_import_error(
            "navegador.ingestion.swift", "_get_swift_language",
            "tree-sitter-swift", "tree_sitter_swift",
        )

    def test_c_missing(self):
        self._assert_import_error(
            "navegador.ingestion.c", "_get_c_language",
            "tree-sitter-c", "tree_sitter_c",
        )

    def test_cpp_missing(self):
        self._assert_import_error(
            "navegador.ingestion.cpp", "_get_cpp_language",
            "tree-sitter-cpp", "tree_sitter_cpp",
        )


# ── KotlinParser ──────────────────────────────────────────────────────────────


def _make_kotlin_parser():
    from navegador.ingestion.kotlin import KotlinParser
    p = KotlinParser.__new__(KotlinParser)
    p._parser = MagicMock()
    return p


class TestKotlinParserFileNode:
    def test_parse_file_creates_file_node(self):
        parser = _make_kotlin_parser()
        store = _make_store()
        root = MockNode("source_file")
        parser._parser.parse.return_value = _make_mock_tree(root)
        with tempfile.NamedTemporaryFile(suffix=".kt", delete=False) as f:
            f.write(b"fun main() {}\n")
            fpath = Path(f.name)
        try:
            parser.parse_file(fpath, fpath.parent, store)
            assert store.create_node.call_args[0][0] == NodeLabel.File
            assert store.create_node.call_args[0][1]["language"] == "kotlin"
        finally:
            fpath.unlink()


class TestKotlinHandleClass:
    def test_creates_class_node(self):
        parser = _make_kotlin_parser()
        store = _make_store()
        source = b"class Foo {}"
        name_node = _text_node(b"Foo", "simple_identifier")
        body = MockNode("class_body")
        node = MockNode("class_declaration", start_point=(0, 0), end_point=(0, 11))
        node.set_field("name", name_node)
        node.set_field("body", body)
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_class(node, source, "Foo.kt", store, stats)
        assert stats["classes"] == 1
        assert store.create_node.call_args[0][0] == NodeLabel.Class

    def test_skips_if_no_name(self):
        parser = _make_kotlin_parser()
        store = _make_store()
        node = MockNode("class_declaration")
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_class(node, b"", "Foo.kt", store, stats)
        assert stats["classes"] == 0

    def test_walks_member_functions(self):
        parser = _make_kotlin_parser()
        store = _make_store()
        source = b"class Foo { fun bar() {} }"
        class_name = _text_node(b"Foo", "simple_identifier")
        fn_name = _text_node(b"bar", "simple_identifier")
        fn_node = MockNode("function_declaration", start_point=(0, 12), end_point=(0, 24))
        fn_node.set_field("name", fn_name)
        body = MockNode("class_body", children=[fn_node])
        node = MockNode("class_declaration", start_point=(0, 0), end_point=(0, 25))
        node.set_field("name", class_name)
        node.set_field("body", body)
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_class(node, source, "Foo.kt", store, stats)
        assert stats["classes"] == 1
        assert stats["functions"] == 1


class TestKotlinHandleFunction:
    def test_creates_function_node(self):
        parser = _make_kotlin_parser()
        store = _make_store()
        source = b"fun greet() {}"
        name_node = _text_node(b"greet", "simple_identifier")
        node = MockNode("function_declaration", start_point=(0, 0), end_point=(0, 13))
        node.set_field("name", name_node)
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_function(node, source, "Foo.kt", store, stats, class_name=None)
        assert stats["functions"] == 1
        assert store.create_node.call_args[0][0] == NodeLabel.Function

    def test_creates_method_node_in_class(self):
        parser = _make_kotlin_parser()
        store = _make_store()
        source = b"fun run() {}"
        name_node = _text_node(b"run", "simple_identifier")
        node = MockNode("function_declaration", start_point=(0, 0), end_point=(0, 11))
        node.set_field("name", name_node)
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_function(node, source, "Foo.kt", store, stats, class_name="Foo")
        assert store.create_node.call_args[0][0] == NodeLabel.Method

    def test_skips_if_no_name(self):
        parser = _make_kotlin_parser()
        store = _make_store()
        node = MockNode("function_declaration")
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_function(node, b"", "Foo.kt", store, stats, class_name=None)
        assert stats["functions"] == 0


class TestKotlinHandleImport:
    def test_creates_import_node(self):
        parser = _make_kotlin_parser()
        store = _make_store()
        source = b"import kotlin.collections.List"
        node = MockNode("import_header", start_byte=0, end_byte=len(source), start_point=(0, 0))
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_import(node, source, "Foo.kt", store, stats)
        assert stats["edges"] == 1
        props = store.create_node.call_args[0][1]
        assert "kotlin.collections.List" in props["name"]


# ── CSharpParser ───────────────────────────────────────────────────────────────


def _make_csharp_parser():
    from navegador.ingestion.csharp import CSharpParser
    p = CSharpParser.__new__(CSharpParser)
    p._parser = MagicMock()
    return p


class TestCSharpParserFileNode:
    def test_parse_file_creates_file_node(self):
        parser = _make_csharp_parser()
        store = _make_store()
        root = MockNode("compilation_unit")
        parser._parser.parse.return_value = _make_mock_tree(root)
        with tempfile.NamedTemporaryFile(suffix=".cs", delete=False) as f:
            f.write(b"class Foo {}\n")
            fpath = Path(f.name)
        try:
            parser.parse_file(fpath, fpath.parent, store)
            assert store.create_node.call_args[0][0] == NodeLabel.File
            assert store.create_node.call_args[0][1]["language"] == "csharp"
        finally:
            fpath.unlink()


class TestCSharpHandleClass:
    def test_creates_class_node(self):
        parser = _make_csharp_parser()
        store = _make_store()
        source = b"class Foo {}"
        name_node = _text_node(b"Foo")
        body = MockNode("declaration_list")
        node = MockNode("class_declaration", start_point=(0, 0), end_point=(0, 11))
        node.set_field("name", name_node)
        node.set_field("body", body)
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_class(node, source, "Foo.cs", store, stats)
        assert stats["classes"] == 1
        assert store.create_node.call_args[0][0] == NodeLabel.Class

    def test_skips_if_no_name(self):
        parser = _make_csharp_parser()
        store = _make_store()
        node = MockNode("class_declaration")
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_class(node, b"", "Foo.cs", store, stats)
        assert stats["classes"] == 0

    def test_creates_inherits_edge(self):
        parser = _make_csharp_parser()
        store = _make_store()
        source = b"class Child : Parent {}"
        name_node = _text_node(b"Child")
        parent_id = _text_node(b"Parent")
        bases = MockNode("base_list", children=[parent_id])
        body = MockNode("declaration_list")
        node = MockNode("class_declaration", start_point=(0, 0), end_point=(0, 22))
        node.set_field("name", name_node)
        node.set_field("bases", bases)
        node.set_field("body", body)
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_class(node, source, "Child.cs", store, stats)
        assert stats["edges"] == 2  # CONTAINS + INHERITS

    def test_walks_methods(self):
        parser = _make_csharp_parser()
        store = _make_store()
        source = b"class Foo { void Save() {} }"
        class_name_node = _text_node(b"Foo")
        method_name_node = _text_node(b"Save")
        method = MockNode("method_declaration", start_point=(0, 12), end_point=(0, 25))
        method.set_field("name", method_name_node)
        body = MockNode("declaration_list", children=[method])
        node = MockNode("class_declaration", start_point=(0, 0), end_point=(0, 27))
        node.set_field("name", class_name_node)
        node.set_field("body", body)
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_class(node, source, "Foo.cs", store, stats)
        assert stats["functions"] == 1


class TestCSharpHandleUsing:
    def test_creates_import_node(self):
        parser = _make_csharp_parser()
        store = _make_store()
        source = b"using System.Collections.Generic;"
        node = MockNode("using_directive", start_byte=0, end_byte=len(source), start_point=(0, 0))
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_using(node, source, "Foo.cs", store, stats)
        assert stats["edges"] == 1
        props = store.create_node.call_args[0][1]
        assert "System.Collections.Generic" in props["name"]


# ── PHPParser ─────────────────────────────────────────────────────────────────


def _make_php_parser():
    from navegador.ingestion.php import PHPParser
    p = PHPParser.__new__(PHPParser)
    p._parser = MagicMock()
    return p


class TestPHPParserFileNode:
    def test_parse_file_creates_file_node(self):
        parser = _make_php_parser()
        store = _make_store()
        root = MockNode("program")
        parser._parser.parse.return_value = _make_mock_tree(root)
        with tempfile.NamedTemporaryFile(suffix=".php", delete=False) as f:
            f.write(b"<?php class Foo {} ?>\n")
            fpath = Path(f.name)
        try:
            parser.parse_file(fpath, fpath.parent, store)
            assert store.create_node.call_args[0][0] == NodeLabel.File
            assert store.create_node.call_args[0][1]["language"] == "php"
        finally:
            fpath.unlink()


class TestPHPHandleClass:
    def test_creates_class_node(self):
        parser = _make_php_parser()
        store = _make_store()
        source = b"class Foo {}"
        name_node = _text_node(b"Foo", "name")
        body = MockNode("declaration_list")
        node = MockNode("class_declaration", start_point=(0, 0), end_point=(0, 11))
        node.set_field("name", name_node)
        node.set_field("body", body)
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_class(node, source, "Foo.php", store, stats)
        assert stats["classes"] == 1

    def test_skips_if_no_name(self):
        parser = _make_php_parser()
        store = _make_store()
        node = MockNode("class_declaration")
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_class(node, b"", "Foo.php", store, stats)
        assert stats["classes"] == 0

    def test_creates_inherits_edge(self):
        parser = _make_php_parser()
        store = _make_store()
        source = b"class Child extends Parent {}"
        name_node = _text_node(b"Child", "name")
        parent_name_node = _text_node(b"Parent", "qualified_name")
        base_clause = MockNode("base_clause", children=[parent_name_node])
        body = MockNode("declaration_list")
        node = MockNode("class_declaration", start_point=(0, 0), end_point=(0, 28))
        node.set_field("name", name_node)
        node.set_field("base_clause", base_clause)
        node.set_field("body", body)
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_class(node, source, "Child.php", store, stats)
        assert stats["edges"] == 2  # CONTAINS + INHERITS


class TestPHPHandleFunction:
    def test_creates_function_node(self):
        parser = _make_php_parser()
        store = _make_store()
        source = b"function save() {}"
        name_node = _text_node(b"save", "name")
        node = MockNode("function_definition", start_point=(0, 0), end_point=(0, 17))
        node.set_field("name", name_node)
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_function(node, source, "Foo.php", store, stats, class_name=None)
        assert stats["functions"] == 1
        assert store.create_node.call_args[0][0] == NodeLabel.Function

    def test_skips_if_no_name(self):
        parser = _make_php_parser()
        store = _make_store()
        node = MockNode("function_definition")
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_function(node, b"", "Foo.php", store, stats, class_name=None)
        assert stats["functions"] == 0


class TestPHPHandleUse:
    def test_creates_import_node(self):
        parser = _make_php_parser()
        store = _make_store()
        source = b"use App\\Http\\Controllers\\Controller;"
        node = MockNode("use_declaration", start_byte=0, end_byte=len(source), start_point=(0, 0))
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_use(node, source, "Foo.php", store, stats)
        assert stats["edges"] == 1
        props = store.create_node.call_args[0][1]
        assert "Controller" in props["name"]


# ── RubyParser ────────────────────────────────────────────────────────────────


def _make_ruby_parser():
    from navegador.ingestion.ruby import RubyParser
    p = RubyParser.__new__(RubyParser)
    p._parser = MagicMock()
    return p


class TestRubyParserFileNode:
    def test_parse_file_creates_file_node(self):
        parser = _make_ruby_parser()
        store = _make_store()
        root = MockNode("program")
        parser._parser.parse.return_value = _make_mock_tree(root)
        with tempfile.NamedTemporaryFile(suffix=".rb", delete=False) as f:
            f.write(b"class Foo; end\n")
            fpath = Path(f.name)
        try:
            parser.parse_file(fpath, fpath.parent, store)
            assert store.create_node.call_args[0][0] == NodeLabel.File
            assert store.create_node.call_args[0][1]["language"] == "ruby"
        finally:
            fpath.unlink()


class TestRubyHandleClass:
    def test_creates_class_node(self):
        parser = _make_ruby_parser()
        store = _make_store()
        source = b"class Foo; end"
        name_node = _text_node(b"Foo", "constant")
        body = MockNode("body_statement")
        node = MockNode("class", start_point=(0, 0), end_point=(0, 13))
        node.set_field("name", name_node)
        node.set_field("body", body)
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_class(node, source, "foo.rb", store, stats)
        assert stats["classes"] == 1

    def test_skips_if_no_name(self):
        parser = _make_ruby_parser()
        store = _make_store()
        node = MockNode("class")
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_class(node, b"", "foo.rb", store, stats)
        assert stats["classes"] == 0

    def test_creates_inherits_edge(self):
        parser = _make_ruby_parser()
        store = _make_store()
        source = b"class Child < Parent; end"
        name_node = _text_node(b"Child", "constant")
        superclass_node = _text_node(b"Parent", "constant")
        body = MockNode("body_statement")
        node = MockNode("class", start_point=(0, 0), end_point=(0, 24))
        node.set_field("name", name_node)
        node.set_field("superclass", superclass_node)
        node.set_field("body", body)
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_class(node, source, "child.rb", store, stats)
        assert stats["edges"] >= 2  # CONTAINS + INHERITS

    def test_walks_body_methods(self):
        parser = _make_ruby_parser()
        store = _make_store()
        source = b"class Foo; def run; end; end"
        class_name_node = _text_node(b"Foo", "constant")
        method_name_node = _text_node(b"run")
        method_node = MockNode("method", start_point=(0, 11), end_point=(0, 22))
        method_node.set_field("name", method_name_node)
        body = MockNode("body_statement", children=[method_node])
        node = MockNode("class", start_point=(0, 0), end_point=(0, 26))
        node.set_field("name", class_name_node)
        node.set_field("body", body)
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_class(node, source, "foo.rb", store, stats)
        assert stats["functions"] == 1


class TestRubyHandleMethod:
    def test_creates_function_node(self):
        parser = _make_ruby_parser()
        store = _make_store()
        source = b"def run; end"
        name_node = _text_node(b"run")
        node = MockNode("method", start_point=(0, 0), end_point=(0, 11))
        node.set_field("name", name_node)
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_method(node, source, "foo.rb", store, stats, class_name=None)
        assert stats["functions"] == 1
        assert store.create_node.call_args[0][0] == NodeLabel.Function

    def test_creates_method_node_in_class(self):
        parser = _make_ruby_parser()
        store = _make_store()
        source = b"def run; end"
        name_node = _text_node(b"run")
        node = MockNode("method", start_point=(0, 0), end_point=(0, 11))
        node.set_field("name", name_node)
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_method(node, source, "foo.rb", store, stats, class_name="Foo")
        assert store.create_node.call_args[0][0] == NodeLabel.Method

    def test_skips_if_no_name(self):
        parser = _make_ruby_parser()
        store = _make_store()
        node = MockNode("method")
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_method(node, b"", "foo.rb", store, stats, class_name=None)
        assert stats["functions"] == 0


class TestRubyHandleModule:
    def test_creates_module_node(self):
        parser = _make_ruby_parser()
        store = _make_store()
        source = b"module Concerns; end"
        name_node = _text_node(b"Concerns", "constant")
        body = MockNode("body_statement")
        node = MockNode("module", start_point=(0, 0), end_point=(0, 19))
        node.set_field("name", name_node)
        node.set_field("body", body)
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_module(node, source, "concerns.rb", store, stats)
        assert stats["classes"] == 1
        props = store.create_node.call_args[0][1]
        assert props.get("docstring") == "module"


# ── SwiftParser ───────────────────────────────────────────────────────────────


def _make_swift_parser():
    from navegador.ingestion.swift import SwiftParser
    p = SwiftParser.__new__(SwiftParser)
    p._parser = MagicMock()
    return p


class TestSwiftParserFileNode:
    def test_parse_file_creates_file_node(self):
        parser = _make_swift_parser()
        store = _make_store()
        root = MockNode("source_file")
        parser._parser.parse.return_value = _make_mock_tree(root)
        with tempfile.NamedTemporaryFile(suffix=".swift", delete=False) as f:
            f.write(b"class Foo {}\n")
            fpath = Path(f.name)
        try:
            parser.parse_file(fpath, fpath.parent, store)
            assert store.create_node.call_args[0][0] == NodeLabel.File
            assert store.create_node.call_args[0][1]["language"] == "swift"
        finally:
            fpath.unlink()


class TestSwiftHandleClass:
    def test_creates_class_node(self):
        parser = _make_swift_parser()
        store = _make_store()
        source = b"class Foo {}"
        name_node = _text_node(b"Foo", "type_identifier")
        body = MockNode("class_body")
        node = MockNode("class_declaration", start_point=(0, 0), end_point=(0, 11))
        node.set_field("name", name_node)
        node.set_field("body", body)
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_class(node, source, "Foo.swift", store, stats)
        assert stats["classes"] == 1

    def test_skips_if_no_name(self):
        parser = _make_swift_parser()
        store = _make_store()
        node = MockNode("class_declaration")
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_class(node, b"", "Foo.swift", store, stats)
        assert stats["classes"] == 0

    def test_creates_inherits_edge(self):
        parser = _make_swift_parser()
        store = _make_store()
        source = b"class Child: Parent {}"
        name_node = _text_node(b"Child", "type_identifier")
        parent_id = _text_node(b"Parent", "type_identifier")
        inheritance = MockNode("type_inheritance_clause", children=[parent_id])
        body = MockNode("class_body")
        node = MockNode("class_declaration", start_point=(0, 0), end_point=(0, 21))
        node.set_field("name", name_node)
        node.set_field("type_inheritance_clause", inheritance)
        node.set_field("body", body)
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_class(node, source, "Child.swift", store, stats)
        assert stats["edges"] == 2  # CONTAINS + INHERITS

    def test_walks_body_functions(self):
        parser = _make_swift_parser()
        store = _make_store()
        source = b"class Foo { func run() {} }"
        class_name = _text_node(b"Foo", "type_identifier")
        fn_name = _text_node(b"run", "simple_identifier")
        fn_node = MockNode("function_declaration", start_point=(0, 12), end_point=(0, 24))
        fn_node.set_field("name", fn_name)
        body = MockNode("class_body", children=[fn_node])
        node = MockNode("class_declaration", start_point=(0, 0), end_point=(0, 26))
        node.set_field("name", class_name)
        node.set_field("body", body)
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_class(node, source, "Foo.swift", store, stats)
        assert stats["functions"] == 1


class TestSwiftHandleImport:
    def test_creates_import_node(self):
        parser = _make_swift_parser()
        store = _make_store()
        source = b"import Foundation"
        node = MockNode("import_declaration", start_byte=0, end_byte=len(source), start_point=(0, 0))
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_import(node, source, "Foo.swift", store, stats)
        assert stats["edges"] == 1
        props = store.create_node.call_args[0][1]
        assert "Foundation" in props["name"]


# ── CParser ───────────────────────────────────────────────────────────────────


def _make_c_parser():
    from navegador.ingestion.c import CParser
    p = CParser.__new__(CParser)
    p._parser = MagicMock()
    return p


class TestCParserFileNode:
    def test_parse_file_creates_file_node(self):
        parser = _make_c_parser()
        store = _make_store()
        root = MockNode("translation_unit")
        parser._parser.parse.return_value = _make_mock_tree(root)
        with tempfile.NamedTemporaryFile(suffix=".c", delete=False) as f:
            f.write(b"void foo() {}\n")
            fpath = Path(f.name)
        try:
            parser.parse_file(fpath, fpath.parent, store)
            assert store.create_node.call_args[0][0] == NodeLabel.File
            assert store.create_node.call_args[0][1]["language"] == "c"
        finally:
            fpath.unlink()


class TestCHandleFunction:
    def _make_function_node(self, fn_name: bytes) -> tuple[MockNode, bytes]:
        source = fn_name + b"(void) {}"
        fn_id = _text_node(fn_name)
        fn_decl = MockNode("function_declarator")
        fn_decl.set_field("declarator", fn_id)
        body = MockNode("compound_statement")
        node = MockNode("function_definition", start_point=(0, 0), end_point=(0, len(source)))
        node.set_field("declarator", fn_decl)
        node.set_field("body", body)
        return node, source

    def test_creates_function_node(self):
        parser = _make_c_parser()
        store = _make_store()
        node, source = self._make_function_node(b"main")
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_function(node, source, "main.c", store, stats)
        assert stats["functions"] == 1
        assert store.create_node.call_args[0][0] == NodeLabel.Function

    def test_skips_if_no_name(self):
        parser = _make_c_parser()
        store = _make_store()
        node = MockNode("function_definition")
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_function(node, b"", "main.c", store, stats)
        assert stats["functions"] == 0


class TestCHandleStruct:
    def test_creates_struct_node(self):
        parser = _make_c_parser()
        store = _make_store()
        source = b"struct Point { int x; int y; };"
        name_node = _text_node(b"Point", "type_identifier")
        node = MockNode("struct_specifier", start_point=(0, 0), end_point=(0, 30))
        node.set_field("name", name_node)
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_struct(node, source, "point.c", store, stats)
        assert stats["classes"] == 1
        props = store.create_node.call_args[0][1]
        assert props["docstring"] == "struct"

    def test_skips_if_no_name(self):
        parser = _make_c_parser()
        store = _make_store()
        node = MockNode("struct_specifier")
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_struct(node, b"", "point.c", store, stats)
        assert stats["classes"] == 0


class TestCHandleInclude:
    def test_creates_import_node_angle_bracket(self):
        parser = _make_c_parser()
        store = _make_store()
        source = b"#include <stdio.h>"
        path_node = MockNode("system_lib_string", start_byte=9, end_byte=18)
        node = MockNode("preproc_include", start_byte=0, end_byte=18, start_point=(0, 0))
        node.set_field("path", path_node)
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_include(node, source, "main.c", store, stats)
        assert stats["edges"] == 1
        props = store.create_node.call_args[0][1]
        assert "stdio.h" in props["name"]

    def test_creates_import_node_quoted(self):
        parser = _make_c_parser()
        store = _make_store()
        source = b'#include "utils.h"'
        path_node = MockNode("string_literal", start_byte=9, end_byte=18)
        node = MockNode("preproc_include", start_byte=0, end_byte=18, start_point=(0, 0))
        node.set_field("path", path_node)
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_include(node, source, "main.c", store, stats)
        assert stats["edges"] == 1
        props = store.create_node.call_args[0][1]
        assert "utils.h" in props["name"]


# ── CppParser ─────────────────────────────────────────────────────────────────


def _make_cpp_parser():
    from navegador.ingestion.cpp import CppParser
    p = CppParser.__new__(CppParser)
    p._parser = MagicMock()
    return p


class TestCppParserFileNode:
    def test_parse_file_creates_file_node(self):
        parser = _make_cpp_parser()
        store = _make_store()
        root = MockNode("translation_unit")
        parser._parser.parse.return_value = _make_mock_tree(root)
        with tempfile.NamedTemporaryFile(suffix=".cpp", delete=False) as f:
            f.write(b"class Foo {};\n")
            fpath = Path(f.name)
        try:
            parser.parse_file(fpath, fpath.parent, store)
            assert store.create_node.call_args[0][0] == NodeLabel.File
            assert store.create_node.call_args[0][1]["language"] == "cpp"
        finally:
            fpath.unlink()


class TestCppHandleClass:
    def test_creates_class_node(self):
        parser = _make_cpp_parser()
        store = _make_store()
        source = b"class Foo {};"
        name_node = _text_node(b"Foo", "type_identifier")
        body = MockNode("field_declaration_list")
        node = MockNode("class_specifier", start_point=(0, 0), end_point=(0, 12))
        node.set_field("name", name_node)
        node.set_field("body", body)
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_class(node, source, "Foo.cpp", store, stats)
        assert stats["classes"] == 1

    def test_skips_if_no_name(self):
        parser = _make_cpp_parser()
        store = _make_store()
        node = MockNode("class_specifier")
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_class(node, b"", "Foo.cpp", store, stats)
        assert stats["classes"] == 0

    def test_creates_inherits_edge(self):
        parser = _make_cpp_parser()
        store = _make_store()
        source = b"class Child : public Parent {};"
        name_node = _text_node(b"Child", "type_identifier")
        parent_id = _text_node(b"Parent", "type_identifier")
        base_clause = MockNode("base_class_clause", children=[parent_id])
        body = MockNode("field_declaration_list")
        node = MockNode("class_specifier", start_point=(0, 0), end_point=(0, 30))
        node.set_field("name", name_node)
        node.set_field("base_clause", base_clause)
        node.set_field("body", body)
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_class(node, source, "Child.cpp", store, stats)
        assert stats["edges"] == 2  # CONTAINS + INHERITS

    def test_walks_member_functions(self):
        parser = _make_cpp_parser()
        store = _make_store()
        source = b"class Foo { void run() {} };"
        class_name = _text_node(b"Foo", "type_identifier")
        fn_id = _text_node(b"run")
        fn_decl = MockNode("function_declarator")
        fn_decl.set_field("declarator", fn_id)
        fn_body = MockNode("compound_statement")
        fn_node = MockNode("function_definition", start_point=(0, 12), end_point=(0, 24))
        fn_node.set_field("declarator", fn_decl)
        fn_node.set_field("body", fn_body)
        body = MockNode("field_declaration_list", children=[fn_node])
        node = MockNode("class_specifier", start_point=(0, 0), end_point=(0, 27))
        node.set_field("name", class_name)
        node.set_field("body", body)
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_class(node, source, "Foo.cpp", store, stats)
        assert stats["functions"] == 1


class TestCppHandleFunction:
    def test_creates_function_node(self):
        parser = _make_cpp_parser()
        store = _make_store()
        source = b"void main() {}"
        fn_id = _text_node(b"main")
        fn_decl = MockNode("function_declarator")
        fn_decl.set_field("declarator", fn_id)
        body = MockNode("compound_statement")
        node = MockNode("function_definition", start_point=(0, 0), end_point=(0, 13))
        node.set_field("declarator", fn_decl)
        node.set_field("body", body)
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_function(node, source, "main.cpp", store, stats, class_name=None)
        assert stats["functions"] == 1
        assert store.create_node.call_args[0][0] == NodeLabel.Function

    def test_creates_method_node_in_class(self):
        parser = _make_cpp_parser()
        store = _make_store()
        source = b"void run() {}"
        fn_id = _text_node(b"run")
        fn_decl = MockNode("function_declarator")
        fn_decl.set_field("declarator", fn_id)
        body = MockNode("compound_statement")
        node = MockNode("function_definition", start_point=(0, 0), end_point=(0, 12))
        node.set_field("declarator", fn_decl)
        node.set_field("body", body)
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_function(node, source, "Foo.cpp", store, stats, class_name="Foo")
        assert store.create_node.call_args[0][0] == NodeLabel.Method

    def test_skips_if_no_name(self):
        parser = _make_cpp_parser()
        store = _make_store()
        node = MockNode("function_definition")
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_function(node, b"", "main.cpp", store, stats, class_name=None)
        assert stats["functions"] == 0


class TestCppHandleInclude:
    def test_creates_import_node(self):
        parser = _make_cpp_parser()
        store = _make_store()
        source = b"#include <vector>"
        path_node = MockNode("system_lib_string", start_byte=9, end_byte=17)
        node = MockNode("preproc_include", start_byte=0, end_byte=17, start_point=(0, 0))
        node.set_field("path", path_node)
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_include(node, source, "Foo.cpp", store, stats)
        assert stats["edges"] == 1
        props = store.create_node.call_args[0][1]
        assert "vector" in props["name"]


class TestCppExtractFunctionName:
    def test_simple_identifier(self):
        from navegador.ingestion.cpp import CppParser
        parser = CppParser.__new__(CppParser)
        source = b"foo"
        node = _text_node(b"foo")
        assert parser._extract_function_name(node, source) == "foo"

    def test_function_declarator(self):
        from navegador.ingestion.cpp import CppParser
        parser = CppParser.__new__(CppParser)
        source = b"foo"
        fn_id = _text_node(b"foo")
        fn_decl = MockNode("function_declarator")
        fn_decl.set_field("declarator", fn_id)
        assert parser._extract_function_name(fn_decl, source) == "foo"

    def test_none_input(self):
        from navegador.ingestion.cpp import CppParser
        parser = CppParser.__new__(CppParser)
        assert parser._extract_function_name(None, b"") is None
