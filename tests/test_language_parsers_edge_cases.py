"""Edge case tests for language parsers: C, Kotlin, PHP, Swift, HCL, C++, Ruby, Ansible.

Focuses on uncovered lines: struct/union/enum handling, call extraction,
include handling, class body walking, fallback node lookup, block/rescue,
handler files, variable files, and heuristic detection.
"""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from navegador.graph.schema import EdgeType, NodeLabel


def _can_import(module_name: str) -> bool:
    """Check if a module can be imported without raising ImportError."""
    try:
        __import__(module_name)
        return True
    except ImportError:
        return False


def _mock_store():
    store = MagicMock()
    return store


# ═══════════════════════════════════════════════════════════════════════════
# C Parser — struct/union/enum, pointer_declarator, include, calls
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.skipif(
    not _can_import("tree_sitter_c"),
    reason="tree-sitter-c not installed",
)
class TestCParserEdgeCases:
    def _parse(self, code, filename="test.c"):
        from navegador.ingestion.c import CParser

        store = _mock_store()
        parser = CParser()
        with tempfile.TemporaryDirectory() as tmpdir:
            f = Path(tmpdir) / filename
            f.write_text(code, encoding="utf-8")
            stats = parser.parse_file(f, Path(tmpdir), store)
        return stats, store

    def test_struct_parsed_as_class(self):
        code = "struct Point { int x; int y; };"
        stats, store = self._parse(code)
        class_calls = [
            c for c in store.create_node.call_args_list
            if c[0][0] == NodeLabel.Class
        ]
        assert len(class_calls) >= 1
        assert class_calls[0][0][1]["name"] == "Point"
        assert class_calls[0][0][1]["docstring"] == "struct"

    def test_union_parsed_as_class(self):
        code = "union Data { int i; float f; char c; };"
        stats, store = self._parse(code)
        class_calls = [
            c for c in store.create_node.call_args_list
            if c[0][0] == NodeLabel.Class
        ]
        assert len(class_calls) >= 1
        assert class_calls[0][0][1]["docstring"] == "union"

    def test_enum_parsed_as_class(self):
        code = "enum Color { RED, GREEN, BLUE };"
        stats, store = self._parse(code)
        class_calls = [
            c for c in store.create_node.call_args_list
            if c[0][0] == NodeLabel.Class
        ]
        assert len(class_calls) >= 1
        assert class_calls[0][0][1]["docstring"] == "enum"

    def test_include_directive_parsed(self):
        code = '#include <stdio.h>\n#include "myheader.h"\n'
        stats, store = self._parse(code)
        import_calls = [
            c for c in store.create_node.call_args_list
            if c[0][0] == NodeLabel.Import
        ]
        assert len(import_calls) >= 1
        assert stats["edges"] >= 1

    def test_function_with_calls(self):
        code = (
            "void helper() {}\n"
            "void main() {\n"
            "    helper();\n"
            "}\n"
        )
        stats, store = self._parse(code)
        assert stats["functions"] == 2
        # Should have CALLS edges
        calls_edges = [
            c for c in store.create_edge.call_args_list
            if c[0][2] == EdgeType.CALLS
        ]
        assert len(calls_edges) >= 1

    def test_pointer_returning_function(self):
        """Functions returning pointers (pointer_declarator) should be extracted."""
        code = "int *get_data() { return 0; }\n"
        stats, store = self._parse(code)
        assert stats["functions"] >= 1

    def test_header_file(self):
        code = (
            "struct Config { int debug; };\n"
            "void init_config();\n"
        )
        stats, store = self._parse(code, filename="config.h")
        assert stats["classes"] >= 1


