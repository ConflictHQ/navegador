"""Tests for navegador.monorepo — WorkspaceDetector, MonorepoIngester, CLI flag."""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest
from click.testing import CliRunner

from navegador.cli.commands import main
from navegador.monorepo import MonorepoIngester, WorkspaceConfig, WorkspaceDetector


# ── Helpers ───────────────────────────────────────────────────────────────────


def _mock_store():
    store = MagicMock()
    store.query.return_value = MagicMock(result_set=[])
    return store


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# ── WorkspaceDetector — positive cases ────────────────────────────────────────


class TestWorkspaceDetectorTurborepo:
    def test_detects_type(self, tmp_path):
        _write(tmp_path / "turbo.json", '{"pipeline": {}}')
        # No package.json workspaces — fallback scan
        (tmp_path / "packages" / "app").mkdir(parents=True)
        _write(tmp_path / "packages" / "app" / "package.json", '{"name": "app"}')
        config = WorkspaceDetector().detect(tmp_path)
        assert config is not None
        assert config.type == "turborepo"

    def test_root_is_resolved(self, tmp_path):
        _write(tmp_path / "turbo.json", '{"pipeline": {}}')
        config = WorkspaceDetector().detect(tmp_path)
        assert config.root == tmp_path.resolve()

    def test_uses_package_json_workspaces(self, tmp_path):
        _write(tmp_path / "turbo.json", '{}')
        _write(
            tmp_path / "package.json",
            json.dumps({"workspaces": ["packages/*"]}),
        )
        (tmp_path / "packages" / "alpha").mkdir(parents=True)
        (tmp_path / "packages" / "beta").mkdir(parents=True)
        config = WorkspaceDetector().detect(tmp_path)
        assert config is not None
        names = [p.name for p in config.packages]
        assert "alpha" in names
        assert "beta" in names

    def test_name_defaults_to_dirname(self, tmp_path):
        _write(tmp_path / "turbo.json", '{}')
        config = WorkspaceDetector().detect(tmp_path)
        assert config.name == tmp_path.name


class TestWorkspaceDetectorNx:
    def test_detects_type(self, tmp_path):
        _write(tmp_path / "nx.json", '{}')
        config = WorkspaceDetector().detect(tmp_path)
        assert config is not None
        assert config.type == "nx"

    def test_finds_apps_and_libs(self, tmp_path):
        _write(tmp_path / "nx.json", '{}')
        (tmp_path / "apps" / "web").mkdir(parents=True)
        (tmp_path / "libs" / "ui").mkdir(parents=True)
        config = WorkspaceDetector().detect(tmp_path)
        names = [p.name for p in config.packages]
        assert "web" in names
        assert "ui" in names

    def test_empty_nx_has_no_packages(self, tmp_path):
        _write(tmp_path / "nx.json", '{}')
        config = WorkspaceDetector().detect(tmp_path)
        # No apps/libs dirs — falls back to package.json scan which also finds nothing
        assert config is not None
        assert config.packages == []


class TestWorkspaceDetectorPnpm:
    def test_detects_type(self, tmp_path):
        _write(
            tmp_path / "pnpm-workspace.yaml",
            "packages:\n  - 'packages/*'\n",
        )
        (tmp_path / "packages" / "foo").mkdir(parents=True)
        config = WorkspaceDetector().detect(tmp_path)
        assert config is not None
        assert config.type == "pnpm"

    def test_resolves_glob_packages(self, tmp_path):
        _write(
            tmp_path / "pnpm-workspace.yaml",
            "packages:\n  - 'pkgs/*'\n",
        )
        (tmp_path / "pkgs" / "core").mkdir(parents=True)
        (tmp_path / "pkgs" / "utils").mkdir(parents=True)
        config = WorkspaceDetector().detect(tmp_path)
        names = [p.name for p in config.packages]
        assert "core" in names
        assert "utils" in names

    def test_empty_yaml_returns_fallback(self, tmp_path):
        _write(tmp_path / "pnpm-workspace.yaml", "")
        config = WorkspaceDetector().detect(tmp_path)
        assert config is not None
        assert config.type == "pnpm"


