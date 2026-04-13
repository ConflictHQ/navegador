"""Tests for navegador.taskpack — TaskPack data model and TaskPackBuilder."""

import json
from unittest.mock import MagicMock

from navegador.taskpack import TaskPack, TaskPackBuilder, TaskPackNode

# ── Helpers ──────────────────────────────────────────────────────────────────


def _mock_store(result_set=None):
    """Return a mock GraphStore whose .query() returns the given result_set."""
    store = MagicMock()
    result = MagicMock()
    result.result_set = result_set or []
    store.query.return_value = result
    return store


def _multi_mock_store(*result_sets):
    """Mock store whose .query() returns successive result_sets per call."""
    store = MagicMock()
    results = []
    for rs in result_sets:
        r = MagicMock()
        r.result_set = rs
        results.append(r)
    store.query.side_effect = results
    return store


# ── TaskPackNode ─────────────────────────────────────────────────────────────


class TestTaskPackNode:
    def test_defaults(self):
        node = TaskPackNode(type="Function", name="foo")
        assert node.file_path == ""
        assert node.line_start is None
        assert node.summary == ""
        assert node.relation == ""

    def test_full_construction(self):
        node = TaskPackNode(
            type="Class", name="Bar", file_path="src/bar.py",
            line_start=10, summary="A bar class", relation="calls this",
        )
        assert node.type == "Class"
        assert node.name == "Bar"
        assert node.line_start == 10


# ── TaskPack.to_markdown ─────────────────────────────────────────────────────


class TestTaskPackMarkdown:
    def test_header_includes_target_name(self):
        pack = TaskPack(target_type="symbol", target_name="foo")
        md = pack.to_markdown()
        assert "# Task Pack" in md
        assert "`foo`" in md

    def test_header_includes_file_when_present(self):
        pack = TaskPack(target_type="symbol", target_name="foo", target_file="src/foo.py")
        md = pack.to_markdown()
        assert "`src/foo.py`" in md

    def test_mode_appears_in_markdown(self):
        for mode in ("implement", "review", "debug", "refactor"):
            pack = TaskPack(target_type="symbol", target_name="x", mode=mode)
            md = pack.to_markdown()
            assert f"**Mode:** {mode}" in md

    def test_target_type_appears_in_markdown(self):
        pack = TaskPack(target_type="file", target_name="x.py")
        md = pack.to_markdown()
        assert "**Type:** file" in md

    def test_sections_with_content_appear(self):
        pack = TaskPack(
            target_type="symbol", target_name="foo",
            code=[TaskPackNode(type="Function", name="foo", file_path="a.py")],
            callers=[TaskPackNode(type="Function", name="bar", file_path="b.py")],
            tests=[TaskPackNode(type="Function", name="test_foo", file_path="test_a.py")],
        )
        md = pack.to_markdown()
        assert "## Target" in md
        assert "## Callers" in md
        assert "## Related Tests" in md

    def test_empty_sections_omitted(self):
        pack = TaskPack(target_type="symbol", target_name="foo")
        md = pack.to_markdown()
        assert "## Callers" not in md
        assert "## Callees" not in md
        assert "## Imports" not in md
        assert "## Governing Rules" not in md
        assert "## Docs" not in md
        assert "## Owners" not in md
        assert "## Related Tests" not in md

    def test_metadata_section_rendered(self):
        pack = TaskPack(target_type="symbol", target_name="foo", metadata={"depth": 2})
        md = pack.to_markdown()
        assert "## Metadata" in md
        assert "**depth:**" in md

    def test_metadata_omitted_when_empty(self):
        pack = TaskPack(target_type="symbol", target_name="foo")
        md = pack.to_markdown()
        assert "## Metadata" not in md

    def test_node_details_in_markdown(self):
        pack = TaskPack(
            target_type="symbol", target_name="foo",
            code=[TaskPackNode(
                type="Function", name="foo", file_path="a.py",
                line_start=42, summary="Does stuff", relation="target",
            )],
        )
        md = pack.to_markdown()
        assert "`foo`" in md
        assert "`a.py`" in md
        assert ":42" in md
        assert "Does stuff" in md
        assert "_target_" in md


