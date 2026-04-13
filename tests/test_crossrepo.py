"""Tests for navegador.analysis.crossrepo — cross-repo blast radius analysis."""

import json
from unittest.mock import MagicMock, patch

from navegador.analysis.crossrepo import CrossRepoImpactAnalyzer, CrossRepoImpactResult

# ── Helpers ──────────────────────────────────────────────────────────────────


def _mock_store(result_set=None):
    store = MagicMock()
    result = MagicMock()
    result.result_set = result_set or []
    store.query.return_value = result
    return store


def _multi_mock_store(*result_sets):
    store = MagicMock()
    results = []
    for rs in result_sets:
        r = MagicMock()
        r.result_set = rs
        results.append(r)
    store.query.side_effect = results
    return store


# ── CrossRepoImpactResult.to_markdown ────────────────────────────────────────


class TestCrossRepoImpactResultMarkdown:
    def test_header_includes_symbol_name(self):
        result = CrossRepoImpactResult(name="UserSchema", source_repo="models", depth=3)
        md = result.to_markdown()
        assert "# Cross-Repo Blast Radius" in md
        assert "`UserSchema`" in md

    def test_source_repo_and_depth_in_header(self):
        result = CrossRepoImpactResult(name="foo", source_repo="core-lib", depth=5)
        md = result.to_markdown()
        assert "core-lib" in md
        assert "5" in md

    def test_affected_repos_section(self):
        result = CrossRepoImpactResult(
            name="foo", source_repo="lib", depth=3,
            affected_repos=["api-service", "web-frontend"],
        )
        md = result.to_markdown()
        assert "## Affected Repos (2)" in md
        assert "`api-service`" in md
        assert "`web-frontend`" in md

    def test_affected_files_section(self):
        result = CrossRepoImpactResult(
            name="foo", source_repo="", depth=3,
            affected_files=["src/handler.py", "src/utils.py"],
        )
        md = result.to_markdown()
        assert "## Affected Files (2)" in md
        assert "`src/handler.py`" in md

    def test_affected_nodes_section(self):
        result = CrossRepoImpactResult(
            name="foo", source_repo="", depth=3,
            affected_nodes=[
                {"type": "Function", "name": "bar", "file_path": "b.py",
                 "line_start": 10, "repo": "api"},
            ],
        )
        md = result.to_markdown()
        assert "## Affected Symbols (1)" in md
        assert "**Function**" in md
        assert "`bar`" in md
        assert "`b.py`" in md
        assert "[api]" in md

    def test_affected_knowledge_section(self):
        result = CrossRepoImpactResult(
            name="foo", source_repo="", depth=3,
            affected_knowledge=[{"type": "Rule", "name": "no_pii"}],
        )
        md = result.to_markdown()
        assert "## Affected Knowledge (1)" in md
        assert "**Rule**" in md
        assert "`no_pii`" in md

    def test_empty_sections_omitted(self):
        result = CrossRepoImpactResult(name="foo", source_repo="", depth=3)
        md = result.to_markdown()
        assert "## Affected Repos" not in md
        assert "## Affected Files" not in md
        assert "## Affected Symbols" not in md
        assert "## Affected Knowledge" not in md

    def test_nodes_capped_at_50_with_overflow_message(self):
        nodes = [
            {"type": "Function", "name": f"fn_{i}", "file_path": f"f{i}.py"}
            for i in range(60)
        ]
        result = CrossRepoImpactResult(
            name="big", source_repo="", depth=3, affected_nodes=nodes,
        )
        md = result.to_markdown()
        assert "and 10 more" in md


# ── CrossRepoImpactResult.to_json ───────────────────────────────────────────


