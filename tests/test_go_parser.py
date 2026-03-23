"""Tests for navegador.ingestion.go — GoParser internal methods."""

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
    from navegador.ingestion.go import GoParser
    parser = GoParser.__new__(GoParser)
    parser._parser = MagicMock()
    return parser


class TestGoGetLanguage:
    def test_raises_when_not_installed(self):
        from navegador.ingestion.go import _get_go_language
        with patch.dict("sys.modules", {"tree_sitter_go": None, "tree_sitter": None}):
            with pytest.raises(ImportError, match="tree-sitter-go"):
                _get_go_language()


class TestGoNodeText:
    def test_extracts_bytes(self):
        from navegador.ingestion.go import _node_text
        source = b"hello world"
        node = MockNode("identifier", start_byte=6, end_byte=11)
        assert _node_text(node, source) == "world"


class TestGoHandleFunction:
    def test_creates_function_node(self):
        parser = _make_parser()
        store = _make_store()
        source = b"func Foo() {}"
        name = _text_node(b"Foo")
        node = MockNode("function_declaration", start_point=(0, 0), end_point=(0, 12))
        node.set_field("name", name)
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_function(node, source, "main.go", store, stats, receiver=None)
        assert stats["functions"] == 1
        store.create_node.assert_called_once()
        label = store.create_node.call_args[0][0]
        assert label == NodeLabel.Function

    def test_creates_method_when_receiver_given(self):
        parser = _make_parser()
        store = _make_store()
        source = b"func (r *Repo) Save() {}"
        name = _text_node(b"Save")
        node = MockNode("method_declaration", start_point=(0, 0), end_point=(0, 23))
        node.set_field("name", name)
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_function(node, source, "repo.go", store, stats, receiver="Repo")
        label = store.create_node.call_args[0][0]
        assert label == NodeLabel.Method

    def test_skips_if_no_name_node(self):
        parser = _make_parser()
        store = _make_store()
        node = MockNode("function_declaration", start_point=(0, 0), end_point=(0, 0))
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_function(node, b"", "main.go", store, stats, receiver=None)
        assert stats["functions"] == 0


class TestGoHandleType:
    def test_ingests_struct(self):
        parser = _make_parser()
        store = _make_store()
        source = b"type User struct {}"
        name_node = _text_node(b"User", "type_identifier")
        type_node = MockNode("struct_type")
        spec = MockNode("type_spec")
        spec.set_field("name", name_node)
        spec.set_field("type", type_node)
        decl = MockNode("type_declaration", children=[spec],
                        start_point=(0, 0), end_point=(0, 18))
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_type(decl, source, "main.go", store, stats)
        assert stats["classes"] == 1
        assert stats["edges"] == 1

    def test_ingests_interface(self):
        parser = _make_parser()
        store = _make_store()
        source = b"type Reader interface {}"
        name_node = _text_node(b"Reader", "type_identifier")
        type_node = MockNode("interface_type")
        spec = MockNode("type_spec")
        spec.set_field("name", name_node)
        spec.set_field("type", type_node)
        decl = MockNode("type_declaration", children=[spec],
                        start_point=(0, 0), end_point=(0, 23))
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_type(decl, source, "main.go", store, stats)
        assert stats["classes"] == 1

    def test_skips_non_struct_interface(self):
        parser = _make_parser()
        store = _make_store()
        source = b"type MyInt int"
        name_node = _text_node(b"MyInt", "type_identifier")
        type_node = MockNode("int")
        spec = MockNode("type_spec")
        spec.set_field("name", name_node)
        spec.set_field("type", type_node)
        decl = MockNode("type_declaration", children=[spec],
                        start_point=(0, 0), end_point=(0, 13))
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_type(decl, source, "main.go", store, stats)
        assert stats["classes"] == 0


class TestGoImportSpec:
    def test_ingests_single_import(self):
        parser = _make_parser()
        store = _make_store()
        source = b'"fmt"'
        path_node = MockNode("interpreted_string_literal", start_byte=0, end_byte=5)
        spec = MockNode("import_spec")
        spec.set_field("path", path_node)
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._ingest_import_spec(spec, source, "main.go", 1, store, stats)
        assert stats["edges"] == 1
        store.create_node.assert_called_once()

    def test_skips_spec_without_path(self):
        parser = _make_parser()
        store = _make_store()
        spec = MockNode("import_spec")
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._ingest_import_spec(spec, b"", "main.go", 1, store, stats)
        assert stats["edges"] == 0