# ── TaskPack.to_json / to_dict ───────────────────────────────────────────────


class TestTaskPackJson:
    def test_round_trip_through_json(self):
        pack = TaskPack(
            target_type="symbol", target_name="validate",
            target_file="auth.py", mode="debug",
            code=[TaskPackNode(
                type="Function", name="validate",
                file_path="auth.py", line_start=5,
            )],
            callers=[TaskPackNode(type="Method", name="login", file_path="views.py")],
        )
        raw = pack.to_json()
        data = json.loads(raw)
        assert data["target"]["name"] == "validate"
        assert data["target"]["mode"] == "debug"
        assert data["target"]["file"] == "auth.py"
        assert len(data["code"]) == 1
        assert data["code"][0]["name"] == "validate"
        assert data["code"][0]["line_start"] == 5
        assert len(data["callers"]) == 1

    def test_empty_pack_json(self):
        pack = TaskPack(target_type="file", target_name="empty.py")
        data = json.loads(pack.to_json())
        assert data["code"] == []
        assert data["callers"] == []
        assert data["metadata"] == {}

    def test_to_dict_strips_empty_fields_from_nodes(self):
        pack = TaskPack(
            target_type="symbol", target_name="foo",
            code=[TaskPackNode(type="Function", name="foo")],
        )
        d = pack.to_dict()
        node = d["code"][0]
        # Empty strings and None should be stripped
        assert "file_path" not in node
        assert "line_start" not in node
        assert "summary" not in node
        assert "relation" not in node
        # Populated fields remain
        assert node["type"] == "Function"
        assert node["name"] == "foo"


# ── TaskPackBuilder.for_symbol ───────────────────────────────────────────────


class TestTaskPackBuilderForSymbol:
    def test_populates_code_from_query(self):
        """Builder should populate pack.code from the symbol lookup query."""
        store = _multi_mock_store(
            # _add_symbol_code
            [["Function", "validate_token", "app/auth.py", 10, "Validates JWT"]],
            # _add_callers_callees: callers
            [],
            # _add_callers_callees: callees
            [],
            # _add_knowledge
            [],
            # _add_knowledge: memory for file
            [],
            # _add_owners
            [],
            # _add_tests
            [],
        )
        builder = TaskPackBuilder(store)
        pack = builder.for_symbol("validate_token", file_path="app/auth.py")

        assert pack.target_type == "symbol"
        assert pack.target_name == "validate_token"
        assert pack.mode == "implement"
        assert len(pack.code) == 1
        assert pack.code[0].type == "Function"
        assert pack.code[0].name == "validate_token"
        assert pack.code[0].line_start == 10
        assert pack.code[0].summary == "Validates JWT"

    def test_populates_callers_and_callees(self):
        store = _multi_mock_store(
            # _add_symbol_code
            [["Function", "foo", "a.py", 1, ""]],
            # callers
            [["Function", "caller1", "b.py", 5], ["Method", "caller2", "c.py", 8]],
            # callees
            [["Function", "callee1", "d.py", 20]],
            # _add_knowledge
            [],
            # memory for file
            [],
            # _add_owners
            [],
            # _add_tests
            [],
        )
        pack = TaskPackBuilder(store).for_symbol("foo", file_path="a.py", depth=2)

        assert len(pack.callers) == 2
        assert pack.callers[0].name == "caller1"
        assert pack.callers[0].relation == "calls this"
        assert len(pack.callees) == 1
        assert pack.callees[0].name == "callee1"
        assert pack.callees[0].relation == "called by this"

    def test_populates_knowledge_rules_and_docs(self):
        store = _multi_mock_store(
            # _add_symbol_code
            [],
            # callers
            [],
            # callees
            [],
            # _add_knowledge: rules/docs query
            [
                ["Rule", "no_plaintext_secrets", "Never store secrets in plaintext"],
                ["Decision", "use_jwt", "Decision to use JWT for auth"],
            ],
            # _add_knowledge: memory for file
            [["Memory", "session_note", "Auth refactor note", "session"]],
            # _add_owners
            [],
            # _add_tests
            [],
        )
        pack = TaskPackBuilder(store).for_symbol("foo", file_path="auth.py")

        assert len(pack.rules) == 2  # the Rule + Memory node
        assert pack.rules[0].name == "no_plaintext_secrets"
        assert pack.rules[1].name == "session_note"
        assert pack.rules[1].relation == "memory:session"
        assert len(pack.docs) == 1
        assert pack.docs[0].name == "use_jwt"
        assert pack.docs[0].type == "Decision"

    def test_populates_tests(self):
        store = _multi_mock_store(
            [],  # symbol code
            [],  # callers
            [],  # callees
            [],  # knowledge
            [],  # memory
            [],  # owners
            [["Function", "test_validate_token", "tests/test_auth.py", 42]],  # tests
        )
        pack = TaskPackBuilder(store).for_symbol("validate_token", file_path="auth.py")

        assert len(pack.tests) == 1
        assert pack.tests[0].name == "test_validate_token"
        assert pack.tests[0].relation == "tests this"
        assert pack.tests[0].file_path == "tests/test_auth.py"

    def test_metadata_includes_depth_and_section_counts(self):
        store = _multi_mock_store(
            [["Function", "foo", "a.py", 1, ""]],  # code
            [],  # callers
            [],  # callees
            [],  # knowledge
            [],  # memory
            [["Alice", "lead", "alice@x.com"]],  # owners
            [],  # tests
        )
        pack = TaskPackBuilder(store).for_symbol("foo", file_path="a.py", depth=4)

        assert pack.metadata["depth"] == 4
        assert pack.metadata["sections"]["code"] == 1
        assert pack.metadata["sections"]["owners"] == 1
        assert pack.metadata["sections"]["callers"] == 0

    def test_mode_propagated(self):
        store = _multi_mock_store([], [], [], [], [], [], [])
        pack = TaskPackBuilder(store).for_symbol("x", mode="refactor")
        assert pack.mode == "refactor"