class TestCrossRepoImpactResultJson:
    def test_round_trip(self):
        result = CrossRepoImpactResult(
            name="UserSchema", source_repo="models", depth=3,
            affected_nodes=[
                {"type": "Class", "name": "UserView", "file_path": "views.py",
                 "line_start": 5, "repo": "api"},
            ],
            affected_files=["views.py"],
            affected_repos=["api"],
            affected_knowledge=[{"type": "Concept", "name": "user-domain"}],
        )
        raw = result.to_json()
        data = json.loads(raw)

        assert data["name"] == "UserSchema"
        assert data["source_repo"] == "models"
        assert data["depth"] == 3
        assert len(data["affected_nodes"]) == 1
        assert data["affected_nodes"][0]["name"] == "UserView"
        assert data["affected_files"] == ["views.py"]
        assert data["affected_repos"] == ["api"]
        assert data["counts"]["nodes"] == 1
        assert data["counts"]["files"] == 1
        assert data["counts"]["repos"] == 1
        assert data["counts"]["knowledge"] == 1

    def test_empty_result_json(self):
        result = CrossRepoImpactResult(name="ghost", source_repo="", depth=2)
        data = json.loads(result.to_json())
        assert data["affected_nodes"] == []
        assert data["affected_files"] == []
        assert data["counts"]["nodes"] == 0

    def test_to_dict_counts_match_lists(self):
        result = CrossRepoImpactResult(
            name="x", source_repo="", depth=1,
            affected_nodes=[{"type": "Function", "name": "a"},
                            {"type": "Function", "name": "b"}],
            affected_files=["a.py", "b.py", "c.py"],
            affected_repos=["repo1"],
        )
        d = result.to_dict()
        assert d["counts"]["nodes"] == 2
        assert d["counts"]["files"] == 3
        assert d["counts"]["repos"] == 1
        assert d["counts"]["knowledge"] == 0


# ── CrossRepoImpactAnalyzer.blast_radius ─────────────────────────────────────


class TestBlastRadius:
    def test_collects_affected_nodes_from_query(self):
        store = _multi_mock_store(
            # _CROSS_REPO_BLAST query
            [
                ["Function", "handler", "api/views.py", 10, "api-service"],
                ["Class", "UserModel", "models/user.py", 1, "shared-models"],
            ],
            # _KNOWLEDGE query
            [],
        )
        analyzer = CrossRepoImpactAnalyzer(store)
        result = analyzer.blast_radius("UserSchema", repo="shared-models", depth=3)

        assert result.name == "UserSchema"
        assert result.source_repo == "shared-models"
        assert result.depth == 3
        assert len(result.affected_nodes) == 2
        assert result.affected_nodes[0]["name"] == "handler"
        assert result.affected_nodes[0]["repo"] == "api-service"
        assert result.affected_nodes[1]["name"] == "UserModel"

    def test_affected_files_deduped_and_sorted(self):
        store = _multi_mock_store(
            [
                ["Function", "a", "z.py", 1, ""],
                ["Function", "b", "a.py", 2, ""],
                ["Function", "c", "z.py", 3, ""],  # duplicate file
            ],
            [],
        )
        result = CrossRepoImpactAnalyzer(store).blast_radius("x")
        assert result.affected_files == ["a.py", "z.py"]

    def test_affected_repos_deduped_and_sorted(self):
        store = _multi_mock_store(
            [
                ["Function", "a", "f.py", 1, "repo-b"],
                ["Function", "b", "g.py", 2, "repo-a"],
                ["Function", "c", "h.py", 3, "repo-b"],  # duplicate repo
            ],
            [],
        )
        result = CrossRepoImpactAnalyzer(store).blast_radius("x")
        assert result.affected_repos == ["repo-a", "repo-b"]

    def test_knowledge_layer_collected(self):
        store = _multi_mock_store(
            # blast radius
            [["Function", "a", "f.py", 1, ""]],
            # knowledge
            [
                ["Rule", "no_pii_logging"],
                ["Concept", "user-domain"],
            ],
        )
        result = CrossRepoImpactAnalyzer(store).blast_radius("foo")
        assert len(result.affected_knowledge) == 2
        assert result.affected_knowledge[0] == {"type": "Rule", "name": "no_pii_logging"}
        assert result.affected_knowledge[1] == {"type": "Concept", "name": "user-domain"}

    def test_empty_graph_returns_empty_result(self):
        store = _multi_mock_store([], [])
        result = CrossRepoImpactAnalyzer(store).blast_radius("ghost")

        assert result.affected_nodes == []
        assert result.affected_files == []
        assert result.affected_repos == []
        assert result.affected_knowledge == []

    def test_query_exception_handled_gracefully(self):
        store = MagicMock()
        store.query.side_effect = Exception("connection lost")
        result = CrossRepoImpactAnalyzer(store).blast_radius("foo")
        assert result.affected_nodes == []
        assert result.affected_knowledge == []

    def test_file_path_param_forwarded(self):
        store = _multi_mock_store([], [])
        analyzer = CrossRepoImpactAnalyzer(store)
        analyzer.blast_radius("foo", file_path="specific.py", depth=5)

        # Verify the first query got the params including file_path
        first_call_args = store.query.call_args_list[0]
        params = first_call_args[0][1]
        assert params["file_path"] == "specific.py"
        assert params["depth"] == 5

    def test_none_values_in_rows_handled(self):
        store = _multi_mock_store(
            [[None, None, None, None, None]],
            [],
        )
        result = CrossRepoImpactAnalyzer(store).blast_radius("foo")
        assert result.affected_nodes[0]["type"] == "Unknown"
        assert result.affected_nodes[0]["name"] == ""