class TestGoExtractCalls:
    def test_extracts_call(self):
        parser = _make_parser()
        store = _make_store()
        source = b"bar"
        callee = _text_node(b"bar")
        call_node = MockNode("call_expression")
        call_node.set_field("function", callee)
        body = MockNode("block", children=[call_node])
        fn_node = MockNode("function_declaration")
        fn_node.set_field("body", body)
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._extract_calls(fn_node, source, "main.go", "foo",
                              NodeLabel.Function, store, stats)
        assert stats["edges"] == 1
        edge_call = store.create_edge.call_args[0]
        assert edge_call[4]["name"] == "bar"

    def test_no_calls_in_empty_body(self):
        parser = _make_parser()
        store = _make_store()
        fn_node = MockNode("function_declaration")
        fn_node.set_field("body", MockNode("block"))
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._extract_calls(fn_node, b"", "main.go", "foo",
                              NodeLabel.Function, store, stats)
        assert stats["edges"] == 0


class TestGoWalkDispatch:
    def test_walk_handles_function(self):
        parser = _make_parser()
        store = _make_store()
        source = b"func foo() {}"
        name = _text_node(b"foo")
        fn = MockNode("function_declaration", start_point=(0, 0), end_point=(0, 12))
        fn.set_field("name", name)
        root = MockNode("source_file", children=[fn])
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._walk(root, source, "main.go", store, stats)
        assert stats["functions"] == 1

    def test_walk_handles_type(self):
        parser = _make_parser()
        store = _make_store()
        source = b"type Foo struct {}"
        name_node = _text_node(b"Foo", "type_identifier")
        type_node = MockNode("struct_type")
        spec = MockNode("type_spec")
        spec.set_field("name", name_node)
        spec.set_field("type", type_node)
        decl = MockNode("type_declaration", children=[spec],
                        start_point=(0, 0), end_point=(0, 17))
        root = MockNode("source_file", children=[decl])
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._walk(root, source, "main.go", store, stats)
        assert stats["classes"] == 1


# ── _get_go_language happy path ───────────────────────────────────────────────

class TestGoGetLanguageHappyPath:
    def test_returns_language_object(self):
        from navegador.ingestion.go import _get_go_language
        mock_tsgo = MagicMock()
        mock_ts = MagicMock()
        with patch.dict("sys.modules", {
            "tree_sitter_go": mock_tsgo,
            "tree_sitter": mock_ts,
        }):
            result = _get_go_language()
        assert result is mock_ts.Language.return_value


# ── GoParser init and parse_file ─────────────────────────────────────────────

class TestGoParserInit:
    def test_init_creates_parser(self):
        mock_tsgo = MagicMock()
        mock_ts = MagicMock()
        with patch.dict("sys.modules", {
            "tree_sitter_go": mock_tsgo,
            "tree_sitter": mock_ts,
        }):
            from navegador.ingestion.go import GoParser
            parser = GoParser()
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
        with tempfile.NamedTemporaryFile(suffix=".go", delete=False) as f:
            f.write(b"package main\n")
            fpath = Path(f.name)
        try:
            parser.parse_file(fpath, fpath.parent, store)
            store.create_node.assert_called_once()
            assert store.create_node.call_args[0][0] == NodeLabel.File
            assert store.create_node.call_args[0][1]["language"] == "go"
        finally:
            fpath.unlink()


# ── _handle_method ────────────────────────────────────────────────────────────

