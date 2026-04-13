# Copyright CONFLICT LLC 2026 (weareconflict.com)
"""Tests for navegador.analysis.diffgraph — structural diff between git refs."""

import json
from unittest.mock import MagicMock, patch

from navegador.analysis.diffgraph import (
    DiffGraphAnalyzer,
    DiffGraphReport,
    StructuralChange,
)

# ── Shared helpers ────────────────────────────────────────────────────────────

# Patch paths — these are imported locally inside _build_report, so we patch
# them at their source modules, not on the diffgraph module.
_IMPACT_ANALYZER = "navegador.analysis.impact.ImpactAnalyzer"
_LINES_OVERLAP = "navegador.diff._lines_overlap"
_PARSE_HUNKS = "navegador.diff._parse_unified_diff_hunks"


def _mock_store(result_set=None):
    """Return a mock GraphStore whose .query() returns the given result_set."""
    store = MagicMock()
    result = MagicMock()
    result.result_set = result_set or []
    store.query.return_value = result
    return store


def _subprocess_result(returncode=0, stdout="", stderr=""):
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = stderr
    return m


def _empty_impact_analyzer():
    """Configure and return a mock ImpactAnalyzer class with empty results."""
    mock_cls = MagicMock()
    mock_instance = MagicMock()
    mock_result = MagicMock()
    mock_result.affected_nodes = []
    mock_result.affected_knowledge = []
    mock_instance.blast_radius.return_value = mock_result
    mock_cls.return_value = mock_instance
    return mock_cls


# ── StructuralChange ─────────────────────────────────────────────────────────


class TestStructuralChange:
    def test_fields_stored_correctly(self):
        sc = StructuralChange(
            kind="new_symbol",
            symbol="authenticate",
            file_path="app/auth.py",
            detail="added function",
            line_start=10,
        )
        assert sc.kind == "new_symbol"
        assert sc.symbol == "authenticate"
        assert sc.file_path == "app/auth.py"
        assert sc.detail == "added function"
        assert sc.line_start == 10

    def test_defaults(self):
        sc = StructuralChange(kind="removed_symbol", symbol="old_fn")
        assert sc.file_path == ""
        assert sc.detail == ""
        assert sc.line_start is None


# ── DiffGraphReport output formats ──────────────────────────────────────────


