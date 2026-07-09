# Copyright CONFLICT LLC 2026 (weareconflict.com)
"""
Tests for navegador.cluster.shards — LRU shard load/unload under ceilings.

Real embedded stores: three seeded shard RDB files are paged in and out by
a ShardManager and the data is re-queried after eviction to prove persisted
state survives the unload/reload cycle.
"""

import pytest

from navegador.cluster.shards import ShardManager
from navegador.graph.store import GraphStore

# ── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def shard_sources(tmp_path_factory):
    """Three repo roots, each with a seeded .navegador/graph.db RDB."""
    sources = {}
    for name in ("alpha", "beta", "gamma"):
        root = tmp_path_factory.mktemp(f"shard-{name}")
        store = GraphStore.sqlite(str(root / ".navegador" / "graph.db"))
        store.create_node("Function", {"name": f"fn_{name}", "file_path": "app.py"})
        store.close()
        sources[name] = root
    return sources


def _fn_name(store: GraphStore) -> str:
    result = store.query("MATCH (f:Function) RETURN f.name")
    return result.result_set[0][0]


# ── Load on demand + LRU eviction ──────────────────────────────────────────


class TestLoadAndEvict:
    def test_loads_on_demand_and_evicts_lru(self, shard_sources):
        with ShardManager(shard_sources, max_resident=2) as shards:
            assert shards.resident() == []

            assert _fn_name(shards.get("alpha")) == "fn_alpha"
            assert _fn_name(shards.get("beta")) == "fn_beta"
            assert shards.resident() == ["alpha", "beta"]

            # Third load exceeds the ceiling → alpha (LRU) is evicted
            assert _fn_name(shards.get("gamma")) == "fn_gamma"
            assert shards.resident() == ["beta", "gamma"]

    def test_access_touches_lru_order(self, shard_sources):
        with ShardManager(shard_sources, max_resident=2) as shards:
            shards.get("alpha")
            shards.get("beta")
            shards.get("alpha")  # touch → beta is now least recently used
            shards.get("gamma")
            assert shards.resident() == ["alpha", "gamma"]

    def test_evicted_shard_reloads_with_data_intact(self, shard_sources):
        with ShardManager(shard_sources, max_resident=1) as shards:
            shards.get("alpha")
            shards.get("beta")  # evicts alpha
            assert shards.resident() == ["beta"]
            # Transparent reload — persisted state survived the eviction
            assert _fn_name(shards.get("alpha")) == "fn_alpha"
            assert shards.resident() == ["alpha"]

    def test_query_routes_through_manager(self, shard_sources):
        with ShardManager(shard_sources, max_resident=2) as shards:
            result = shards.query("beta", "MATCH (f:Function) RETURN f.name")
            assert result.result_set == [["fn_beta"]]

    def test_unknown_shard_raises(self, shard_sources):
        with ShardManager(shard_sources) as shards:
            with pytest.raises(KeyError, match="delta"):
                shards.get("delta")

    def test_close_all(self, shard_sources):
        shards = ShardManager(shard_sources, max_resident=3)
        shards.get("alpha")
        shards.get("beta")
        shards.close_all()
        assert shards.resident() == []

    def test_max_resident_must_be_positive(self, shard_sources):
        with pytest.raises(ValueError):
            ShardManager(shard_sources, max_resident=0)


# ── Memory ceiling ─────────────────────────────────────────────────────────


class TestMemoryCeiling:
    def test_memory_usage_reports_resident_shards(self, shard_sources):
        with ShardManager(shard_sources, max_resident=2) as shards:
            shards.get("alpha")
            usage = shards.memory_usage()
            assert set(usage) == {"alpha"}
            assert usage["alpha"] > 0

    def test_memory_ceiling_evicts_down(self, shard_sources):
        # An empty redislite instance uses ~3MB — a 1MB ceiling forces
        # eviction down to the minimum of one resident shard.
        with ShardManager(shard_sources, max_resident=3, max_memory_mb=1) as shards:
            shards.get("alpha")
            shards.get("beta")
            assert shards.resident() == ["beta"]

    def test_no_ceiling_keeps_all_resident(self, shard_sources):
        with ShardManager(shard_sources, max_resident=3) as shards:
            for name in ("alpha", "beta", "gamma"):
                shards.get(name)
            assert len(shards.resident()) == 3


# ── Config loading ─────────────────────────────────────────────────────────


class TestFromConfig:
    def test_reads_cluster_ceilings(self, tmp_path):
        nav = tmp_path / ".navegador"
        nav.mkdir()
        (nav / "config.toml").write_text(
            "[cluster]\nenabled = false\nmax_resident_shards = 2\nmax_shard_memory_mb = 64\n"
        )
        shards = ShardManager.from_config({}, project_dir=tmp_path)
        assert shards.max_resident == 2
        assert shards.max_memory_mb == 64.0

    def test_defaults_without_config(self, tmp_path):
        shards = ShardManager.from_config({}, project_dir=tmp_path)
        assert shards.max_resident == 4
        assert shards.max_memory_mb is None

    def test_generated_config_parses(self, tmp_path):
        import tomllib

        from navegador.config import init_project

        nav_dir = init_project(tmp_path)
        config = tomllib.loads((nav_dir / "config.toml").read_text())
        assert "cluster" in config  # commented shard keys must not break parsing
