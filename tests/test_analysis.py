# Copyright CONFLICT LLC 2026 (weareconflict.com)
"""Tests for navegador.analysis — structural analysis tools."""

import json
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from navegador.cli.commands import main

# ── Shared helpers ─────────────────────────────────────────────────────────────


def _mock_store(result_set=None):
    """Return a mock GraphStore whose .query() returns the given result_set."""
    store = MagicMock()
    result = MagicMock()
    result.result_set = result_set or []
    store.query.return_value = result
    return store


def _multi_mock_store(*result_sets):
    """
    Return a mock GraphStore whose .query() returns successive result_sets.
    Each call to .query() gets the next item from the list.
    """
    store = MagicMock()
    results = []
    for rs in result_sets:
        r = MagicMock()
        r.result_set = rs
        results.append(r)
    store.query.side_effect = results
    return store


# ── #3: ImpactAnalyzer ────────────────────────────────────────────────────────


class TestImpactAnalyzer:
    def test_returns_impact_result_structure(self):
        from navegador.analysis.impact import ImpactAnalyzer, ImpactResult

        store = _multi_mock_store(
            # blast radius query
            [
                ["Function", "callee_a", "src/a.py", 10],
                ["Class", "ClassB", "src/b.py", 20],
            ],
            # knowledge query
            [],
        )
        analyzer = ImpactAnalyzer(store)
        result = analyzer.blast_radius("my_func")

        assert isinstance(result, ImpactResult)
        assert result.name == "my_func"
        assert result.depth == 3
        assert len(result.affected_nodes) == 2
        assert "src/a.py" in result.affected_files
        assert "src/b.py" in result.affected_files

    def test_affected_nodes_have_correct_keys(self):
        from navegador.analysis.impact import ImpactAnalyzer

        store = _multi_mock_store(
            [["Function", "do_thing", "utils.py", 5]],
            [],
        )
        result = ImpactAnalyzer(store).blast_radius("entry")
        node = result.affected_nodes[0]
        assert "type" in node
        assert "name" in node
        assert "file_path" in node
        assert "line_start" in node

    def test_empty_graph_returns_empty_result(self):
        from navegador.analysis.impact import ImpactAnalyzer

        store = _multi_mock_store([], [])
        result = ImpactAnalyzer(store).blast_radius("nothing")

        assert result.affected_nodes == []
        assert result.affected_files == []
        assert result.affected_knowledge == []
        assert result.depth_reached == 0

    def test_with_file_path_narrowing(self):
        from navegador.analysis.impact import ImpactAnalyzer

        store = _multi_mock_store([], [])
        result = ImpactAnalyzer(store).blast_radius("func", file_path="src/auth.py", depth=2)

        assert result.file_path == "src/auth.py"
        assert result.depth == 2

    def test_knowledge_layer_populated(self):
        from navegador.analysis.impact import ImpactAnalyzer

        store = _multi_mock_store(
            [["Function", "impl", "src/impl.py", 1]],
            [["Concept", "AuthToken"]],
        )
        result = ImpactAnalyzer(store).blast_radius("validate")
        assert len(result.affected_knowledge) == 1
        assert result.affected_knowledge[0]["name"] == "AuthToken"

    def test_to_dict_keys(self):
        from navegador.analysis.impact import ImpactAnalyzer

        store = _multi_mock_store([], [])
        d = ImpactAnalyzer(store).blast_radius("fn").to_dict()
        for key in ("name", "file_path", "depth", "depth_reached",
                    "affected_nodes", "affected_files", "affected_knowledge"):
            assert key in d

    def test_query_exception_returns_empty(self):
        from navegador.analysis.impact import ImpactAnalyzer

        store = MagicMock()
        store.query.side_effect = RuntimeError("db error")
        result = ImpactAnalyzer(store).blast_radius("x")
        assert result.affected_nodes == []

    def test_affected_files_sorted(self):
        from navegador.analysis.impact import ImpactAnalyzer

        store = _multi_mock_store(
            [
                ["Function", "b", "zzz.py", 1],
                ["Function", "a", "aaa.py", 2],
            ],
            [],
        )
        result = ImpactAnalyzer(store).blast_radius("root")
        assert result.affected_files == ["aaa.py", "zzz.py"]


