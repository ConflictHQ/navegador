"""Tests for navegador.graph.export — text-based graph export and import."""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from navegador.graph.export import (
    _export_edges,
    _export_nodes,
    _import_edge,
    _import_node,
    export_graph,
    import_graph,
)


def _mock_store(nodes=None, edges=None):
    store = MagicMock()

    def query_side_effect(cypher, params=None):
        result = MagicMock()
        if "labels(n)" in cypher and "properties" in cypher:
            result.result_set = nodes or []
        elif "type(r)" in cypher:
            result.result_set = edges or []
        elif "DETACH DELETE" in cypher:
            result.result_set = []
        else:
            result.result_set = []
        return result

    store.query.side_effect = query_side_effect
    store.clear = MagicMock()
    return store


# ── export_graph ─────────────────────────────────────────────────────────────

class TestExportGraph:
    def test_creates_output_file(self):
        store = _mock_store(nodes=[], edges=[])
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "graph.jsonl"
            export_graph(store, output)
            assert output.exists()

    def test_returns_counts(self):
        nodes = [["Function", {"name": "foo", "file_path": "app.py"}]]
        store = _mock_store(nodes=nodes, edges=[])
        with tempfile.TemporaryDirectory() as tmpdir:
            stats = export_graph(store, Path(tmpdir) / "graph.jsonl")
            assert stats["nodes"] == 1
            assert stats["edges"] == 0

    def test_writes_valid_jsonl(self):
        nodes = [
            ["Function", {"name": "foo", "file_path": "app.py"}],
            ["Class", {"name": "Bar", "file_path": "bar.py"}],
        ]
        edges = [["CALLS", "Function", "foo", "app.py", "Function", "bar", "bar.py"]]
        store = _mock_store(nodes=nodes, edges=edges)
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "graph.jsonl"
            export_graph(store, output)
            lines = output.read_text().strip().split("\n")
            assert len(lines) == 3  # 2 nodes + 1 edge
            for line in lines:
                record = json.loads(line)
                assert record["kind"] in ("node", "edge")

    def test_output_is_sorted(self):
        nodes = [
            ["Function", {"name": "z_func", "file_path": "z.py"}],
            ["Class", {"name": "a_class", "file_path": "a.py"}],
        ]
        store = _mock_store(nodes=nodes, edges=[])
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "graph.jsonl"
            export_graph(store, output)
            lines = output.read_text().strip().split("\n")
            labels = [json.loads(line)["label"] for line in lines]
            # Class comes before Function alphabetically
            assert labels[0] == "Class"
            assert labels[1] == "Function"

    def test_creates_parent_dirs(self):
        store = _mock_store(nodes=[], edges=[])
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "sub" / "dir" / "graph.jsonl"
            export_graph(store, output)
            assert output.exists()


# ── import_graph ─────────────────────────────────────────────────────────────

class TestImportGraph:
    def test_raises_on_missing_file(self):
        store = MagicMock()
        with pytest.raises(FileNotFoundError):
            import_graph(store, "/nonexistent/graph.jsonl")

    def test_clears_graph_by_default(self):
        store = MagicMock()
        store.query.return_value = MagicMock(result_set=[])
        with tempfile.TemporaryDirectory() as tmpdir:
            f = Path(tmpdir) / "graph.jsonl"
            f.write_text("")
            import_graph(store, f)
            store.clear.assert_called_once()

    def test_no_clear_flag(self):
        store = MagicMock()
        store.query.return_value = MagicMock(result_set=[])
        with tempfile.TemporaryDirectory() as tmpdir:
            f = Path(tmpdir) / "graph.jsonl"
            f.write_text("")
            import_graph(store, f, clear=False)
            store.clear.assert_not_called()

    def test_imports_nodes(self):
        store = MagicMock()
        store.query.return_value = MagicMock(result_set=[])
        with tempfile.TemporaryDirectory() as tmpdir:
            f = Path(tmpdir) / "graph.jsonl"
            node = {"kind": "node", "label": "Function", "props": {"name": "foo", "file_path": "app.py"}}
            f.write_text(json.dumps(node) + "\n")
            stats = import_graph(store, f)
            assert stats["nodes"] == 1

    def test_imports_edges(self):
        store = MagicMock()
        store.query.return_value = MagicMock(result_set=[])
        with tempfile.TemporaryDirectory() as tmpdir:
            f = Path(tmpdir) / "graph.jsonl"
            edge = {
                "kind": "edge",
                "type": "CALLS",
                "from": {"label": "Function", "name": "foo", "path": "app.py"},
                "to": {"label": "Function", "name": "bar", "path": "bar.py"},
            }
            f.write_text(json.dumps(edge) + "\n")
            stats = import_graph(store, f)
            assert stats["edges"] == 1

    def test_returns_counts(self):
        store = MagicMock()
        store.query.return_value = MagicMock(result_set=[])
        with tempfile.TemporaryDirectory() as tmpdir:
            f = Path(tmpdir) / "graph.jsonl"
            lines = [
                json.dumps({"kind": "node", "label": "Function", "props": {"name": "foo", "file_path": "app.py"}}),
                json.dumps({"kind": "node", "label": "Class", "props": {"name": "Bar", "file_path": "bar.py"}}),
                json.dumps({"kind": "edge", "type": "CALLS",
                           "from": {"label": "Function", "name": "foo", "path": ""},
                           "to": {"label": "Class", "name": "Bar", "path": ""}}),
            ]
            f.write_text("\n".join(lines) + "\n")
            stats = import_graph(store, f)
            assert stats["nodes"] == 2
            assert stats["edges"] == 1

    def test_skips_blank_lines(self):
        store = MagicMock()
        store.query.return_value = MagicMock(result_set=[])
        with tempfile.TemporaryDirectory() as tmpdir:
            f = Path(tmpdir) / "graph.jsonl"
            node = json.dumps({"kind": "node", "label": "Function", "props": {"name": "foo", "file_path": ""}})
            f.write_text(f"\n{node}\n\n")
            stats = import_graph(store, f)
            assert stats["nodes"] == 1


