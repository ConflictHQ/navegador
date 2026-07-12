"""Tests for #130 — native exclusion (--exclude / .navignore) and metarepo
authored/full ingest modes (recursive nested-clone discovery)."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from navegador.cli.commands import main
from navegador.graph.store import GraphStore
from navegador.ingestion.parser import RepoIngester
from navegador.multirepo import discover_nested_repos


def _mock_store():
    store = MagicMock()
    store.query.return_value = MagicMock(result_set=[])
    return store


def _write(root: Path, rel: str, content: str = "x = 1\n") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# ── Exclusion patterns ─────────────────────────────────────────────────────


class TestExclusion:
    def test_exclude_glob_prunes_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write(root, "app.py")
            _write(root, "generated/big.py")
            files = {
                p.name
                for p in RepoIngester(_mock_store(), exclude=["generated"])._iter_source_files(root)
            }
        assert files == {"app.py"}

    def test_exclude_matches_relative_path_glob(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write(root, "docs/gen/a.py")
            _write(root, "docs/handwritten/b.py")
            files = {
                p.name
                for p in RepoIngester(_mock_store(), exclude=["docs/gen/*"])._iter_source_files(
                    root
                )
            }
        assert files == {"b.py"}

    def test_exclude_matches_file_component(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write(root, "app.py")
            _write(root, "sub/schema.gen.py")
            files = {
                p.name
                for p in RepoIngester(_mock_store(), exclude=["*.gen.py"])._iter_source_files(root)
            }
        assert files == {"app.py"}

    def test_navignore_is_honored(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write(root, "app.py")
            _write(root, "vendored-core/huge.py")
            _write(root, "tmp.gen.py")
            (root / ".navignore").write_text(
                "# vendored OSS tree\nvendored-core/\n*.gen.py\n", encoding="utf-8"
            )
            files = {p.name for p in RepoIngester(_mock_store())._iter_source_files(root)}
        assert files == {"app.py"}

    def test_include_nested_repos_descends_into_clones(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write(root, "own.py")
            nested = root / "core"
            (nested / ".git").mkdir(parents=True)
            _write(root, "core/vendored.py")

            default = {p.name for p in RepoIngester(_mock_store())._iter_source_files(root)}
            full = {
                p.name
                for p in RepoIngester(_mock_store(), include_nested_repos=True)._iter_source_files(
                    root
                )
            }
        assert default == {"own.py"}
        assert full == {"own.py", "vendored.py"}


# ── Nested repo discovery ──────────────────────────────────────────────────


def _metarepo(tmpdir: str) -> Path:
    """Metarepo root: own source + two nested clones, one vendoring a clone."""
    root = Path(tmpdir)
    (root / ".git").mkdir()
    _write(root, "scripts/deploy.py")

    plain = root / "nodes" / "plain-node"
    (plain / ".git").mkdir(parents=True)
    _write(root, "nodes/plain-node/node.py")

    wrapper = root / "nodes" / "wrapper-node"
    (wrapper / ".git").mkdir(parents=True)
    _write(root, "nodes/wrapper-node/wrapper.py")
    vendored = wrapper / "oss-core"
    (vendored / ".git").mkdir(parents=True)
    _write(root, "nodes/wrapper-node/oss-core/core.py")
    return root


class TestDiscoverNestedRepos:
    def test_finds_clones_at_depth_and_stops_at_boundaries(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = _metarepo(tmpdir)
            found = discover_nested_repos(root)
        names = [name for name, _ in found]
        assert names == ["nodes-plain-node", "nodes-wrapper-node"]
        # the vendored clone inside wrapper-node stays inside its boundary
        assert "nodes-wrapper-node-oss-core" not in names

    def test_skip_dirs_and_hidden_dirs_are_not_scanned(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            hidden_clone = root / ".cache" / "repo"
            (hidden_clone / ".git").mkdir(parents=True)
            vendor_clone = root / "node_modules" / "dep"
            (vendor_clone / ".git").mkdir(parents=True)
            assert discover_nested_repos(root) == []


# ── workspace ingest CLI (metarepo modes) ──────────────────────────────────


@pytest.fixture(scope="module")
def central(tmp_path_factory):
    s = GraphStore.sqlite(str(tmp_path_factory.mktemp("metarepo") / "graph.db"))
    yield s
    s.close()


def _graph_files(central, graph_name: str) -> set:
    result = central.with_graph(graph_name).query("MATCH (f:File) RETURN f.name")
    return {row[0] for row in result.result_set or []}


class TestWorkspaceIngestMetarepoModes:
    def test_authored_mode_federates_clones_and_skips_vendored(self, central):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = _metarepo(tmpdir)
            runner = CliRunner()
            with patch("navegador.cli.commands._get_store", return_value=central):
                result = runner.invoke(
                    main,
                    ["workspace", "ingest", f"meta={root}", "--recursive", "--mode", "authored"],
                )

            assert result.exit_code == 0, result.output
            assert _graph_files(central, "navegador_meta") == {"deploy.py"}
            assert _graph_files(central, "navegador_meta-nodes-plain-node") == {"node.py"}
            assert _graph_files(central, "navegador_meta-nodes-wrapper-node") == {"wrapper.py"}

    def test_full_mode_includes_vendored_cores(self, central):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = _metarepo(tmpdir)
            runner = CliRunner()
            with patch("navegador.cli.commands._get_store", return_value=central):
                result = runner.invoke(
                    main, ["workspace", "ingest", f"meta={root}", "--mode", "full"]
                )

            assert result.exit_code == 0, result.output
            # full implies --recursive; the vendored core lands in its wrapper's graph
            assert _graph_files(central, "navegador_meta-nodes-wrapper-node") == {
                "wrapper.py",
                "core.py",
            }

    def test_bare_path_defaults_name_to_basename(self, central):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "solo"
            root.mkdir()
            _write(root, "solo.py")
            runner = CliRunner()
            with patch("navegador.cli.commands._get_store", return_value=central):
                result = runner.invoke(
                    main, ["workspace", "ingest", str(root), "--mode", "federated"]
                )

            assert result.exit_code == 0, result.output
            assert _graph_files(central, "navegador_solo") == {"solo.py"}

    def test_name_path_specs_still_work_unified(self, central):
        central.clear()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "uni"
            root.mkdir()
            _write(root, "uni.py")
            runner = CliRunner()
            with patch("navegador.cli.commands._get_store", return_value=central):
                result = runner.invoke(main, ["workspace", "ingest", f"uni={root}"])

            assert result.exit_code == 0, result.output
            result_set = central.query("MATCH (f:File) RETURN f.name").result_set or []
        assert {row[0] for row in result_set} == {"uni.py"}
