"""
No *new* tests may mock the graph store (#191).

This is the rule that would have caught the worst bug in 1.5. Export and import
dropped every edge for months because the tests mocked the store: a MagicMock
``create_edge`` is always truthy, so the code checking whether the write
succeeded passed forever. The test asserted the bug, coverage looked fine, and
a 4,000-node round trip restored 4,000 nodes and zero edges in production.

Coverage cannot catch that class of defect — those lines *were* covered. The
only thing that catches it is asserting against a real store.

55 of 103 test files already mock the store, so a hard ban is a project rather
than a lint. This is a ratchet instead: the existing files are frozen below,
new ones are rejected, and the list is expected to shrink. When you fix a file,
delete its entry — the test fails on stale entries too, so the list cannot rot
into a fiction.

Real stores are cheap here. ``GraphStore.sqlite(tmp_path / "g.db")`` is an
embedded FalkorDB and costs milliseconds; every regression test written for
1.5 and #180 uses one.
"""

import re
from pathlib import Path

TESTS = Path(__file__).parent

# Detects a mocked graph store: MagicMock(GraphStore), patch(... GraphStore),
# or the common `store = MagicMock()` shorthand.
STORE_MOCK = re.compile(
    r"(MagicMock|Mock)\(\s*(spec=)?GraphStore|patch.*GraphStore|store\s*=\s*MagicMock|store\s*=\s*Mock"
)

# Frozen 2026-08-16. Shrink this list; never grow it.
KNOWN_STORE_MOCKERS = {
    "test_analysis.py",
    "test_ansible_parser.py",
    "test_bash_parser.py",
    "test_chef_enricher.py",
    "test_churn.py",
    "test_cicd.py",
    "test_cli_new_commands.py",
    "test_cli_storage.py",
    "test_cli.py",
    "test_cluster.py",
    "test_cluster2.py",
    "test_context.py",
    "test_coverage_boost.py",
    "test_crossrepo.py",
    "test_diff.py",
    "test_diffgraph.py",
    "test_doclink_semantic.py",
    "test_doclink.py",
    "test_drift.py",
    "test_enrichment_terraform.py",
    "test_explorer.py",
    "test_fossil_ingester.py",
    "test_go_parser.py",
    "test_grammar_skip.py",
    "test_hcl_parser.py",
    "test_history.py",
    "test_ingestion_code.py",
    "test_ingestion_gaps.py",
    "test_ingestion_planopticon.py",
    "test_ingestion_wiki.py",
    "test_intelligence.py",
    "test_java_parser.py",
    "test_language_parsers_edge_cases.py",
    "test_lenses.py",
    "test_markdown_parser.py",
    "test_mcp_security.py",
    "test_mcp_server.py",
    "test_memory_ingester.py",
    "test_metarepo_ingest.py",
    "test_migrations.py",
    "test_monorepo.py",
    "test_nested_repo_walk.py",
    "test_new_language_parsers.py",
    "test_optimization.py",
    "test_parser_dispatch.py",
    "test_puppet_parser.py",
    "test_python_parser.py",
    "test_release.py",
    "test_review.py",
    "test_rust_parser.py",
    "test_sdk.py",
    "test_taskpack.py",
    "test_typescript_parser.py",
    "test_v04_batch3.py",
    "test_v04_features.py",
}


def mocks_the_store(path: Path) -> bool:
    return bool(STORE_MOCK.search(path.read_text(encoding="utf-8", errors="replace")))


def current_mockers() -> set[str]:
    # This file is excluded: it contains the pattern as a literal and would
    # otherwise report itself.
    return {
        p.name
        for p in sorted(TESTS.glob("test_*.py"))
        if p.name != Path(__file__).name and mocks_the_store(p)
    }


def test_no_new_files_mock_the_graph_store():
    new = current_mockers() - KNOWN_STORE_MOCKERS
    assert not new, (
        "These test files mock the graph store:\n"
        + "".join(f"  - {name}\n" for name in sorted(new))
        + "\nA mocked store cannot fail the way a real one does — a MagicMock "
        "write always succeeds, which is how #173 shipped. Use "
        'GraphStore.sqlite(str(tmp_path / "g.db")) instead; it is an embedded '
        "FalkorDB and costs milliseconds."
    )


def test_the_allowlist_has_not_gone_stale():
    """A fixed file left on the list makes the ratchet a fiction."""
    fixed = KNOWN_STORE_MOCKERS - current_mockers()
    assert not fixed, (
        "These files no longer mock the store and should be removed from "
        "KNOWN_STORE_MOCKERS:\n" + "".join(f"  - {name}\n" for name in sorted(fixed))
    )