# ── #4: FlowTracer ────────────────────────────────────────────────────────────


class TestFlowTracer:
    def test_returns_list_of_call_chains(self):
        from navegador.analysis.flow import CallChain, FlowTracer

        # entry resolve → one result; CALLS query → one callee; next CALLS → empty
        store = _multi_mock_store(
            [["entry", "src/main.py"]],               # _RESOLVE_ENTRY
            [["entry", "helper", "src/util.py"]],      # _CALLS_FROM (depth 0)
            [],                                         # _CALLS_FROM (depth 1, no more)
        )
        tracer = FlowTracer(store)
        chains = tracer.trace("entry")

        assert isinstance(chains, list)
        # At least one chain should have been produced
        assert len(chains) >= 1
        assert all(isinstance(c, CallChain) for c in chains)

    def test_entry_not_found_returns_empty(self):
        from navegador.analysis.flow import FlowTracer

        store = _mock_store(result_set=[])
        chains = FlowTracer(store).trace("nonexistent")
        assert chains == []

    def test_call_chain_to_list_format(self):
        from navegador.analysis.flow import CallChain

        chain = CallChain(steps=[("a", "b", "src/b.py"), ("b", "c", "src/c.py")])
        lst = chain.to_list()
        assert lst[0] == {"caller": "a", "callee": "b", "file_path": "src/b.py"}
        assert lst[1] == {"caller": "b", "callee": "c", "file_path": "src/c.py"}

    def test_empty_chain_length(self):
        from navegador.analysis.flow import CallChain

        chain = CallChain(steps=[])
        assert len(chain) == 0

    def test_chain_length(self):
        from navegador.analysis.flow import CallChain

        chain = CallChain(steps=[("a", "b", ""), ("b", "c", "")])
        assert len(chain) == 2

    def test_max_depth_respected(self):
        """With max_depth=1 the tracer should not go beyond one level."""
        from navegador.analysis.flow import FlowTracer

        store = _multi_mock_store(
            [["entry", ""]],                             # _RESOLVE_ENTRY
            [["entry", "level1", "a.py"]],               # depth 0 CALLS
            # No further calls needed since max_depth=1
        )
        chains = FlowTracer(store).trace("entry", max_depth=1)
        # All chains should have at most 1 step
        for chain in chains:
            assert len(chain) <= 1

    def test_cycle_does_not_loop_forever(self):
        """A cycle (a→b→a) should not produce an infinite loop."""
        from navegador.analysis.flow import FlowTracer

        call_results = [
            [["entry", ""]],                    # resolve entry
            [["entry", "entry", "src.py"]],     # entry calls itself (cycle)
        ]
        store = MagicMock()
        results = []
        for rs in call_results:
            r = MagicMock()
            r.result_set = rs
            results.append(r)
        store.query.side_effect = results + [MagicMock(result_set=[])] * 20

        chains = FlowTracer(store).trace("entry", max_depth=5)
        # Must terminate and return something (or empty)
        assert isinstance(chains, list)

    def test_no_calls_from_entry(self):
        """Entry exists but calls nothing — should return empty chains list."""
        from navegador.analysis.flow import FlowTracer

        store = _multi_mock_store(
            [["entry", "src/main.py"]],   # resolve entry
            [],                            # no CALLS edges
        )
        chains = FlowTracer(store).trace("entry")
        assert chains == []


# ── #35: DeadCodeDetector ─────────────────────────────────────────────────────


