"""
DocGenerator — markdown documentation generated from the navegador graph.

Supports two modes:
  * **Template mode** (``provider=None``): uses graph data to fill a
    structured markdown template — zero external dependencies.
  * **LLM mode** (``provider=`` an :class:`~navegador.llm.LLMProvider`):
    delegates to :class:`~navegador.intelligence.nlp.NLPEngine` for richer,
    narrative documentation.

Usage::

    from navegador.graph import GraphStore
    from navegador.intelligence.docgen import DocGenerator

    store = GraphStore.sqlite(".navegador/graph.db")

    # Template mode (no LLM required)
    gen = DocGenerator(store)
    print(gen.generate_file_docs("navegador/graph/store.py"))
    print(gen.generate_module_docs("navegador.graph"))
    print(gen.generate_project_docs())

    # LLM mode
    from navegador.llm import get_provider
    provider = get_provider("openai")
    gen = DocGenerator(store, provider=provider)
    print(gen.generate_file_docs("navegador/graph/store.py"))
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from navegador.graph.store import GraphStore
    from navegador.llm import LLMProvider


# ── Cypher helpers ────────────────────────────────────────────────────────────

_FILE_SYMBOLS = """
MATCH (f:File {path: $path})-[:CONTAINS]->(n)
RETURN labels(n)[0] AS type, n.name AS name,
       coalesce(n.docstring, '') AS docstring,
       coalesce(n.signature, '') AS signature,
       n.line_start AS line
ORDER BY n.line_start
"""

_FILE_BY_PATH = """
MATCH (f:File)
WHERE f.path CONTAINS $path
RETURN f.path AS path
LIMIT 20
"""

_MODULE_SYMBOLS = """
MATCH (n)
WHERE (n:Function OR n:Class OR n:Method)
  AND n.file_path CONTAINS $module_path
RETURN labels(n)[0] AS type, n.name AS name,
       coalesce(n.file_path, '') AS file_path,
       coalesce(n.docstring, '') AS docstring,
       coalesce(n.signature, '') AS signature
ORDER BY n.file_path, n.name
"""

_ALL_SYMBOLS_SUMMARY = """
MATCH (n)
WHERE n:Function OR n:Class OR n:Method
RETURN labels(n)[0] AS type, count(n) AS count
"""

_TOP_SYMBOLS = """
MATCH (n)
WHERE n:Function OR n:Class OR n:Method
RETURN labels(n)[0] AS type, n.name AS name,
       coalesce(n.file_path, '') AS file_path,
       coalesce(n.docstring, '') AS docstring
