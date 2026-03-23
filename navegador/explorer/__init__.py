# Copyright CONFLICT LLC 2026 (weareconflict.com)
"""
Navegador Graph Explorer — HTTP server + browser-based visualisation.

Usage::

    from navegador.graph import GraphStore
    from navegador.explorer import ExplorerServer

    store = GraphStore.sqlite(".navegador/graph.db")
    server = ExplorerServer(store, host="127.0.0.1", port=8080)
    server.start()   # opens http://127.0.0.1:8080 in a thread
    ...
    server.stop()
"""

from .server import ExplorerServer

__all__ = ["ExplorerServer"]