class TestDeadCodeDetector:
    def test_returns_dead_code_report(self):
        from navegador.analysis.deadcode import DeadCodeDetector, DeadCodeReport

        store = _multi_mock_store(
            [["Function", "orphan_fn", "src/util.py", 5]],  # dead functions
            [["UnusedClass", "src/models.py", 10]],          # dead classes
            [["src/unused.py"]],                              # orphan files
        )
        report = DeadCodeDetector(store).detect()
        assert isinstance(report, DeadCodeReport)
        assert len(report.unreachable_functions) == 1
        assert len(report.unreachable_classes) == 1
        assert len(report.orphan_files) == 1

    def test_empty_graph_all_empty(self):
        from navegador.analysis.deadcode import DeadCodeDetector

        store = _multi_mock_store([], [], [])
        report = DeadCodeDetector(store).detect()
        assert report.unreachable_functions == []
        assert report.unreachable_classes == []
        assert report.orphan_files == []

    def test_to_dict_contains_summary(self):
        from navegador.analysis.deadcode import DeadCodeDetector

        store = _multi_mock_store(
            [["Function", "dead_fn", "a.py", 1]],
            [],
            [],
        )
        d = DeadCodeDetector(store).detect().to_dict()
        assert "summary" in d
        assert d["summary"]["unreachable_functions"] == 1
        assert d["summary"]["unreachable_classes"] == 0
        assert d["summary"]["orphan_files"] == 0

    def test_function_node_structure(self):
        from navegador.analysis.deadcode import DeadCodeDetector

        store = _multi_mock_store(
            [["Method", "stale_method", "service.py", 88]],
            [],
            [],
        )
        report = DeadCodeDetector(store).detect()
        fn = report.unreachable_functions[0]
        assert fn["type"] == "Method"
        assert fn["name"] == "stale_method"
        assert fn["file_path"] == "service.py"
        assert fn["line_start"] == 88

    def test_class_node_structure(self):
        from navegador.analysis.deadcode import DeadCodeDetector

        store = _multi_mock_store(
            [],
            [["LegacyWidget", "widgets.py", 20]],
            [],
        )
        report = DeadCodeDetector(store).detect()
        cls = report.unreachable_classes[0]
        assert cls["name"] == "LegacyWidget"
        assert cls["file_path"] == "widgets.py"

    def test_orphan_files_as_strings(self):
        from navegador.analysis.deadcode import DeadCodeDetector

        store = _multi_mock_store(
            [],
            [],
            [["legacy/old.py"], ["legacy/dead.py"]],
        )
        report = DeadCodeDetector(store).detect()
        assert "legacy/old.py" in report.orphan_files
        assert "legacy/dead.py" in report.orphan_files

    def test_query_exception_returns_empty_report(self):
        from navegador.analysis.deadcode import DeadCodeDetector

        store = MagicMock()
        store.query.side_effect = RuntimeError("db down")
        report = DeadCodeDetector(store).detect()
        assert report.unreachable_functions == []
        assert report.unreachable_classes == []
        assert report.orphan_files == []

    def test_multiple_dead_functions(self):
        from navegador.analysis.deadcode import DeadCodeDetector

        store = _multi_mock_store(
            [
                ["Function", "fn_a", "a.py", 1],
                ["Function", "fn_b", "b.py", 2],
                ["Method", "meth_c", "c.py", 3],
            ],
            [],
            [],
        )
        report = DeadCodeDetector(store).detect()
        assert len(report.unreachable_functions) == 3


# ── #36: TestMapper ───────────────────────────────────────────────────────────


