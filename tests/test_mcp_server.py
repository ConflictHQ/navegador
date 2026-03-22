"""Tests for navegador.mcp.server — create_mcp_server and tool handlers."""

from unittest.mock import MagicMock, patch

import pytest

from navegador.context.loader import ContextBundle, ContextNode


def _node(name="foo", type_="Function", file_path="app.py"):
    return ContextNode(name=name, type=type_, file_path=file_path)


def _bundle(name="target"):
    return ContextBundle(target=_node(name), nodes=[])


def _mock_store():
    store = MagicMock()
    store.query.return_value = MagicMock(result_set=[])
    store.node_count.return_value = 5
    store.edge_count.return_value = 3
    return store


def _mock_loader(store=None):
    from navegador.context import ContextLoader
    loader = MagicMock(spec=ContextLoader)
    loader.store = store or _mock_store()
    loader.load_file.return_value = _bundle()
    loader.load_function.return_value = _bundle()
    loader.load_class.return_value = _bundle()
    loader.search.return_value = []
    return loader


# ── create_mcp_server — import error ─────────────────────────────────────────

class TestCreateMcpServerImport:
    def test_raises_import_error_if_mcp_not_installed(self):
        with patch.dict("sys.modules", {"mcp": None, "mcp.server": None, "mcp.types": None}):
            from navegador.mcp.server import create_mcp_server
            with pytest.raises(ImportError, match="mcp"):
                create_mcp_server(lambda: _mock_store())


# ── create_mcp_server — happy path ────────────────────────────────────────────

class TestCreateMcpServer:
    def _make_server(self, loader=None):
        """Build a server with mocked mcp module."""
        mock_loader = loader or _mock_loader()
        mock_server = MagicMock()

        # Capture the decorated functions
        list_tools_fn = None
        call_tool_fn = None

        def list_tools_decorator():
            def decorator(fn):
                nonlocal list_tools_fn
                list_tools_fn = fn
                return fn
            return decorator

        def call_tool_decorator():
            def decorator(fn):
                nonlocal call_tool_fn
                call_tool_fn = fn
                return fn
            return decorator

        mock_server.list_tools = list_tools_decorator
        mock_server.call_tool = call_tool_decorator

        mock_mcp_server_module = MagicMock()
        mock_mcp_server_module.Server.return_value = mock_server
        mock_mcp_types_module = MagicMock()
        mock_mcp_types_module.Tool = MagicMock
        mock_mcp_types_module.TextContent = MagicMock

        with patch.dict("sys.modules", {
            "mcp": MagicMock(),
            "mcp.server": mock_mcp_server_module,
            "mcp.types": mock_mcp_types_module,
        }), patch("navegador.context.ContextLoader", return_value=mock_loader):
            from importlib import reload

            import navegador.mcp.server as srv
            reload(srv)
            srv.create_mcp_server(lambda: mock_loader.store)

        return list_tools_fn, call_tool_fn, mock_loader

    def test_returns_server(self):
        mock_server = MagicMock()
        mock_server.list_tools = lambda: lambda f: f
        mock_server.call_tool = lambda: lambda f: f

        mock_mcp_server_module = MagicMock()
        mock_mcp_server_module.Server.return_value = mock_server

        with patch.dict("sys.modules", {
            "mcp": MagicMock(),
            "mcp.server": mock_mcp_server_module,
            "mcp.types": MagicMock(),
        }):
            from importlib import reload

            import navegador.mcp.server as srv
            reload(srv)
            result = srv.create_mcp_server(lambda: _mock_store())
            assert result is mock_server

    def test_raises_if_mcp_not_available(self):
        with patch.dict("sys.modules", {
            "mcp": None, "mcp.server": None, "mcp.types": None,
        }):
            from importlib import reload

            import navegador.mcp.server as srv
            reload(srv)
            with pytest.raises(ImportError):
                srv.create_mcp_server(lambda: _mock_store())