# ═══════════════════════════════════════════════════════════════════════════
# Kotlin Parser — class body, object/interface, imports, calls
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.skipif(
    not _can_import("tree_sitter_kotlin"),
    reason="tree-sitter-kotlin not installed",
)
class TestKotlinParserEdgeCases:
    def _parse(self, code, filename="Test.kt"):
        from navegador.ingestion.kotlin import KotlinParser

        store = _mock_store()
        parser = KotlinParser()
        with tempfile.TemporaryDirectory() as tmpdir:
            f = Path(tmpdir) / filename
            f.write_text(code, encoding="utf-8")
            stats = parser.parse_file(f, Path(tmpdir), store)
        return stats, store

    def test_class_with_method(self):
        code = (
            "class MyService {\n"
            "    fun handleRequest() {\n"
            "        println(\"hello\")\n"
            "    }\n"
            "}\n"
        )
        stats, store = self._parse(code)
        assert stats["classes"] >= 1
        assert stats["functions"] >= 1

    def test_object_declaration(self):
        code = "object Singleton {\n    fun instance() {}\n}\n"
        stats, store = self._parse(code)
        assert stats["classes"] >= 1

    def test_interface_declaration(self):
        code = "interface Drawable {\n    fun draw()\n}\n"
        stats, store = self._parse(code)
        assert stats["classes"] >= 1

    def test_import_header(self):
        code = "import kotlin.collections.mutableListOf\n\nfun main() {}\n"
        stats, store = self._parse(code)
        import_calls = [
            c for c in store.create_node.call_args_list
            if c[0][0] == NodeLabel.Import
        ]
        assert len(import_calls) >= 1

    def test_function_with_calls(self):
        code = (
            "fun helper(): Int { return 42 }\n"
            "fun main() {\n"
            "    val x = helper()\n"
            "}\n"
        )
        stats, store = self._parse(code)
        assert stats["functions"] >= 2
        calls_edges = [
            c for c in store.create_edge.call_args_list
            if c[0][2] == EdgeType.CALLS
        ]
        assert len(calls_edges) >= 1

    def test_kts_file(self):
        code = "println(\"build script\")\n"
        stats, store = self._parse(code, filename="build.gradle.kts")
        # Should at least create the File node
        file_calls = [
            c for c in store.create_node.call_args_list
            if c[0][0] == NodeLabel.File
        ]
        assert len(file_calls) >= 1


# ═══════════════════════════════════════════════════════════════════════════
# PHP Parser — class/interface/trait, use statements, method calls
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.skipif(
    not _can_import("tree_sitter_php"),
    reason="tree-sitter-php not installed",
)
class TestPHPParserEdgeCases:
    def _parse(self, code, filename="test.php"):
        from navegador.ingestion.php import PHPParser

        store = _mock_store()
        parser = PHPParser()
        with tempfile.TemporaryDirectory() as tmpdir:
            f = Path(tmpdir) / filename
            f.write_text(code, encoding="utf-8")
            stats = parser.parse_file(f, Path(tmpdir), store)
        return stats, store

    def test_class_with_method(self):
        code = (
            "<?php\n"
            "class UserController {\n"
            "    public function index() {\n"
            "        return 'hello';\n"
            "    }\n"
            "}\n"
        )
        stats, store = self._parse(code)
        assert stats["classes"] >= 1
        assert stats["functions"] >= 1

    def test_interface_declaration(self):
        code = (
            "<?php\n"
            "interface Authenticatable {\n"
            "    public function authenticate();\n"
            "}\n"
        )
        stats, store = self._parse(code)
        assert stats["classes"] >= 1

    def test_trait_declaration(self):
        code = (
            "<?php\n"
            "trait HasTimestamps {\n"
            "    public function createdAt() {\n"
            "        return $this->created_at;\n"
            "    }\n"
            "}\n"
        )
        stats, store = self._parse(code)
        assert stats["classes"] >= 1

    def test_use_declaration(self):
        code = "<?php\nuse App\\Models\\User;\n\nfunction main() {}\n"
        stats, store = self._parse(code)
        import_calls = [
            c for c in store.create_node.call_args_list
            if c[0][0] == NodeLabel.Import
        ]
        assert len(import_calls) >= 1

    def test_function_call_extraction(self):
        code = (
            "<?php\n"
            "function helper() { return 1; }\n"
            "function main() {\n"
            "    $x = helper();\n"
            "}\n"
        )
        stats, store = self._parse(code)
        assert stats["functions"] >= 2
        calls_edges = [
            c for c in store.create_edge.call_args_list
            if c[0][2] == EdgeType.CALLS
        ]
        assert len(calls_edges) >= 1