class TestTestMapper:
    def test_returns_test_map_result(self):
        from navegador.analysis.testmap import TestMapper, TestMapResult

        # Query calls: _TEST_FUNCTIONS_QUERY, then for each test:
        # _CALLS_FROM_TEST, _CALLS_FROM_TEST (again for source detection), _CREATE_TESTS_EDGE
        store = _multi_mock_store(
            [["test_validate", "tests/test_auth.py", 10]],  # test functions
            [["Function", "validate", "auth.py"]],            # CALLS_FROM_TEST
            [["Function", "validate", "auth.py"]],            # CALLS_FROM_TEST (source)
            [],                                                # CREATE_TESTS_EDGE
        )
        result = TestMapper(store).map_tests()
        assert isinstance(result, TestMapResult)

    def test_no_test_functions_returns_empty(self):
        from navegador.analysis.testmap import TestMapper

        store = _mock_store(result_set=[])
        result = TestMapper(store).map_tests()
        assert result.links == []
        assert result.unmatched_tests == []
        assert result.edges_created == 0

    def test_link_via_calls_edge(self):
        from navegador.analysis.testmap import TestMapper

        store = _multi_mock_store(
            [["test_process", "tests/test_core.py", 5]],  # test functions
            [["Function", "process", "core.py"]],           # CALLS_FROM_TEST
            [["Function", "process", "core.py"]],           # CALLS_FROM_TEST (source)
            [],                                              # CREATE edge
        )
        result = TestMapper(store).map_tests()
        assert len(result.links) == 1
        link = result.links[0]
        assert link.test_name == "test_process"
        assert link.prod_name == "process"
        assert link.prod_file == "core.py"

    def test_link_via_heuristic(self):
        """When no CALLS edge exists, fall back to name heuristic."""
        from navegador.analysis.testmap import TestMapper

        store = _multi_mock_store(
            [["test_render_output", "tests/test_renderer.py", 1]],  # test fns
            [],                                                        # no CALLS
            [["Function", "render_output", "renderer.py"]],           # heuristic
            [["Function", "render_output", "renderer.py"]],           # verify calls
            [],                                                        # CREATE edge
        )
        result = TestMapper(store).map_tests()
        assert len(result.links) == 1
        assert result.links[0].prod_name == "render_output"

    def test_unmatched_test_recorded(self):
        """A test with no call and no matching heuristic goes to unmatched."""
        from navegador.analysis.testmap import TestMapper

        # Test functions: one test. Then all queries return empty.
        store = MagicMock()
        results_iter = [
            MagicMock(result_set=[["test_xyzzy", "tests/t.py", 1]]),
            MagicMock(result_set=[]),   # no CALLS
            MagicMock(result_set=[]),   # heuristic: test_xyzzy
            MagicMock(result_set=[]),   # heuristic: test_xyz (truncated)
            MagicMock(result_set=[]),   # heuristic: test_x
        ] + [MagicMock(result_set=[])] * 10
        store.query.side_effect = results_iter

        result = TestMapper(store).map_tests()
        assert len(result.unmatched_tests) == 1
        assert result.unmatched_tests[0]["name"] == "test_xyzzy"

    def test_to_dict_structure(self):
        from navegador.analysis.testmap import TestMapper

        store = _mock_store(result_set=[])
        d = TestMapper(store).map_tests().to_dict()
        for key in ("links", "unmatched_tests", "edges_created", "summary"):
            assert key in d
        assert "matched" in d["summary"]
        assert "unmatched" in d["summary"]
        assert "edges_created" in d["summary"]

    def test_edges_created_count(self):
        from navegador.analysis.testmap import TestMapper

        store = _multi_mock_store(
            [["test_foo", "tests/t.py", 1]],   # test fns
            [["Function", "foo", "app.py"]],    # CALLS_FROM_TEST
            [["Function", "foo", "app.py"]],    # source verify
            [],                                  # CREATE edge (no error = success)
        )
        result = TestMapper(store).map_tests()
        assert result.edges_created == 1


# ── #37: CycleDetector ────────────────────────────────────────────────────────


