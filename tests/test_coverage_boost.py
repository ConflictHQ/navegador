"""
Targeted tests to boost coverage from ~91% to 95%+.

One test class per module.  All external dependencies (Redis, tree-sitter,
HTTP) are mocked; no real infrastructure is required.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_store():
    store = MagicMock()
    store.query.return_value = MagicMock(result_set=[])
    store.node_count.return_value = 0
    store.edge_count.return_value = 0
    return store


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# ── MockNode for AST tests ────────────────────────────────────────────────────


class MockNode:
    def __init__(
        self,
        type_: str,
        text: bytes = b"",
        children: list = None,
        start_byte: int = 0,
        end_byte: int = 0,
        start_point: tuple = (0, 0),
        end_point: tuple = (5, 0),
    ):
        self.type = type_
        self.children = children or []
        self.start_byte = start_byte
        self.end_byte = end_byte
        self.start_point = start_point
        self.end_point = end_point
        self._fields: dict = {}

    def child_by_field_name(self, name: str):
        return self._fields.get(name)

    def set_field(self, name: str, node: "MockNode") -> "MockNode":
        self._fields[name] = node
        return self


def _text_node(text: bytes, type_: str = "identifier") -> MockNode:
    return MockNode(type_, text, start_byte=0, end_byte=len(text))


def _make_mock_tree(root_node: MockNode):
    tree = MagicMock()
    tree.root_node = root_node
    return tree


def _mock_ts(lang_module_name: str):
    mock_lang_module = MagicMock()
    mock_ts = MagicMock()
    return patch.dict("sys.modules", {lang_module_name: mock_lang_module, "tree_sitter": mock_ts})


# ===========================================================================
# navegador.api_schema  (57% → target ~90%)
# ===========================================================================


class TestAPISchemaIngesterOpenAPI:
    """Cover lines 72, 105, 213, 217-225, 234-241, 255-294, 299-319."""

    def _make(self):
        from navegador.api_schema import APISchemaIngester

        return APISchemaIngester(_make_store())

    def test_ingest_openapi_yaml_with_paths(self, tmp_path):
        content = """
openapi: 3.0.0
info:
  title: Test
paths:
  /users:
    get:
      operationId: listUsers
      summary: List users
      tags:
        - users
    post:
      summary: Create user
components:
  schemas:
    User:
      type: object
      description: A user object
"""
        f = tmp_path / "api.yaml"
        f.write_text(content)
        ingester = self._make()
        stats = ingester.ingest_openapi(str(f))
        assert stats["endpoints"] >= 2
        assert stats["schemas"] >= 1

    def test_ingest_openapi_json(self, tmp_path):
        spec = {
            "openapi": "3.0.0",
            "paths": {
                "/items": {
                    "get": {"operationId": "listItems", "summary": "List"},
                    "delete": {"summary": "Delete"},
                }
            },
            "components": {"schemas": {"Item": {"description": "item"}}},
        }
        f = tmp_path / "api.json"
        f.write_text(json.dumps(spec))
        ingester = self._make()
        stats = ingester.ingest_openapi(str(f))
        assert stats["endpoints"] >= 2
        assert stats["schemas"] >= 1

    def test_ingest_openapi_missing_file(self, tmp_path):
        ingester = self._make()
        stats = ingester.ingest_openapi(str(tmp_path / "missing.yaml"))
        assert stats == {"endpoints": 0, "schemas": 0}

    def test_ingest_openapi_swagger2_definitions(self, tmp_path):
        spec = {
            "swagger": "2.0",
            "paths": {
                "/pets": {"get": {"summary": "List pets"}}
            },
            "definitions": {"Pet": {"description": "pet"}},
        }
        f = tmp_path / "swagger.json"
        f.write_text(json.dumps(spec))
        ingester = self._make()
        stats = ingester.ingest_openapi(str(f))
        assert stats["schemas"] >= 1

    def test_ingest_openapi_operation_no_id(self, tmp_path):
        # operationId absent → synthesised as "METHOD /path"
        spec = {
            "paths": {"/x": {"put": {"summary": "update"}}},
        }
        f = tmp_path / "api.json"
        f.write_text(json.dumps(spec))
        ingester = self._make()
        stats = ingester.ingest_openapi(str(f))
        assert stats["endpoints"] == 1

    def test_ingest_openapi_invalid_json(self, tmp_path):
        f = tmp_path / "bad.json"
        f.write_text("{not valid json")
        ingester = self._make()
        stats = ingester.ingest_openapi(str(f))
        assert stats == {"endpoints": 0, "schemas": 0}

    def test_ingest_openapi_unknown_extension_tries_json(self, tmp_path):
        spec = {"paths": {"/x": {"get": {"summary": "hi"}}}}
        f = tmp_path / "api.txt"
        f.write_text(json.dumps(spec))
        ingester = self._make()
        stats = ingester.ingest_openapi(str(f))
        assert stats["endpoints"] == 1

    def test_ingest_openapi_unknown_extension_falls_back_to_yaml(self, tmp_path):
        # Not valid JSON → falls back to _parse_yaml
        f = tmp_path / "api.txt"
        f.write_text("paths:\n  /x:\n    get:\n      summary: hi\n")
        ingester = self._make()
        # Should not raise
        ingester.ingest_openapi(str(f))


class TestAPISchemaIngesterGraphQL:
    def _make(self):
        from navegador.api_schema import APISchemaIngester

        return APISchemaIngester(_make_store())

    def test_ingest_graphql_types_and_fields(self, tmp_path):
        sdl = """
