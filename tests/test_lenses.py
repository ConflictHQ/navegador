# Copyright CONFLICT LLC 2026 (weareconflict.com)
"""
Tests for navegador.lenses — LensEngine, LensResult, and all built-in lenses.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from navegador.lenses import (
    BUILTIN_LENSES,
    LensEdge,
    LensEngine,
    LensNode,
    LensResult,
)

# ── Helpers ──────────────────────────────────────────────────────────────────


def _mock_store(rows: list | None = None):
    """Return a minimal GraphStore mock that returns *rows* for any query."""
    store = MagicMock()
    result = MagicMock()
    result.result_set = rows or []
    store.query.return_value = result
    return store


# ── LensResult data model ───────────────────────────────────────────────────


class TestLensNode:
    def test_to_dict_minimal(self):
        node = LensNode(label="Function", name="foo")
        d = node.to_dict()
        assert d["label"] == "Function"
        assert d["name"] == "foo"
        assert "domain" not in d
        assert "owner" not in d

    def test_to_dict_with_all_fields(self):
        node = LensNode(
            label="Class",
            name="Bar",
            file_path="app.py",
            domain="billing",
            owner="alice",
            extra={"key": "val"},
        )
        d = node.to_dict()
        assert d["domain"] == "billing"
        assert d["owner"] == "alice"
        assert d["extra"] == {"key": "val"}


class TestLensEdge:
    def test_to_dict(self):
        edge = LensEdge(source="foo", target="bar", type="CALLS")
        d = edge.to_dict()
        assert d == {"source": "foo", "target": "bar", "type": "CALLS"}


class TestLensResult:
    def _sample(self):
        return LensResult(
            lens="test_lens",
            nodes=[
                LensNode(label="Function", name="foo", file_path="app.py"),
                LensNode(label="Class", name="Bar", file_path="models.py", domain="billing"),
            ],
            edges=[LensEdge(source="foo", target="Bar", type="CALLS")],
            params={"symbol": "foo"},
        )

    def test_to_dict_structure(self):
        r = self._sample()
        d = r.to_dict()
        assert d["lens"] == "test_lens"
        assert len(d["nodes"]) == 2
        assert len(d["edges"]) == 1
        assert d["params"] == {"symbol": "foo"}

    def test_to_json_roundtrip(self):
        r = self._sample()
        j = r.to_json()
        parsed = json.loads(j)
        assert parsed["lens"] == "test_lens"
        assert len(parsed["nodes"]) == 2
        assert len(parsed["edges"]) == 1

    def test_to_markdown_includes_lens_name(self):
        r = self._sample()
        md = r.to_markdown()
        assert "# Lens: test_lens" in md

    def test_to_markdown_includes_node_count(self):
        r = self._sample()
        md = r.to_markdown()
        assert "2 nodes" in md

    def test_to_markdown_includes_edge_count(self):
        r = self._sample()
        md = r.to_markdown()
        assert "1 edges" in md

    def test_to_markdown_includes_node_names(self):
        r = self._sample()
        md = r.to_markdown()
        assert "foo" in md
        assert "Bar" in md

    def test_to_markdown_includes_edges(self):
        r = self._sample()
        md = r.to_markdown()
        assert "foo -[CALLS]-> Bar" in md


# ── LensEngine.list_lenses ──────────────────────────────────────────────────


class TestListLenses:
    def test_returns_all_five_builtins(self):
        store = _mock_store()
        engine = LensEngine(store)
        lenses = engine.list_lenses()
        names = [item["name"] for item in lenses]
        assert set(names) == set(BUILTIN_LENSES)
        assert len(lenses) == 5

    def test_builtin_flag_is_true(self):
        store = _mock_store()
        engine = LensEngine(store)
        for item in engine.list_lenses():
            assert item["builtin"] is True

    def test_descriptions_are_nonempty(self):
        store = _mock_store()
        engine = LensEngine(store)
        for item in engine.list_lenses():
            assert item["description"]

    def test_includes_custom_lenses(self):
        store = _mock_store()
        engine = LensEngine(store)
        engine.register("my_lens", "MATCH (n) RETURN n", description="test")
        lenses = engine.list_lenses()
        names = [item["name"] for item in lenses]
        assert "my_lens" in names
        assert len(lenses) == 6

    def test_custom_lens_builtin_is_false(self):
        store = _mock_store()
        engine = LensEngine(store)
        engine.register("my_lens", "MATCH (n) RETURN n")
        custom = [x for x in engine.list_lenses() if x["name"] == "my_lens"][0]
        assert custom["builtin"] is False


# ── LensEngine.register ─────────────────────────────────────────────────────


class TestRegister:
    def test_register_adds_to_list(self):
        store = _mock_store()
        engine = LensEngine(store)
        engine.register("custom_one", "MATCH (n) RETURN n.name", "desc")
        names = [item["name"] for item in engine.list_lenses()]
        assert "custom_one" in names

    def test_register_stores_cypher(self):
        store = _mock_store()
        engine = LensEngine(store)
        engine.register("cust", "MATCH (n) RETURN labels(n)[0], n.name, n.file_path")
        assert "cust" in engine._custom
        assert engine._custom["cust"]["cypher"].startswith("MATCH")


# ── LensEngine.apply — built-in lenses ──────────────────────────────────────


class TestApplyRequestPath:
    def test_returns_nodes_and_edges(self):
        rows = [
            ["Controller", "UserController", "ctrl.py", "Function", "create_user", "user.py"],
            ["Controller", "UserController", "ctrl.py", "Function", "validate", "util.py"],
        ]
        store = _mock_store(rows)
        engine = LensEngine(store)
        result = engine.apply("request_path")
        assert result.lens == "request_path"
        names = {n.name for n in result.nodes}
        assert "UserController" in names
        assert "create_user" in names
        assert "validate" in names
        assert len(result.edges) == 2

    def test_passes_symbol_param(self):
        store = _mock_store([])
        engine = LensEngine(store)
        result = engine.apply("request_path", symbol="MyHandler")
        assert result.params["symbol"] == "MyHandler"
        store.query.assert_called_once()
        call_params = store.query.call_args[0][1]
        assert call_params["symbol"] == "MyHandler"

    def test_empty_result(self):
        store = _mock_store([])
        engine = LensEngine(store)
        result = engine.apply("request_path")
        assert result.nodes == []
        assert result.edges == []


class TestApplyOwnershipMap:
    def test_returns_nodes_with_owners(self):
        rows = [
            ["Function", "auth_check", "auth.py", "alice"],
            ["Class", "PaymentService", "pay.py", "bob"],
        ]
        store = _mock_store(rows)
        engine = LensEngine(store)
        result = engine.apply("ownership_map")
        assert result.lens == "ownership_map"
        symbol_nodes = [n for n in result.nodes if n.label != "Person"]
        assert len(symbol_nodes) == 2
        assert symbol_nodes[0].owner == "alice"

    def test_creates_person_nodes(self):
        rows = [
            ["Function", "auth_check", "auth.py", "alice"],
        ]
        store = _mock_store(rows)
        engine = LensEngine(store)
        result = engine.apply("ownership_map")
        person_nodes = [n for n in result.nodes if n.label == "Person"]
        assert len(person_nodes) == 1
        assert person_nodes[0].name == "alice"

    def test_creates_assigned_to_edges(self):
        rows = [
            ["Function", "auth_check", "auth.py", "alice"],
        ]
        store = _mock_store(rows)
        engine = LensEngine(store)
        result = engine.apply("ownership_map")
        assert len(result.edges) == 1
        assert result.edges[0].type == "ASSIGNED_TO"
        assert result.edges[0].source == "auth_check"
        assert result.edges[0].target == "alice"

    def test_domain_param_forwarded(self):
        store = _mock_store([])
        engine = LensEngine(store)
        result = engine.apply("ownership_map", domain="billing")
        assert result.params["domain"] == "billing"


class TestApplyDomainBoundaries:
    def test_returns_cross_domain_calls(self):
        rows = [
            [
                "Function",
                "process_order",
                "order.py",
                "orders",
                "Function",
                "charge_card",
                "pay.py",
                "payments",
            ],
        ]
        store = _mock_store(rows)
        engine = LensEngine(store)
        result = engine.apply("domain_boundaries")
        assert result.lens == "domain_boundaries"
        assert len(result.nodes) == 2
        domains = {n.domain for n in result.nodes}
        assert "orders" in domains
        assert "payments" in domains
        assert len(result.edges) == 1
        assert result.edges[0].type == "CALLS"

    def test_empty_on_no_cross_domain(self):
        store = _mock_store([])
        engine = LensEngine(store)
        result = engine.apply("domain_boundaries")
        assert result.nodes == []
        assert result.edges == []


class TestApplyDependencyLayers:
    def test_returns_import_edges(self):
        rows = [
            ["Module", "app", "app.py", "Module", "utils", "utils.py"],
            ["Module", "app", "app.py", "Module", "models", "models.py"],
        ]
        store = _mock_store(rows)
        engine = LensEngine(store)
        result = engine.apply("dependency_layers")
        assert result.lens == "dependency_layers"
        assert len(result.nodes) == 3
        assert len(result.edges) == 2
        assert all(e.type == "IMPORTS" for e in result.edges)

    def test_file_path_param_forwarded(self):
        store = _mock_store([])
        engine = LensEngine(store)
        result = engine.apply("dependency_layers", file_path="app.py")
        assert result.params["file_path"] == "app.py"


class TestApplyFrameworkComponents:
    def test_returns_framework_nodes(self):
        rows = [
            ["Controller", "UserController", "ctrl.py"],
            ["Service", "AuthService", "auth.py"],
        ]
        store = _mock_store(rows)
        engine = LensEngine(store)
        result = engine.apply("framework_components")
        assert result.lens == "framework_components"
        assert len(result.nodes) == 2
        assert result.edges == []

    def test_label_param_forwarded(self):
        store = _mock_store([])
        engine = LensEngine(store)
        result = engine.apply("framework_components", label="Controller")
        assert result.params["label"] == "Controller"


# ── LensEngine.apply — custom lenses ────────────────────────────────────────


class TestApplyCustom:
    def test_runs_custom_cypher(self):
        rows = [
            ["Function", "my_func", "custom.py"],
        ]
        store = _mock_store(rows)
        engine = LensEngine(store)
        engine.register("my_lens", "MATCH (n) RETURN labels(n)[0], n.name, n.file_path")
        result = engine.apply("my_lens")
        assert result.lens == "my_lens"
        assert len(result.nodes) == 1
        assert result.nodes[0].name == "my_func"

    def test_custom_lens_passes_params(self):
        store = _mock_store([])
        engine = LensEngine(store)
        cypher = "MATCH (n) WHERE n.name = $symbol RETURN labels(n)[0], n.name, n.file_path"
        engine.register("cust", cypher)
        engine.apply("cust", symbol="test")
        call_kwargs = store.query.call_args[0][1]
        assert call_kwargs.get("symbol") == "test"


# ── LensEngine.apply — error handling ────────────────────────────────────────


class TestApplyErrors:
    def test_unknown_lens_raises_value_error(self):
        store = _mock_store()
        engine = LensEngine(store)
        with pytest.raises(ValueError, match="Unknown lens"):
            engine.apply("nonexistent_lens")

    def test_query_failure_returns_empty(self):
        store = MagicMock()
        store.query.side_effect = RuntimeError("db down")
        engine = LensEngine(store)
        result = engine.apply("request_path")
        assert result.nodes == []
        assert result.edges == []