class TestDiffGraphReport:
    def test_to_dict_contains_expected_keys(self):
        report = DiffGraphReport(base_ref="main", head_ref="HEAD")
        d = report.to_dict()
        for key in (
            "base",
            "head",
            "new_symbols",
            "changed_symbols",
            "affected_files",
            "affected_knowledge",
            "blast_radius_summary",
        ):
            assert key in d
        assert d["base"] == "main"
        assert d["head"] == "HEAD"

    def test_to_json_roundtrips(self):
        report = DiffGraphReport(
            base_ref="main",
            head_ref="feature/auth",
            affected_files=["app/auth.py"],
            new_symbols=[
                StructuralChange(
                    kind="new_symbol", symbol="login", file_path="app/auth.py", line_start=5
                )
            ],
        )
        data = json.loads(report.to_json())
        assert data["base"] == "main"
        assert data["head"] == "feature/auth"
        assert data["affected_files"] == ["app/auth.py"]
        assert len(data["new_symbols"]) == 1
        assert data["new_symbols"][0]["symbol"] == "login"

    def test_to_markdown_heading_includes_refs(self):
        report = DiffGraphReport(base_ref="v1.0", head_ref="v2.0")
        md = report.to_markdown()
        assert "v1.0" in md
        assert "v2.0" in md
        assert "# Structural Diff" in md

    def test_to_markdown_shows_changed_files_section(self):
        report = DiffGraphReport(
            base_ref="main",
            head_ref="HEAD",
            affected_files=["app/models.py", "app/views.py"],
        )
        md = report.to_markdown()
        assert "## Changed Files (2)" in md
        assert "`app/models.py`" in md
        assert "`app/views.py`" in md

    def test_to_markdown_shows_new_symbols_section(self):
        report = DiffGraphReport(
            base_ref="main",
            head_ref="HEAD",
            new_symbols=[
                StructuralChange(
                    kind="new_symbol",
                    symbol="create_user",
                    file_path="app/users.py",
                    line_start=42,
                )
            ],
        )
        md = report.to_markdown()
        assert "## New / Modified Symbols (1)" in md
        assert "`create_user`" in md

    def test_to_markdown_shows_changed_symbols_section(self):
        report = DiffGraphReport(
            base_ref="main",
            head_ref="HEAD",
            changed_symbols=[
                StructuralChange(
                    kind="changed_symbol",
                    symbol="validate",
                    file_path="app/auth.py",
                    detail="signature changed",
                )
            ],
        )
        md = report.to_markdown()
        assert "## Structurally Changed Symbols (1)" in md
        assert "`validate`" in md
        assert "signature changed" in md

    def test_to_markdown_shows_affected_knowledge_section(self):
        report = DiffGraphReport(
            base_ref="main",
            head_ref="HEAD",
            affected_knowledge=[{"type": "Concept", "name": "AuthToken"}],
        )
        md = report.to_markdown()
        assert "## Affected Knowledge (1)" in md
        assert "**Concept**" in md
        assert "`AuthToken`" in md

    def test_to_markdown_shows_blast_radius_section(self):
        report = DiffGraphReport(
            base_ref="main",
            head_ref="HEAD",
            blast_radius_summary={
                "total_affected": 5,
                "affected_files": 3,
                "affected_knowledge": 1,
            },
        )
        md = report.to_markdown()
        assert "## Blast Radius Summary" in md
        assert "Affected nodes: 5" in md
        assert "Affected files: 3" in md
        assert "Affected knowledge: 1" in md

    def test_to_markdown_skips_empty_sections(self):
        """Sections with no data should not appear in the markdown output."""
        report = DiffGraphReport(base_ref="a", head_ref="b")
        md = report.to_markdown()
        assert "## Changed Files" not in md
        assert "## New / Modified Symbols" not in md
        assert "## Structurally Changed Symbols" not in md
        assert "## Affected Knowledge" not in md
        assert "## Blast Radius Summary" not in md

    def test_to_dict_serialises_structural_changes(self):
        """to_dict converts StructuralChange objects to plain dicts."""
        sc = StructuralChange(
            kind="new_symbol", symbol="fn", file_path="f.py", line_start=1
        )
        report = DiffGraphReport(base_ref="a", head_ref="b", new_symbols=[sc])
        d = report.to_dict()
        assert isinstance(d["new_symbols"][0], dict)
        assert d["new_symbols"][0]["kind"] == "new_symbol"
        assert d["new_symbols"][0]["symbol"] == "fn"


# ── DiffGraphAnalyzer ────────────────────────────────────────────────────────