type Query {
    user(id: ID!): User
    users: [User]
}
type Mutation {
    createUser(name: String!): User
}
type User {
    id: ID
    name: String
}
input CreateUserInput {
    name: String!
}
"""
        f = tmp_path / "schema.graphql"
        f.write_text(sdl)
        ingester = self._make()
        stats = ingester.ingest_graphql(str(f))
        assert stats["fields"] >= 2  # Query fields
        assert stats["types"] >= 2   # User + CreateUserInput

    def test_ingest_graphql_missing_file(self, tmp_path):
        ingester = self._make()
        stats = ingester.ingest_graphql(str(tmp_path / "missing.graphql"))
        assert stats == {"types": 0, "fields": 0}

    def test_ingest_graphql_empty_schema(self, tmp_path):
        f = tmp_path / "empty.graphql"
        f.write_text("# just a comment")
        ingester = self._make()
        stats = ingester.ingest_graphql(str(f))
        assert stats["types"] == 0
        assert stats["fields"] == 0


class TestMinimalYamlLoad:
    def test_simple_key_value(self):
        from navegador.api_schema import _minimal_yaml_load

        result = _minimal_yaml_load("title: My API\nversion: 3")
        assert result.get("title") == "My API"

    def test_boolean_scalars(self):
        from navegador.api_schema import _yaml_scalar

        assert _yaml_scalar("true") is True
        assert _yaml_scalar("false") is False
        assert _yaml_scalar("yes") is True
        assert _yaml_scalar("no") is False

    def test_null_scalars(self):
        from navegador.api_schema import _yaml_scalar

        assert _yaml_scalar("null") is None
        assert _yaml_scalar("~") is None
        assert _yaml_scalar("") is None

    def test_quoted_strings(self):
        from navegador.api_schema import _yaml_scalar

        assert _yaml_scalar('"hello"') == "hello"
        assert _yaml_scalar("'world'") == "world"

    def test_int_float(self):
        from navegador.api_schema import _yaml_scalar

        assert _yaml_scalar("42") == 42
        assert _yaml_scalar("3.14") == pytest.approx(3.14)

    def test_bare_string(self):
        from navegador.api_schema import _yaml_scalar

        assert _yaml_scalar("application/json") == "application/json"

    def test_list_items(self):
        from navegador.api_schema import _minimal_yaml_load

        text = "tags:\n  - users\n  - admin\n"
        # Should not raise even if list parsing is minimal
        result = _minimal_yaml_load(text)
        assert isinstance(result, dict)

    def test_comments_skipped(self):
        from navegador.api_schema import _minimal_yaml_load

        text = "# comment\ntitle: test\n"
        result = _minimal_yaml_load(text)
        assert result.get("title") == "test"

    def test_block_scalar_placeholder(self):
        from navegador.api_schema import _minimal_yaml_load

        text = "description: |\n  some text\ntitle: test\n"
        result = _minimal_yaml_load(text)
        # Should have a nested dict for the block scalar key
        assert "description" in result

    def test_parse_yaml_uses_pyyaml_if_available(self, tmp_path):
        from navegador.api_schema import APISchemaIngester

        ingester = APISchemaIngester(_make_store())
        mock_yaml = MagicMock()
        mock_yaml.safe_load.return_value = {"openapi": "3.0.0", "paths": {}}
        with patch.dict("sys.modules", {"yaml": mock_yaml}):
            result = ingester._parse_yaml("openapi: 3.0.0")
        assert result == {"openapi": "3.0.0", "paths": {}}

    def test_parse_yaml_falls_back_when_no_pyyaml(self):
        from navegador.api_schema import APISchemaIngester

        ingester = APISchemaIngester(_make_store())
        import sys

        original = sys.modules.pop("yaml", None)
        try:
            # Simulate no yaml installed
            with patch.dict("sys.modules", {"yaml": None}):
                result = ingester._parse_yaml("title: test\n")
                assert isinstance(result, dict)
        finally:
            if original is not None:
                sys.modules["yaml"] = original


# ===========================================================================
# navegador.cluster.core  (50% → target ~85%)
# ===========================================================================


class TestClusterManagerLocalVersion:
    """Cover _local_version, _set_local_version, snapshot_to_local, push_to_shared, sync."""

    def _make(self, tmp_path):
        from navegador.cluster.core import ClusterManager

        r = MagicMock()
        pipe = MagicMock()
        pipe.execute.return_value = [True, True, True]
        r.pipeline.return_value = pipe
        r.get.return_value = b"3"
        return ClusterManager(
            "redis://localhost:6379",
            local_db_path=str(tmp_path / "graph.db"),
            redis_client=r,
        ), r, pipe

    def test_local_version_zero_when_no_meta(self, tmp_path):
        mgr, _, _ = self._make(tmp_path)
        assert mgr._local_version() == 0

    def test_set_and_read_local_version(self, tmp_path):
        mgr, _, _ = self._make(tmp_path)
        mgr._set_local_version(7)
        assert mgr._local_version() == 7

    def test_set_local_version_merges_existing_keys(self, tmp_path):
        mgr, _, _ = self._make(tmp_path)
        mgr._set_local_version(1)
        mgr._set_local_version(2)
        assert mgr._local_version() == 2

    def test_local_version_handles_corrupt_json(self, tmp_path):
        mgr, _, _ = self._make(tmp_path)
        meta = tmp_path / ".navegador" / "cluster_meta.json"
        meta.parent.mkdir(parents=True, exist_ok=True)
        meta.write_text("{bad json")
        # Should fall back to 0
        # local_db_path is tmp_path/graph.db, so meta is tmp_path/cluster_meta.json
        mgr2, _, _ = self._make(tmp_path)
        assert mgr2._local_version() == 0

    def test_snapshot_to_local_no_snapshot(self, tmp_path):
        mgr, r, _ = self._make(tmp_path)
        r.get.return_value = None  # no snapshot key
        # Should log warning and return without error
        with patch("navegador.cluster.core.logger") as mock_log:
            mgr.snapshot_to_local()
        mock_log.warning.assert_called_once()

    def test_snapshot_to_local_imports_data(self, tmp_path):
        mgr, r, _ = self._make(tmp_path)
        snapshot_data = {"nodes": [], "edges": []}
        # First call returns version key, subsequent get returns snapshot
        r.get.side_effect = [json.dumps(snapshot_data).encode(), b"5"]

        with patch.object(mgr, "_import_to_local_graph") as mock_import:
            with patch.object(mgr, "_set_local_version") as _mock_set:
                with patch.object(mgr, "_redis_version", return_value=5):
                    r.get.side_effect = None
                    r.get.return_value = json.dumps(snapshot_data).encode()
                    mgr.snapshot_to_local()
                    mock_import.assert_called_once_with(snapshot_data)

    def test_push_to_shared(self, tmp_path):
        mgr, r, pipe = self._make(tmp_path)
        r.get.return_value = b"2"

        with patch.object(mgr, "_export_local_graph", return_value={"nodes": [], "edges": []}):
            mgr.push_to_shared()

        pipe.set.assert_called()
        pipe.execute.assert_called_once()

    def test_sync_pulls_when_shared_newer(self, tmp_path):
        mgr, r, _ = self._make(tmp_path)
        with patch.object(mgr, "_local_version", return_value=1):
            with patch.object(mgr, "_redis_version", return_value=5):
                with patch.object(mgr, "snapshot_to_local") as mock_pull:
                    mgr.sync()
                    mock_pull.assert_called_once()

    def test_sync_pushes_when_local_current(self, tmp_path):
        mgr, r, _ = self._make(tmp_path)
        with patch.object(mgr, "_local_version", return_value=5):
            with patch.object(mgr, "_redis_version", return_value=3):
                with patch.object(mgr, "push_to_shared") as mock_push:
                    mgr.sync()
                    mock_push.assert_called_once()

    def test_connect_redis_raises_on_missing_dep(self):
        from navegador.cluster.core import ClusterManager

        with patch.dict("sys.modules", {"redis": None}):
            with pytest.raises(ImportError, match="redis"):
                ClusterManager._connect_redis("redis://localhost")

    def test_redis_version_returns_zero_when_none(self, tmp_path):
        mgr, r, _ = self._make(tmp_path)
        r.get.return_value = None
        assert mgr._redis_version() == 0

    def test_export_local_graph_calls_store(self, tmp_path):
        mgr, r, _ = self._make(tmp_path)
        mock_store = MagicMock()
        nodes_result = MagicMock()
        nodes_result.result_set = []
        edges_result = MagicMock()
        edges_result.result_set = []
        mock_store.query.side_effect = [nodes_result, edges_result]

        with patch("navegador.graph.store.GraphStore.sqlite", return_value=mock_store):
            data = mgr._export_local_graph()
        assert data == {"nodes": [], "edges": []}
        mock_store.close.assert_called_once()

    def test_import_to_local_graph_creates_nodes(self, tmp_path):
        mgr, r, _ = self._make(tmp_path)
        mock_store = MagicMock()

        data = {
            "nodes": [{"labels": ["Function"], "properties": {"name": "foo"}}],
            "edges": [
                {
                    "src_labels": ["Function"],
                    "src_props": {"name": "foo", "file_path": "f.py"},
                    "rel_type": "CALLS",
                    "dst_labels": ["Function"],
                    "dst_props": {"name": "bar", "file_path": "f.py"},
                    "rel_props": {},
                }
            ],
        }
        with patch("navegador.graph.store.GraphStore.sqlite", return_value=mock_store):
            mgr._import_to_local_graph(data)
        mock_store.create_node.assert_called_once()
        mock_store.create_edge.assert_called_once()
        mock_store.close.assert_called_once()

    def test_import_to_local_graph_skips_edge_without_src_key(self, tmp_path):
        mgr, r, _ = self._make(tmp_path)
        mock_store = MagicMock()

        data = {
            "nodes": [],
            "edges": [
                {
                    "src_labels": ["Function"],
                    "src_props": {},  # no name/file_path → no key
                    "rel_type": "CALLS",
                    "dst_labels": ["Function"],
                    "dst_props": {"name": "bar", "file_path": "f.py"},
                    "rel_props": {},
                }
            ],
        }
        with patch("navegador.graph.store.GraphStore.sqlite", return_value=mock_store):
            mgr._import_to_local_graph(data)
        mock_store.create_edge.assert_not_called()
        mock_store.close.assert_called_once()


# ===========================================================================
# navegador.monorepo  (76% → target ~90%)
# ===========================================================================


class TestWorkspaceDetectorEdgeCases:
    """Cover lines 89-90, 124-125, 128, 142-143, 165-166, 180-181, 193,
    223, 235-236, 253-255, 270-274, 288-289."""

    def test_yarn_workspaces_berry_format(self, tmp_path):
        from navegador.monorepo import WorkspaceDetector

        pkg_json = {
            "name": "root",
            "workspaces": {"packages": ["packages/*"]},
        }
        _write(tmp_path / "package.json", json.dumps(pkg_json))
        (tmp_path / "packages" / "app").mkdir(parents=True)
        _write(tmp_path / "packages" / "app" / "package.json", '{"name":"app"}')
        config = WorkspaceDetector().detect(tmp_path)
        assert config is not None
        assert config.type == "yarn"

    def test_yarn_package_json_parse_error(self, tmp_path):
        from navegador.monorepo import WorkspaceDetector

        _write(tmp_path / "package.json", "{bad json")
        # No workspaces key (parse failed) → no yarn config returned
        config = WorkspaceDetector().detect(tmp_path)
        assert config is None

    def test_js_workspace_packages_berry_patterns(self, tmp_path):
        from navegador.monorepo import WorkspaceDetector

        pkg_json = {"workspaces": {"packages": ["packages/*"]}}
        _write(tmp_path / "package.json", json.dumps(pkg_json))
        (tmp_path / "packages" / "a").mkdir(parents=True)
        det = WorkspaceDetector()
        packages = det._js_workspace_packages(tmp_path)
        assert any(p.name == "a" for p in packages)

    def test_js_workspace_packages_no_package_json(self, tmp_path):
        from navegador.monorepo import WorkspaceDetector

        # fallback: scan for package.json one level down
        (tmp_path / "pkg_a").mkdir()
        _write(tmp_path / "pkg_a" / "package.json", '{"name":"pkg_a"}')
        det = WorkspaceDetector()
        packages = det._js_workspace_packages(tmp_path)
        assert any(p.name == "pkg_a" for p in packages)

    def test_js_workspace_packages_parse_error(self, tmp_path):
        from navegador.monorepo import WorkspaceDetector

        _write(tmp_path / "package.json", "{bad json")
        # Falls back to _fallback_packages
        det = WorkspaceDetector()
        packages = det._js_workspace_packages(tmp_path)
        assert isinstance(packages, list)

    def test_nx_packages_from_subdirs(self, tmp_path):
        from navegador.monorepo import WorkspaceDetector

        _write(tmp_path / "nx.json", '{}')
        (tmp_path / "apps" / "app1").mkdir(parents=True)
        (tmp_path / "libs" / "lib1").mkdir(parents=True)
        config = WorkspaceDetector().detect(tmp_path)
        assert config is not None
        assert config.type == "nx"
        pkg_names = [p.name for p in config.packages]
        assert "app1" in pkg_names
        assert "lib1" in pkg_names

    def test_nx_packages_fallback_to_js_workspaces(self, tmp_path):
        from navegador.monorepo import WorkspaceDetector

        # nx.json exists but no apps/libs/packages dirs
        _write(tmp_path / "nx.json", '{}')
        # fallback triggers _js_workspace_packages → _fallback_packages
        config = WorkspaceDetector().detect(tmp_path)
        assert config is not None
        assert config.type == "nx"

    def test_pnpm_workspace_parse(self, tmp_path):
        from navegador.monorepo import WorkspaceDetector

        _write(
            tmp_path / "pnpm-workspace.yaml",
            "packages:\n  - 'packages/*'\n  - 'apps/*'\n",
        )
        (tmp_path / "packages" / "core").mkdir(parents=True)
        config = WorkspaceDetector().detect(tmp_path)
        assert config is not None
        assert config.type == "pnpm"

    def test_pnpm_workspace_read_error(self, tmp_path):
        from navegador.monorepo import WorkspaceDetector

        # pnpm-workspace.yaml exists but cannot be read → IOError path
        yaml_path = tmp_path / "pnpm-workspace.yaml"
        yaml_path.touch()
        det = WorkspaceDetector()
        with patch.object(Path, "read_text", side_effect=OSError("perm")):
            packages = det._pnpm_packages(tmp_path)
        assert isinstance(packages, list)

    def test_pnpm_no_patterns_fallback(self, tmp_path):
        from navegador.monorepo import WorkspaceDetector

        _write(tmp_path / "pnpm-workspace.yaml", "# empty\n")
        (tmp_path / "sub").mkdir()
        _write(tmp_path / "sub" / "package.json", '{"name":"sub"}')
        det = WorkspaceDetector()
        packages = det._pnpm_packages(tmp_path)
        assert any(p.name == "sub" for p in packages)

    def test_cargo_workspace_parse(self, tmp_path):
        from navegador.monorepo import WorkspaceDetector

        cargo_toml = """