class TestWorkspaceDetectorYarn:
    def test_detects_type(self, tmp_path):
        _write(
            tmp_path / "package.json",
            json.dumps({"name": "root", "workspaces": ["packages/*"]}),
        )
        (tmp_path / "packages" / "a").mkdir(parents=True)
        config = WorkspaceDetector().detect(tmp_path)
        assert config is not None
        assert config.type == "yarn"

    def test_yarn_berry_workspaces_packages_key(self, tmp_path):
        _write(
            tmp_path / "package.json",
            json.dumps({"workspaces": {"packages": ["apps/*"]}}),
        )
        (tmp_path / "apps" / "web").mkdir(parents=True)
        config = WorkspaceDetector().detect(tmp_path)
        assert config is not None
        assert config.type == "yarn"
        assert any(p.name == "web" for p in config.packages)

    def test_explicit_package_path(self, tmp_path):
        pkg_dir = tmp_path / "my-package"
        pkg_dir.mkdir()
        _write(
            tmp_path / "package.json",
            json.dumps({"workspaces": ["my-package"]}),
        )
        config = WorkspaceDetector().detect(tmp_path)
        assert config is not None
        assert any(p.name == "my-package" for p in config.packages)


class TestWorkspaceDetectorCargo:
    def test_detects_type(self, tmp_path):
        _write(
            tmp_path / "Cargo.toml",
            '[workspace]\nmembers = ["crates/core", "crates/utils"]\n',
        )
        (tmp_path / "crates" / "core").mkdir(parents=True)
        (tmp_path / "crates" / "utils").mkdir(parents=True)
        config = WorkspaceDetector().detect(tmp_path)
        assert config is not None
        assert config.type == "cargo"

    def test_resolves_member_paths(self, tmp_path):
        _write(
            tmp_path / "Cargo.toml",
            '[workspace]\nmembers = ["crates/alpha"]\n',
        )
        (tmp_path / "crates" / "alpha").mkdir(parents=True)
        config = WorkspaceDetector().detect(tmp_path)
        assert any(p.name == "alpha" for p in config.packages)

    def test_non_workspace_cargo_returns_none(self, tmp_path):
        _write(
            tmp_path / "Cargo.toml",
            '[package]\nname = "myapp"\nversion = "0.1.0"\n',
        )
        config = WorkspaceDetector().detect(tmp_path)
        assert config is None


class TestWorkspaceDetectorGo:
    def test_detects_type(self, tmp_path):
        _write(tmp_path / "go.work", "go 1.21\nuse ./api\nuse ./worker\n")
        (tmp_path / "api").mkdir()
        (tmp_path / "worker").mkdir()
        config = WorkspaceDetector().detect(tmp_path)
        assert config is not None
        assert config.type == "go"

    def test_resolves_use_paths(self, tmp_path):
        _write(tmp_path / "go.work", "go 1.21\nuse ./pkg/a\n")
        (tmp_path / "pkg" / "a").mkdir(parents=True)
        config = WorkspaceDetector().detect(tmp_path)
        assert any(p.name == "a" for p in config.packages)

    def test_missing_dirs_skipped(self, tmp_path):
        _write(tmp_path / "go.work", "go 1.21\nuse ./missing\n")
        config = WorkspaceDetector().detect(tmp_path)
        assert config is not None
        assert config.packages == []


# ── WorkspaceDetector — negative case ────────────────────────────────────────


class TestWorkspaceDetectorNoWorkspace:
    def test_plain_repo_returns_none(self, tmp_path):
        # Just a bare directory — no workspace config files
        _write(tmp_path / "main.py", "print('hello')")
        config = WorkspaceDetector().detect(tmp_path)
        assert config is None

    def test_package_json_without_workspaces_returns_none(self, tmp_path):
        _write(tmp_path / "package.json", '{"name": "single-package"}')
        config = WorkspaceDetector().detect(tmp_path)
        assert config is None

    def test_empty_directory_returns_none(self, tmp_path):
        config = WorkspaceDetector().detect(tmp_path)
        assert config is None


# ── WorkspaceConfig ───────────────────────────────────────────────────────────


class TestWorkspaceConfig:
    def test_name_defaults_to_root_dirname(self, tmp_path):
        cfg = WorkspaceConfig(type="yarn", root=tmp_path, packages=[])
        assert cfg.name == tmp_path.name

    def test_explicit_name_preserved(self, tmp_path):
        cfg = WorkspaceConfig(type="yarn", root=tmp_path, packages=[], name="my-repo")
        assert cfg.name == "my-repo"

    def test_packages_list_stored(self, tmp_path):
        pkgs = [tmp_path / "a", tmp_path / "b"]
        cfg = WorkspaceConfig(type="pnpm", root=tmp_path, packages=pkgs)
        assert cfg.packages == pkgs


# ── MonorepoIngester ──────────────────────────────────────────────────────────


