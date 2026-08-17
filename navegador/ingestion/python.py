"""
Python AST parser — extracts classes, functions, imports, calls, and
their relationships from .py files using tree-sitter.
"""

import logging
from pathlib import Path

from navegador.graph.schema import EdgeType, NodeLabel
from navegador.graph.store import GraphStore
from navegador.ingestion.parser import LanguageParser

logger = logging.getLogger(__name__)


def _get_python_language():
    try:
        import tree_sitter_python as tspython  # type: ignore[import]
        from tree_sitter import Language

        return Language(tspython.language())
    except ImportError as e:
        raise ImportError("Install tree-sitter-python: pip install tree-sitter-python") from e


def _get_parser():
    from tree_sitter import Parser  # type: ignore[import]

    parser = Parser(_get_python_language())
    return parser


def _node_text(node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _get_docstring(node, source: bytes) -> str | None:
    """Extract the first string literal from a function/class body as docstring."""
    body = next((c for c in node.children if c.type == "block"), None)
    if not body:
        return None
    first_stmt = next((c for c in body.children if c.type == "expression_statement"), None)
    if not first_stmt:
        return None
    string_node = next(
        (c for c in first_stmt.children if c.type in ("string", "string_content")), None
    )
    if string_node:
        raw = _node_text(string_node, source)
        return raw.strip('"""').strip("'''").strip('"').strip("'").strip()
    return None


def _parse_import_from(node, source: bytes) -> tuple[str, list[tuple[str, str]]]:
    """
    Split a ``from X import a, b as c`` statement into its module and bindings.

    The grammar labels the module and every imported member alike, as
    ``dotted_name``; only their position relative to the ``import`` keyword
    tells them apart. Matching on a node type called ``import_from_member`` —
    which the grammar does not produce — meant `from X import Y` created no
    Import nodes whatsoever.

    Returns ``(module, [(local_name, original_name), ...])``. A wildcard import
    yields no bindings, since nothing nameable is introduced.
    """
    module = ""
    bindings: list[tuple[str, str]] = []
    seen_import_keyword = False

    for child in node.children:
        if child.type == "import":
            seen_import_keyword = True
            continue
        if not seen_import_keyword:
            if child.type in ("dotted_name", "relative_import"):
                module = _node_text(child, source)
            continue
        if child.type == "dotted_name":
            name = _node_text(child, source)
            bindings.append((name, name))
        elif child.type == "aliased_import":
            original = next(
                (_node_text(c, source) for c in child.children if c.type == "dotted_name"), ""
            )
            alias = next(
                (_node_text(c, source) for c in reversed(child.children) if c.type == "identifier"),
                "",
            )
            if original:
                bindings.append((alias or original, original))

    return module, bindings


def _module_to_repo_file(module: str, current: Path, repo_root: Path) -> str | None:
    """
    Repo-relative file defining *module*, or None when it is not in the repo.

    Handles absolute (``pkg.mod``) and relative (``.mod``, ``..pkg.mod``)
    imports, and both module and package layouts. Returning None for anything
    outside the repository is what distinguishes a first-party call worth an
    edge from a standard-library or third-party one.
    """
    module = (module or "").strip()
    if not module:
        return None

    leading = len(module) - len(module.lstrip("."))
    if leading:
        # A relative import is resolved against the current file's package.
        base = current.parent
        for _ in range(leading - 1):
            base = base.parent
        parts = [p for p in module[leading:].split(".") if p]
    else:
        base = repo_root
        parts = [p for p in module.split(".") if p]

    if not parts and leading:
        candidates = [base / "__init__.py"]
    else:
        candidates = [
            base.joinpath(*parts).with_suffix(".py"),
            base.joinpath(*parts) / "__init__.py",
        ]

    for candidate in candidates:
        try:
            if candidate.is_file():
                return str(candidate.resolve().relative_to(repo_root.resolve()))
        except (OSError, ValueError):
            continue
    return None


class PythonParser(LanguageParser):
    def __init__(self) -> None:
        self._parser = _get_parser()

    def parse_file(self, path: Path, repo_root: Path, store: GraphStore) -> dict[str, int]:
        source = path.read_bytes()
        tree = self._parser.parse(source)
        rel_path = str(path.relative_to(repo_root))

        stats = {"functions": 0, "classes": 0, "edges": 0}

        # File node
        store.create_node(
            NodeLabel.File,
            {
                "name": path.name,
                "path": rel_path,
                "language": "python",
                "line_count": source.count(b"\n"),
            },
        )

        # Import bindings are collected from the whole file first, including
        # those inside functions and methods. A call is resolved against them,
        # so the walk cannot do this lazily: `_handle_function` extracts a
        # function's calls the moment it reaches it, which is before a
        # function-local import further down the file has been seen.
        self._bindings = self._collect_bindings(tree.root_node, source, path, repo_root)

        self._walk(tree.root_node, source, rel_path, store, stats, class_name=None)
        self._bindings = {}
        return stats

    # ── Import resolution ────────────────────────────────────────────────

    def _collect_bindings(self, root, source: bytes, path: Path, repo_root: Path) -> dict:
        """
        Map every name bound by an import to the repo file that defines it.

        Two shapes are recorded:

        - ``symbol`` → ``(file, original_name)`` for ``from mod import name``
        - ``alias``  → ``(file, "")`` for ``import pkg.mod`` / ``from pkg import
          mod``, so a later ``alias.attr()`` can be resolved

        Modules that do not resolve to a file inside the repository — the
        standard library, third-party packages — are simply absent, which is
        what keeps `asyncio.to_thread` from inventing an edge.
        """
        bindings: dict[str, tuple[str, str]] = {}

        def module_file(module: str) -> str | None:
            return _module_to_repo_file(module, path, repo_root)

        def visit(node):
            if node.type == "import_statement":
                for child in node.children:
                    if child.type == "dotted_name":
                        module = _node_text(child, source)
                        target = module_file(module)
                        if target:
                            # `import pkg.mod` binds the leaf for `pkg.mod.f()`
                            bindings.setdefault(module.split(".")[-1], (target, ""))
                            bindings.setdefault(module, (target, ""))
            elif node.type == "import_from_statement":
                module, members = _parse_import_from(node, source)
                for local, original in members:
                    # `from pkg import mod` — the member may itself be a module
                    submodule = module_file(f"{module}.{original}") if module else None
                    if submodule:
                        bindings.setdefault(local, (submodule, ""))
                        continue
                    target = module_file(module)
                    if target:
                        bindings.setdefault(local, (target, original))
            for child in node.children:
                visit(child)

        visit(root)
        return bindings

    def _resolve_callee(self, expression: str, file_path: str) -> tuple[str, str]:
        """
        Where a called expression's definition lives: (file, symbol).

        Falls back to the current file, which is what the parser assumed for
        every call before — the reason no cross-module edge was ever created
        (#163).
        """
        parts = expression.split(".")
        bindings = getattr(self, "_bindings", {}) or {}

        if len(parts) == 1:
            target = bindings.get(parts[0])
            if target and target[1]:
                return target[0], target[1]
            return file_path, parts[0]

        # `alias.symbol` — resolve the alias to its module file
        target = bindings.get(".".join(parts[:-1])) or bindings.get(parts[-2])
        if target:
            return target[0], parts[-1]
        return file_path, parts[-1]

    def _walk(
        self,
        node,
        source: bytes,
        file_path: str,
        store: GraphStore,
        stats: dict,
        class_name: str | None,
    ) -> None:
        if node.type == "import_statement":
            self._handle_import(node, source, file_path, store, stats)

        elif node.type == "import_from_statement":
            self._handle_import_from(node, source, file_path, store, stats)

        elif node.type == "class_definition":
            self._handle_class(node, source, file_path, store, stats)
            return  # class walker handles children

        elif node.type == "function_definition":
            self._handle_function(node, source, file_path, store, stats, class_name)
            return  # function walker handles children

        for child in node.children:
            self._walk(child, source, file_path, store, stats, class_name)

    def _handle_import(
        self, node, source: bytes, file_path: str, store: GraphStore, stats: dict
    ) -> None:
        for child in node.children:
            if child.type == "dotted_name":
                name = _node_text(child, source)
                store.create_node(
                    NodeLabel.Import,
                    {
                        "name": name,
                        "file_path": file_path,
                        "line_start": node.start_point[0] + 1,
                        "module": name,
                    },
                )
                store.create_edge(
                    NodeLabel.File,
                    {"path": file_path},
                    EdgeType.IMPORTS,
                    NodeLabel.Import,
                    {"name": name, "file_path": file_path},
                )
                stats["edges"] += 1

    def _handle_import_from(
        self, node, source: bytes, file_path: str, store: GraphStore, stats: dict
    ) -> None:
        module, bindings = _parse_import_from(node, source)
        for name, _original in bindings:
            store.create_node(
                NodeLabel.Import,
                {
                    "name": name,
                    "file_path": file_path,
                    "line_start": node.start_point[0] + 1,
                    "module": module,
                },
            )
            store.create_edge(
                NodeLabel.File,
                {"path": file_path},
                EdgeType.IMPORTS,
                NodeLabel.Import,
                {"name": name, "file_path": file_path},
            )
            stats["edges"] += 1

    def _handle_class(
        self, node, source: bytes, file_path: str, store: GraphStore, stats: dict
    ) -> None:
        name_node = next((c for c in node.children if c.type == "identifier"), None)
        if not name_node:
            return
        name = _node_text(name_node, source)
        docstring = _get_docstring(node, source)

        store.create_node(
            NodeLabel.Class,
            {
                "name": name,
                "file_path": file_path,
                "line_start": node.start_point[0] + 1,
                "line_end": node.end_point[0] + 1,
                "docstring": docstring or "",
            },
        )
        store.create_edge(
            NodeLabel.File,
            {"path": file_path},
            EdgeType.CONTAINS,
            NodeLabel.Class,
            {"name": name, "file_path": file_path},
        )
        stats["classes"] += 1
        stats["edges"] += 1

        # Inheritance
        for child in node.children:
            if child.type == "argument_list":
                for arg in child.children:
                    if arg.type == "identifier":
                        parent_name = _node_text(arg, source)
                        store.create_edge(
                            NodeLabel.Class,
                            {"name": name, "file_path": file_path},
                            EdgeType.INHERITS,
                            NodeLabel.Class,
                            {"name": parent_name, "file_path": file_path},
                        )
                        stats["edges"] += 1

        # Walk class body for methods
        body = next((c for c in node.children if c.type == "block"), None)
        if body:
            for child in body.children:
                if child.type == "function_definition":
                    self._handle_function(child, source, file_path, store, stats, class_name=name)

    def _handle_function(
        self,
        node,
        source: bytes,
        file_path: str,
        store: GraphStore,
        stats: dict,
        class_name: str | None,
    ) -> None:
        name_node = next((c for c in node.children if c.type == "identifier"), None)
        if not name_node:
            return
        name = _node_text(name_node, source)
        docstring = _get_docstring(node, source)

        label = NodeLabel.Method if class_name else NodeLabel.Function
        props = {
            "name": name,
            "file_path": file_path,
            "line_start": node.start_point[0] + 1,
            "line_end": node.end_point[0] + 1,
            "docstring": docstring or "",
            "class_name": class_name or "",
        }
        store.create_node(label, props)

        container_label = NodeLabel.Class if class_name else NodeLabel.File
        container_key = (
            {"name": class_name, "file_path": file_path} if class_name else {"path": file_path}
        )
        store.create_edge(
            container_label,
            container_key,
            EdgeType.CONTAINS,
            label,
            {"name": name, "file_path": file_path},
        )
        stats["functions"] += 1
        stats["edges"] += 1

        # Call edges — find all call expressions in the body
        self._extract_calls(node, source, file_path, name, label, store, stats)

    def _extract_calls(
        self,
        fn_node,
        source: bytes,
        file_path: str,
        fn_name: str,
        fn_label: str,
        store: GraphStore,
        stats: dict,
    ) -> None:
        def emit(target_file: str, target_name: str, edge_type) -> None:
            store.create_edge(
                fn_label,
                {"name": fn_name, "file_path": file_path},
                edge_type,
                NodeLabel.Function,
                {"name": target_name, "file_path": target_file},
                # tree-sitter is syntax only: it has no name resolution and no
                # types, so `foo.bar()` cannot be resolved to Baz.bar. The
                # callee here was matched by import heuristics and is usually
                # but not always right (#163, #166). Recording that lets a
                # caller tell a fact from a good guess, and leaves room for
                # compiler-accurate edges to be marked `resolved` (#188).
                {"resolution": "inferred"},
            )
            stats["edges"] += 1

        def walk_calls(node):
            if node.type == "call":
                func = next(
                    (c for c in node.children if c.type in ("identifier", "attribute")), None
                )
                if func:
                    expression = _node_text(func, source)
                    target_file, target_name = self._resolve_callee(expression, file_path)
                    emit(target_file, target_name, EdgeType.CALLS)

                    # A callable passed to another call is a reference to it,
                    # not a call of it: `asyncio.to_thread(pipeline.process)`
                    # never appears as a call node, so flow analysis stopped
                    # dead at the handoff (#163).
                    args = next((c for c in node.children if c.type == "argument_list"), None)
                    for arg in args.children if args else []:
                        if arg.type not in ("identifier", "attribute"):
                            continue
                        ref_file, ref_name = self._resolve_callee(
                            _node_text(arg, source), file_path
                        )
                        if ref_file != file_path or "." in _node_text(arg, source):
                            emit(ref_file, ref_name, EdgeType.REFERENCES)
            for child in node.children:
                walk_calls(child)

        body = next((c for c in fn_node.children if c.type == "block"), None)
        if body:
            walk_calls(body)
