# Copyright CONFLICT LLC 2026 (weareconflict.com)
"""
Tests for navegador.explorer — ExplorerServer, API endpoints, HTML template,
and the CLI `explore` command.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from navegador.cli.commands import main
from navegador.explorer import ExplorerServer
from navegador.explorer.templates import HTML_TEMPLATE

# ── Helpers ────────────────────────────────────────────────────────────────


def _mock_store(
    *,
    nodes: list | None = None,
    edges: list | None = None,
    node_count: int = 3,
    edge_count: int = 2,
):
    """Return a minimal GraphStore mock suitable for explorer tests."""
    store = MagicMock()
    store.node_count.return_value = node_count
    store.edge_count.return_value = edge_count

    # Each query() call returns a result-set mock. We cycle through prebuilt
    # responses so different Cypher patterns get appropriate data.
    _node_rows = nodes or []
    _edge_rows = edges or []

    def _query_side_effect(cypher: str, params=None):
        result = MagicMock()
        cypher_lower = cypher.lower()
        if "match (a)-[r]->(b)" in cypher_lower:
            result.result_set = _edge_rows
        elif "match (n)-[r]->(nb)" in cypher_lower or "match (nb)-[r]->(n)" in cypher_lower:
            result.result_set = []
        elif "match (n) where n.name" in cypher_lower and "properties" in cypher_lower:
            # node detail: single node row
            result.result_set = [["Function", {"name": "foo", "file_path": "app.py"}]]
        elif "match (n)" in cypher_lower and "tolow" in cypher_lower:
            result.result_set = [
                ["Function", "foo", "app.py", ""],
            ]
        elif "labels(n)" in cypher_lower and "count" in cypher_lower:
            result.result_set = [["Function", 2], ["Class", 1]]
        elif "type(r)" in cypher_lower and "count" in cypher_lower:
            result.result_set = [["CALLS", 2]]
        else:
            result.result_set = _node_rows
        return result

    store.query.side_effect = _query_side_effect
    return store


def _free_port() -> int:
    """Return an available TCP port on localhost."""
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _fetch(url: str, timeout: float = 5.0) -> tuple[int, str]:
    """GET *url* and return (status_code, response_body_str)."""
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.status, resp.read().decode()


def _fetch_json(url: str, timeout: float = 5.0) -> tuple[int, dict | list]:
    status, body = _fetch(url, timeout)
    return status, json.loads(body)


# ── ExplorerServer creation ────────────────────────────────────────────────


class TestExplorerServerCreation:
    def test_default_host_and_port(self):
        store = _mock_store()
        server = ExplorerServer(store)
        assert server.host == "127.0.0.1"
        assert server.port == 8080
        assert server.store is store

    def test_custom_host_and_port(self):
        store = _mock_store()
        server = ExplorerServer(store, host="0.0.0.0", port=9999)
        assert server.host == "0.0.0.0"
        assert server.port == 9999

    def test_url_property(self):
        server = ExplorerServer(_mock_store(), host="127.0.0.1", port=8080)
        assert server.url == "http://127.0.0.1:8080"

    def test_not_running_by_default(self):
        server = ExplorerServer(_mock_store(), port=_free_port())
        assert server._server is None
        assert server._thread is None

    def test_double_start_raises(self):
        port = _free_port()
        server = ExplorerServer(_mock_store(), port=port)
        server.start()
        try:
            with pytest.raises(RuntimeError, match="already running"):
                server.start()
        finally:
            server.stop()

    def test_stop_when_not_started_is_noop(self):
        server = ExplorerServer(_mock_store(), port=_free_port())
        server.stop()  # should not raise

    def test_context_manager(self):
        port = _free_port()
        store = _mock_store()
        with ExplorerServer(store, port=port) as srv:
            assert srv._server is not None
        assert srv._server is None


# ── Start / stop lifecycle ─────────────────────────────────────────────────


class TestExplorerServerLifecycle:
    def test_start_makes_server_accessible(self):
        port = _free_port()
        server = ExplorerServer(_mock_store(), port=port)
        server.start()
        try:
            status, _ = _fetch(f"http://127.0.0.1:{port}/")
            assert status == 200
        finally:
            server.stop()

    def test_stop_takes_server_offline(self):
        port = _free_port()
        server = ExplorerServer(_mock_store(), port=port)
        server.start()
        server.stop()
        with pytest.raises(Exception):
            _fetch(f"http://127.0.0.1:{port}/", timeout=1.0)

    def test_thread_is_daemon(self):
        port = _free_port()
        server = ExplorerServer(_mock_store(), port=port)
        server.start()
        try:
            assert server._thread is not None
            assert server._thread.daemon is True
        finally:
            server.stop()


# ── API endpoint: GET / ────────────────────────────────────────────────────


class TestRootEndpoint:
    def test_returns_html(self):
        port = _free_port()
        with ExplorerServer(_mock_store(), port=port):
            status, body = _fetch(f"http://127.0.0.1:{port}/")
        assert status == 200
        assert "<!DOCTYPE html>" in body or "<!doctype html>" in body.lower()

    def test_html_contains_canvas(self):
        port = _free_port()
        with ExplorerServer(_mock_store(), port=port):
            _, body = _fetch(f"http://127.0.0.1:{port}/")
        assert "graph-canvas" in body

    def test_html_contains_search_box(self):
        port = _free_port()
        with ExplorerServer(_mock_store(), port=port):
            _, body = _fetch(f"http://127.0.0.1:{port}/")
        assert "search-box" in body

    def test_html_contains_api_calls(self):
        port = _free_port()
        with ExplorerServer(_mock_store(), port=port):
            _, body = _fetch(f"http://127.0.0.1:{port}/")
        assert "/api/graph" in body


# ── API endpoint: GET /api/graph ──────────────────────────────────────────


class TestGraphEndpoint:
    def _make_node_rows(self):
        # Rows returned for the full-node Cypher query
        return [
            [1, "Function", "foo", {"name": "foo", "file_path": "app.py"}],
            [2, "Class", "Bar", {"name": "Bar", "file_path": "app.py"}],
        ]

    def test_returns_nodes_and_edges_keys(self):
        port = _free_port()
        store = _mock_store(nodes=self._make_node_rows(), edges=[])
        with ExplorerServer(store, port=port):
            status, data = _fetch_json(f"http://127.0.0.1:{port}/api/graph")
        assert status == 200
        assert "nodes" in data
        assert "edges" in data

    def test_nodes_have_required_fields(self):
        port = _free_port()
        store = _mock_store(nodes=self._make_node_rows())
        with ExplorerServer(store, port=port):
            _, data = _fetch_json(f"http://127.0.0.1:{port}/api/graph")
        for node in data["nodes"]:
            assert "id" in node
            assert "label" in node
            assert "name" in node

    def test_empty_graph(self):
        port = _free_port()
        store = _mock_store(nodes=[], edges=[], node_count=0, edge_count=0)
        with ExplorerServer(store, port=port):
            _, data = _fetch_json(f"http://127.0.0.1:{port}/api/graph")
        assert data["nodes"] == []
        assert data["edges"] == []

    def test_edges_have_required_fields(self):
        port = _free_port()
        edge_rows = [[1, 2, "CALLS"]]
        store = _mock_store(nodes=self._make_node_rows(), edges=edge_rows)
        with ExplorerServer(store, port=port):
            _, data = _fetch_json(f"http://127.0.0.1:{port}/api/graph")
        for edge in data["edges"]:
            assert "source" in edge
            assert "target" in edge
            assert "type" in edge


# ── API endpoint: GET /api/search ─────────────────────────────────────────


class TestSearchEndpoint:
    def test_returns_nodes_key(self):
        port = _free_port()
        with ExplorerServer(_mock_store(), port=port):
            _, data = _fetch_json(f"http://127.0.0.1:{port}/api/search?q=foo")
        assert "nodes" in data

    def test_empty_query_returns_empty(self):
        port = _free_port()
        with ExplorerServer(_mock_store(), port=port):
            _, data = _fetch_json(f"http://127.0.0.1:{port}/api/search?q=")
        assert data["nodes"] == []

    def test_missing_q_returns_empty(self):
        port = _free_port()
        with ExplorerServer(_mock_store(), port=port):
            _, data = _fetch_json(f"http://127.0.0.1:{port}/api/search")
        assert data["nodes"] == []

    def test_result_nodes_have_name_and_label(self):
        port = _free_port()
        with ExplorerServer(_mock_store(), port=port):
            _, data = _fetch_json(f"http://127.0.0.1:{port}/api/search?q=foo")
        for node in data["nodes"]:
            assert "name" in node
            assert "label" in node


# ── API endpoint: GET /api/node/<name> ────────────────────────────────────


class TestNodeDetailEndpoint:
    def test_returns_name_label_props_neighbors(self):
        port = _free_port()
        with ExplorerServer(_mock_store(), port=port):
            _, data = _fetch_json(f"http://127.0.0.1:{port}/api/node/foo")
        assert "name" in data
        assert "label" in data
        assert "props" in data
        assert "neighbors" in data

    def test_name_matches_request(self):
        port = _free_port()
        with ExplorerServer(_mock_store(), port=port):
            _, data = _fetch_json(f"http://127.0.0.1:{port}/api/node/foo")
        assert data["name"] == "foo"

    def test_url_encoded_name(self):
        port = _free_port()
        with ExplorerServer(_mock_store(), port=port):
            _, data = _fetch_json(f"http://127.0.0.1:{port}/api/node/my%20node")
        assert data["name"] == "my node"

    def test_unknown_node_returns_empty_detail(self):
        port = _free_port()
        store = _mock_store()
        # Override query to return empty for the node-detail lookup
        original_side_effect = store.query.side_effect

        def _empty_node(cypher, params=None):
            if "where n.name" in cypher.lower() and "properties" in cypher.lower():
                r = MagicMock()
                r.result_set = []
                return r
            return original_side_effect(cypher, params)

        store.query.side_effect = _empty_node
        with ExplorerServer(store, port=port):
            _, data = _fetch_json(f"http://127.0.0.1:{port}/api/node/nonexistent")
        assert data["neighbors"] == []


# ── API endpoint: GET /api/stats ──────────────────────────────────────────


class TestStatsEndpoint:
    def test_returns_nodes_and_edges_counts(self):
        port = _free_port()
        with ExplorerServer(_mock_store(node_count=5, edge_count=3), port=port):
            _, data = _fetch_json(f"http://127.0.0.1:{port}/api/stats")
        assert data["nodes"] == 5
        assert data["edges"] == 3

    def test_returns_node_types_and_edge_types(self):
        port = _free_port()
        with ExplorerServer(_mock_store(), port=port):
            _, data = _fetch_json(f"http://127.0.0.1:{port}/api/stats")
        assert "node_types" in data
        assert "edge_types" in data
        assert isinstance(data["node_types"], dict)
        assert isinstance(data["edge_types"], dict)

    def test_node_type_counts_sum(self):
        port = _free_port()
        with ExplorerServer(_mock_store(node_count=3), port=port):
            _, data = _fetch_json(f"http://127.0.0.1:{port}/api/stats")
        total = sum(data["node_types"].values())
        # The mock returns Function:2, Class:1 → total 3
        assert total == 3


# ── API endpoint: GET /api/snapshots ─────────────────────────────────────


class TestSnapshotsEndpoint:
    def _snapshot_store(self):
        """Return a store mock that returns snapshot data for the /api/snapshots Cypher."""
        store = MagicMock()
        store.node_count.return_value = 3
        store.edge_count.return_value = 2

        def _query_side_effect(cypher, params=None):
            result = MagicMock()
            cypher_lower = cypher.lower()
            if "match (s:snapshot)" in cypher_lower and "order by" in cypher_lower:
                result.result_set = [
                    ["v1.0.0", "abc123", "2025-01-10 12:00:00", 42],
                    ["v2.0.0", "def456", "2025-06-15 09:30:00", 87],
                ]
            else:
                result.result_set = []
            return result

        store.query.side_effect = _query_side_effect
        return store

    def test_returns_list(self):
        port = _free_port()
        with ExplorerServer(self._snapshot_store(), port=port):
            status, data = _fetch_json(f"http://127.0.0.1:{port}/api/snapshots")
        assert status == 200
        assert isinstance(data, list)

    def test_snapshot_fields(self):
        port = _free_port()
        with ExplorerServer(self._snapshot_store(), port=port):
            _, data = _fetch_json(f"http://127.0.0.1:{port}/api/snapshots")
        assert len(data) == 2
        snap = data[0]
        assert snap["ref"] == "v1.0.0"
        assert snap["commit_sha"] == "abc123"
        assert snap["committed_at"] == "2025-01-10 12:00:00"
        assert snap["symbol_count"] == 42

    def test_empty_snapshots(self):
        port = _free_port()
        store = MagicMock()
        store.node_count.return_value = 0
        store.edge_count.return_value = 0

        def _query_side_effect(cypher, params=None):
            result = MagicMock()
            result.result_set = []
            return result

        store.query.side_effect = _query_side_effect
        with ExplorerServer(store, port=port):
            _, data = _fetch_json(f"http://127.0.0.1:{port}/api/snapshots")
        assert data == []


# ── API endpoint: GET /api/node/<name>/history ───────────────────────────


class TestNodeHistoryEndpoint:
    def _history_store(self):
        """Return a store mock whose queries satisfy HistoryStore.history()."""
        store = MagicMock()
        store.node_count.return_value = 3
        store.edge_count.return_value = 2

        def _query_side_effect(cypher, params=None):
            result = MagicMock()
            cypher_lower = cypher.lower()
            if (
                "match (s:snapshot)-[:snapshot_of]->(n)" in cypher_lower
                and "n.name" in cypher_lower
            ):
                # _SNAPSHOTS_FOR_SYMBOL result
                result.result_set = [
                    ["v1.0.0", "2025-01-10", "Function", "AuthService", "app/auth.py"],
                    ["v2.0.0", "2025-06-15", "Function", "AuthService", "app/auth.py"],
                ]
            elif "match (s:snapshot)" in cypher_lower and "order by" in cypher_lower:
                # _LIST_SNAPSHOTS result (used for removal detection)
                result.result_set = [
                    ["v1.0.0", "abc123", "2025-01-10", 42],
                    ["v2.0.0", "def456", "2025-06-15", 87],
                ]
            else:
                result.result_set = []
            return result

        store.query.side_effect = _query_side_effect
        return store

    def test_returns_symbol_and_events(self):
        port = _free_port()
        with ExplorerServer(self._history_store(), port=port):
            status, data = _fetch_json(f"http://127.0.0.1:{port}/api/node/AuthService/history")
        assert status == 200
        assert data["symbol"] == "AuthService"
        assert "events" in data
        assert isinstance(data["events"], list)

    def test_first_event_is_first_seen(self):
        port = _free_port()
        with ExplorerServer(self._history_store(), port=port):
            _, data = _fetch_json(f"http://127.0.0.1:{port}/api/node/AuthService/history")
        events = data["events"]
        assert len(events) >= 1
        assert events[0]["event"] == "first_seen"
        assert events[0]["ref"] == "v1.0.0"

    def test_event_fields(self):
        port = _free_port()
        with ExplorerServer(self._history_store(), port=port):
            _, data = _fetch_json(f"http://127.0.0.1:{port}/api/node/AuthService/history")
        for event in data["events"]:
            assert "ref" in event
            assert "event" in event
            assert "name" in event
            assert "file_path" in event
            assert "detail" in event

    def test_file_path_query_param(self):
        port = _free_port()
        with ExplorerServer(self._history_store(), port=port):
            status, data = _fetch_json(
                f"http://127.0.0.1:{port}/api/node/AuthService/history?file_path=app/auth.py"
            )
        assert status == 200
        assert data["file_path"] == "app/auth.py"

    def test_no_history_returns_empty_events(self):
        port = _free_port()
        store = MagicMock()
        store.node_count.return_value = 0
        store.edge_count.return_value = 0

        def _query_side_effect(cypher, params=None):
            result = MagicMock()
            result.result_set = []
            return result

        store.query.side_effect = _query_side_effect
        with ExplorerServer(store, port=port):
            _, data = _fetch_json(f"http://127.0.0.1:{port}/api/node/Unknown/history")
        assert data["events"] == []


# ── API endpoint: GET /api/snapshots/<ref>/symbols ───────────────────────


class TestSnapshotSymbolsEndpoint:
    def _symbols_store(self):
        """Return a store mock whose queries satisfy HistoryStore.symbols_at()."""
        store = MagicMock()
        store.node_count.return_value = 3
        store.edge_count.return_value = 2

        def _query_side_effect(cypher, params=None):
            result = MagicMock()
            cypher_lower = cypher.lower()
            if "match (s:snapshot" in cypher_lower and "snapshot_of" in cypher_lower:
                result.result_set = [
                    ["Function", "authenticate", "app/auth.py", 10, 45],
                    ["Class", "AuthService", "app/auth.py", 1, 80],
                ]
            else:
                result.result_set = []
            return result

        store.query.side_effect = _query_side_effect
        return store

    def test_returns_list(self):
        port = _free_port()
        with ExplorerServer(self._symbols_store(), port=port):
            status, data = _fetch_json(f"http://127.0.0.1:{port}/api/snapshots/v1.0.0/symbols")
        assert status == 200
        assert isinstance(data, list)

    def test_symbol_fields(self):
        port = _free_port()
        with ExplorerServer(self._symbols_store(), port=port):
            _, data = _fetch_json(f"http://127.0.0.1:{port}/api/snapshots/v1.0.0/symbols")
        assert len(data) == 2
        sym = data[0]
        assert sym["ref"] == "v1.0.0"
        assert sym["label"] == "Function"
        assert sym["name"] == "authenticate"
        assert sym["file_path"] == "app/auth.py"
        assert sym["line_start"] == 10
        assert sym["line_end"] == 45

    def test_url_encoded_ref(self):
        port = _free_port()
        with ExplorerServer(self._symbols_store(), port=port):
            status, _ = _fetch_json(f"http://127.0.0.1:{port}/api/snapshots/v1.0.0/symbols")
        assert status == 200

    def test_empty_snapshot(self):
        port = _free_port()
        store = MagicMock()
        store.node_count.return_value = 0
        store.edge_count.return_value = 0

        def _query_side_effect(cypher, params=None):
            result = MagicMock()
            result.result_set = []
            return result

        store.query.side_effect = _query_side_effect
        with ExplorerServer(store, port=port):
            _, data = _fetch_json(f"http://127.0.0.1:{port}/api/snapshots/nonexistent/symbols")
        assert data == []


# ── API endpoint: GET /api/lenses ────────────────────────────────────────


class TestLensesListEndpoint:
    def test_returns_list(self):
        port = _free_port()
        with ExplorerServer(_mock_store(), port=port):
            status, data = _fetch_json(f"http://127.0.0.1:{port}/api/lenses")
        assert status == 200
        assert isinstance(data, list)

    def test_contains_builtin_lenses(self):
        port = _free_port()
        with ExplorerServer(_mock_store(), port=port):
            _, data = _fetch_json(f"http://127.0.0.1:{port}/api/lenses")
        names = {item["name"] for item in data}
        assert "request_path" in names
        assert "ownership_map" in names
        assert "domain_boundaries" in names

    def test_lens_fields(self):
        port = _free_port()
        with ExplorerServer(_mock_store(), port=port):
            _, data = _fetch_json(f"http://127.0.0.1:{port}/api/lenses")
        for item in data:
            assert "name" in item
            assert "description" in item
            assert "builtin" in item


# ── API endpoint: GET /api/lenses/<name> ─────────────────────────────────


class TestLensApplyEndpoint:
    def test_returns_lens_result(self):
        port = _free_port()
        with ExplorerServer(_mock_store(), port=port):
            status, data = _fetch_json(f"http://127.0.0.1:{port}/api/lenses/ownership_map")
        assert status == 200
        assert "lens" in data
        assert "nodes" in data
        assert "edges" in data
        assert data["lens"] == "ownership_map"

    def test_unknown_lens_returns_400(self):
        port = _free_port()
        with ExplorerServer(_mock_store(), port=port):
            with pytest.raises(urllib.error.HTTPError) as exc_info:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/api/lenses/nonexistent")
        assert exc_info.value.code == 400

    def test_accepts_query_params(self):
        port = _free_port()
        with ExplorerServer(_mock_store(), port=port):
            status, data = _fetch_json(
                f"http://127.0.0.1:{port}/api/lenses/ownership_map?domain=billing"
            )
        assert status == 200
        assert data["params"]["domain"] == "billing"


# ── 404 for unknown routes ─────────────────────────────────────────────────


class TestNotFound:
    def test_unknown_path_returns_404(self):
        port = _free_port()
        with ExplorerServer(_mock_store(), port=port):
            with pytest.raises(urllib.error.HTTPError) as exc_info:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/api/nonexistent")
        assert exc_info.value.code == 404


# ── HTML template ─────────────────────────────────────────────────────────


class TestHtmlTemplate:
    def test_is_string(self):
        assert isinstance(HTML_TEMPLATE, str)

    def test_contains_doctype(self):
        assert "<!DOCTYPE html>" in HTML_TEMPLATE

    def test_contains_canvas(self):
        assert "graph-canvas" in HTML_TEMPLATE

    def test_contains_search_box(self):
        assert "search-box" in HTML_TEMPLATE

    def test_contains_detail_panel(self):
        assert "detail-panel" in HTML_TEMPLATE

    def test_contains_api_graph_fetch(self):
        assert "/api/graph" in HTML_TEMPLATE

    def test_contains_api_search_fetch(self):
        assert "/api/search" in HTML_TEMPLATE

    def test_contains_api_node_fetch(self):
        assert "/api/node/" in HTML_TEMPLATE

    def test_contains_api_stats_fetch(self):
        assert "/api/stats" in HTML_TEMPLATE

    def test_contains_timeline_panel(self):
        assert "timeline-panel" in HTML_TEMPLATE

    def test_contains_api_snapshots_fetch(self):
        assert "/api/snapshots" in HTML_TEMPLATE

    def test_contains_history_fetch(self):
        assert "/history" in HTML_TEMPLATE

    def test_contains_snapshot_filter_state(self):
        assert "snapshotFilterNames" in HTML_TEMPLATE

    def test_contains_lens_panel(self):
        assert "lens-panel" in HTML_TEMPLATE

    def test_contains_lens_select(self):
        assert "lens-select" in HTML_TEMPLATE

    def test_contains_lens_api_fetch(self):
        assert "/api/lenses" in HTML_TEMPLATE

    def test_contains_lens_node_state(self):
        assert "lensNodeNames" in HTML_TEMPLATE

    def test_no_external_deps(self):
        """No CDN or external URLs should appear in the template."""
        import re

        # Look for any http(s):// URLs — internal /api/ paths are fine
        external = re.findall(r"https?://\S+", HTML_TEMPLATE)
        assert external == [], f"External URLs found: {external}"

    def test_contains_force_directed_physics(self):
        lower = HTML_TEMPLATE.lower()
        assert "REPEL" in HTML_TEMPLATE or "repulsion" in lower or "force" in lower

    def test_colors_injected(self):
        assert "Function" in HTML_TEMPLATE
        assert "Class" in HTML_TEMPLATE

    def test_self_contained_script_tag(self):
        assert "<script>" in HTML_TEMPLATE

    def test_self_contained_style_tag(self):
        assert "<style>" in HTML_TEMPLATE


# ── CLI command: navegador explore ────────────────────────────────────────


class TestExploreCLI:
    def test_help_text(self):
        runner = CliRunner()
        result = runner.invoke(main, ["explore", "--help"])
        assert result.exit_code == 0
        assert "explore" in result.output.lower() or "graph" in result.output.lower()

    def test_explore_command_registered(self):
        """Verify the explore command is registered under the main group."""
        from navegador.cli.commands import main as cli_main

        assert "explore" in cli_main.commands

    def test_explore_starts_and_stops(self):
        """CLI explore should start ExplorerServer and stop cleanly on KeyboardInterrupt."""
        runner = CliRunner()
        port = _free_port()

        mock_srv = MagicMock()
        mock_srv.url = f"http://127.0.0.1:{port}"

        call_count = [0]

        def _fake_sleep(seconds):
            # Let the first call (browser delay) pass, raise on second (main loop)
            call_count[0] += 1
            if call_count[0] >= 2:
                raise KeyboardInterrupt

        # The explore command does local imports, so patch at the source modules.
        with (
            patch("navegador.explorer.ExplorerServer", return_value=mock_srv),
            patch("navegador.cli.commands._get_store", return_value=MagicMock()),
            patch("time.sleep", side_effect=_fake_sleep),
            patch("webbrowser.open"),
        ):
            result = runner.invoke(main, ["explore", "--port", str(port)])

        mock_srv.start.assert_called_once()
        mock_srv.stop.assert_called_once()
        assert result.exit_code == 0

    def test_explore_no_browser_flag(self):
        """--no-browser should skip webbrowser.open."""
        runner = CliRunner()
        port = _free_port()

        mock_srv = MagicMock()
        mock_srv.url = f"http://127.0.0.1:{port}"

        def _fake_sleep(seconds):
            raise KeyboardInterrupt

        with (
            patch("navegador.explorer.ExplorerServer", return_value=mock_srv),
            patch("navegador.cli.commands._get_store", return_value=MagicMock()),
            patch("time.sleep", side_effect=_fake_sleep),
            patch("webbrowser.open") as mock_open,
        ):
            result = runner.invoke(main, ["explore", "--no-browser", "--port", str(port)])

        mock_open.assert_not_called()
        assert result.exit_code == 0

    def test_explore_custom_port(self):
        """--port option should be forwarded to ExplorerServer."""
        runner = CliRunner()
        port = _free_port()

        captured = {}

        def _fake_server(store, host, port):  # noqa: A002
            captured["port"] = port
            srv = MagicMock()
            srv.url = f"http://{host}:{port}"
            return srv

        def _fake_sleep(seconds):
            raise KeyboardInterrupt

        with (
            patch("navegador.explorer.ExplorerServer", side_effect=_fake_server),
            patch("navegador.cli.commands._get_store", return_value=MagicMock()),
            patch("time.sleep", side_effect=_fake_sleep),
            patch("webbrowser.open"),
        ):
            runner.invoke(main, ["explore", "--port", str(port)])

        assert captured.get("port") == port

    def test_explore_custom_host(self):
        """--host option should be forwarded to ExplorerServer."""
        runner = CliRunner()
        captured = {}

        def _fake_server(store, host, port):  # noqa: A002
            captured["host"] = host
            srv = MagicMock()
            srv.url = f"http://{host}:{port}"
            return srv

        def _fake_sleep(seconds):
            raise KeyboardInterrupt

        with (
            patch("navegador.explorer.ExplorerServer", side_effect=_fake_server),
            patch("navegador.cli.commands._get_store", return_value=MagicMock()),
            patch("time.sleep", side_effect=_fake_sleep),
            patch("webbrowser.open"),
        ):
            runner.invoke(main, ["explore", "--host", "0.0.0.0"])

        assert captured.get("host") == "0.0.0.0"

    def test_explore_output_shows_url(self):
        """explore should print the server URL to stdout."""
        runner = CliRunner()
        port = _free_port()

        mock_srv = MagicMock()
        mock_srv.url = f"http://127.0.0.1:{port}"

        def _fake_sleep(seconds):
            raise KeyboardInterrupt

        with (
            patch("navegador.explorer.ExplorerServer", return_value=mock_srv),
            patch("navegador.cli.commands._get_store", return_value=MagicMock()),
            patch("time.sleep", side_effect=_fake_sleep),
            patch("webbrowser.open"),
        ):
            result = runner.invoke(main, ["explore", "--port", str(port)])

        assert str(port) in result.output or "127.0.0.1" in result.output