class TestCycleDetector:
    def test_no_import_cycles(self):
        from navegador.analysis.cycles import CycleDetector

        # Linear imports: a → b → c, no cycle
        store = _mock_store(
            result_set=[
                ["a", "a.py", "b", "b.py"],
                ["b", "b.py", "c", "c.py"],
            ]
        )
        cycles = CycleDetector(store).detect_import_cycles()
        assert cycles == []

    def test_detects_simple_import_cycle(self):
        from navegador.analysis.cycles import CycleDetector

        # a → b → a (cycle)
        store = _mock_store(
            result_set=[
                ["a", "a.py", "b", "b.py"],
                ["b", "b.py", "a", "a.py"],
            ]
        )
        cycles = CycleDetector(store).detect_import_cycles()
        assert len(cycles) == 1
        cycle = cycles[0]
        assert "a.py" in cycle
        assert "b.py" in cycle

    def test_detects_three_node_cycle(self):
        from navegador.analysis.cycles import CycleDetector

        store = _mock_store(
            result_set=[
                ["a", "a.py", "b", "b.py"],
                ["b", "b.py", "c", "c.py"],
                ["c", "c.py", "a", "a.py"],
            ]
        )
        cycles = CycleDetector(store).detect_import_cycles()
        assert len(cycles) >= 1
        cycle = cycles[0]
        assert len(cycle) == 3

    def test_no_call_cycles(self):
        from navegador.analysis.cycles import CycleDetector

        store = _mock_store(
            result_set=[
                ["fn_a", "fn_b"],
                ["fn_b", "fn_c"],
            ]
        )
        cycles = CycleDetector(store).detect_call_cycles()
        assert cycles == []

    def test_detects_call_cycle(self):
        from navegador.analysis.cycles import CycleDetector

        # fn_a → fn_b → fn_a
        store = _mock_store(
            result_set=[
                ["fn_a", "fn_b"],
                ["fn_b", "fn_a"],
            ]
        )
        cycles = CycleDetector(store).detect_call_cycles()
        assert len(cycles) == 1
        assert "fn_a" in cycles[0]
        assert "fn_b" in cycles[0]

    def test_empty_graph_no_cycles(self):
        from navegador.analysis.cycles import CycleDetector

        store = _mock_store(result_set=[])
        assert CycleDetector(store).detect_import_cycles() == []
        assert CycleDetector(store).detect_call_cycles() == []

    def test_self_loop_not_included(self):
        """A self-loop (a → a) should be skipped by the adjacency builder."""
        from navegador.analysis.cycles import CycleDetector

        store = _mock_store(result_set=[["a", "a.py", "a", "a.py"]])
        cycles = CycleDetector(store).detect_import_cycles()
        # Self-loops filtered out in _build_import_adjacency
        assert cycles == []

    def test_cycle_normalised_no_duplicates(self):
        """The same cycle reported from different start points should appear once."""
        from navegador.analysis.cycles import CycleDetector

        store = _mock_store(
            result_set=[
                ["fn_b", "fn_a"],
                ["fn_a", "fn_b"],
            ]
        )
        cycles = CycleDetector(store).detect_call_cycles()
        assert len(cycles) == 1

    def test_query_exception_returns_empty(self):
        from navegador.analysis.cycles import CycleDetector

        store = MagicMock()
        store.query.side_effect = RuntimeError("connection refused")
        assert CycleDetector(store).detect_import_cycles() == []
        assert CycleDetector(store).detect_call_cycles() == []

    def test_multiple_independent_cycles(self):
        """Two independent cycles (a↔b and c↔d) should both be found."""
        from navegador.analysis.cycles import CycleDetector

        store = _mock_store(
            result_set=[
                ["fn_a", "fn_b"],
                ["fn_b", "fn_a"],
                ["fn_c", "fn_d"],
                ["fn_d", "fn_c"],
            ]
        )
        cycles = CycleDetector(store).detect_call_cycles()
        assert len(cycles) == 2


# ── CLI command tests ──────────────────────────────────────────────────────────