class TestDiffGraphAnalyzer:
    @patch(_IMPACT_ANALYZER)
    @patch("subprocess.run")
    def test_diff_working_tree_calls_correct_git_commands(self, mock_run, mock_ia):
        """diff_working_tree() should invoke git diff -U0 HEAD and git diff HEAD --name-only."""
        mock_run.return_value = _subprocess_result(stdout="")
        mock_ia.return_value = _empty_impact_analyzer().return_value
        store = _mock_store()
        analyzer = DiffGraphAnalyzer(store, repo_path="/tmp/repo")
        analyzer.diff_working_tree()

        git_commands = [call.args[0] for call in mock_run.call_args_list]
        name_only_cmd = ["git", "diff", "HEAD", "--name-only"]
        diff_u0_cmd = ["git", "diff", "-U0", "HEAD"]
        assert name_only_cmd in git_commands
        assert diff_u0_cmd in git_commands

    @patch(_IMPACT_ANALYZER)
    @patch("subprocess.run")
    def test_diff_refs_calls_correct_git_commands(self, mock_run, mock_ia):
        """diff_refs(base, head) should use three-dot syntax in git diff."""
        mock_run.return_value = _subprocess_result(stdout="")
        mock_ia.return_value = _empty_impact_analyzer().return_value
        store = _mock_store()
        analyzer = DiffGraphAnalyzer(store, repo_path="/tmp/repo")
        analyzer.diff_refs(base="main", head="feature/auth")

        git_commands = [call.args[0] for call in mock_run.call_args_list]
        assert ["git", "diff", "-U0", "main...feature/auth"] in git_commands
        assert ["git", "diff", "main...feature/auth", "--name-only"] in git_commands

    @patch(_IMPACT_ANALYZER)
    @patch("subprocess.run")
    def test_no_changed_files_returns_empty_report(self, mock_run, mock_ia):
        """When git diff returns no files, affected_files should be empty."""
        mock_run.return_value = _subprocess_result(stdout="")
        mock_ia.return_value = _empty_impact_analyzer().return_value
        store = _mock_store()
        analyzer = DiffGraphAnalyzer(store, repo_path="/tmp/repo")
        report = analyzer.diff_working_tree()

        assert report.affected_files == []
        assert report.new_symbols == []
        assert report.changed_symbols == []

    @patch(_IMPACT_ANALYZER)
    @patch("subprocess.run")
    def test_changed_files_appear_in_report(self, mock_run, mock_ia):
        """Changed files from git diff --name-only should appear in affected_files."""
        mock_run.return_value = _subprocess_result(stdout="app/auth.py\napp/models.py\n")
        mock_ia.return_value = _empty_impact_analyzer().return_value
        store = _mock_store()
        analyzer = DiffGraphAnalyzer(store, repo_path="/tmp/repo")
        report = analyzer.diff_working_tree()

        assert "app/auth.py" in report.affected_files
        assert "app/models.py" in report.affected_files

    @patch("subprocess.run")
    def test_is_new_returns_true_when_file_missing_in_base(self, mock_run):
        """_is_new should return True when git show exits non-zero (file not in base)."""
        mock_run.return_value = _subprocess_result(returncode=128)
        store = _mock_store()
        analyzer = DiffGraphAnalyzer(store, repo_path="/tmp/repo")

        assert analyzer._is_new("new_fn", "app/new_module.py", "main") is True

    @patch("subprocess.run")
    def test_is_new_returns_false_when_file_exists_in_base(self, mock_run):
        """_is_new should return False when git show exits zero (file existed in base)."""
        mock_run.return_value = _subprocess_result(returncode=0)
        store = _mock_store()
        analyzer = DiffGraphAnalyzer(store, repo_path="/tmp/repo")

        assert analyzer._is_new("old_fn", "app/existing.py", "main") is False

    @patch("subprocess.run")
    def test_is_new_returns_false_for_empty_file_path(self, mock_run):
        """_is_new returns False for empty file_path without calling git."""
        store = _mock_store()
        analyzer = DiffGraphAnalyzer(store, repo_path="/tmp/repo")

        assert analyzer._is_new("fn", "", "main") is False
        mock_run.assert_not_called()

    @patch(_IMPACT_ANALYZER)
    @patch(_LINES_OVERLAP, return_value=True)
    @patch("subprocess.run")
    def test_symbols_classified_as_new_when_file_is_new(
        self, mock_run, _mock_overlap, mock_ia_cls
    ):
        """Symbols in a newly created file should be classified as new_symbol."""
        mock_run.side_effect = [
            _subprocess_result(stdout="app/new.py\n"),  # --name-only
            _subprocess_result(stdout=""),               # -U0 diff (empty => fallback)
            _subprocess_result(returncode=128),          # git show => file is new
        ]

        store = _mock_store(
            result_set=[["Function", "new_handler", "app/new.py", 1, 20]]
        )

        mock_impact = MagicMock()
        mock_br_result = MagicMock()
        mock_br_result.affected_nodes = []
        mock_br_result.affected_knowledge = []
        mock_impact.blast_radius.return_value = mock_br_result
        mock_ia_cls.return_value = mock_impact

        analyzer = DiffGraphAnalyzer(store, repo_path="/tmp/repo")
        report = analyzer.diff_refs(base="main", head="HEAD")

        assert len(report.new_symbols) == 1
        assert report.new_symbols[0].symbol == "new_handler"
        assert report.new_symbols[0].kind == "new_symbol"

    @patch(_IMPACT_ANALYZER)
    @patch(_LINES_OVERLAP, return_value=True)
    @patch("subprocess.run")
    def test_symbols_classified_as_changed_when_file_exists(
        self, mock_run, _mock_overlap, mock_ia_cls
    ):
        """Symbols in an existing file should be classified as changed_symbol."""
        mock_run.side_effect = [
            _subprocess_result(stdout="app/auth.py\n"),  # --name-only
            _subprocess_result(stdout=""),                # -U0 diff
            _subprocess_result(returncode=0),             # git show => file existed
        ]

        store = _mock_store(
            result_set=[["Function", "authenticate", "app/auth.py", 10, 45]]
        )

        mock_impact = MagicMock()
        mock_br_result = MagicMock()
        mock_br_result.affected_nodes = []
        mock_br_result.affected_knowledge = []
        mock_impact.blast_radius.return_value = mock_br_result
        mock_ia_cls.return_value = mock_impact

        analyzer = DiffGraphAnalyzer(store, repo_path="/tmp/repo")
        report = analyzer.diff_refs(base="main", head="HEAD")

        assert len(report.changed_symbols) == 1
        assert report.changed_symbols[0].symbol == "authenticate"
        assert report.changed_symbols[0].kind == "changed_symbol"

    @patch(_IMPACT_ANALYZER)
    @patch(_LINES_OVERLAP, return_value=True)
    @patch("subprocess.run")
    def test_blast_radius_summary_aggregated(self, mock_run, _mock_overlap, mock_ia_cls):
        """blast_radius_summary should reflect aggregated impact results."""
        mock_run.side_effect = [
            _subprocess_result(stdout="app/svc.py\n"),
            _subprocess_result(stdout=""),
            _subprocess_result(returncode=0),  # existing file
        ]

        store = _mock_store(
            result_set=[["Function", "process", "app/svc.py", 5, 30]]
        )

        mock_impact = MagicMock()
        mock_br_result = MagicMock()
        mock_br_result.affected_nodes = [
            {"type": "Function", "name": "caller_a", "file_path": "app/a.py", "line_start": 1},
            {"type": "Class", "name": "ModelB", "file_path": "app/b.py", "line_start": 10},
        ]
        mock_br_result.affected_knowledge = [
            {"type": "Concept", "name": "PaymentFlow"},
        ]
        mock_impact.blast_radius.return_value = mock_br_result
        mock_ia_cls.return_value = mock_impact

        analyzer = DiffGraphAnalyzer(store, repo_path="/tmp/repo")
        report = analyzer.diff_refs(base="main", head="HEAD")

        assert report.blast_radius_summary["total_affected"] == 2
        assert report.blast_radius_summary["affected_files"] == 2
        assert report.blast_radius_summary["affected_knowledge"] == 1

    @patch(_IMPACT_ANALYZER)
    @patch(_LINES_OVERLAP, return_value=True)
    @patch("subprocess.run")
    def test_affected_knowledge_deduped(self, mock_run, _mock_overlap, mock_ia_cls):
        """Duplicate knowledge entries should be collapsed in the report."""
        mock_run.side_effect = [
            _subprocess_result(stdout="a.py\n"),
            _subprocess_result(stdout=""),
            _subprocess_result(returncode=0),
            _subprocess_result(returncode=0),
        ]

        # Two symbols in the same file
        store = _mock_store(
            result_set=[
                ["Function", "fn_a", "a.py", 1, 10],
                ["Function", "fn_b", "a.py", 20, 30],
            ]
        )

        mock_impact = MagicMock()
        mock_br_result = MagicMock()
        mock_br_result.affected_nodes = []
        mock_br_result.affected_knowledge = [{"type": "Concept", "name": "Shared"}]
        mock_impact.blast_radius.return_value = mock_br_result
        mock_ia_cls.return_value = mock_impact

        analyzer = DiffGraphAnalyzer(store, repo_path="/tmp/repo")
        report = analyzer.diff_refs(base="main", head="HEAD")

        # Should only appear once despite being returned by both symbols
        assert len(report.affected_knowledge) == 1
        assert report.affected_knowledge[0]["name"] == "Shared"

    @patch(_IMPACT_ANALYZER)
    @patch(_LINES_OVERLAP, return_value=True)
    @patch("subprocess.run")
    def test_duplicate_symbols_not_counted_twice(
        self, mock_run, _mock_overlap, mock_ia_cls
    ):
        """A symbol appearing in multiple rows should only be counted once."""
        mock_run.side_effect = [
            _subprocess_result(stdout="a.py\n"),
            _subprocess_result(stdout=""),
            _subprocess_result(returncode=0),
        ]

        # Same symbol name+file twice in result set
        store = _mock_store(
            result_set=[
                ["Function", "handler", "a.py", 1, 10],
                ["Method", "handler", "a.py", 1, 10],
            ]
        )

        mock_impact = MagicMock()
        mock_br = MagicMock()
        mock_br.affected_nodes = []
        mock_br.affected_knowledge = []
        mock_impact.blast_radius.return_value = mock_br
        mock_ia_cls.return_value = mock_impact

        analyzer = DiffGraphAnalyzer(store, repo_path="/tmp/repo")
        report = analyzer.diff_refs(base="main", head="HEAD")

        total_symbols = len(report.new_symbols) + len(report.changed_symbols)
        assert total_symbols == 1

    def test_report_base_and_head_refs_set(self):
        """DiffGraphReport stores the ref names from the analyzer."""
        report = DiffGraphReport(base_ref="release/1.0", head_ref="develop")
        assert report.base_ref == "release/1.0"
        assert report.head_ref == "develop"

    @patch(_IMPACT_ANALYZER)
    @patch("subprocess.run")
    def test_affected_files_sorted(self, mock_run, mock_ia):
        """affected_files should be sorted alphabetically."""
        mock_run.return_value = _subprocess_result(stdout="z.py\na.py\nm.py\n")
        mock_ia.return_value = _empty_impact_analyzer().return_value
        store = _mock_store()
        analyzer = DiffGraphAnalyzer(store, repo_path="/tmp/repo")
        report = analyzer.diff_working_tree()

        assert report.affected_files == ["a.py", "m.py", "z.py"]

    @patch(_IMPACT_ANALYZER)
    @patch(_LINES_OVERLAP, return_value=True)
    @patch("subprocess.run")
    def test_new_symbol_line_start_preserved(self, mock_run, _mock_overlap, mock_ia_cls):
        """The line_start from the query row should appear in the StructuralChange."""
        mock_run.side_effect = [
            _subprocess_result(stdout="mod.py\n"),
            _subprocess_result(stdout=""),
            _subprocess_result(returncode=128),  # new file
        ]
        store = _mock_store(
            result_set=[["Class", "Widget", "mod.py", 55, 99]]
        )

        mock_impact = MagicMock()
        mock_br = MagicMock()
        mock_br.affected_nodes = []
        mock_br.affected_knowledge = []
        mock_impact.blast_radius.return_value = mock_br
        mock_ia_cls.return_value = mock_impact

        analyzer = DiffGraphAnalyzer(store, repo_path="/tmp/repo")
        report = analyzer.diff_refs(base="main", head="HEAD")

        assert report.new_symbols[0].line_start == 55
        assert report.new_symbols[0].file_path == "mod.py"