# ═══════════════════════════════════════════════════════════════════════════
# Swift Parser — struct/enum/protocol, imports, call extraction
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.skipif(
    not _can_import("tree_sitter_swift"),
    reason="tree-sitter-swift not installed",
)
class TestSwiftParserEdgeCases:
    def _parse(self, code, filename="test.swift"):
        from navegador.ingestion.swift import SwiftParser

        store = _mock_store()
        parser = SwiftParser()
        with tempfile.TemporaryDirectory() as tmpdir:
            f = Path(tmpdir) / filename
            f.write_text(code, encoding="utf-8")
            stats = parser.parse_file(f, Path(tmpdir), store)
        return stats, store

    def test_class_with_method(self):
        code = (
            "class ViewController {\n"
            "    func viewDidLoad() {\n"
            "        print(\"loaded\")\n"
            "    }\n"
            "}\n"
        )
        stats, store = self._parse(code)
        assert stats["classes"] >= 1
        assert stats["functions"] >= 1

    def test_struct_declaration(self):
        code = "struct Point {\n    var x: Int\n    var y: Int\n}\n"
        stats, store = self._parse(code)
        assert stats["classes"] >= 1

    def test_enum_declaration(self):
        code = "enum Direction {\n    case north, south, east, west\n}\n"
        stats, store = self._parse(code)
        assert stats["classes"] >= 1

    def test_protocol_declaration(self):
        code = "protocol Drawable {\n    func draw()\n}\n"
        stats, store = self._parse(code)
        assert stats["classes"] >= 1

    def test_import_declaration(self):
        code = "import Foundation\nimport UIKit\n\nfunc main() {}\n"
        stats, store = self._parse(code)
        import_calls = [
            c for c in store.create_node.call_args_list
            if c[0][0] == NodeLabel.Import
        ]
        assert len(import_calls) >= 1

    def test_function_call_extraction(self):
        code = (
            "func helper() -> Int { return 42 }\n"
            "func main() {\n"
            "    let x = helper()\n"
            "}\n"
        )
        stats, store = self._parse(code)
        assert stats["functions"] >= 2
        calls_edges = [
            c for c in store.create_edge.call_args_list
            if c[0][2] == EdgeType.CALLS
        ]
        assert len(calls_edges) >= 1


# ═══════════════════════════════════════════════════════════════════════════
# HCL Parser — data source, provider, variable, output, module, locals
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.skipif(
    not _can_import("tree_sitter_hcl"),
    reason="tree-sitter-hcl not installed",
)
class TestHCLParserEdgeCases:
    def _parse(self, code, filename="main.tf"):
        from navegador.ingestion.hcl import HCLParser

        store = _mock_store()
        parser = HCLParser()
        with tempfile.TemporaryDirectory() as tmpdir:
            f = Path(tmpdir) / filename
            f.write_text(code, encoding="utf-8")
            stats = parser.parse_file(f, Path(tmpdir), store)
        return stats, store

    def test_resource_block(self):
        code = 'resource "aws_instance" "web" {\n  ami = "abc-123"\n}\n'
        stats, store = self._parse(code)
        assert stats["classes"] >= 1
        class_calls = [
            c for c in store.create_node.call_args_list
            if c[0][0] == NodeLabel.Class
        ]
        assert any(c[0][1]["semantic_type"] == "terraform_resource" for c in class_calls)

    def test_data_source_block(self):
        code = 'data "aws_ami" "latest" {\n  most_recent = true\n}\n'
        stats, store = self._parse(code)
        assert stats["classes"] >= 1
        class_calls = [
            c for c in store.create_node.call_args_list
            if c[0][0] == NodeLabel.Class
        ]
        assert any(c[0][1]["semantic_type"] == "terraform_data" for c in class_calls)

    def test_provider_block(self):
        code = 'provider "aws" {\n  region = "us-east-1"\n}\n'
        stats, store = self._parse(code)
        assert stats["classes"] >= 1
        class_calls = [
            c for c in store.create_node.call_args_list
            if c[0][0] == NodeLabel.Class
        ]
        assert any(c[0][1]["semantic_type"] == "terraform_provider" for c in class_calls)

    def test_variable_block(self):
        code = 'variable "region" {\n  default = "us-east-1"\n}\n'
        stats, store = self._parse(code)
        assert stats["functions"] >= 1  # variables counted as functions in stats
        var_calls = [
            c for c in store.create_node.call_args_list
            if c[0][0] == NodeLabel.Variable
        ]
        assert any(c[0][1]["semantic_type"] == "terraform_variable" for c in var_calls)

    def test_output_block(self):
        code = 'output "vpc_id" {\n  value = var.vpc_id\n}\n'
        stats, store = self._parse(code)
        var_calls = [
            c for c in store.create_node.call_args_list
            if c[0][0] == NodeLabel.Variable
        ]
        assert any(c[0][1]["semantic_type"] == "terraform_output" for c in var_calls)

    def test_module_block(self):
        code = 'module "vpc" {\n  source = "./modules/vpc"\n}\n'
        stats, store = self._parse(code)
        mod_calls = [
            c for c in store.create_node.call_args_list
            if c[0][0] == NodeLabel.Module
        ]
        assert len(mod_calls) >= 1
        assert mod_calls[0][0][1]["semantic_type"] == "terraform_module"

    def test_locals_block(self):
        code = (
            "locals {\n"
            '  env = "production"\n'
            '  prefix = "myapp"\n'
            "}\n"
        )
        stats, store = self._parse(code)
        var_calls = [
            c for c in store.create_node.call_args_list
            if c[0][0] == NodeLabel.Variable
        ]
        local_vars = [c for c in var_calls if c[0][1].get("semantic_type") == "terraform_local"]
        assert len(local_vars) >= 1

    def test_reference_extraction_var_ref(self):
        """Resource body referencing var.X should create a REFERENCES edge."""
        code = (
            'resource "aws_instance" "web" {\n'
            "  instance_type = var.instance_type\n"
            "}\n"
        )
        stats, store = self._parse(code)
        ref_edges = [
            c for c in store.create_edge.call_args_list
            if c[0][2] == EdgeType.REFERENCES
        ]
        assert len(ref_edges) >= 1

    def test_reference_extraction_local_ref(self):
        code = (
            'resource "aws_instance" "web" {\n'
            "  tags = { Name = local.prefix }\n"
            "}\n"
        )
        stats, store = self._parse(code)
        ref_edges = [
            c for c in store.create_edge.call_args_list
            if c[0][2] == EdgeType.REFERENCES
        ]
        assert len(ref_edges) >= 1

    def test_reference_extraction_module_ref(self):
        code = (
            'resource "aws_route53_record" "main" {\n'
            "  zone_id = module.dns.zone_id\n"
            "}\n"
        )
        stats, store = self._parse(code)
        ref_edges = [
            c for c in store.create_edge.call_args_list
            if c[0][2] == EdgeType.REFERENCES
        ]
        assert len(ref_edges) >= 1

    def test_reference_extraction_data_ref(self):
        code = (
            'resource "aws_instance" "web" {\n'
            "  ami = data.aws_ami.latest.id\n"
            "}\n"
        )
        stats, store = self._parse(code)
        dep_edges = [
            c for c in store.create_edge.call_args_list
            if c[0][2] == EdgeType.DEPENDS_ON
        ]
        assert len(dep_edges) >= 1

    def test_terraform_block_skipped(self):
        """terraform {} configuration blocks should be silently skipped."""
        code = "terraform {\n  required_version = \">= 1.0\"\n}\n"
        stats, store = self._parse(code)
        # No classes, functions, or errors
        assert stats["classes"] == 0
        assert stats["functions"] == 0

    def test_output_with_var_reference(self):
        """Output block referencing var.X should have a REFERENCES edge."""
        code = 'output "result" {\n  value = var.my_var\n}\n'
        stats, store = self._parse(code)
        ref_edges = [
            c for c in store.create_edge.call_args_list
            if c[0][2] == EdgeType.REFERENCES
        ]
        assert len(ref_edges) >= 1