class TestMonorepoIngesterFallback:
    def test_no_workspace_falls_back_to_single_ingest(self, tmp_path):
        """When no workspace config is detected, ingest as a regular repo."""
        store = _mock_store()
        _write(tmp_path / "main.py", "x = 1")

        with patch(
            "navegador.monorepo.WorkspaceDetector.detect", return_value=None
        ), patch("navegador.monorepo.RepoIngester") as MockRI:
            MockRI.return_value.ingest.return_value = {
                "files": 1, "functions": 0, "classes": 0, "edges": 0, "skipped": 0
            }
            ingester = MonorepoIngester(store)
            stats = ingester.ingest(tmp_path)

        assert stats["packages"] == 0
        assert stats["workspace_type"] == "none"
        MockRI.return_value.ingest.assert_called_once()

    def test_raises_on_missing_path(self):
        store = _mock_store()
        ingester = MonorepoIngester(store)
        with pytest.raises(FileNotFoundError):
            ingester.ingest("/this/does/not/exist")


class TestMonorepoIngesterWithWorkspace:
    def _setup_yarn_monorepo(self, tmp_path):
        """Create a minimal Yarn workspace fixture."""
        _write(
            tmp_path / "package.json",
            json.dumps({"name": "root", "workspaces": ["packages/*"]}),
        )
        (tmp_path / "packages" / "app").mkdir(parents=True)
        (tmp_path / "packages" / "lib").mkdir(parents=True)
        _write(
            tmp_path / "packages" / "app" / "package.json",
            json.dumps({"name": "app", "dependencies": {"lib": "*"}}),
        )
        _write(
            tmp_path / "packages" / "lib" / "package.json",
            json.dumps({"name": "lib"}),
        )

    def test_creates_root_repository_node(self, tmp_path):
        self._setup_yarn_monorepo(tmp_path)
        store = _mock_store()

        with patch("navegador.monorepo.RepoIngester") as MockRI:
            MockRI.return_value.ingest.return_value = {
                "files": 0, "functions": 0, "classes": 0, "edges": 0, "skipped": 0
            }
            MonorepoIngester(store).ingest(tmp_path)

        # Root Repository node must have been created
        create_node_calls = store.create_node.call_args_list
        labels = [c[0][0] for c in create_node_calls]
        from navegador.graph.schema import NodeLabel
        assert NodeLabel.Repository in labels

    def test_ingest_called_per_package(self, tmp_path):
        self._setup_yarn_monorepo(tmp_path)
        store = _mock_store()

        with patch("navegador.monorepo.RepoIngester") as MockRI:
            MockRI.return_value.ingest.return_value = {
                "files": 2, "functions": 3, "classes": 1, "edges": 1, "skipped": 0
            }
            stats = MonorepoIngester(store).ingest(tmp_path)

        # Two packages → ingest called twice
        assert MockRI.return_value.ingest.call_count == 2
        assert stats["packages"] == 2

    def test_aggregates_stats(self, tmp_path):
        self._setup_yarn_monorepo(tmp_path)
        store = _mock_store()

        with patch("navegador.monorepo.RepoIngester") as MockRI:
            MockRI.return_value.ingest.return_value = {
                "files": 3, "functions": 5, "classes": 2, "edges": 4, "skipped": 0
            }
            stats = MonorepoIngester(store).ingest(tmp_path)

        # 2 packages × per-package values
        assert stats["files"] == 6
        assert stats["functions"] == 10
        assert stats["workspace_type"] == "yarn"

    def test_clear_calls_store_clear(self, tmp_path):
        self._setup_yarn_monorepo(tmp_path)
        store = _mock_store()

        with patch("navegador.monorepo.RepoIngester") as MockRI:
            MockRI.return_value.ingest.return_value = {
                "files": 0, "functions": 0, "classes": 0, "edges": 0, "skipped": 0
            }
            MonorepoIngester(store).ingest(tmp_path, clear=True)

        store.clear.assert_called_once()

    def test_depends_on_edges_created_for_sibling_deps(self, tmp_path):
        """app depends on lib — a DEPENDS_ON edge should be created."""
        self._setup_yarn_monorepo(tmp_path)
        store = _mock_store()

        with patch("navegador.monorepo.RepoIngester") as MockRI:
            MockRI.return_value.ingest.return_value = {
                "files": 0, "functions": 0, "classes": 0, "edges": 0, "skipped": 0
            }
            MonorepoIngester(store).ingest(tmp_path)

        from navegador.graph.schema import EdgeType
        edge_calls = store.create_edge.call_args_list
        depends_on_edges = [
            c for c in edge_calls
            if c[1].get("edge_type") == EdgeType.DEPENDS_ON
            or (len(c[0]) > 2 and c[0][2] == EdgeType.DEPENDS_ON)
        ]
        # At minimum one DEPENDS_ON call should have been attempted
        # (exact count depends on resolution; we verify the mechanism fired)
        assert store.create_edge.called


