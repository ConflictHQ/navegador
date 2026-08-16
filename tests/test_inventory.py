# Copyright CONFLICT LLC 2026 (weareconflict.com)
"""
Tests for navegador.inventory — finding projects and spotting stranded graphs.

The behaviour that matters is detecting a project that declares a shared
backend while holding real local graph data: an ingest that reported success
and wrote where no reader looks (#169).
"""

import pytest

from navegador.inventory import (
    LARGE_GRAPH_BYTES,
    MANY_PROJECTS,
    find_projects,
    inspect_project,
    recommend_central_server,
    scan,
)


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch, tmp_path):
    """Keep the developer's real user config out of these results."""
    monkeypatch.delenv("NAVEGADOR_REDIS_URL", raising=False)
    monkeypatch.delenv("NAVEGADOR_DB", raising=False)
    monkeypatch.setenv("NAVEGADOR_CONFIG", str(tmp_path / "absent.toml"))


def make_project(root, backend="sqlite", db_bytes=0, redis_url=""):
    """Create a project directory with a config and an optionally sized graph."""
    nav = root / ".navegador"
    nav.mkdir(parents=True, exist_ok=True)
    lines = ["[storage]", f'backend = "{backend}"']
    if backend == "redis":
        lines.append(f'redis_url = "{redis_url or "redis://localhost:6379"}"')
    (nav / "config.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    if db_bytes:
        (nav / "graph.db").write_bytes(b"\0" * db_bytes)
    return root


class TestFindProjects:
    def test_finds_nothing_in_empty_tree(self, tmp_path):
        assert find_projects(tmp_path) == []

    def test_finds_a_project(self, tmp_path):
        make_project(tmp_path / "repo")
        assert find_projects(tmp_path) == [tmp_path / "repo"]

    def test_finds_nested_projects(self, tmp_path):
        make_project(tmp_path / "parent")
        make_project(tmp_path / "parent" / "child")
        found = find_projects(tmp_path)
        assert tmp_path / "parent" in found
        assert tmp_path / "parent" / "child" in found

    def test_skips_dependency_directories(self, tmp_path):
        make_project(tmp_path / "node_modules" / "pkg")
        make_project(tmp_path / "real")
        assert find_projects(tmp_path) == [tmp_path / "real"]

    def test_respects_max_depth(self, tmp_path):
        make_project(tmp_path / "a" / "b" / "c" / "d")
        assert find_projects(tmp_path, max_depth=2) == []
        assert find_projects(tmp_path, max_depth=6) == [tmp_path / "a" / "b" / "c" / "d"]

    def test_missing_root_returns_empty(self, tmp_path):
        assert find_projects(tmp_path / "nope") == []


class TestInspectProject:
    def test_reads_declared_backend(self, tmp_path):
        make_project(tmp_path, backend="redis")
        assert inspect_project(tmp_path).declared_backend == "redis"

    def test_unset_when_no_config(self, tmp_path):
        (tmp_path / ".navegador").mkdir()
        assert inspect_project(tmp_path).declared_backend == "unset"

    def test_records_graph_size(self, tmp_path):
        make_project(tmp_path, db_bytes=10_000)
        assert inspect_project(tmp_path).db_bytes == 10_000

    def test_tiny_graph_is_not_data(self, tmp_path):
        """An empty falkordblite snapshot must not read as a populated graph."""
        make_project(tmp_path, backend="redis", db_bytes=500)
        assert inspect_project(tmp_path).has_local_data is False

    def test_stranded_when_redis_declared_but_data_local(self, tmp_path):
        make_project(tmp_path, backend="redis", db_bytes=10_000)
        assert inspect_project(tmp_path).is_stranded is True

    def test_not_stranded_when_embedded_declared(self, tmp_path):
        make_project(tmp_path, backend="sqlite", db_bytes=10_000)
        assert inspect_project(tmp_path).is_stranded is False

    def test_not_stranded_without_local_data(self, tmp_path):
        make_project(tmp_path, backend="redis")
        assert inspect_project(tmp_path).is_stranded is False

    def test_to_dict_is_serialisable(self, tmp_path):
        import json

        make_project(tmp_path, backend="redis", db_bytes=10_000)
        payload = inspect_project(tmp_path).to_dict()
        assert json.loads(json.dumps(payload))["stranded"] is True


class TestScan:
    def test_scans_a_tree(self, tmp_path):
        make_project(tmp_path / "a")
        make_project(tmp_path / "b", backend="redis", db_bytes=10_000)
        records = scan(tmp_path)
        assert len(records) == 2
        assert sum(r.is_stranded for r in records) == 1


class TestRecommendation:
    def test_quiet_for_one_small_embedded_project(self, tmp_path):
        make_project(tmp_path / "solo", db_bytes=1000)
        recommended, reasons = recommend_central_server(scan(tmp_path))
        assert recommended is False
        assert reasons == []

    def test_recommends_on_stranded_project(self, tmp_path):
        make_project(tmp_path / "a", backend="redis", db_bytes=10_000)
        recommended, reasons = recommend_central_server(scan(tmp_path))
        assert recommended is True
        assert any("declare a Redis backend" in r for r in reasons)

    def test_recommends_on_large_graph(self, tmp_path):
        make_project(tmp_path / "big", db_bytes=LARGE_GRAPH_BYTES + 1)
        recommended, reasons = recommend_central_server(scan(tmp_path))
        assert recommended is True
        assert any("exceed" in r for r in reasons)

    def test_recommends_on_many_projects(self, tmp_path):
        for i in range(MANY_PROJECTS):
            make_project(tmp_path / f"p{i}")
        recommended, reasons = recommend_central_server(scan(tmp_path))
        assert recommended is True
        assert any("projects on this machine" in r for r in reasons)

    def test_empty_scan_recommends_nothing(self):
        assert recommend_central_server([]) == (False, [])