# ═══════════════════════════════════════════════════════════════════════════
# C++ Parser — class with base, namespace, qualified identifier
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.skipif(
    not _can_import("tree_sitter_cpp"),
    reason="tree-sitter-cpp not installed",
)
class TestCppParserEdgeCases:
    def _parse(self, code, filename="test.cpp"):
        from navegador.ingestion.cpp import CppParser

        store = _mock_store()
        parser = CppParser()
        with tempfile.TemporaryDirectory() as tmpdir:
            f = Path(tmpdir) / filename
            f.write_text(code, encoding="utf-8")
            stats = parser.parse_file(f, Path(tmpdir), store)
        return stats, store

    def test_class_with_method(self):
        code = (
            "class MyClass {\n"
            "public:\n"
            "    void doWork() {}\n"
            "};\n"
        )
        stats, store = self._parse(code)
        assert stats["classes"] >= 1
        assert stats["functions"] >= 1

    def test_struct_declaration(self):
        code = "struct Config {\n    int debug;\n};\n"
        stats, store = self._parse(code)
        assert stats["classes"] >= 1

    def test_class_with_inheritance(self):
        code = "class Derived : public Base {\n};\n"
        stats, store = self._parse(code)
        assert stats["classes"] >= 1
        inherit_edges = [
            c for c in store.create_edge.call_args_list
            if c[0][2] == EdgeType.INHERITS
        ]
        assert len(inherit_edges) >= 1

    def test_namespace_definition(self):
        code = (
            "namespace mylib {\n"
            "    void helper() {}\n"
            "}\n"
        )
        stats, store = self._parse(code)
        assert stats["functions"] >= 1

    def test_include_directive(self):
        code = '#include <iostream>\n#include "myheader.h"\n'
        stats, store = self._parse(code)
        import_calls = [
            c for c in store.create_node.call_args_list
            if c[0][0] == NodeLabel.Import
        ]
        assert len(import_calls) >= 1

    def test_function_with_calls(self):
        code = (
            "void helper() {}\n"
            "void main() {\n"
            "    helper();\n"
            "}\n"
        )
        stats, store = self._parse(code)
        calls_edges = [
            c for c in store.create_edge.call_args_list
            if c[0][2] == EdgeType.CALLS
        ]
        assert len(calls_edges) >= 1

    def test_hpp_file(self):
        code = "class Widget {\npublic:\n    void render() {}\n};\n"
        stats, store = self._parse(code, filename="widget.hpp")
        assert stats["classes"] >= 1