class TestImpactCLI:
    def _make_result(self):
        from navegador.analysis.impact import ImpactResult
        return ImpactResult(
            name="fn",
            file_path="",
            depth=3,
            affected_nodes=[
                {"type": "Function", "name": "callee", "file_path": "b.py", "line_start": 5}
            ],
            affected_files=["b.py"],
            affected_knowledge=[],
            depth_reached=3,
        )

    _BR_PATH = "navegador.analysis.impact.ImpactAnalyzer.blast_radius"

    def test_impact_json_output(self):
        runner = CliRunner()
        mock_result = self._make_result()
        with patch("navegador.cli.commands._get_store"), \
             patch(self._BR_PATH, return_value=mock_result):
            result = runner.invoke(main, ["impact", "fn", "--json"])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data["name"] == "fn"
            assert len(data["affected_nodes"]) == 1

    def test_impact_markdown_output(self):
        runner = CliRunner()
        mock_result = self._make_result()
        with patch("navegador.cli.commands._get_store"), \
             patch(self._BR_PATH, return_value=mock_result):
            result = runner.invoke(main, ["impact", "fn"])
            assert result.exit_code == 0
            assert "Blast radius" in result.output

    def test_impact_no_affected_nodes(self):
        from navegador.analysis.impact import ImpactResult
        runner = CliRunner()
        empty_result = ImpactResult(name="x", file_path="", depth=3)
        with patch("navegador.cli.commands._get_store"), \
             patch(self._BR_PATH, return_value=empty_result):
            result = runner.invoke(main, ["impact", "x"])
            assert result.exit_code == 0
            assert "No affected nodes" in result.output

    def test_impact_depth_option(self):
        from navegador.analysis.impact import ImpactResult
        runner = CliRunner()
        empty_result = ImpactResult(name="x", file_path="", depth=5)
        with patch("navegador.cli.commands._get_store"), \
             patch(self._BR_PATH, return_value=empty_result) as mock_br:
            result = runner.invoke(main, ["impact", "x", "--depth", "5"])
            assert result.exit_code == 0
            mock_br.assert_called_once()
            call_kwargs = mock_br.call_args
            assert call_kwargs[1]["depth"] == 5 or call_kwargs[0][1] == 5


class TestTraceCLI:
    def test_trace_json_output(self):
        from navegador.analysis.flow import CallChain
        runner = CliRunner()
        chains = [CallChain(steps=[("a", "b", "b.py")])]
        with patch("navegador.cli.commands._get_store"), \
             patch("navegador.analysis.flow.FlowTracer.trace", return_value=chains):
            result = runner.invoke(main, ["trace", "a", "--json"])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert len(data) == 1
            assert data[0][0]["caller"] == "a"

    def test_trace_no_chains(self):
        runner = CliRunner()
        with patch("navegador.cli.commands._get_store"), \
             patch("navegador.analysis.flow.FlowTracer.trace", return_value=[]):
            result = runner.invoke(main, ["trace", "entry"])
            assert result.exit_code == 0
            assert "No call chains" in result.output

    def test_trace_markdown_shows_path(self):
        from navegador.analysis.flow import CallChain
        runner = CliRunner()
        chains = [CallChain(steps=[("entry", "helper", "util.py")])]
        with patch("navegador.cli.commands._get_store"), \
             patch("navegador.analysis.flow.FlowTracer.trace", return_value=chains):
            result = runner.invoke(main, ["trace", "entry"])
            assert result.exit_code == 0
            assert "entry" in result.output
            assert "helper" in result.output


class TestDeadcodeCLI:
    def test_deadcode_json_output(self):
        from navegador.analysis.deadcode import DeadCodeReport
        runner = CliRunner()
        report = DeadCodeReport(
            unreachable_functions=[
                {"type": "Function", "name": "dead_fn", "file_path": "a.py", "line_start": 1}
            ],
            unreachable_classes=[],
            orphan_files=[],
        )
        with patch("navegador.cli.commands._get_store"), \
             patch("navegador.analysis.deadcode.DeadCodeDetector.detect", return_value=report):
            result = runner.invoke(main, ["deadcode", "--json"])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert len(data["unreachable_functions"]) == 1

    def test_deadcode_no_dead_code(self):
        from navegador.analysis.deadcode import DeadCodeReport
        runner = CliRunner()
        with patch("navegador.cli.commands._get_store"), \
             patch("navegador.analysis.deadcode.DeadCodeDetector.detect",
                   return_value=DeadCodeReport()):
            result = runner.invoke(main, ["deadcode"])
            assert result.exit_code == 0
            assert "No dead code" in result.output

    def test_deadcode_shows_summary_line(self):
        from navegador.analysis.deadcode import DeadCodeReport
        runner = CliRunner()
        report = DeadCodeReport(
            unreachable_functions=[
                {"type": "Function", "name": "fn", "file_path": "", "line_start": None}
            ],
            unreachable_classes=[],
            orphan_files=["old.py"],
        )
        with patch("navegador.cli.commands._get_store"), \
             patch("navegador.analysis.deadcode.DeadCodeDetector.detect", return_value=report):
            result = runner.invoke(main, ["deadcode"])
            assert result.exit_code == 0
            assert "dead functions" in result.output
            assert "orphan files" in result.output