class TestGoHandleMethod:
    def test_extracts_value_receiver(self):
        parser = _make_parser()
        store = _make_store()
        source = b"Repo"
        type_id = MockNode("type_identifier", start_byte=0, end_byte=4)
        param_decl = MockNode("parameter_declaration", children=[type_id])
        recv_list = MockNode("parameter_list", children=[param_decl])
        name = _text_node(b"Save")
        node = MockNode("method_declaration", start_point=(0, 0), end_point=(0, 30))
        node.set_field("receiver", recv_list)
        node.set_field("name", name)
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_method(node, source, "main.go", store, stats)
        assert stats["functions"] == 1
        label = store.create_node.call_args[0][0]
        from navegador.graph.schema import NodeLabel
        assert label == NodeLabel.Method

    def test_extracts_pointer_receiver(self):
        parser = _make_parser()
        store = _make_store()
        source = b"*Repo"
        ptr_type = MockNode("pointer_type", start_byte=0, end_byte=5)
        param_decl = MockNode("parameter_declaration", children=[ptr_type])
        recv_list = MockNode("parameter_list", children=[param_decl])
        name = _text_node(b"Delete")
        node = MockNode("method_declaration", start_point=(0, 0), end_point=(0, 30))
        node.set_field("receiver", recv_list)
        node.set_field("name", name)
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_method(node, source, "main.go", store, stats)
        # pointer receiver "*Repo" → lstrip("*") → "Repo"
        assert stats["functions"] == 1

    def test_no_receiver_field(self):
        parser = _make_parser()
        store = _make_store()
        source = b"foo"
        name = _text_node(b"foo")
        node = MockNode("method_declaration", start_point=(0, 0), end_point=(0, 10))
        node.set_field("name", name)
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_method(node, source, "main.go", store, stats)
        # No receiver → treated as plain function
        assert stats["functions"] == 1


# ── _handle_import with import_spec_list ─────────────────────────────────────

class TestGoHandleImportSpecList:
    def test_handles_grouped_imports(self):
        parser = _make_parser()
        store = _make_store()
        source = b'"fmt"'
        path_node = MockNode("interpreted_string_literal", start_byte=0, end_byte=5)
        spec1 = MockNode("import_spec")
        spec1.set_field("path", path_node)
        spec_list = MockNode("import_spec_list", children=[spec1])
        import_node = MockNode("import_declaration",
                               children=[spec_list],
                               start_point=(0, 0))
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_import(import_node, source, "main.go", store, stats)
        assert stats["edges"] == 1


# ── _walk dispatch additions ──────────────────────────────────────────────────

class TestGoWalkDispatchAdditional:
    def test_walk_handles_method_declaration(self):
        parser = _make_parser()
        store = _make_store()
        source = b"Repo"
        type_id = MockNode("type_identifier", start_byte=0, end_byte=4)
        param_decl = MockNode("parameter_declaration", children=[type_id])
        recv_list = MockNode("parameter_list", children=[param_decl])
        name = _text_node(b"Save")
        method = MockNode("method_declaration", start_point=(0, 0), end_point=(0, 30))
        method.set_field("receiver", recv_list)
        method.set_field("name", name)
        root = MockNode("source_file", children=[method])
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._walk(root, source, "main.go", store, stats)
        assert stats["functions"] == 1

    def test_walk_handles_import_declaration(self):
        parser = _make_parser()
        store = _make_store()
        source = b'"fmt"'
        path_node = MockNode("interpreted_string_literal", start_byte=0, end_byte=5)
        spec = MockNode("import_spec")
        spec.set_field("path", path_node)
        import_node = MockNode("import_declaration",
                               children=[spec],
                               start_point=(0, 0))
        root = MockNode("source_file", children=[import_node])
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._walk(root, source, "main.go", store, stats)
        assert stats["edges"] == 1


class TestGoHandleTypeContinueBranches:
    def test_skips_non_type_spec_children(self):
        parser = _make_parser()
        store = _make_store()
        source = b""
        # Child that is not type_spec
        comment_child = MockNode("comment")
        decl = MockNode("type_declaration", children=[comment_child],
                        start_point=(0, 0), end_point=(0, 10))
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_type(decl, source, "main.go", store, stats)
        assert stats["classes"] == 0

    def test_skips_type_spec_without_name_or_type(self):
        parser = _make_parser()
        store = _make_store()
        source = b""
        # type_spec with no fields set
        spec = MockNode("type_spec")
        decl = MockNode("type_declaration", children=[spec],
                        start_point=(0, 0), end_point=(0, 10))
        stats = {"functions": 0, "classes": 0, "edges": 0}
        parser._handle_type(decl, source, "main.go", store, stats)
        assert stats["classes"] == 0