ORDER BY n.file_path, n.name
LIMIT 200
"""

_PROJECT_FILES = """
MATCH (f:File)
RETURN f.path AS path
ORDER BY f.path
"""

_STATS = """
MATCH (n)
RETURN labels(n)[0] AS type, count(n) AS count
ORDER BY count DESC
"""


class DocGenerator:
    """
    Generate markdown documentation from graph context.

    Args:
        store: A :class:`~navegador.graph.GraphStore` instance.
        provider: Optional :class:`~navegador.llm.LLMProvider`.  When
            ``None`` (default) template-based generation is used.
    """

    def __init__(self, store: "GraphStore", provider: "LLMProvider | None" = None) -> None:
        self._store = store
        self._provider = provider

    # ── Public API ────────────────────────────────────────────────────────────

    def generate_file_docs(self, file_path: str) -> str:
        """
        Generate markdown documentation for all symbols in a file.

        Args:
            file_path: Path to the source file (as stored in the graph).

        Returns:
            Markdown string describing the file's symbols.
        """
        if self._provider:
            return self._llm_file_docs(file_path)
        return self._template_file_docs(file_path)

    def generate_module_docs(self, module_name: str) -> str:
        """
        Generate markdown documentation for a Python module (by dotted name
        or partial path).

        Args:
            module_name: Dotted module name (``"navegador.graph"``) or partial
                file path substring.

        Returns:
            Markdown string describing all symbols in the module.
        """
        # Convert dotted module name to a path fragment
        module_path = module_name.replace(".", "/")
        if self._provider:
            return self._llm_module_docs(module_name, module_path)
        return self._template_module_docs(module_name, module_path)

    def generate_project_docs(self) -> str:
        """
        Generate a full project documentation overview.

        Returns:
            Markdown string with a project overview, file listing, and
            symbol summary.
        """
        if self._provider:
            return self._llm_project_docs()
        return self._template_project_docs()

    # ── Template-based generation ─────────────────────────────────────────

    def _template_file_docs(self, file_path: str) -> str:
        result = self._store.query(_FILE_SYMBOLS, {"path": file_path})
        rows = result.result_set or []

        lines = [f"# File: `{file_path}`", ""]
        if not rows:
            lines.append("_No symbols found in the graph for this file._")
            return "\n".join(lines)

        for row in rows:
            sym_type, name, docstring, signature, line = (row[0], row[1], row[2], row[3], row[4])
            lines.append(f"## {sym_type}: `{name}`")
            if line is not None:
                lines.append(f"_Line {line}_")
            if signature:
                lines += ["", f"```python\n{signature}\n```"]
            if docstring:
                lines += ["", docstring]
            lines.append("")

        return "\n".join(lines)

    def _template_module_docs(self, module_name: str, module_path: str) -> str:
        result = self._store.query(_MODULE_SYMBOLS, {"module_path": module_path})
        rows = result.result_set or []

        lines = [f"# Module: `{module_name}`", ""]
        if not rows:
            lines.append("_No symbols found in the graph for this module._")
            return "\n".join(lines)

        # Group by file
        files: dict[str, list[tuple]] = {}
        for row in rows:
            fp = row[2] or ""
            files.setdefault(fp, []).append(row)

        for fp, file_rows in sorted(files.items()):
            lines.append(f"## `{fp}`")
            lines.append("")
            for row in file_rows:
                sym_type, name, _, docstring, signature = (row[0], row[1], row[2], row[3], row[4])
                lines.append(f"### {sym_type}: `{name}`")
                if signature:
                    lines += ["", f"```python\n{signature}\n```"]
                if docstring:
                    lines += ["", docstring]
                lines.append("")

        return "\n".join(lines)

    def _template_project_docs(self) -> str:
        # Stats
        stats_result = self._store.query(_STATS, {})
        stats_rows = stats_result.result_set or []

        # Files
        files_result = self._store.query(_PROJECT_FILES, {})
        files = [row[0] for row in (files_result.result_set or []) if row[0]]

        # Symbols (capped)
        syms_result = self._store.query(_TOP_SYMBOLS, {})
        sym_rows = syms_result.result_set or []

        lines = ["# Project Documentation", ""]
        lines += ["## Overview", ""]

        if stats_rows:
            lines.append("| Node type | Count |")
            lines.append("|-----------|-------|")
            for row in stats_rows:
                if row[0]:
                    lines.append(f"| {row[0]} | {row[1]} |")
            lines.append("")

        if files:
            lines += ["## Files", ""]
            for fp in files[:50]:
                lines.append(f"- `{fp}`")
            if len(files) > 50:
                lines.append(f"- _…and {len(files) - 50} more_")
            lines.append("")

        if sym_rows:
            lines += ["## Symbols", ""]
            for row in sym_rows:
                sym_type, name, fp, docstring = row[0], row[1], row[2], row[3]
                summary = docstring.split("\n")[0][:80] if docstring else ""
                loc = f" — `{fp}`" if fp else ""
                lines.append(f"- **[{sym_type}]** `{name}`{loc}")
                if summary:
                    lines.append(f"  > {summary}")
            lines.append("")

        return "\n".join(lines)

    # ── LLM-based generation ──────────────────────────────────────────────

    def _llm_file_docs(self, file_path: str) -> str:
        from navegador.intelligence.nlp import NLPEngine

        engine = NLPEngine(self._store, self._provider)  # type: ignore[arg-type]
        # Use the NLPEngine generate_docs per symbol, then stitch together
        result = self._store.query(_FILE_SYMBOLS, {"path": file_path})
        rows = result.result_set or []

        lines = [f"# File: `{file_path}`", ""]
        if not rows:
            lines.append("_No symbols found in the graph for this file._")
            return "\n".join(lines)

        for row in rows[:10]:  # cap to avoid excessive API calls
            name = row[1]
            lines.append(engine.generate_docs(name, file_path=file_path))
            lines.append("")

        return "\n".join(lines)

    def _llm_module_docs(self, module_name: str, module_path: str) -> str:
        from navegador.intelligence.nlp import NLPEngine

        engine = NLPEngine(self._store, self._provider)  # type: ignore[arg-type]

        result = self._store.query(_MODULE_SYMBOLS, {"module_path": module_path})
        rows = result.result_set or []

        lines = [f"# Module: `{module_name}`", ""]
        if not rows:
            lines.append("_No symbols found in the graph for this module._")
            return "\n".join(lines)

        for row in rows[:10]:
            name, fp = row[1], row[2]
            lines.append(engine.generate_docs(name, file_path=fp or ""))
            lines.append("")

        return "\n".join(lines)

    def _llm_project_docs(self) -> str:
        assert self._provider is not None
        # Build a compact summary then ask the LLM to produce an overview
        template_summary = self._template_project_docs()

        prompt = (
            "You are a technical documentation writer.  "
            "Based on the following auto-generated project summary, write a "
            "polished, human-readable project README in markdown.\n\n"
            f"{template_summary}"
        )
        return self._provider.complete(prompt)