class TestTestmapCLI:
    def test_testmap_json_output(self):
        from navegador.analysis.testmap import TestLink, TestMapResult
        runner = CliRunner()
        link = TestLink(
            test_name="test_foo", test_file="tests/t.py",
            prod_name="foo", prod_file="app.py", prod_type="Function", source="calls"
        )
        mock_result = TestMapResult(links=[link], unmatched_tests=[], edges_created=1)
        with patch("navegador.cli.commands._get_store"), \
             patch("navegador.analysis.testmap.TestMapper.map_tests", return_value=mock_result):
            result = runner.invoke(main, ["testmap", "--json"])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data["edges_created"] == 1
            assert len(data["links"]) == 1

    def test_testmap_no_tests(self):
        from navegador.analysis.testmap import TestMapResult
        runner = CliRunner()
        with patch("navegador.cli.commands._get_store"), \
             patch("navegador.analysis.testmap.TestMapper.map_tests",
                   return_value=TestMapResult()):
            result = runner.invoke(main, ["testmap"])
            assert result.exit_code == 0
            assert "0 linked" in result.output

    def test_testmap_unmatched_shown(self):
        from navegador.analysis.testmap import TestMapResult
        runner = CliRunner()
        mock_result = TestMapResult(
            links=[],
            unmatched_tests=[{"name": "test_mystery", "file_path": "t.py"}],
            edges_created=0,
        )
        with patch("navegador.cli.commands._get_store"), \
             patch("navegador.analysis.testmap.TestMapper.map_tests", return_value=mock_result):
            result = runner.invoke(main, ["testmap"])
            assert result.exit_code == 0
            assert "test_mystery" in result.output


class TestCyclesCLI:
    def test_cycles_json_output(self):
        runner = CliRunner()
        with patch("navegador.cli.commands._get_store"), \
             patch("navegador.analysis.cycles.CycleDetector.detect_import_cycles",
                   return_value=[["a.py", "b.py"]]), \
             patch("navegador.analysis.cycles.CycleDetector.detect_call_cycles",
                   return_value=[]):
            result = runner.invoke(main, ["cycles", "--json"])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert "import_cycles" in data
            assert "call_cycles" in data
            assert len(data["import_cycles"]) == 1

    def test_no_cycles_message(self):
        runner = CliRunner()
        with patch("navegador.cli.commands._get_store"), \
             patch("navegador.analysis.cycles.CycleDetector.detect_import_cycles",
                   return_value=[]), \
             patch("navegador.analysis.cycles.CycleDetector.detect_call_cycles",
                   return_value=[]):
            result = runner.invoke(main, ["cycles"])
            assert result.exit_code == 0
            assert "No circular dependencies" in result.output

    def test_imports_only_flag(self):
        runner = CliRunner()
        with patch("navegador.cli.commands._get_store"), \
             patch("navegador.analysis.cycles.CycleDetector.detect_import_cycles",
                   return_value=[["x.py", "y.py"]]) as mock_imp, \
             patch("navegador.analysis.cycles.CycleDetector.detect_call_cycles",
                   return_value=[]) as mock_call:
            result = runner.invoke(main, ["cycles", "--imports"])
            assert result.exit_code == 0
            # --imports restricts to import cycle detection only
            mock_imp.assert_called_once()
            mock_call.assert_not_called()

    def test_cycles_with_call_cycles_shown(self):
        runner = CliRunner()
        with patch("navegador.cli.commands._get_store"), \
             patch("navegador.analysis.cycles.CycleDetector.detect_import_cycles",
                   return_value=[]), \
             patch("navegador.analysis.cycles.CycleDetector.detect_call_cycles",
                   return_value=[["fn_a", "fn_b"]]):
            result = runner.invoke(main, ["cycles"])
            assert result.exit_code == 0
            assert "fn_a" in result.output
            assert "fn_b" in result.output