# ── TaskPackBuilder.for_file ─────────────────────────────────────────────────


class TestTaskPackBuilderForFile:
    def test_populates_code_and_imports_from_file(self):
        store = _multi_mock_store(
            # _add_file_symbols (FILE_CONTENTS query)
            [
                ["Function", "handle_request", 10, "Handles HTTP request", ""],
                ["Import", "os", 1, "", "os"],
                ["Class", "Handler", 20, "Main handler class", ""],
            ],
            # _add_file_knowledge: knowledge query
            [],
            # _add_file_knowledge: memory query
            [],
            # _add_owners
            [],
            # _add_tests
            [],
        )
        pack = TaskPackBuilder(store).for_file("app/views.py")

        assert pack.target_type == "file"
        assert pack.target_name == "views.py"
        assert pack.target_file == "app/views.py"
        assert len(pack.code) == 2  # Function + Class (Import goes to imports)
        assert len(pack.imports) == 1
        assert pack.imports[0].name == "os"

    def test_file_mode_propagated(self):
        store = _multi_mock_store([], [], [], [], [])
        pack = TaskPackBuilder(store).for_file("x.py", mode="review")
        assert pack.mode == "review"

    def test_file_knowledge_rules_vs_docs(self):
        store = _multi_mock_store(
            # file symbols
            [],
            # file knowledge query
            [
                ["Rule", "no_global_state", "Avoid global mutable state"],
                ["WikiPage", "Architecture", "System architecture overview"],
            ],
            # memory
            [],
            # owners
            [],
            # tests
            [],
        )
        pack = TaskPackBuilder(store).for_file("app/core.py")

        assert len(pack.rules) == 1
        assert pack.rules[0].type == "Rule"
        assert len(pack.docs) == 1
        assert pack.docs[0].type == "WikiPage"

    def test_metadata_section_counts(self):
        store = _multi_mock_store(
            [["Function", "main", 1, "Entry point", ""]],  # file symbols
            [],  # file knowledge
            [],  # memory
            [],  # owners
            [],  # tests
        )
        pack = TaskPackBuilder(store).for_file("app/main.py")
        assert "sections" in pack.metadata
        assert pack.metadata["sections"]["code"] == 1