# ── _export_nodes / _export_edges ────────────────────────────────────────────

class TestExportHelpers:
    def test_export_nodes_handles_non_dict_props(self):
        store = MagicMock()
        store.query.return_value = MagicMock(result_set=[["Function", "not_a_dict"]])
        nodes = _export_nodes(store)
        assert len(nodes) == 1
        assert nodes[0]["props"] == {}

    def test_export_edges_returns_structured_data(self):
        store = MagicMock()
        store.query.return_value = MagicMock(
            result_set=[["CALLS", "Function", "foo", "app.py", "Function", "bar", "bar.py"]]
        )
        edges = _export_edges(store)
        assert len(edges) == 1
        assert edges[0]["type"] == "CALLS"
        assert edges[0]["from"]["name"] == "foo"
        assert edges[0]["to"]["name"] == "bar"


# ── _import_node / _import_edge ──────────────────────────────────────────────

class TestImportHelpers:
    def test_import_node_adds_missing_file_path(self):
        store = MagicMock()
        store.query.return_value = MagicMock(result_set=[])
        record = {"kind": "node", "label": "Concept", "props": {"name": "JWT"}}
        _import_node(store, record)
        store.query.assert_called_once()
        cypher = store.query.call_args[0][0]
        assert "MERGE" in cypher

    def test_import_node_adds_missing_name(self):
        store = MagicMock()
        store.query.return_value = MagicMock(result_set=[])
        record = {"kind": "node", "label": "Domain", "props": {"description": "Auth domain"}}
        _import_node(store, record)
        # Should have added name="" to props
        store.query.assert_called_once()
        params = store.query.call_args[0][1]
        assert params["name"] == ""

    def test_import_node_uses_path_key_for_repos(self):
        store = MagicMock()
        store.query.return_value = MagicMock(result_set=[])
        record = {"kind": "node", "label": "Repository", "props": {"name": "myrepo", "path": "/code/myrepo"}}
        _import_node(store, record)
        cypher = store.query.call_args[0][0]
        assert "path" in cypher

    def test_import_edge_with_paths(self):
        store = MagicMock()
        store.query.return_value = MagicMock(result_set=[])
        record = {
            "kind": "edge",
            "type": "CALLS",
            "from": {"label": "Function", "name": "foo", "path": "app.py"},
            "to": {"label": "Function", "name": "bar", "path": "bar.py"},
        }
        _import_edge(store, record)
        store.query.assert_called_once()

    def test_import_edge_without_paths(self):
        store = MagicMock()
        store.query.return_value = MagicMock(result_set=[])
        record = {
            "kind": "edge",
            "type": "RELATED_TO",
            "from": {"label": "Concept", "name": "JWT", "path": ""},
            "to": {"label": "Concept", "name": "OAuth", "path": ""},
        }
        _import_edge(store, record)
        store.query.assert_called_once()
        cypher = store.query.call_args[0][0]
        assert "file_path" not in cypher
