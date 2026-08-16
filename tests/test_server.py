# Copyright CONFLICT LLC 2026 (weareconflict.com)
"""
Tests for navegador.server — native FalkorDB install and configuration.

Covers the pure decisions: which module asset a platform needs, what the
generated redis.conf and service definitions contain, and how module versions
decode. Downloading and service registration are exercised by
``navegador server install`` itself, not here.
"""

import pytest

from navegador import server as srv


class TestModuleAsset:
    def test_macos_arm64(self):
        assert srv.module_asset_name("Darwin", "arm64") == "falkordb-macos-arm64v8.so"

    def test_macos_intel_is_refused_with_a_way_forward(self):
        with pytest.raises(srv.ServerError, match="arm64"):
            srv.module_asset_name("Darwin", "x86_64")

    def test_linux_x64(self, monkeypatch):
        monkeypatch.setattr(srv, "_is_musl", lambda: False)
        assert srv.module_asset_name("Linux", "x86_64") == "falkordb-x64.so"

    def test_linux_arm64(self, monkeypatch):
        monkeypatch.setattr(srv, "_is_musl", lambda: False)
        assert srv.module_asset_name("Linux", "aarch64") == "falkordb-arm64v8.so"

    def test_alpine_gets_the_musl_build(self, monkeypatch):
        monkeypatch.setattr(srv, "_is_musl", lambda: True)
        assert srv.module_asset_name("Linux", "x86_64") == "falkordb-alpine-x64.so"

    def test_windows_points_at_wsl2(self):
        with pytest.raises(srv.ServerError, match="WSL2"):
            srv.module_asset_name("Windows", "amd64")


class TestPaths:
    def test_home_honours_override(self, monkeypatch, tmp_path):
        monkeypatch.setenv("NAVEGADOR_HOME", str(tmp_path))
        assert srv.server_home() == tmp_path

    def test_layout_is_under_home(self, monkeypatch, tmp_path):
        monkeypatch.setenv("NAVEGADOR_HOME", str(tmp_path))
        p = srv.paths()
        assert p.module == tmp_path / "lib" / "falkordb.so"
        assert p.data == tmp_path / "data"
        assert p.config == tmp_path / "redis.conf"

    def test_mkdirs_creates_the_tree(self, monkeypatch, tmp_path):
        monkeypatch.setenv("NAVEGADOR_HOME", str(tmp_path / "fresh"))
        p = srv.paths()
        p.mkdirs()
        assert p.lib.is_dir()
        assert p.data.is_dir()
        assert p.logs.is_dir()


class TestRenderConfig:
    @pytest.fixture
    def conf(self, monkeypatch, tmp_path):
        monkeypatch.setenv("NAVEGADOR_HOME", str(tmp_path))
        return srv.render_config(srv.paths())

    def test_loads_the_module(self, conf, tmp_path):
        assert f"loadmodule {tmp_path / 'lib' / 'falkordb.so'}" in conf

    def test_never_evicts(self, conf):
        """Eviction would silently drop part of a graph rather than shed cache."""
        assert "maxmemory-policy noeviction" in conf

    def test_unlimited_memory_by_default(self, conf):
        assert "maxmemory 0" in conf

    def test_memory_cap_is_applied_when_asked(self, monkeypatch, tmp_path):
        monkeypatch.setenv("NAVEGADOR_HOME", str(tmp_path))
        conf = srv.render_config(srv.paths(), maxmemory_mb=2048)
        assert "maxmemory 2048mb" in conf
        assert "maxmemory-policy noeviction" in conf

    def test_durability_is_on(self, conf):
        assert "appendonly yes" in conf
        assert "save " in conf

    def test_appendonly_can_be_disabled(self, monkeypatch, tmp_path):
        monkeypatch.setenv("NAVEGADOR_HOME", str(tmp_path))
        assert "appendonly no" in srv.render_config(srv.paths(), appendonly=False)

    def test_exposes_a_unix_socket(self, conf):
        assert "unixsocket " in conf

    def test_binds_locally_by_default(self, conf):
        assert "bind 127.0.0.1" in conf
        assert "protected-mode yes" in conf

    def test_port_is_configurable(self, monkeypatch, tmp_path):
        monkeypatch.setenv("NAVEGADOR_HOME", str(tmp_path))
        assert "port 6390" in srv.render_config(srv.paths(), port=6390)

    def test_write_config_creates_the_file(self, monkeypatch, tmp_path):
        monkeypatch.setenv("NAVEGADOR_HOME", str(tmp_path))
        path = srv.write_config(srv.paths())
        assert path.is_file()
        assert "loadmodule" in path.read_text()


class TestServiceDefinitions:
    def test_launch_agent_runs_the_server(self, monkeypatch, tmp_path):
        monkeypatch.setenv("NAVEGADOR_HOME", str(tmp_path))
        plist = srv.render_launch_agent("/usr/bin/redis-server", srv.paths())
        assert srv.SERVICE_LABEL in plist
        assert "/usr/bin/redis-server" in plist
        assert "<key>RunAtLoad</key>" in plist
        assert "<key>KeepAlive</key>" in plist

    def test_launch_agent_is_valid_plist(self, monkeypatch, tmp_path):
        import plistlib

        monkeypatch.setenv("NAVEGADOR_HOME", str(tmp_path))
        plist = srv.render_launch_agent("/usr/bin/redis-server", srv.paths())
        parsed = plistlib.loads(plist.encode())
        assert parsed["Label"] == srv.SERVICE_LABEL
        assert parsed["ProgramArguments"][0] == "/usr/bin/redis-server"

    def test_systemd_unit_restarts_on_failure(self, monkeypatch, tmp_path):
        monkeypatch.setenv("NAVEGADOR_HOME", str(tmp_path))
        unit = srv.render_systemd_unit("/usr/bin/redis-server", srv.paths())
        assert "Restart=on-failure" in unit
        assert "/usr/bin/redis-server" in unit


class TestModuleVersionDecoding:
    @pytest.mark.parametrize(
        ("encoded", "expected"),
        [(42003, "4.20.3"), (41811, "4.18.11"), (41602, "4.16.2")],
    )
    def test_decodes_falkordb_versions(self, encoded, expected):
        assert srv._decode_module_version(encoded) == expected

    def test_passes_through_unparseable(self):
        assert srv._decode_module_version("weird") == "weird"


class TestManifest:
    def test_missing_manifest_is_empty(self, monkeypatch, tmp_path):
        monkeypatch.setenv("NAVEGADOR_HOME", str(tmp_path))
        assert srv.read_manifest(srv.paths()) == {}

    def test_round_trip(self, monkeypatch, tmp_path):
        monkeypatch.setenv("NAVEGADOR_HOME", str(tmp_path))
        p = srv.paths()
        p.mkdirs()
        srv.write_manifest(p, {"port": 6379})
        assert srv.read_manifest(p)["port"] == 6379

    def test_corrupt_manifest_is_empty(self, monkeypatch, tmp_path):
        monkeypatch.setenv("NAVEGADOR_HOME", str(tmp_path))
        p = srv.paths()
        p.mkdirs()
        p.manifest.write_text("{not json", encoding="utf-8")
        assert srv.read_manifest(p) == {}


class TestProbe:
    def test_unreachable_server_reports_cleanly(self):
        """A probe must never raise — it is the thing that diagnoses failures."""
        info = srv.probe("redis://127.0.0.1:1")
        assert info["reachable"] is False
        assert info["graph_module"] is False
        assert "error" in info