# ═══════════════════════════════════════════════════════════════════════════
# Ruby Parser — module, singleton_method, require, call extraction
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.skipif(
    not _can_import("tree_sitter_ruby"),
    reason="tree-sitter-ruby not installed",
)
class TestRubyParserEdgeCases:
    def _parse(self, code, filename="test.rb"):
        from navegador.ingestion.ruby import RubyParser

        store = _mock_store()
        parser = RubyParser()
        with tempfile.TemporaryDirectory() as tmpdir:
            f = Path(tmpdir) / filename
            f.write_text(code, encoding="utf-8")
            stats = parser.parse_file(f, Path(tmpdir), store)
        return stats, store

    def test_class_with_method(self):
        code = (
            "class UserService\n"
            "  def authenticate(user)\n"
            "    true\n"
            "  end\n"
            "end\n"
        )
        stats, store = self._parse(code)
        assert stats["classes"] >= 1
        assert stats["functions"] >= 1

    def test_module_declaration(self):
        code = (
            "module Authentication\n"
            "  def self.verify(token)\n"
            "    true\n"
            "  end\n"
            "end\n"
        )
        stats, store = self._parse(code)
        class_calls = [
            c for c in store.create_node.call_args_list
            if c[0][0] == NodeLabel.Class
        ]
        # Module is stored as Class with docstring "module"
        mod_calls = [c for c in class_calls if c[0][1].get("docstring") == "module"]
        assert len(mod_calls) >= 1

    def test_class_with_inheritance(self):
        code = "class Admin < User\nend\n"
        stats, store = self._parse(code)
        assert stats["classes"] >= 1
        inherit_edges = [
            c for c in store.create_edge.call_args_list
            if c[0][2] == EdgeType.INHERITS
        ]
        assert len(inherit_edges) >= 1

    def test_require_statement(self):
        code = "require 'json'\nrequire_relative './helpers'\n"
        stats, store = self._parse(code)
        import_calls = [
            c for c in store.create_node.call_args_list
            if c[0][0] == NodeLabel.Import
        ]
        assert len(import_calls) >= 1

    def test_method_call_extraction(self):
        code = (
            "class Foo\n"
            "  def bar\n"
            "    baz()\n"
            "  end\n"
            "  def baz\n"
            "    42\n"
            "  end\n"
            "end\n"
        )
        stats, store = self._parse(code)
        calls_edges = [
            c for c in store.create_edge.call_args_list
            if c[0][2] == EdgeType.CALLS
        ]
        assert len(calls_edges) >= 1

    def test_top_level_method(self):
        """Methods defined outside a class should be parsed as Functions."""
        code = "def standalone_helper\n  42\nend\n"
        stats, store = self._parse(code)
        fn_calls = [
            c for c in store.create_node.call_args_list
            if c[0][0] == NodeLabel.Function
        ]
        assert len(fn_calls) >= 1


# ═══════════════════════════════════════════════════════════════════════════
# Ansible Parser — playbook, task file, handler file, variable file,
#                    block/rescue/always, role references, vars blocks
# ═══════════════════════════════════════════════════════════════════════════


