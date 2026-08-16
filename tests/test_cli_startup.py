"""
The CLI does not import what it does not need (#190).

Importing ``navegador.cli.commands`` cost ~137ms, of which ~95ms was
``rich.markdown`` (which drags in markdown_it and pygments) and ``asyncio``.
Only ``manual`` renders markdown and only the MCP server needs asyncio, but
every invocation paid for both — including the agent hooks, which shell out
once per call against a graph query that takes 0.45ms.

These tests pin the cost down. They are worth more than a benchmark because
they fail deterministically the moment someone adds a convenient top-level
import, rather than slowly getting worse.
"""

import subprocess
import sys

import pytest

# Modules no command should pull in merely by being importable. Each is either
# expensive on its own or drags in a large tree.
FORBIDDEN_AT_IMPORT = [
    "rich.markdown",
    "markdown_it",
    "pygments",
    "asyncio",
]


def import_and_report(module: str, probes: list[str]) -> dict[str, bool]:
    """Import *module* in a clean interpreter and report which probes loaded."""
    code = (
        f"import importlib, json, sys;"
        f"importlib.import_module({module!r});"
        f"print(json.dumps({{p: p in sys.modules for p in {probes!r}}}))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    import json

    return json.loads(result.stdout.strip().splitlines()[-1])


class TestExpensiveImportsAreDeferred:
    @pytest.mark.parametrize("module", FORBIDDEN_AT_IMPORT)
    def test_not_imported_by_the_cli_module(self, module):
        loaded = import_and_report("navegador.cli.commands", FORBIDDEN_AT_IMPORT)
        assert not loaded[module], (
            f"{module} is imported at CLI module load. It was moved into the "
            f"function that needs it in #190; a top-level import puts ~95ms "
            f"back onto every invocation."
        )

    def test_the_cheap_ones_are_still_eager(self):
        """
        click and rich.console are needed by essentially every command, so
        deferring them would trade startup cost for scattered import lines
        and no benefit. This documents where the line sits.
        """
        loaded = import_and_report("navegador.cli.commands", ["click", "rich.console"])
        assert loaded["click"]
        assert loaded["rich.console"]


class TestStillWorks:
    """Deferring an import must not break the feature that needs it."""

    def test_manual_renders_markdown(self):
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "from navegador.cli.commands import main; main()",
                "manual",
                "getting-started/quickstart",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        assert "Quick Start" in result.stdout

    def test_mcp_command_resolves(self):
        from navegador.cli.commands import main

        assert main.commands.get("mcp") is not None