[workspace]
members = [
    "crates/core",
    "crates/cli",
]
"""
        _write(tmp_path / "Cargo.toml", cargo_toml)
        (tmp_path / "crates" / "core").mkdir(parents=True)
        (tmp_path / "crates" / "cli").mkdir(parents=True)
        config = WorkspaceDetector().detect(tmp_path)
        assert config is not None
        assert config.type == "cargo"
        assert len(config.packages) == 2

    def test_cargo_workspace_not_workspace(self, tmp_path):
        from navegador.monorepo import WorkspaceDetector

        _write(tmp_path / "Cargo.toml", "[package]\nname = \"myapp\"\n")
        config = WorkspaceDetector().detect(tmp_path)
        assert config is None

    def test_cargo_read_error(self, tmp_path):
        from navegador.monorepo import WorkspaceDetector

        cargo_toml = tmp_path / "Cargo.toml"
        cargo_toml.touch()
        det = WorkspaceDetector()
        with patch.object(Path, "read_text", side_effect=OSError("perm")):
            result = det._cargo_packages(tmp_path, cargo_toml)
        assert result is None

    def test_cargo_wildcard_members(self, tmp_path):
        from navegador.monorepo import WorkspaceDetector

        cargo_toml = "[workspace]\nmembers = [\"crates/*\"]\n"
        _write(tmp_path / "Cargo.toml", cargo_toml)
        (tmp_path / "crates" / "a").mkdir(parents=True)
        (tmp_path / "crates" / "b").mkdir(parents=True)
        det = WorkspaceDetector()
        pkgs = det._cargo_packages(tmp_path, tmp_path / "Cargo.toml")
        assert pkgs is not None
        assert len(pkgs) == 2

    def test_go_workspace_parse(self, tmp_path):
        from navegador.monorepo import WorkspaceDetector

        (tmp_path / "cmd").mkdir()
        (tmp_path / "pkg").mkdir()
        _write(tmp_path / "go.work", "go 1.21\nuse ./cmd\nuse ./pkg\n")
        config = WorkspaceDetector().detect(tmp_path)
        assert config is not None
        assert config.type == "go"

    def test_go_workspace_read_error(self, tmp_path):
        from navegador.monorepo import WorkspaceDetector

        go_work = tmp_path / "go.work"
        go_work.touch()
        det = WorkspaceDetector()
        with patch.object(Path, "read_text", side_effect=OSError("perm")):
            packages = det._go_packages(tmp_path)
        assert isinstance(packages, list)

    def test_glob_packages_negation_skipped(self, tmp_path):
        from navegador.monorepo import WorkspaceDetector

        (tmp_path / "packages" / "a").mkdir(parents=True)
        det = WorkspaceDetector()
        pkgs = det._glob_packages(tmp_path, ["!packages/*"])
        assert pkgs == []

    def test_glob_packages_literal_path(self, tmp_path):
        from navegador.monorepo import WorkspaceDetector

        (tmp_path / "myapp").mkdir()
        det = WorkspaceDetector()
        pkgs = det._glob_packages(tmp_path, ["myapp"])
        assert any(p.name == "myapp" for p in pkgs)

    def test_fallback_packages_skips_dotdirs(self, tmp_path):
        from navegador.monorepo import WorkspaceDetector

        (tmp_path / ".git").mkdir()
        _write(tmp_path / ".git" / "package.json", '{}')
        (tmp_path / "real").mkdir()
        _write(tmp_path / "real" / "package.json", '{}')
        det = WorkspaceDetector()
        pkgs = det._fallback_packages(tmp_path)
        names = [p.name for p in pkgs]
        assert ".git" not in names
        assert "real" in names


class TestMonorepoIngesterEdgeCases:
    """Cover lines 373, 404-405, 451-452, 466, 471, 474-475, 485-503, 509-531."""

    def test_ingest_fallback_when_no_workspace(self, tmp_path):
        from navegador.monorepo import MonorepoIngester

        store = _make_store()
        ingester = MonorepoIngester(store)
        with patch("navegador.monorepo.RepoIngester") as MockRI:
            instance = MockRI.return_value
            instance.ingest.return_value = {
                "files": 1, "functions": 2, "classes": 0, "edges": 0, "skipped": 0
            }
            stats = ingester.ingest(str(tmp_path))
        assert stats["packages"] == 0
        assert stats["workspace_type"] == "none"

    def test_ingest_raises_on_missing_path(self, tmp_path):
        from navegador.monorepo import MonorepoIngester

        ingester = MonorepoIngester(_make_store())
        with pytest.raises(FileNotFoundError):
            ingester.ingest(str(tmp_path / "does_not_exist"))

    def test_ingest_package_exception_logged(self, tmp_path):
        from navegador.monorepo import MonorepoIngester, WorkspaceConfig

        store = _make_store()
        pkg_dir = tmp_path / "pkg"
        pkg_dir.mkdir()
        config = WorkspaceConfig(type="yarn", root=tmp_path, packages=[pkg_dir])

        with patch("navegador.monorepo.WorkspaceDetector") as MockDet:
            MockDet.return_value.detect.return_value = config
            with patch("navegador.monorepo.RepoIngester") as MockRI:
                MockRI.return_value.ingest.side_effect = RuntimeError("parse fail")
                stats = MonorepoIngester(store).ingest(str(tmp_path))
        assert stats["packages"] == 0

    def test_js_deps(self, tmp_path):
        from navegador.monorepo import MonorepoIngester

        ingester = MonorepoIngester(_make_store())
        pkg_json = {
            "dependencies": {"react": "^18"},
            "devDependencies": {"jest": "^29"},
            "peerDependencies": {"typescript": ">=5"},
        }
        _write(tmp_path / "package.json", json.dumps(pkg_json))
        deps = ingester._js_deps(tmp_path)
        assert "react" in deps
        assert "jest" in deps
        assert "typescript" in deps

    def test_js_deps_no_file(self, tmp_path):
        from navegador.monorepo import MonorepoIngester

        ingester = MonorepoIngester(_make_store())
        assert ingester._js_deps(tmp_path) == []

    def test_js_deps_parse_error(self, tmp_path):
        from navegador.monorepo import MonorepoIngester

        ingester = MonorepoIngester(_make_store())
        _write(tmp_path / "package.json", "{bad json")
        assert ingester._js_deps(tmp_path) == []

    def test_cargo_deps(self, tmp_path):
        from navegador.monorepo import MonorepoIngester

        ingester = MonorepoIngester(_make_store())
        cargo = "[dependencies]\nserde = \"1.0\"\ntokio = { version = \"1\" }\n[dev-dependencies]\ntempfile = \"3\"\n"
        _write(tmp_path / "Cargo.toml", cargo)
        deps = ingester._cargo_deps(tmp_path)
        assert "serde" in deps
        assert "tokio" in deps
        assert "tempfile" in deps

    def test_cargo_deps_no_file(self, tmp_path):
        from navegador.monorepo import MonorepoIngester

        ingester = MonorepoIngester(_make_store())
        assert ingester._cargo_deps(tmp_path) == []

    def test_cargo_deps_read_error(self, tmp_path):
        from navegador.monorepo import MonorepoIngester

        ingester = MonorepoIngester(_make_store())
        cargo = tmp_path / "Cargo.toml"
        cargo.touch()
        with patch.object(Path, "read_text", side_effect=OSError("perm")):
            result = ingester._cargo_deps(tmp_path)
        assert result == []

    def test_go_deps(self, tmp_path):
        from navegador.monorepo import MonorepoIngester

        ingester = MonorepoIngester(_make_store())
        go_mod = "module example.com/myapp\ngo 1.21\n\nrequire (\n    github.com/pkg/errors v0.9.1\n    golang.org/x/net v0.17.0\n)\n\nrequire github.com/single/dep v1.0.0\n"
        _write(tmp_path / "go.mod", go_mod)
        deps = ingester._go_deps(tmp_path)
        assert "github.com/pkg/errors" in deps
        assert "golang.org/x/net" in deps
        assert "github.com/single/dep" in deps

    def test_go_deps_no_file(self, tmp_path):
        from navegador.monorepo import MonorepoIngester

        ingester = MonorepoIngester(_make_store())
        assert ingester._go_deps(tmp_path) == []

    def test_go_deps_read_error(self, tmp_path):
        from navegador.monorepo import MonorepoIngester

        ingester = MonorepoIngester(_make_store())
        go_mod = tmp_path / "go.mod"
        go_mod.touch()
        with patch.object(Path, "read_text", side_effect=OSError("perm")):
            result = ingester._go_deps(tmp_path)
        assert result == []

    def test_read_package_deps_unknown_type(self, tmp_path):
        from navegador.monorepo import MonorepoIngester

        ingester = MonorepoIngester(_make_store())
        assert ingester._read_package_deps("unknown", tmp_path) == []

    def test_dependency_edges_scoped_package(self, tmp_path):
        from navegador.monorepo import MonorepoIngester, WorkspaceConfig

        store = _make_store()
        ingester = MonorepoIngester(store)

        pkg_a = tmp_path / "pkg_a"
        pkg_b = tmp_path / "pkg_b"
        pkg_a.mkdir()
        pkg_b.mkdir()

        _write(pkg_a / "package.json", json.dumps({
            "dependencies": {"@scope/pkg_b": "^1.0"}
        }))

        config = WorkspaceConfig(type="yarn", root=tmp_path, packages=[pkg_a, pkg_b])
        packages = [("pkg_a", pkg_a), ("pkg_b", pkg_b)]

        ingester._create_dependency_edges(config, packages)
        # store.create_edge should have been called at least once for the dependency
        # (pkg_b matches the bare name)
        store.create_edge.assert_called()

    def test_dependency_edges_exception_logged(self, tmp_path):
        from navegador.monorepo import MonorepoIngester, WorkspaceConfig

        store = _make_store()
        store.create_edge.side_effect = Exception("DB error")
        ingester = MonorepoIngester(store)

        pkg_a = tmp_path / "pkg_a"
        pkg_b = tmp_path / "pkg_b"
        pkg_a.mkdir()
        pkg_b.mkdir()
        _write(pkg_a / "package.json", json.dumps({"dependencies": {"pkg_b": "^1"}}))

        config = WorkspaceConfig(type="yarn", root=tmp_path, packages=[pkg_a, pkg_b])
        packages = [("pkg_a", pkg_a), ("pkg_b", pkg_b)]
        # Should not raise
        count = ingester._create_dependency_edges(config, packages)
        assert count == 0


# ===========================================================================
# navegador.pm  (79% → target ~90%)
# ===========================================================================


class TestTicketIngester:
    """Cover lines 243-245, 261-287."""

    def _make(self):
        from navegador.pm import TicketIngester

        return TicketIngester(_make_store())

    def test_ingest_linear_raises(self):
        ing = self._make()
        with pytest.raises(NotImplementedError, match="Linear"):
            ing.ingest_linear(api_key="lin_xxx")

    def test_ingest_jira_raises(self):
        ing = self._make()
        with pytest.raises(NotImplementedError, match="Jira"):
            ing.ingest_jira(url="https://co.atlassian.net", token="tok")

    def test_github_severity_critical(self):
        from navegador.pm import TicketIngester

        assert TicketIngester._github_severity(["critical"]) == "critical"
        assert TicketIngester._github_severity(["blocker"]) == "critical"
        assert TicketIngester._github_severity(["p0", "other"]) == "critical"

    def test_github_severity_warning(self):
        from navegador.pm import TicketIngester

        assert TicketIngester._github_severity(["bug"]) == "warning"
        assert TicketIngester._github_severity(["high"]) == "warning"
        assert TicketIngester._github_severity(["important"]) == "warning"

    def test_github_severity_info(self):
        from navegador.pm import TicketIngester

        assert TicketIngester._github_severity([]) == "info"
        assert TicketIngester._github_severity(["enhancement"]) == "info"

    def test_link_to_code_returns_zero_on_empty_graph(self):
        from navegador.pm import TicketIngester

        store = _make_store()
        store.query.return_value = MagicMock(result_set=[])
        ing = TicketIngester(store)
        result = ing._link_to_code("myrepo")
        assert result == 0

    def test_link_to_code_returns_zero_on_query_failure(self):
        from navegador.pm import TicketIngester

        store = _make_store()
        store.query.side_effect = Exception("DB down")
        ing = TicketIngester(store)
        result = ing._link_to_code("myrepo")
        assert result == 0

    def test_link_to_code_matches_tokens(self):
        from navegador.pm import TicketIngester

        store = _make_store()
        # First call: tickets
        ticket_result = MagicMock()
        ticket_result.result_set = [("#1: authenticate user", "fix auth flow")]
        # Second call: code nodes
        code_result = MagicMock()
        code_result.result_set = [("Function", "authenticate"), ("Function", "unrelated")]
        store.query.side_effect = [ticket_result, code_result, None]

        ing = TicketIngester(store)
        result = ing._link_to_code("myrepo")
        assert result >= 1

    def test_link_to_code_skips_short_tokens(self):
        from navegador.pm import TicketIngester

        store = _make_store()
        ticket_result = MagicMock()
        # Only short words (< 4 chars)
        ticket_result.result_set = [("#1: fix", "x")]
        code_result = MagicMock()
        code_result.result_set = [("Function", "fix")]
        store.query.side_effect = [ticket_result, code_result]

        ing = TicketIngester(store)
        # "fix" is exactly 3 chars → skipped as a token
        result = ing._link_to_code("myrepo")
        assert result == 0

    def test_ingest_github_issues_http_error(self):
        from navegador.pm import TicketIngester

        store = _make_store()
        ing = TicketIngester(store)
        with patch("urllib.request.urlopen", side_effect=Exception("network err")):
            with pytest.raises(RuntimeError, match="Failed to fetch"):
                ing.ingest_github_issues("owner/repo", token="tok")

    def test_ingest_github_issues_success(self):
        from navegador.pm import TicketIngester

        store = _make_store()
        ing = TicketIngester(store)
        issues = [
            {"number": 1, "title": "Fix auth", "body": "desc", "html_url": "http://x",
             "labels": [{"name": "bug"}], "assignees": [{"login": "alice"}]},
        ]
        mock_resp = MagicMock()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.read.return_value = json.dumps(issues).encode()

        with patch("urllib.request.urlopen", return_value=mock_resp):
            stats = ing.ingest_github_issues("owner/repo")
        assert stats["tickets"] == 1


# ===========================================================================
# navegador.completions — install path  (lines 66-70)
# ===========================================================================


class TestGetInstallInstruction:
    def test_bash(self):
        from navegador.completions import get_install_instruction

        instruction = get_install_instruction("bash")
        assert "~/.bashrc" in instruction
        assert "bash_source" in instruction

    def test_zsh(self):
        from navegador.completions import get_install_instruction

        instruction = get_install_instruction("zsh")
        assert "~/.zshrc" in instruction
        assert "zsh_source" in instruction

    def test_fish(self):
        from navegador.completions import get_install_instruction

        instruction = get_install_instruction("fish")
        assert "config.fish" in instruction
        assert "fish_source" in instruction

    def test_invalid_raises(self):
        from navegador.completions import get_install_instruction

        with pytest.raises(ValueError, match="Unsupported"):
            get_install_instruction("pwsh")


# ===========================================================================
# navegador.cli.commands — watch callback (lines ~179-185)
# ===========================================================================


class TestIngestWatchCallback:
    """Exercise the _on_cycle callback inside the watch branch of ingest."""

    def test_watch_callback_with_changed_files(self, tmp_path):
        from click.testing import CliRunner

        from navegador.cli.commands import main

        runner = CliRunner()
        repo = str(tmp_path)

        cycle_calls = []

        def fake_watch(path, interval=2.0, callback=None):
            if callback:
                # Simulate a cycle with changed files
                result = callback({"files": 3, "skipped": 10})
                cycle_calls.append(result)
            # Second call: simulate KeyboardInterrupt to exit
            raise KeyboardInterrupt

        with patch("navegador.cli.commands._get_store") as mock_gs:
            mock_gs.return_value = _make_store()
            with patch("navegador.ingestion.RepoIngester") as MockRI:
                MockRI.return_value.watch.side_effect = fake_watch
                result = runner.invoke(main, ["ingest", repo, "--watch", "--interval", "1"])

        assert result.exit_code == 0
        assert cycle_calls == [True]

    def test_watch_callback_no_changed_files(self, tmp_path):
        """Callback with 0 changed files should still return True."""
        from click.testing import CliRunner

        from navegador.cli.commands import main

        runner = CliRunner()

        def fake_watch(path, interval=2.0, callback=None):
            if callback:
                result = callback({"files": 0, "skipped": 5})
                assert result is True
            raise KeyboardInterrupt

        with patch("navegador.cli.commands._get_store") as mock_gs:
            mock_gs.return_value = _make_store()
            with patch("navegador.ingestion.RepoIngester") as MockRI:
                MockRI.return_value.watch.side_effect = fake_watch
                result = runner.invoke(main, ["ingest", str(tmp_path), "--watch"])

        assert result.exit_code == 0


# ===========================================================================
# navegador.cli.commands — additional uncovered CLI branches
# ===========================================================================


class TestCLIBranchesDeadcode:
    """Cover lines 1395-1407 (unreachable_classes and orphan_files branches)."""

    def test_deadcode_shows_unreachable_classes(self, tmp_path):
        from click.testing import CliRunner

        from navegador.analysis.deadcode import DeadCodeReport
        from navegador.cli.commands import main

        runner = CliRunner()
        report = DeadCodeReport(
            unreachable_functions=[],
            unreachable_classes=[
                {"name": "OldClass", "file_path": "old.py", "line_start": 1, "type": "Class"}
            ],
            orphan_files=["orphan.py"],
        )
        with patch("navegador.cli.commands._get_store") as mock_gs:
            mock_gs.return_value = _make_store()
            with patch("navegador.analysis.deadcode.DeadCodeDetector") as MockDC:
                MockDC.return_value.detect.return_value = report
                result = runner.invoke(main, ["deadcode"])
        assert result.exit_code == 0
        assert "OldClass" in result.output
        assert "orphan.py" in result.output

    def test_deadcode_no_dead_code_message(self, tmp_path):
        from click.testing import CliRunner

        from navegador.analysis.deadcode import DeadCodeReport
        from navegador.cli.commands import main

        runner = CliRunner()
        report = DeadCodeReport(
            unreachable_functions=[], unreachable_classes=[], orphan_files=[]
        )
        with patch("navegador.cli.commands._get_store") as mock_gs:
            mock_gs.return_value = _make_store()
            with patch("navegador.analysis.deadcode.DeadCodeDetector") as MockDC:
                MockDC.return_value.detect.return_value = report
                result = runner.invoke(main, ["deadcode"])
        assert result.exit_code == 0
        assert "No dead code" in result.output


class TestCLIBranchesTestmap:
    """Cover lines 1439-1452 (testmap table and unmatched branches)."""

    def test_testmap_shows_table_and_unmatched(self):
        from click.testing import CliRunner

        from navegador.analysis.testmap import TestLink, TestMapResult
        from navegador.cli.commands import main

        runner = CliRunner()
        link = TestLink(
            test_name="test_foo",
            test_file="test_foo.py",
            prod_name="foo",
            prod_file="foo.py",
            prod_type="Function",
            source="name",
        )
        result_obj = TestMapResult(
            links=[link],
            unmatched_tests=[{"name": "test_orphan", "file_path": "test_x.py"}],
            edges_created=1,
        )
        with patch("navegador.cli.commands._get_store") as mock_gs:
            mock_gs.return_value = _make_store()
            with patch("navegador.analysis.testmap.TestMapper") as MockTM:
                MockTM.return_value.map_tests.return_value = result_obj
                result = runner.invoke(main, ["testmap"])
        assert result.exit_code == 0
        assert "test_foo" in result.output
        assert "test_orphan" in result.output


class TestCLIBranchesRename:
    """Cover lines 1640-1650 (rename non-JSON output)."""

    def test_rename_preview_output(self):
        from click.testing import CliRunner

        from navegador.cli.commands import main
        from navegador.refactor import RenameResult

        runner = CliRunner()
        rename_result = RenameResult(
            old_name="old_func",
            new_name="new_func",
            affected_nodes=[{"name": "old_func", "file_path": "f.py", "type": "Function", "line_start": 1}],
            affected_files=["f.py", "g.py"],
            edges_updated=3,
        )
        with patch("navegador.cli.commands._get_store") as mock_gs:
            mock_gs.return_value = _make_store()
            with patch("navegador.refactor.SymbolRenamer") as MockSR:
                MockSR.return_value.preview_rename.return_value = rename_result
                result = runner.invoke(main, ["rename", "old_func", "new_func", "--preview"])
        assert result.exit_code == 0
        assert "old_func" in result.output
        assert "f.py" in result.output

    def test_rename_apply_output(self):
        from click.testing import CliRunner

        from navegador.cli.commands import main
        from navegador.refactor import RenameResult

        runner = CliRunner()
        rename_result = RenameResult(
            old_name="old_func",
            new_name="new_func",
            affected_nodes=[],
            affected_files=[],
            edges_updated=0,
        )
        with patch("navegador.cli.commands._get_store") as mock_gs:
            mock_gs.return_value = _make_store()
            with patch("navegador.refactor.SymbolRenamer") as MockSR:
                MockSR.return_value.apply_rename.return_value = rename_result
                result = runner.invoke(main, ["rename", "old_func", "new_func"])
        assert result.exit_code == 0
        assert "Renamed" in result.output


class TestCLIBranchesSemantic:
    """Cover lines 2068-2080 (semantic-search table output)."""

    def test_semantic_search_table_output(self):
        from click.testing import CliRunner

        from navegador.cli.commands import main

        runner = CliRunner()
        search_results = [
            {"score": 0.95, "type": "Function", "name": "authenticate", "file_path": "auth.py"},
        ]
        with patch("navegador.cli.commands._get_store") as mock_gs:
            mock_gs.return_value = _make_store()
            with patch("navegador.intelligence.search.SemanticSearch") as MockSS:
                MockSS.return_value.search.return_value = search_results
                with patch("navegador.llm.auto_provider") as mock_ap:
                    mock_ap.return_value = MagicMock()
                    result = runner.invoke(main, ["semantic-search", "auth tokens"])
        assert result.exit_code == 0
        assert "authenticate" in result.output

    def test_semantic_search_no_results(self):
        from click.testing import CliRunner

        from navegador.cli.commands import main

        runner = CliRunner()
        with patch("navegador.cli.commands._get_store") as mock_gs:
            mock_gs.return_value = _make_store()
            with patch("navegador.intelligence.search.SemanticSearch") as MockSS:
                MockSS.return_value.search.return_value = []
                with patch("navegador.llm.auto_provider") as mock_ap:
                    mock_ap.return_value = MagicMock()
                    result = runner.invoke(main, ["semantic-search", "nothing"])
        assert result.exit_code == 0
        assert "--index" in result.output

    def test_semantic_search_with_index_flag(self):
        from click.testing import CliRunner

        from navegador.cli.commands import main

        runner = CliRunner()
        with patch("navegador.cli.commands._get_store") as mock_gs:
            mock_gs.return_value = _make_store()
            with patch("navegador.intelligence.search.SemanticSearch") as MockSS:
                inst = MockSS.return_value
                inst.index.return_value = 42
                inst.search.return_value = []
                with patch("navegador.llm.auto_provider") as mock_ap:
                    mock_ap.return_value = MagicMock()
                    result = runner.invoke(main, ["semantic-search", "auth", "--index"])
        assert result.exit_code == 0
        assert "42" in result.output


class TestCLIBranchesRepoCommands:
    """Cover lines 1539-1572 (repo list/ingest-all table output)."""

    def test_repo_list_table_output(self):
        from click.testing import CliRunner

        from navegador.cli.commands import main

        runner = CliRunner()
        repos = [{"name": "myrepo", "path": "/path/to/myrepo"}]
        with patch("navegador.cli.commands._get_store") as mock_gs:
            mock_gs.return_value = _make_store()
            with patch("navegador.multirepo.MultiRepoManager") as MockMRM:
                MockMRM.return_value.list_repos.return_value = repos
                result = runner.invoke(main, ["repo", "list"])
        assert result.exit_code == 0
        assert "myrepo" in result.output

    def test_repo_list_empty(self):
        from click.testing import CliRunner

        from navegador.cli.commands import main

        runner = CliRunner()
        with patch("navegador.cli.commands._get_store") as mock_gs:
            mock_gs.return_value = _make_store()
            with patch("navegador.multirepo.MultiRepoManager") as MockMRM:
                MockMRM.return_value.list_repos.return_value = []
                result = runner.invoke(main, ["repo", "list"])
        assert result.exit_code == 0
        assert "No repositories" in result.output

    def test_repo_ingest_all_table(self):
        from click.testing import CliRunner

        from navegador.cli.commands import main

        runner = CliRunner()
        summary = {"myrepo": {"files": 5, "functions": 10, "classes": 2}}
        with patch("navegador.cli.commands._get_store") as mock_gs:
            mock_gs.return_value = _make_store()
            with patch("navegador.multirepo.MultiRepoManager") as MockMRM:
                MockMRM.return_value.ingest_all.return_value = summary
                result = runner.invoke(main, ["repo", "ingest-all"])
        assert result.exit_code == 0
        assert "myrepo" in result.output

    def test_repo_search_table(self):
        from click.testing import CliRunner

        from navegador.cli.commands import main

        runner = CliRunner()
        results = [{"label": "Function", "name": "foo", "file_path": "foo.py"}]
        with patch("navegador.cli.commands._get_store") as mock_gs:
            mock_gs.return_value = _make_store()
            with patch("navegador.multirepo.MultiRepoManager") as MockMRM:
                MockMRM.return_value.cross_repo_search.return_value = results
                result = runner.invoke(main, ["repo", "search", "foo"])
        assert result.exit_code == 0
        assert "foo" in result.output

    def test_repo_search_empty(self):
        from click.testing import CliRunner

        from navegador.cli.commands import main

        runner = CliRunner()
        with patch("navegador.cli.commands._get_store") as mock_gs:
            mock_gs.return_value = _make_store()
            with patch("navegador.multirepo.MultiRepoManager") as MockMRM:
                MockMRM.return_value.cross_repo_search.return_value = []
                result = runner.invoke(main, ["repo", "search", "nothing"])
        assert result.exit_code == 0
        assert "No results" in result.output


class TestCLIBranchesPM:
    """Cover lines 1793-1806 (pm ingest output)."""

    def test_pm_ingest_no_github_raises(self):
        from click.testing import CliRunner

        from navegador.cli.commands import main

        runner = CliRunner()
        result = runner.invoke(main, ["pm", "ingest"])
        assert result.exit_code != 0

    def test_pm_ingest_table_output(self):
        from click.testing import CliRunner

        from navegador.cli.commands import main

        runner = CliRunner()
        with patch("navegador.cli.commands._get_store") as mock_gs:
            mock_gs.return_value = _make_store()
            with patch("navegador.pm.TicketIngester") as MockTI:
                MockTI.return_value.ingest_github_issues.return_value = {
                    "tickets": 5, "linked": 2
                }
                result = runner.invoke(main, ["pm", "ingest", "--github", "owner/repo"])
        assert result.exit_code == 0
        assert "5" in result.output

    def test_pm_ingest_json(self):
        from click.testing import CliRunner

        from navegador.cli.commands import main

        runner = CliRunner()
        with patch("navegador.cli.commands._get_store") as mock_gs:
            mock_gs.return_value = _make_store()
            with patch("navegador.pm.TicketIngester") as MockTI:
                MockTI.return_value.ingest_github_issues.return_value = {
                    "tickets": 3, "linked": 1
                }
                result = runner.invoke(
                    main, ["pm", "ingest", "--github", "owner/repo", "--json"]
                )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["tickets"] == 3


class TestCLIBranchesIngest:
    """Cover lines 179-185 (ingest --monorepo table output)."""

    def test_ingest_monorepo_table(self, tmp_path):
        from click.testing import CliRunner

        from navegador.cli.commands import main

        runner = CliRunner()
        with patch("navegador.cli.commands._get_store") as mock_gs:
            mock_gs.return_value = _make_store()
            with patch("navegador.monorepo.MonorepoIngester") as MockMI:
                MockMI.return_value.ingest.return_value = {
                    "files": 10, "functions": 20, "packages": 3, "workspace_type": "yarn"
                }
                result = runner.invoke(
                    main, ["ingest", str(tmp_path), "--monorepo"]
                )
        assert result.exit_code == 0

    def test_ingest_monorepo_json(self, tmp_path):
        from click.testing import CliRunner

        from navegador.cli.commands import main

        runner = CliRunner()
        with patch("navegador.cli.commands._get_store") as mock_gs:
            mock_gs.return_value = _make_store()
            with patch("navegador.monorepo.MonorepoIngester") as MockMI:
                MockMI.return_value.ingest.return_value = {
                    "files": 5, "packages": 2
                }
                result = runner.invoke(
                    main, ["ingest", str(tmp_path), "--monorepo", "--json"]
                )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["files"] == 5


class TestCLIBranchesSubmodulesIngest:
    """Cover lines 1901-1916 (submodules ingest output)."""

    def test_submodules_ingest_output(self, tmp_path):
        from click.testing import CliRunner

        from navegador.cli.commands import main

        runner = CliRunner()
        with patch("navegador.cli.commands._get_store") as mock_gs:
            mock_gs.return_value = _make_store()
            with patch("navegador.submodules.SubmoduleIngester") as MockSI:
                MockSI.return_value.ingest_with_submodules.return_value = {
                    "total_files": 10,
                    "submodules": {"sub1": {}, "sub2": {}},
                }
                result = runner.invoke(main, ["submodules", "ingest", str(tmp_path)])
        assert result.exit_code == 0
        assert "sub1" in result.output

    def test_submodules_ingest_json(self, tmp_path):
        from click.testing import CliRunner

        from navegador.cli.commands import main

        runner = CliRunner()
        with patch("navegador.cli.commands._get_store") as mock_gs:
            mock_gs.return_value = _make_store()
            with patch("navegador.submodules.SubmoduleIngester") as MockSI:
                MockSI.return_value.ingest_with_submodules.return_value = {
                    "total_files": 5,
                    "submodules": {},
                }
                result = runner.invoke(
                    main, ["submodules", "ingest", str(tmp_path), "--json"]
                )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["total_files"] == 5


class TestCLIBranchesCommunities:
    """Cover lines 2105-2141 (communities store-labels + table)."""

    def test_communities_store_labels(self):
        from click.testing import CliRunner

        from navegador.cli.commands import main
        from navegador.intelligence.community import Community

        runner = CliRunner()
        comm = Community(name="c1", members=["a", "b", "c"], density=0.5)
        with patch("navegador.cli.commands._get_store") as mock_gs:
            mock_gs.return_value = _make_store()
            with patch("navegador.intelligence.community.CommunityDetector") as MockCD:
                inst = MockCD.return_value
                inst.detect.return_value = [comm]
                inst.store_communities.return_value = 3
                result = runner.invoke(main, ["communities", "--store-labels"])
        assert result.exit_code == 0
        assert "3" in result.output

    def test_communities_empty(self):
        from click.testing import CliRunner

        from navegador.cli.commands import main

        runner = CliRunner()
        with patch("navegador.cli.commands._get_store") as mock_gs:
            mock_gs.return_value = _make_store()
            with patch("navegador.intelligence.community.CommunityDetector") as MockCD:
                MockCD.return_value.detect.return_value = []
                result = runner.invoke(main, ["communities"])
        assert result.exit_code == 0
        assert "No communities" in result.output

    def test_communities_large_preview(self):
        from click.testing import CliRunner

        from navegador.cli.commands import main
        from navegador.intelligence.community import Community

        runner = CliRunner()
        comm = Community(name="big", members=list("abcdefgh"), density=0.8)
        with patch("navegador.cli.commands._get_store") as mock_gs:
            mock_gs.return_value = _make_store()
            with patch("navegador.intelligence.community.CommunityDetector") as MockCD:
                MockCD.return_value.detect.return_value = [comm]
                result = runner.invoke(main, ["communities"])
        assert result.exit_code == 0
        assert "+" in result.output  # preview truncation


# ===========================================================================
# navegador.cluster.messaging  (86% → target ~95%)
# ===========================================================================


class TestMessageBus:
    """Cover lines 74-78, 154, 178-182, 208."""

    def _make(self):
        from navegador.cluster.messaging import MessageBus

        r = MagicMock()
        r.smembers.return_value = set()
        r.lrange.return_value = []
        return MessageBus("redis://localhost:6379", _redis_client=r), r

    def test_send_returns_message_id(self):
        bus, r = self._make()
        msg_id = bus.send("agent1", "agent2", "task", {"k": "v"})
        assert isinstance(msg_id, str) and len(msg_id) == 36
        r.rpush.assert_called_once()

    def test_receive_returns_unacked_messages(self):
        import json as _json
        import time

        from navegador.cluster.messaging import Message

        bus, r = self._make()
        msg = Message(
            id="abc123", from_agent="a1", to_agent="a2",
            type="task", payload={}, timestamp=time.time()
        )
        r.lrange.return_value = [_json.dumps(msg.to_dict()).encode()]
        r.smembers.return_value = set()
        messages = bus.receive("a2")
        assert len(messages) == 1
        assert messages[0].id == "abc123"

    def test_receive_filters_acked(self):
        import json as _json
        import time

        from navegador.cluster.messaging import Message

        bus, r = self._make()
        msg = Message(
            id="acked_id", from_agent="a1", to_agent="a2",
            type="task", payload={}, timestamp=time.time()
        )
        r.lrange.return_value = [_json.dumps(msg.to_dict()).encode()]
        r.smembers.return_value = {b"acked_id"}
        messages = bus.receive("a2")
        assert messages == []

    def test_acknowledge_with_agent_id(self):
        bus, r = self._make()
        bus.acknowledge("msg123", agent_id="a2")
        r.sadd.assert_called()

    def test_acknowledge_without_agent_id_broadcasts(self):
        bus, r = self._make()
        r.smembers.return_value = {b"agent1", b"agent2"}
        bus.acknowledge("msg123")
        assert r.sadd.call_count == 2

    def test_broadcast_skips_sender(self):
        bus, r = self._make()
        r.smembers.return_value = {b"sender", b"agent2", b"agent3"}
        with patch.object(bus, "send", return_value="mid") as mock_send:
            ids = bus.broadcast("sender", "task", {})
        assert len(ids) == 2
        for call_args in mock_send.call_args_list:
            assert call_args[0][0] == "sender"
            assert call_args[0][1] != "sender"

    def test_client_lazy_init_raises_on_missing_redis(self):
        from navegador.cluster.messaging import MessageBus

        bus = MessageBus("redis://localhost:6379")
        with patch.dict("sys.modules", {"redis": None}):
            with pytest.raises(ImportError, match="redis"):
                bus._client()


# ===========================================================================
# navegador.cluster.locking  (90% → target ~97%)
# ===========================================================================


class TestDistributedLock:
    """Cover lines 72-76, 120."""

    def _make(self):
        from navegador.cluster.locking import DistributedLock

        r = MagicMock()
        return DistributedLock("redis://localhost:6379", "test-lock", _redis_client=r), r

    def test_acquire_success(self):
        lock, r = self._make()
        r.set.return_value = True
        assert lock.acquire() is True
        assert lock._token is not None

    def test_acquire_failure(self):
        lock, r = self._make()
        r.set.return_value = None
        assert lock.acquire() is False

    def test_release_when_token_matches(self):
        lock, r = self._make()
        lock._token = "mytoken"
        r.get.return_value = b"mytoken"
        lock.release()
        r.delete.assert_called_once()
        assert lock._token is None

    def test_release_when_token_not_held(self):
        lock, r = self._make()
        lock._token = None
        lock.release()
        r.delete.assert_not_called()

    def test_release_when_stored_token_differs(self):
        lock, r = self._make()
        lock._token = "mytoken"
        r.get.return_value = b"other_token"
        lock.release()
        r.delete.assert_not_called()

    def test_context_manager_acquires_and_releases(self):
        lock, r = self._make()
        r.set.return_value = True
        r.get.return_value = None  # simulate already released
        with lock:
            pass
        assert lock._token is None

    def test_context_manager_raises_on_timeout(self):
        from navegador.cluster.locking import LockTimeout

        lock, r = self._make()
        r.set.return_value = None  # never acquired
        lock._timeout = 0
        lock._retry_interval = 0

        with pytest.raises(LockTimeout):
            with lock:
                pass

    def test_client_lazy_init_raises_on_missing_redis(self):
        from navegador.cluster.locking import DistributedLock

        lock = DistributedLock("redis://localhost:6379", "x")
        with patch.dict("sys.modules", {"redis": None}):
            with pytest.raises(ImportError, match="redis"):
                lock._client()


# ===========================================================================
# navegador.cluster.observability  (89% → target ~97%)
# ===========================================================================


class TestSwarmDashboard:
    """Cover lines 44-48, 93, 108, 160."""

    def _make(self):
        from navegador.cluster.observability import SwarmDashboard

        r = MagicMock()
        r.keys.return_value = []
        r.get.return_value = None
        return SwarmDashboard("redis://localhost:6379", _redis_client=r), r

    def test_register_agent(self):
        dash, r = self._make()
        dash.register_agent("agent1", metadata={"role": "ingester"})
        r.setex.assert_called_once()

    def test_register_agent_no_metadata(self):
        dash, r = self._make()
        dash.register_agent("agent1")
        r.setex.assert_called_once()

    def test_agent_status_empty(self):
        dash, r = self._make()
        r.keys.return_value = []
        agents = dash.agent_status()
        assert agents == []

    def test_agent_status_returns_active_agents(self):
        import json as _json
        dash, r = self._make()
        payload = {"agent_id": "a1", "last_seen": 12345, "state": "active"}
        r.keys.return_value = [b"navegador:obs:agent:a1"]
        r.get.return_value = _json.dumps(payload).encode()
        agents = dash.agent_status()
        assert len(agents) == 1
        assert agents[0]["agent_id"] == "a1"

    def test_task_metrics_default(self):
        dash, r = self._make()
        r.get.return_value = None
        metrics = dash.task_metrics()
        assert metrics == {"pending": 0, "active": 0, "completed": 0, "failed": 0}

    def test_task_metrics_from_redis(self):
        import json as _json
        dash, r = self._make()
        stored = {"pending": 3, "active": 1, "completed": 10, "failed": 0}
        r.get.return_value = _json.dumps(stored).encode()
        metrics = dash.task_metrics()
        assert metrics["pending"] == 3

    def test_update_task_metrics(self):
        import json as _json
        dash, r = self._make()
        r.get.return_value = _json.dumps(
            {"pending": 0, "active": 0, "completed": 0, "failed": 0}
        ).encode()
        dash.update_task_metrics(pending=5, active=2)
        r.set.assert_called_once()

    def test_graph_metrics(self):
        store = _make_store()
        store.node_count.return_value = 42
        store.edge_count.return_value = 100
        dash, r = self._make()
        result = dash.graph_metrics(store)
        assert result["node_count"] == 42
        assert result["edge_count"] == 100

    def test_to_json_structure(self):
        import json as _json
        dash, r = self._make()
        r.keys.return_value = []
        r.get.side_effect = [None, None]  # graph_meta + task_metrics
        snapshot = _json.loads(dash.to_json())
        assert "agents" in snapshot
        assert "task_metrics" in snapshot

    def test_client_lazy_init_raises_on_missing_redis(self):
        from navegador.cluster.observability import SwarmDashboard

        dash = SwarmDashboard("redis://localhost:6379")
        with patch.dict("sys.modules", {"redis": None}):
            with pytest.raises(ImportError, match="redis"):
                dash._client()


# ===========================================================================
# navegador.cluster.pubsub  (86% → target ~97%)
# ===========================================================================


class TestGraphNotifier:
    """Cover lines 72-76, 159-162."""

    def _make(self):
        from navegador.cluster.pubsub import GraphNotifier

        r = MagicMock()
        pubsub_mock = MagicMock()
        pubsub_mock.listen.return_value = iter([])
        r.pubsub.return_value = pubsub_mock
        r.publish.return_value = 1
        return GraphNotifier("redis://localhost:6379", redis_client=r), r

    def test_publish_returns_receiver_count(self):
        from navegador.cluster.pubsub import EventType

        notifier, r = self._make()
        count = notifier.publish(EventType.NODE_CREATED, {"name": "foo"})
        assert count == 1
        r.publish.assert_called_once()

    def test_publish_with_string_event_type(self):
        notifier, r = self._make()
        count = notifier.publish("custom_event", {"key": "val"})
        assert count == 1

    def test_subscribe_run_in_thread(self):
        from navegador.cluster.pubsub import EventType

        notifier, r = self._make()
        import json as _json
        import threading

        # Return one message then stop
        msg = {
            "type": "message",
            "data": _json.dumps({"event_type": "node_created", "data": {"k": "v"}}).encode(),
        }
        r.pubsub.return_value.listen.return_value = iter([msg])

        received = []

        def callback(et, data):
            received.append((et, data))

        t = notifier.subscribe([EventType.NODE_CREATED], callback, run_in_thread=True)
        assert isinstance(t, threading.Thread)
        t.join(timeout=2)
        assert received == [("node_created", {"k": "v"})]

    def test_close(self):
        notifier, r = self._make()
        notifier.close()
        r.close.assert_called_once()

    def test_close_ignores_exception(self):
        notifier, r = self._make()
        r.close.side_effect = Exception("closed")
        notifier.close()  # should not raise

    def test_connect_redis_raises_on_missing_dep(self):
        from navegador.cluster.pubsub import GraphNotifier

        with patch.dict("sys.modules", {"redis": None}):
            with pytest.raises(ImportError, match="redis"):
                GraphNotifier._connect_redis("redis://localhost")


# ===========================================================================
# navegador.ingestion.ruby — extra branches  (66% → target ~85%)
# ===========================================================================


class TestRubyParserBranches:
    """Exercise _handle_class superclass, _handle_module body, _maybe_handle_require,
    _extract_calls and fallback paths."""

    def _make_parser(self):
        with _mock_ts("tree_sitter_ruby"):
            from navegador.ingestion.ruby import RubyParser
            return RubyParser()

    def test_handle_class_with_superclass(self):
        with _mock_ts("tree_sitter_ruby"):
            from navegador.ingestion.ruby import RubyParser

            parser = RubyParser()
            store = _make_store()

            name_node = _text_node(b"MyClass")
            superclass_node = _text_node(b"< BaseClass", "constant")
            class_node = MockNode("class", children=[name_node, superclass_node])
            class_node.set_field("name", name_node)
            class_node.set_field("superclass", superclass_node)

            stats = {"functions": 0, "classes": 0, "edges": 0}
            parser._handle_class(class_node, b"class MyClass < BaseClass\nend", "f.rb", store, stats)
            assert stats["classes"] == 1
            assert stats["edges"] >= 2  # CONTAINS + INHERITS

    def test_handle_class_no_name_node(self):
        with _mock_ts("tree_sitter_ruby"):
            from navegador.ingestion.ruby import RubyParser

            parser = RubyParser()
            store = _make_store()
            anon_class = MockNode("class")
            stats = {"functions": 0, "classes": 0, "edges": 0}
            parser._handle_class(anon_class, b"class; end", "f.rb", store, stats)
            assert stats["classes"] == 0

    def test_handle_module_with_body(self):
        with _mock_ts("tree_sitter_ruby"):
            from navegador.ingestion.ruby import RubyParser

            parser = RubyParser()
            store = _make_store()

            name_node = _text_node(b"MyModule")
            method_name = _text_node(b"my_method")
            method_node = MockNode("method", children=[method_name])
            method_node.set_field("name", method_name)
            body_node = MockNode("body_statement", children=[method_node])
            mod_node = MockNode("module", children=[name_node, body_node])
            mod_node.set_field("name", name_node)
            # body found via body_statement child (no "body" field)

            stats = {"functions": 0, "classes": 0, "edges": 0}
            src = b"module MyModule\n  def my_method; end\nend"
            parser._handle_module(mod_node, src, "f.rb", store, stats)
            assert stats["classes"] == 1

    def test_handle_method_standalone_function(self):
        with _mock_ts("tree_sitter_ruby"):
            from navegador.ingestion.ruby import RubyParser

            parser = RubyParser()
            store = _make_store()

            name_node = _text_node(b"standalone_fn")
            fn_node = MockNode("method", children=[name_node])
            fn_node.set_field("name", name_node)

            stats = {"functions": 0, "classes": 0, "edges": 0}
            parser._handle_method(fn_node, b"def standalone_fn; end", "f.rb", store, stats, class_name=None)
            assert stats["functions"] == 1
            # No class → Function node
            create_call = store.create_node.call_args_list[-1]
            from navegador.graph.schema import NodeLabel
            assert create_call[0][0] == NodeLabel.Function

    def test_handle_method_no_name(self):
        with _mock_ts("tree_sitter_ruby"):
            from navegador.ingestion.ruby import RubyParser

            parser = RubyParser()
            store = _make_store()
            anon_method = MockNode("method")
            stats = {"functions": 0, "classes": 0, "edges": 0}
            parser._handle_method(anon_method, b"", "f.rb", store, stats, class_name=None)
            assert stats["functions"] == 0

    def test_maybe_handle_require(self):
        with _mock_ts("tree_sitter_ruby"):
            from navegador.ingestion.ruby import RubyParser

            parser = RubyParser()
            store = _make_store()

            method_node = _text_node(b"require", "identifier")
            string_node = _text_node(b"'json'", "string")
            args_node = MockNode("argument_list", children=[string_node])
            call_node = MockNode("call", children=[method_node, args_node])
            call_node.set_field("method", method_node)
            call_node.set_field("arguments", args_node)

            stats = {"functions": 0, "classes": 0, "edges": 0}
            src = b"require 'json'"
            parser._maybe_handle_require(call_node, src, "f.rb", store, stats)
            store.create_node.assert_called_once()

    def test_maybe_handle_require_skips_non_require(self):
        with _mock_ts("tree_sitter_ruby"):
            from navegador.ingestion.ruby import RubyParser

            parser = RubyParser()
            store = _make_store()

            method_node = _text_node(b"puts", "identifier")
            call_node = MockNode("call", children=[method_node])
            call_node.set_field("method", method_node)

            stats = {"functions": 0, "classes": 0, "edges": 0}
            parser._maybe_handle_require(call_node, b"puts 'hi'", "f.rb", store, stats)
            store.create_node.assert_not_called()

    def test_extract_calls(self):
        with _mock_ts("tree_sitter_ruby"):
            from navegador.graph.schema import NodeLabel
            from navegador.ingestion.ruby import RubyParser

            parser = RubyParser()
            store = _make_store()

            callee_node = _text_node(b"helper", "identifier")
            call_node = MockNode("call", children=[callee_node])
            call_node.set_field("method", callee_node)
            body_node = MockNode("body_statement", children=[call_node])
            fn_node = MockNode("method", children=[body_node])

            stats = {"functions": 0, "classes": 0, "edges": 0}
            parser._extract_calls(fn_node, b"def foo; helper; end", "f.rb", "foo", NodeLabel.Function, store, stats)
            store.create_edge.assert_called()


# ===========================================================================
# navegador.ingestion.cpp — extra branches  (73% → target ~90%)
# ===========================================================================


class TestCppParserBranches:
    def _make_parser(self):
        with _mock_ts("tree_sitter_cpp"):
            from navegador.ingestion.cpp import CppParser
            return CppParser()

    def test_handle_class_with_inheritance(self):
        with _mock_ts("tree_sitter_cpp"):
            from navegador.ingestion.cpp import CppParser

            parser = CppParser()
            store = _make_store()

            name_node = _text_node(b"MyClass", "type_identifier")
            parent_node = _text_node(b"BaseClass", "type_identifier")
            base_clause_node = MockNode("base_class_clause", children=[parent_node])
            class_node = MockNode("class_specifier", children=[name_node, base_clause_node])
            class_node.set_field("name", name_node)
            class_node.set_field("base_clause", base_clause_node)

            stats = {"functions": 0, "classes": 0, "edges": 0}
            parser._handle_class(class_node, b"class MyClass : public BaseClass {};", "f.cpp", store, stats)
            assert stats["classes"] == 1
            assert stats["edges"] >= 2

    def test_handle_class_no_name(self):
        with _mock_ts("tree_sitter_cpp"):
            from navegador.ingestion.cpp import CppParser

            parser = CppParser()
            store = _make_store()
            anon_class = MockNode("class_specifier")
            stats = {"functions": 0, "classes": 0, "edges": 0}
            parser._handle_class(anon_class, b"struct {};", "f.cpp", store, stats)
            assert stats["classes"] == 0

    def test_handle_function_with_class_name(self):
        with _mock_ts("tree_sitter_cpp"):
            from navegador.ingestion.cpp import CppParser

            parser = CppParser()
            store = _make_store()

            fn_name_node = _text_node(b"myMethod", "identifier")
            declarator = MockNode("function_declarator", children=[fn_name_node])
            declarator.set_field("declarator", fn_name_node)
            body = MockNode("compound_statement")
            fn_node = MockNode("function_definition", children=[declarator, body])
            fn_node.set_field("declarator", declarator)
            fn_node.set_field("body", body)

            stats = {"functions": 0, "classes": 0, "edges": 0}
            parser._handle_function(fn_node, b"void myMethod() {}", "f.cpp", store, stats, class_name="MyClass")
            assert stats["functions"] == 1
            from navegador.graph.schema import NodeLabel
            assert store.create_node.call_args[0][0] == NodeLabel.Method

    def test_extract_function_name_qualified(self):
        with _mock_ts("tree_sitter_cpp"):
            from navegador.ingestion.cpp import CppParser

            parser = CppParser()
            src = b"method"
            name_node = MockNode("identifier", start_byte=0, end_byte=len(src))
            qualified = MockNode("qualified_identifier", children=[name_node])
            qualified.set_field("name", name_node)
            result = parser._extract_function_name(qualified, src)
            assert result == "method"

    def test_extract_function_name_qualified_fallback(self):
        with _mock_ts("tree_sitter_cpp"):
            from navegador.ingestion.cpp import CppParser

            parser = CppParser()
            src = b"method"
            id_node = MockNode("identifier", start_byte=0, end_byte=len(src))
            qualified = MockNode("qualified_identifier", children=[id_node])
            # No name field → fallback to last identifier child
            result = parser._extract_function_name(qualified, src)
            assert result == "method"

    def test_extract_function_name_pointer_declarator(self):
        with _mock_ts("tree_sitter_cpp"):
            from navegador.ingestion.cpp import CppParser

            parser = CppParser()
            src = b"fp"
            inner = MockNode("identifier", start_byte=0, end_byte=len(src))
            ptr_decl = MockNode("pointer_declarator", children=[inner])
            ptr_decl.set_field("declarator", inner)
            result = parser._extract_function_name(ptr_decl, src)
            assert result == "fp"

    def test_extract_function_name_fallback_child(self):
        with _mock_ts("tree_sitter_cpp"):
            from navegador.ingestion.cpp import CppParser

            parser = CppParser()
            src = b"fallbackFn"
            id_node = MockNode("identifier", start_byte=0, end_byte=len(src))
            unknown_decl = MockNode("unknown_declarator", children=[id_node])
            result = parser._extract_function_name(unknown_decl, src)
            assert result == "fallbackFn"

    def test_extract_function_name_none(self):
        with _mock_ts("tree_sitter_cpp"):
            from navegador.ingestion.cpp import CppParser

            parser = CppParser()
            assert parser._extract_function_name(None, b"") is None

    def test_handle_include(self):
        with _mock_ts("tree_sitter_cpp"):
            from navegador.ingestion.cpp import CppParser

            parser = CppParser()
            store = _make_store()

            path_node = _text_node(b'"vector"', "string_literal")
            include_node = MockNode("preproc_include", children=[path_node])
            include_node.set_field("path", path_node)

            stats = {"functions": 0, "classes": 0, "edges": 0}
            parser._handle_include(include_node, b'#include "vector"', "f.cpp", store, stats)
            store.create_node.assert_called_once()

    def test_namespace_recurse(self):
        with _mock_ts("tree_sitter_cpp"):
            from navegador.ingestion.cpp import CppParser

            parser = CppParser()
            store = _make_store()

            # namespace body contains a function
            fn_name = _text_node(b"inner_fn", "identifier")
            fn_decl = MockNode("function_declarator", children=[fn_name])
            fn_decl.set_field("declarator", fn_name)
            fn_body = MockNode("compound_statement")
            fn_def = MockNode("function_definition", children=[fn_decl, fn_body])
            fn_def.set_field("declarator", fn_decl)

            decl_list = MockNode("declaration_list", children=[fn_def])
            ns_node = MockNode("namespace_definition", children=[decl_list])

            stats = {"functions": 0, "classes": 0, "edges": 0}
            parser._walk(ns_node, b"namespace ns { void inner_fn(){} }", "f.cpp", store, stats, class_name=None)
            assert stats["functions"] == 1


# ===========================================================================
# navegador.ingestion.csharp — extra branches  (79% → target ~90%)
# ===========================================================================


class TestCSharpParserBranches:
    def test_handle_class_with_bases(self):
        with _mock_ts("tree_sitter_c_sharp"):
            from navegador.ingestion.csharp import CSharpParser

            parser = CSharpParser()
            store = _make_store()

            name_node = _text_node(b"MyService", "identifier")
            base_id = _text_node(b"IService", "identifier")
            bases_node = MockNode("base_list", children=[base_id])
            class_node = MockNode("class_declaration", children=[name_node, bases_node])
            class_node.set_field("name", name_node)
            class_node.set_field("bases", bases_node)

            stats = {"functions": 0, "classes": 0, "edges": 0}
            parser._handle_class(class_node, b"class MyService : IService {}", "f.cs", store, stats)
            assert stats["classes"] == 1
            assert stats["edges"] >= 2

    def test_handle_class_no_name(self):
        with _mock_ts("tree_sitter_c_sharp"):
            from navegador.ingestion.csharp import CSharpParser

            parser = CSharpParser()
            store = _make_store()
            anon = MockNode("class_declaration")
            stats = {"functions": 0, "classes": 0, "edges": 0}
            parser._handle_class(anon, b"", "f.cs", store, stats)
            assert stats["classes"] == 0

    def test_handle_method_standalone(self):
        with _mock_ts("tree_sitter_c_sharp"):
            from navegador.ingestion.csharp import CSharpParser

            parser = CSharpParser()
            store = _make_store()

            name_node = _text_node(b"DoWork", "identifier")
            fn_node = MockNode("method_declaration", children=[name_node])
            fn_node.set_field("name", name_node)

            stats = {"functions": 0, "classes": 0, "edges": 0}
            parser._handle_method(fn_node, b"void DoWork() {}", "f.cs", store, stats, class_name=None)
            assert stats["functions"] == 1

    def test_handle_method_no_name(self):
        with _mock_ts("tree_sitter_c_sharp"):
            from navegador.ingestion.csharp import CSharpParser

            parser = CSharpParser()
            store = _make_store()
            anon = MockNode("method_declaration")
            stats = {"functions": 0, "classes": 0, "edges": 0}
            parser._handle_method(anon, b"", "f.cs", store, stats, class_name=None)
            assert stats["functions"] == 0

    def test_handle_using(self):
        with _mock_ts("tree_sitter_c_sharp"):
            from navegador.ingestion.csharp import CSharpParser

            parser = CSharpParser()
            store = _make_store()

            src = b"using System.Collections.Generic;"
            using_node = MockNode(
                "using_directive",
                start_byte=0,
                end_byte=len(src),
                start_point=(0, 0),
                end_point=(0, len(src)),
            )
            stats = {"functions": 0, "classes": 0, "edges": 0}
            parser._handle_using(using_node, src, "f.cs", store, stats)
            store.create_node.assert_called_once()

    def test_extract_calls(self):
        with _mock_ts("tree_sitter_c_sharp"):
            from navegador.graph.schema import NodeLabel
            from navegador.ingestion.csharp import CSharpParser

            parser = CSharpParser()
            store = _make_store()

            callee_node = _text_node(b"DoWork", "identifier")
            invoke_node = MockNode("invocation_expression", children=[callee_node])
            invoke_node.set_field("function", callee_node)
            block_node = MockNode("block", children=[invoke_node])
            fn_node = MockNode("method_declaration", children=[block_node])
            fn_node.set_field("body", block_node)

            stats = {"functions": 0, "classes": 0, "edges": 0}
            parser._extract_calls(fn_node, b"DoWork()", "f.cs", "Run", NodeLabel.Method, store, stats)
            store.create_edge.assert_called()


# ===========================================================================
# navegador.ingestion.kotlin — extra branches  (79% → target ~90%)
# ===========================================================================


class TestKotlinParserBranches:
    def test_handle_class_no_name(self):
        with _mock_ts("tree_sitter_kotlin"):
            from navegador.ingestion.kotlin import KotlinParser

            parser = KotlinParser()
            store = _make_store()
            anon = MockNode("class_declaration")
            stats = {"functions": 0, "classes": 0, "edges": 0}
            parser._handle_class(anon, b"", "f.kt", store, stats)
            assert stats["classes"] == 0

    def test_handle_class_with_body(self):
        with _mock_ts("tree_sitter_kotlin"):
            from navegador.ingestion.kotlin import KotlinParser

            parser = KotlinParser()
            store = _make_store()

            name_node = _text_node(b"MyClass", "simple_identifier")
            fn_name = _text_node(b"doSomething", "simple_identifier")
            fn_node = MockNode("function_declaration", children=[fn_name])
            fn_node.set_field("name", fn_name)
            body_node = MockNode("class_body", children=[fn_node])
            class_node = MockNode("class_declaration", children=[name_node, body_node])
            class_node.set_field("name", name_node)
            class_node.set_field("body", body_node)

            stats = {"functions": 0, "classes": 0, "edges": 0}
            src = b"class MyClass { fun doSomething() {} }"
            parser._handle_class(class_node, src, "f.kt", store, stats)
            assert stats["classes"] == 1
            assert stats["functions"] == 1

    def test_handle_function_no_name(self):
        with _mock_ts("tree_sitter_kotlin"):
            from navegador.ingestion.kotlin import KotlinParser

            parser = KotlinParser()
            store = _make_store()
            anon = MockNode("function_declaration")
            stats = {"functions": 0, "classes": 0, "edges": 0}
            parser._handle_function(anon, b"", "f.kt", store, stats, class_name=None)
            assert stats["functions"] == 0

    def test_handle_import(self):
        with _mock_ts("tree_sitter_kotlin"):
            from navegador.ingestion.kotlin import KotlinParser

            parser = KotlinParser()
            store = _make_store()

            src = b"import kotlin.collections.List"
            import_node = MockNode(
                "import_header",
                start_byte=0,
                end_byte=len(src),
                start_point=(0, 0),
                end_point=(0, len(src)),
            )
            stats = {"functions": 0, "classes": 0, "edges": 0}
            parser._handle_import(import_node, src, "f.kt", store, stats)
            store.create_node.assert_called_once()


# ===========================================================================
# navegador.ingestion.php — extra branches  (79% → target ~90%)
# ===========================================================================


class TestPHPParserBranches:
    def test_handle_class_no_name(self):
        with _mock_ts("tree_sitter_php"):
            from navegador.ingestion.php import PHPParser

            parser = PHPParser()
            store = _make_store()
            anon = MockNode("class_declaration")
            stats = {"functions": 0, "classes": 0, "edges": 0}
            parser._handle_class(anon, b"", "f.php", store, stats)
            assert stats["classes"] == 0

    def test_handle_function_no_name(self):
        with _mock_ts("tree_sitter_php"):
            from navegador.ingestion.php import PHPParser

            parser = PHPParser()
            store = _make_store()
            anon = MockNode("function_definition")
            stats = {"functions": 0, "classes": 0, "edges": 0}
            parser._handle_function(anon, b"", "f.php", store, stats, class_name=None)
            assert stats["functions"] == 0

    def test_handle_class_with_body_methods(self):
        with _mock_ts("tree_sitter_php"):
            from navegador.ingestion.php import PHPParser

            parser = PHPParser()
            store = _make_store()

            name_node = _text_node(b"MyController", "name")
            fn_name = _text_node(b"index", "name")
            method_node = MockNode("method_declaration", children=[fn_name])
            method_node.set_field("name", fn_name)
            body_node = MockNode("declaration_list", children=[method_node])
            class_node = MockNode("class_declaration", children=[name_node, body_node])
            class_node.set_field("name", name_node)
            class_node.set_field("body", body_node)

            stats = {"functions": 0, "classes": 0, "edges": 0}
            src = b"class MyController { public function index() {} }"
            parser._handle_class(class_node, src, "f.php", store, stats)
            assert stats["classes"] == 1
            assert stats["functions"] >= 1


# ===========================================================================
# navegador.ingestion.swift — extra branches  (79% → target ~90%)
# ===========================================================================


class TestSwiftParserBranches:
    def test_handle_class_no_name(self):
        with _mock_ts("tree_sitter_swift"):
            from navegador.ingestion.swift import SwiftParser

            parser = SwiftParser()
            store = _make_store()
            anon = MockNode("class_declaration")
            stats = {"functions": 0, "classes": 0, "edges": 0}
            parser._handle_class(anon, b"", "f.swift", store, stats)
            assert stats["classes"] == 0

    def test_handle_function_no_name(self):
        with _mock_ts("tree_sitter_swift"):
            from navegador.ingestion.swift import SwiftParser

            parser = SwiftParser()
            store = _make_store()
            anon = MockNode("function_declaration")
            stats = {"functions": 0, "classes": 0, "edges": 0}
            parser._handle_function(anon, b"", "f.swift", store, stats, class_name=None)
            assert stats["functions"] == 0

    def test_handle_import(self):
        with _mock_ts("tree_sitter_swift"):
            from navegador.ingestion.swift import SwiftParser

            parser = SwiftParser()
            store = _make_store()

            src = b"import Foundation"
            import_node = MockNode(
                "import_declaration",
                start_byte=0,
                end_byte=len(src),
                start_point=(0, 0),
                end_point=(0, len(src)),
            )
            stats = {"functions": 0, "classes": 0, "edges": 0}
            parser._handle_import(import_node, src, "f.swift", store, stats)
            store.create_node.assert_called_once()

    def test_handle_class_with_body(self):
        with _mock_ts("tree_sitter_swift"):
            from navegador.ingestion.swift import SwiftParser

            parser = SwiftParser()
            store = _make_store()

            name_node = _text_node(b"MyView", "type_identifier")
            fn_name = _text_node(b"body", "simple_identifier")
            fn_node = MockNode("function_declaration", children=[fn_name])
            fn_node.set_field("name", fn_name)
            body_node = MockNode("class_body", children=[fn_node])
            class_node = MockNode("class_declaration", children=[name_node, body_node])
            class_node.set_field("name", name_node)
            class_node.set_field("body", body_node)

            stats = {"functions": 0, "classes": 0, "edges": 0}
            src = b"class MyView { func body() {} }"
            parser._handle_class(class_node, src, "f.swift", store, stats)
            assert stats["classes"] == 1
            assert stats["functions"] == 1


# ===========================================================================
# navegador.ingestion.c — extra branches  (76% → target ~90%)
# ===========================================================================


class TestCParserBranches:
    def test_handle_function(self):
        with _mock_ts("tree_sitter_c"):
            from navegador.ingestion.c import CParser

            parser = CParser()
            store = _make_store()

            fn_name_node = _text_node(b"myFunc", "identifier")
            declarator = MockNode("function_declarator", children=[fn_name_node])
            declarator.set_field("declarator", fn_name_node)
            body = MockNode("compound_statement")
            fn_node = MockNode("function_definition", children=[declarator, body])
            fn_node.set_field("declarator", declarator)
            fn_node.set_field("body", body)

            stats = {"functions": 0, "classes": 0, "edges": 0}
            parser._handle_function(fn_node, b"void myFunc() {}", "f.c", store, stats)
            assert stats["functions"] == 1

    def test_handle_struct(self):
        with _mock_ts("tree_sitter_c"):
            from navegador.ingestion.c import CParser

            parser = CParser()
            store = _make_store()

            name_node = _text_node(b"Point", "type_identifier")
            struct_node = MockNode("struct_specifier", children=[name_node])
            struct_node.set_field("name", name_node)

            stats = {"functions": 0, "classes": 0, "edges": 0}
            parser._handle_struct(struct_node, b"struct Point {};", "f.c", store, stats)
            assert stats["classes"] == 1

    def test_handle_struct_no_name(self):
        with _mock_ts("tree_sitter_c"):
            from navegador.ingestion.c import CParser

            parser = CParser()
            store = _make_store()
            anon = MockNode("struct_specifier")
            stats = {"functions": 0, "classes": 0, "edges": 0}
            parser._handle_struct(anon, b"", "f.c", store, stats)
            assert stats["classes"] == 0

    def test_handle_include(self):
        with _mock_ts("tree_sitter_c"):
            from navegador.ingestion.c import CParser

            parser = CParser()
            store = _make_store()

            path_node = _text_node(b"<stdio.h>", "system_lib_string")
            include_node = MockNode("preproc_include", children=[path_node])
            include_node.set_field("path", path_node)

            stats = {"functions": 0, "classes": 0, "edges": 0}
            parser._handle_include(include_node, b"#include <stdio.h>", "f.c", store, stats)
            store.create_node.assert_called_once()

    def test_handle_function_no_name(self):
        with _mock_ts("tree_sitter_c"):
            from navegador.ingestion.c import CParser

            parser = CParser()
            store = _make_store()

            fn_node = MockNode("function_definition")
            # No declarator field → _extract_function_name returns None
            stats = {"functions": 0, "classes": 0, "edges": 0}
            parser._handle_function(fn_node, b"", "f.c", store, stats)
            assert stats["functions"] == 0