class TestAnsibleParserEdgeCases:
    def _parse(self, code, filename="playbook.yml", subdir=""):
        from navegador.ingestion.ansible import AnsibleParser

        store = _mock_store()
        parser = AnsibleParser()
        with tempfile.TemporaryDirectory() as tmpdir:
            if subdir:
                dirpath = Path(tmpdir) / subdir
                dirpath.mkdir(parents=True)
                f = dirpath / filename
            else:
                f = Path(tmpdir) / filename
            f.write_text(code, encoding="utf-8")
            stats = parser.parse_file(f, Path(tmpdir), store)
        return stats, store

    def test_playbook_with_tasks(self):
        code = (
            "---\n"
            "- hosts: webservers\n"
            "  name: Deploy web app\n"
            "  tasks:\n"
            "    - name: Install nginx\n"
            "      apt:\n"
            "        name: nginx\n"
            "        state: present\n"
        )
        stats, store = self._parse(code)
        assert stats["classes"] >= 1  # play node
        assert stats["functions"] >= 1  # task node

    def test_playbook_with_handlers(self):
        code = (
            "---\n"
            "- hosts: all\n"
            "  tasks:\n"
            "    - name: Configure app\n"
            "      template:\n"
            "        src: app.conf.j2\n"
            "        dest: /etc/app.conf\n"
            "      notify: Restart app\n"
            "  handlers:\n"
            "    - name: Restart app\n"
            "      service:\n"
            "        name: app\n"
            "        state: restarted\n"
        )
        stats, store = self._parse(code)
        fn_calls = [
            c for c in store.create_node.call_args_list
            if c[0][0] == NodeLabel.Function
        ]
        # Should have both a task and a handler
        handler_calls = [
            c for c in fn_calls if c[0][1].get("semantic_type") == "ansible_handler"
        ]
        assert len(handler_calls) >= 1

    def test_playbook_with_roles(self):
        code = (
            "---\n"
            "- hosts: all\n"
            "  roles:\n"
            "    - common\n"
            "    - role: webserver\n"
        )
        stats, store = self._parse(code)
        import_calls = [
            c for c in store.create_node.call_args_list
            if c[0][0] == NodeLabel.Import
        ]
        # At least 2 role references
        assert len(import_calls) >= 2

    def test_playbook_with_vars(self):
        code = (
            "---\n"
            "- hosts: all\n"
            "  vars:\n"
            "    http_port: 80\n"
            "    max_clients: 200\n"
            "  tasks:\n"
            "    - name: Print vars\n"
            "      debug:\n"
            "        msg: done\n"
        )
        stats, store = self._parse(code)
        var_calls = [
            c for c in store.create_node.call_args_list
            if c[0][0] == NodeLabel.Variable
        ]
        assert len(var_calls) >= 2

    def test_playbook_with_pre_and_post_tasks(self):
        code = (
            "---\n"
            "- hosts: all\n"
            "  pre_tasks:\n"
            "    - name: Pre-check\n"
            "      command: echo pre\n"
            "  post_tasks:\n"
            "    - name: Post-cleanup\n"
            "      command: echo post\n"
        )
        stats, store = self._parse(code)
        fn_calls = [
            c for c in store.create_node.call_args_list
            if c[0][0] == NodeLabel.Function
        ]
        assert len(fn_calls) >= 2

    def test_task_with_block_rescue_always(self):
        code = (
            "---\n"
            "- hosts: all\n"
            "  tasks:\n"
            "    - name: Main block\n"
            "      block:\n"
            "        - name: Try this\n"
            "          command: echo try\n"
            "      rescue:\n"
            "        - name: Handle failure\n"
            "          command: echo rescue\n"
            "      always:\n"
            "        - name: Cleanup\n"
            "          command: echo always\n"
        )
        stats, store = self._parse(code)
        fn_calls = [
            c for c in store.create_node.call_args_list
            if c[0][0] == NodeLabel.Function
        ]
        # Should have at least: Main block + Try this + Handle failure + Cleanup
        assert len(fn_calls) >= 4

    def test_task_with_notify(self):
        """Tasks with notify should create CALLS edges to handlers."""
        code = (
            "---\n"
            "- hosts: all\n"
            "  tasks:\n"
            "    - name: Install package\n"
            "      apt:\n"
            "        name: nginx\n"
            "      notify: Restart nginx\n"
        )
        stats, store = self._parse(code)
        calls_edges = [
            c for c in store.create_edge.call_args_list
            if c[0][2] == EdgeType.CALLS
        ]
        assert len(calls_edges) >= 1

    def test_standalone_task_file(self):
        """roles/*/tasks/main.yml should be parsed as a task file."""
        code = (
            "---\n"
            "- name: Install deps\n"
            "  apt:\n"
            "    name: curl\n"
        )
        stats, store = self._parse(
            code, filename="main.yml", subdir="roles/app/tasks"
        )
        assert stats["functions"] >= 1

    def test_standalone_handler_file(self):
        """roles/*/handlers/main.yml should be parsed as a handler file."""
        code = (
            "---\n"
            "- name: Restart app\n"
            "  service:\n"
            "    name: app\n"
            "    state: restarted\n"
        )
        stats, store = self._parse(
            code, filename="main.yml", subdir="roles/app/handlers"
        )
        fn_calls = [
            c for c in store.create_node.call_args_list
            if c[0][0] == NodeLabel.Function
        ]
        handler_calls = [
            c for c in fn_calls if c[0][1].get("semantic_type") == "ansible_handler"
        ]
        assert len(handler_calls) >= 1

    def test_standalone_variable_file_defaults(self):
        """roles/*/defaults/main.yml should be parsed as a variable file."""
        code = "---\nhttp_port: 80\nmax_clients: 200\n"
        stats, store = self._parse(
            code, filename="main.yml", subdir="roles/app/defaults"
        )
        var_calls = [
            c for c in store.create_node.call_args_list
            if c[0][0] == NodeLabel.Variable
        ]
        assert len(var_calls) >= 2

    def test_standalone_variable_file_vars(self):
        """roles/*/vars/main.yml should be parsed as a variable file."""
        code = "---\ndb_host: localhost\ndb_port: 5432\n"
        stats, store = self._parse(
            code, filename="main.yml", subdir="roles/db/vars"
        )
        var_calls = [
            c for c in store.create_node.call_args_list
            if c[0][0] == NodeLabel.Variable
        ]
        assert len(var_calls) >= 2

    def test_group_vars_file(self):
        """group_vars/*.yml should be parsed as a variable file."""
        code = "---\nenv: production\nregion: us-east-1\n"
        stats, store = self._parse(
            code, filename="all.yml", subdir="group_vars"
        )
        var_calls = [
            c for c in store.create_node.call_args_list
            if c[0][0] == NodeLabel.Variable
        ]
        assert len(var_calls) >= 2

    def test_host_vars_file(self):
        """host_vars/*.yml should be parsed as a variable file."""
        code = "---\nansible_host: 10.0.0.1\n"
        stats, store = self._parse(
            code, filename="webserver.yml", subdir="host_vars"
        )
        var_calls = [
            c for c in store.create_node.call_args_list
            if c[0][0] == NodeLabel.Variable
        ]
        assert len(var_calls) >= 1

    def test_task_name_falls_back_to_module_name(self):
        """Tasks without a 'name' key should use the module name as task name."""
        code = (
            "---\n"
            "- hosts: all\n"
            "  tasks:\n"
            "    - apt:\n"
            "        name: curl\n"
            "        state: present\n"
        )
        stats, store = self._parse(code)
        fn_calls = [
            c for c in store.create_node.call_args_list
            if c[0][0] == NodeLabel.Function
        ]
        # Task name should be "apt" (the module name)
        task_names = [c[0][1]["name"] for c in fn_calls]
        assert "apt" in task_names

    def test_empty_yaml_returns_zero_stats(self):
        code = "---\n"
        stats, store = self._parse(code)
        assert stats["functions"] == 0
        assert stats["classes"] == 0

    def test_dict_yaml_parsed_as_variable_file(self):
        """A standalone dict YAML not matching any pattern is parsed as variables."""
        code = "---\nkey1: value1\nkey2: value2\n"
        stats, store = self._parse(code, filename="custom.yml")
        # Should be parsed as a variable file since it's a dict
        var_calls = [
            c for c in store.create_node.call_args_list
            if c[0][0] == NodeLabel.Variable
        ]
        assert len(var_calls) >= 2