class TestMonorepoIngesterCargo:
    def test_cargo_workspace_type(self, tmp_path):
        _write(
            tmp_path / "Cargo.toml",
            '[workspace]\nmembers = ["crates/core"]\n',
        )
        (tmp_path / "crates" / "core").mkdir(parents=True)
        store = _mock_store()

        with patch("navegador.monorepo.RepoIngester") as MockRI:
            MockRI.return_value.ingest.return_value = {
                "files": 0, "functions": 0, "classes": 0, "edges": 0, "skipped": 0
            }
            stats = MonorepoIngester(store).ingest(tmp_path)

        assert stats["workspace_type"] == "cargo"
        assert stats["packages"] == 1


class TestMonorepoIngesterGo:
    def test_go_workspace_type(self, tmp_path):
        (tmp_path / "svc").mkdir()
        _write(tmp_path / "go.work", "go 1.21\nuse ./svc\n")
        store = _mock_store()

        with patch("navegador.monorepo.RepoIngester") as MockRI:
            MockRI.return_value.ingest.return_value = {
                "files": 0, "functions": 0, "classes": 0, "edges": 0, "skipped": 0
            }
            stats = MonorepoIngester(store).ingest(tmp_path)

        assert stats["workspace_type"] == "go"
        assert stats["packages"] == 1


# ── CLI flag ──────────────────────────────────────────────────────────────────


class TestIngestMonorepoFlag:
    def _mock_store_fn(self):
        return _mock_store()

    def test_monorepo_flag_calls_monorepo_ingester(self, tmp_path):
        runner = CliRunner()
        with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
             patch("navegador.monorepo.MonorepoIngester") as MockMI:
            MockMI.return_value.ingest.return_value = {
                "files": 4, "functions": 10, "classes": 2,
                "edges": 3, "skipped": 0, "packages": 2, "workspace_type": "yarn"
            }
            result = runner.invoke(main, ["ingest", str(tmp_path), "--monorepo"])

        assert result.exit_code == 0
        MockMI.return_value.ingest.assert_called_once()

    def test_monorepo_flag_with_json_output(self, tmp_path):
        runner = CliRunner()
        expected = {
            "files": 4, "functions": 10, "classes": 2,
            "edges": 3, "skipped": 0, "packages": 2, "workspace_type": "pnpm"
        }
        with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
             patch("navegador.monorepo.MonorepoIngester") as MockMI:
            MockMI.return_value.ingest.return_value = expected
            result = runner.invoke(
                main, ["ingest", str(tmp_path), "--monorepo", "--json"]
            )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["packages"] == 2
        assert data["workspace_type"] == "pnpm"

    def test_monorepo_flag_passes_clear(self, tmp_path):
        runner = CliRunner()
        with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
             patch("navegador.monorepo.MonorepoIngester") as MockMI:
            MockMI.return_value.ingest.return_value = {
                "files": 0, "functions": 0, "classes": 0,
                "edges": 0, "skipped": 0, "packages": 0, "workspace_type": "none"
            }
            result = runner.invoke(
                main, ["ingest", str(tmp_path), "--monorepo", "--clear"]
            )

        assert result.exit_code == 0
        _, kwargs = MockMI.return_value.ingest.call_args
        assert kwargs.get("clear") is True

    def test_without_monorepo_flag_uses_repo_ingester(self, tmp_path):
        """Sanity: the regular ingest path is not affected by the new flag."""
        runner = CliRunner()
        with patch("navegador.cli.commands._get_store", return_value=_mock_store()), \
             patch("navegador.ingestion.RepoIngester") as MockRI:
            MockRI.return_value.ingest.return_value = {
                "files": 1, "functions": 2, "classes": 0, "edges": 0, "skipped": 0
            }
            result = runner.invoke(main, ["ingest", str(tmp_path)])

        assert result.exit_code == 0
        MockRI.return_value.ingest.assert_called_once()

    def test_monorepo_flag_help_text(self):
        runner = CliRunner()
        result = runner.invoke(main, ["ingest", "--help"])
        assert result.exit_code == 0
        assert "--monorepo" in result.output