# ── CrossRepoImpactAnalyzer.blast_radius_federated ───────────────────────────


class TestBlastRadiusFederated:
    @patch("navegador.analysis.crossrepo.CrossRepoImpactAnalyzer")
    def test_queries_each_store(self, _mock_cls):
        """Each store in the federation should be queried."""
        store_a = _mock_store([
            ["Function", "handler_a", "a.py", 10],
        ])
        store_b = _mock_store([
            ["Class", "ModelB", "b.py", 1],
        ])

        # Use real analyzer on a primary store
        primary = _mock_store()
        analyzer = CrossRepoImpactAnalyzer.__wrapped__(primary) if hasattr(
            CrossRepoImpactAnalyzer, '__wrapped__') else CrossRepoImpactAnalyzer(primary)

        # Call blast_radius_federated directly — it does not use self.store
        result = analyzer.blast_radius_federated(
            "UserSchema",
            stores={"repo-a": store_a, "repo-b": store_b},
            depth=2,
        )

        assert isinstance(result, CrossRepoImpactResult)
        # Each store should have been queried
        assert store_a.query.called
        assert store_b.query.called

    def test_merges_results_from_multiple_stores(self):
        store_a = _mock_store([
            ["Function", "fn_a", "a.py", 10],
        ])
        store_b = _mock_store([
            ["Class", "ClassB", "b.py", 1],
            ["Function", "fn_b2", "b2.py", 5],
        ])

        primary = _mock_store()
        analyzer = CrossRepoImpactAnalyzer(primary)
        result = analyzer.blast_radius_federated(
            "SharedType",
            stores={"alpha": store_a, "beta": store_b},
            depth=3,
        )

        assert len(result.affected_nodes) == 3
        repos_in_nodes = {n["repo"] for n in result.affected_nodes}
        assert "alpha" in repos_in_nodes
        assert "beta" in repos_in_nodes

    def test_affected_repos_populated(self):
        store_a = _mock_store([["Function", "fn", "f.py", 1]])
        store_b = _mock_store([])  # no results from this repo

        primary = _mock_store()
        analyzer = CrossRepoImpactAnalyzer(primary)
        result = analyzer.blast_radius_federated(
            "X", stores={"has-hits": store_a, "no-hits": store_b},
        )
        # Only repos with actual results should be in affected_repos
        assert "has-hits" in result.affected_repos

    def test_affected_files_merged_and_sorted(self):
        store_a = _mock_store([["Function", "a", "z.py", 1]])
        store_b = _mock_store([["Function", "b", "a.py", 2]])

        primary = _mock_store()
        analyzer = CrossRepoImpactAnalyzer(primary)
        result = analyzer.blast_radius_federated(
            "X", stores={"r1": store_a, "r2": store_b},
        )
        assert result.affected_files == ["a.py", "z.py"]

    def test_empty_stores_returns_empty(self):
        primary = _mock_store()
        analyzer = CrossRepoImpactAnalyzer(primary)
        result = analyzer.blast_radius_federated("X", stores={})

        assert result.affected_nodes == []
        assert result.affected_files == []
        assert result.affected_repos == []

    def test_store_query_exception_handled(self):
        bad_store = MagicMock()
        bad_store.query.side_effect = Exception("connection refused")
        good_store = _mock_store([["Function", "fn", "f.py", 1]])

        primary = _mock_store()
        analyzer = CrossRepoImpactAnalyzer(primary)
        result = analyzer.blast_radius_federated(
            "X", stores={"bad": bad_store, "good": good_store},
        )
        # Should still get results from the good store
        assert len(result.affected_nodes) == 1
        assert result.affected_nodes[0]["repo"] == "good"