class TestAnsibleIsAnsibleFile:
    """Test the is_ansible_file static method heuristics."""

    def test_non_yaml_extension_rejected(self):
        from navegador.ingestion.ansible import AnsibleParser

        with tempfile.TemporaryDirectory() as tmpdir:
            f = Path(tmpdir) / "data.json"
            f.write_text("{}", encoding="utf-8")
            assert AnsibleParser.is_ansible_file(f, Path(tmpdir)) is False

    def test_role_tasks_path_detected(self):
        from navegador.ingestion.ansible import AnsibleParser

        with tempfile.TemporaryDirectory() as tmpdir:
            task_dir = Path(tmpdir) / "roles" / "web" / "tasks"
            task_dir.mkdir(parents=True)
            f = task_dir / "main.yml"
            f.write_text("---\n- name: task\n  debug: msg=hi\n", encoding="utf-8")
            assert AnsibleParser.is_ansible_file(f, Path(tmpdir)) is True

    def test_role_handlers_path_detected(self):
        from navegador.ingestion.ansible import AnsibleParser

        with tempfile.TemporaryDirectory() as tmpdir:
            handler_dir = Path(tmpdir) / "roles" / "web" / "handlers"
            handler_dir.mkdir(parents=True)
            f = handler_dir / "main.yml"
            f.write_text("---\n- name: handler\n  service: name=nginx\n", encoding="utf-8")
            assert AnsibleParser.is_ansible_file(f, Path(tmpdir)) is True

    def test_role_defaults_path_detected(self):
        from navegador.ingestion.ansible import AnsibleParser

        with tempfile.TemporaryDirectory() as tmpdir:
            defaults_dir = Path(tmpdir) / "roles" / "web" / "defaults"
            defaults_dir.mkdir(parents=True)
            f = defaults_dir / "main.yml"
            f.write_text("---\nhttp_port: 80\n", encoding="utf-8")
            assert AnsibleParser.is_ansible_file(f, Path(tmpdir)) is True

    def test_role_vars_path_detected(self):
        from navegador.ingestion.ansible import AnsibleParser

        with tempfile.TemporaryDirectory() as tmpdir:
            vars_dir = Path(tmpdir) / "roles" / "web" / "vars"
            vars_dir.mkdir(parents=True)
            f = vars_dir / "main.yml"
            f.write_text("---\nkey: value\n", encoding="utf-8")
            assert AnsibleParser.is_ansible_file(f, Path(tmpdir)) is True

    def test_playbooks_dir_detected(self):
        from navegador.ingestion.ansible import AnsibleParser

        with tempfile.TemporaryDirectory() as tmpdir:
            pb_dir = Path(tmpdir) / "playbooks"
            pb_dir.mkdir()
            f = pb_dir / "deploy.yml"
            f.write_text("---\n- hosts: all\n  tasks: []\n", encoding="utf-8")
            assert AnsibleParser.is_ansible_file(f, Path(tmpdir)) is True

    def test_group_vars_detected(self):
        from navegador.ingestion.ansible import AnsibleParser

        with tempfile.TemporaryDirectory() as tmpdir:
            gv_dir = Path(tmpdir) / "group_vars"
            gv_dir.mkdir()
            f = gv_dir / "all.yml"
            f.write_text("---\nenv: prod\n", encoding="utf-8")
            assert AnsibleParser.is_ansible_file(f, Path(tmpdir)) is True

    def test_host_vars_detected(self):
        from navegador.ingestion.ansible import AnsibleParser

        with tempfile.TemporaryDirectory() as tmpdir:
            hv_dir = Path(tmpdir) / "host_vars"
            hv_dir.mkdir()
            f = hv_dir / "webserver.yml"
            f.write_text("---\nansible_host: 10.0.0.1\n", encoding="utf-8")
            assert AnsibleParser.is_ansible_file(f, Path(tmpdir)) is True

    def test_ansible_cfg_sibling_with_common_name(self):
        """With ansible.cfg present, common playbook names are detected."""
        from navegador.ingestion.ansible import AnsibleParser

        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "ansible.cfg").write_text("[defaults]\n", encoding="utf-8")
            f = Path(tmpdir) / "site.yml"
            f.write_text("---\n- hosts: all\n  tasks: []\n", encoding="utf-8")
            assert AnsibleParser.is_ansible_file(f, Path(tmpdir)) is True

    def test_content_based_detection_hosts_key(self):
        """Files with --- and a list containing 'hosts' key are detected."""
        from navegador.ingestion.ansible import AnsibleParser

        with tempfile.TemporaryDirectory() as tmpdir:
            f = Path(tmpdir) / "custom.yml"
            f.write_text(
                "---\n- hosts: all\n  tasks:\n    - debug: msg=hi\n",
                encoding="utf-8",
            )
            assert AnsibleParser.is_ansible_file(f, Path(tmpdir)) is True

    def test_non_ansible_yaml_not_detected(self):
        """A generic YAML file without Ansible patterns is not detected."""
        from navegador.ingestion.ansible import AnsibleParser

        with tempfile.TemporaryDirectory() as tmpdir:
            f = Path(tmpdir) / "config.yml"
            f.write_text("---\ndebug: true\nport: 8080\n", encoding="utf-8")
            assert AnsibleParser.is_ansible_file(f, Path(tmpdir)) is False

    def test_yaml_without_triple_dash_not_detected(self):
        """YAML not starting with --- is rejected at content check."""
        from navegador.ingestion.ansible import AnsibleParser

        with tempfile.TemporaryDirectory() as tmpdir:
            f = Path(tmpdir) / "data.yml"
            f.write_text("key: value\n", encoding="utf-8")
            assert AnsibleParser.is_ansible_file(f, Path(tmpdir)) is False
