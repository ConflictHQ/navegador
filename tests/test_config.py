"""Tests for navegador.config — layered storage resolution and init_project()."""

import tempfile
from pathlib import Path

import pytest

from navegador.config import (
    DEFAULT_DB_PATH,
    DEFAULT_REDIS_URL,
    find_project_config,
    init_project,
    resolve_storage,
    user_config_path,
)


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch, tmp_path):
    """
    Keep resolution tests off the developer's real machine configuration.

    Without this, a user-level ~/.config/navegador/config.toml — exactly what
    the centralized setup installs — would silently decide these tests.
    """
    monkeypatch.delenv("NAVEGADOR_REDIS_URL", raising=False)
    monkeypatch.delenv("NAVEGADOR_DB", raising=False)
    monkeypatch.setenv("NAVEGADOR_CONFIG", str(tmp_path / "absent" / "config.toml"))


def write_project_config(root: Path, body: str) -> Path:
    nav = root / ".navegador"
    nav.mkdir(parents=True, exist_ok=True)
    path = nav / "config.toml"
    path.write_text(body, encoding="utf-8")
    return path


class TestResolveStorage:
    def test_explicit_db_wins(self, tmp_path):
        write_project_config(tmp_path, '[storage]\nbackend = "redis"\n')
        cfg = resolve_storage(db_path="/tmp/test.db", target=tmp_path)
        assert cfg.backend == "embedded"
        assert cfg.db_path == "/tmp/test.db"
        assert cfg.source == "--db"

    def test_explicit_redis_url_wins(self, tmp_path):
        write_project_config(tmp_path, '[storage]\nbackend = "sqlite"\n')
        cfg = resolve_storage(redis_url="redis://explicit:6379", target=tmp_path)
        assert cfg.backend == "redis"
        assert cfg.redis_url == "redis://explicit:6379"

    def test_env_redis_beats_project_config(self, tmp_path, monkeypatch):
        write_project_config(tmp_path, '[storage]\nbackend = "sqlite"\n')
        monkeypatch.setenv("NAVEGADOR_REDIS_URL", "redis://envhost:6379")
        cfg = resolve_storage(target=tmp_path)
        assert cfg.backend == "redis"
        assert cfg.redis_url == "redis://envhost:6379"
        assert "env var" in cfg.source

    def test_env_redis_beats_env_db(self, monkeypatch):
        monkeypatch.setenv("NAVEGADOR_REDIS_URL", "redis://myhost:6379")
        monkeypatch.setenv("NAVEGADOR_DB", "/tmp/other.db")
        cfg = resolve_storage()
        assert cfg.backend == "redis"
        assert cfg.redis_url == "redis://myhost:6379"

    def test_project_config_redis_is_honoured(self, tmp_path):
        """The #169 regression: a declared redis backend must actually be used."""
        write_project_config(
            tmp_path,
            '[storage]\nbackend = "redis"\nredis_url = "redis://localhost:6379"\n',
        )
        cfg = resolve_storage(target=tmp_path)
        assert cfg.backend == "redis"
        assert cfg.redis_url == "redis://localhost:6379"
        assert "project config" in cfg.source

    def test_project_config_redis_defaults_url(self, tmp_path):
        write_project_config(tmp_path, '[storage]\nbackend = "redis"\n')
        cfg = resolve_storage(target=tmp_path)
        assert cfg.redis_url == DEFAULT_REDIS_URL

    def test_project_config_found_from_subdirectory(self, tmp_path):
        write_project_config(tmp_path, '[storage]\nbackend = "redis"\n')
        nested = tmp_path / "src" / "deep"
        nested.mkdir(parents=True)
        cfg = resolve_storage(target=nested)
        assert cfg.backend == "redis"

    def test_project_config_resolved_from_target_not_cwd(self, tmp_path, monkeypatch):
        """#170: naming a repo elsewhere must use that repo's config."""
        repo = tmp_path / "repo"
        repo.mkdir()
        write_project_config(repo, '[storage]\nbackend = "redis"\n')
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)

        assert resolve_storage(target=repo).backend == "redis"
        # …and without a target, the config-less cwd falls through to default.
        assert resolve_storage().backend == "embedded"

    def test_relative_db_path_anchored_to_project_root(self, tmp_path, monkeypatch):
        write_project_config(
            tmp_path, '[storage]\nbackend = "sqlite"\ndb_path = ".navegador/graph.db"\n'
        )
        nested = tmp_path / "sub"
        nested.mkdir()
        monkeypatch.chdir(nested)
        cfg = resolve_storage(target=nested)
        assert Path(cfg.db_path) == tmp_path / ".navegador" / "graph.db"

    def test_user_config_used_when_no_project_config(self, tmp_path, monkeypatch):
        user_cfg = tmp_path / "user" / "config.toml"
        user_cfg.parent.mkdir(parents=True)
        user_cfg.write_text(
            '[storage]\nbackend = "redis"\nredis_url = "redis://central:6379"\n',
            encoding="utf-8",
        )
        monkeypatch.setenv("NAVEGADOR_CONFIG", str(user_cfg))
        empty = tmp_path / "no-config"
        empty.mkdir()
        cfg = resolve_storage(target=empty)
        assert cfg.backend == "redis"
        assert cfg.redis_url == "redis://central:6379"
        assert "user config" in cfg.source

    def test_project_config_beats_user_config(self, tmp_path, monkeypatch):
        user_cfg = tmp_path / "user" / "config.toml"
        user_cfg.parent.mkdir(parents=True)
        user_cfg.write_text('[storage]\nbackend = "redis"\n', encoding="utf-8")
        monkeypatch.setenv("NAVEGADOR_CONFIG", str(user_cfg))
        repo = tmp_path / "repo"
        repo.mkdir()
        write_project_config(repo, '[storage]\nbackend = "sqlite"\n')
        assert resolve_storage(target=repo).backend == "embedded"

    def test_default_when_nothing_configured(self, tmp_path):
        empty = tmp_path / "bare"
        empty.mkdir()
        cfg = resolve_storage(target=empty)
        assert cfg.backend == "embedded"
        assert cfg.db_path == DEFAULT_DB_PATH
        assert cfg.source == "default"

    def test_unknown_backend_falls_through(self, tmp_path):
        write_project_config(tmp_path, '[storage]\nbackend = "postgres"\n')
        cfg = resolve_storage(target=tmp_path)
        assert cfg.backend == "embedded"
        assert cfg.source == "default"

    def test_malformed_toml_falls_through(self, tmp_path):
        write_project_config(tmp_path, "[storage\nbackend = broken")
        cfg = resolve_storage(target=tmp_path)
        assert cfg.source == "default"

    @pytest.mark.parametrize("alias", ["sqlite", "embedded", "falkordblite", "local", "file"])
    def test_embedded_aliases(self, tmp_path, alias):
        write_project_config(tmp_path, f'[storage]\nbackend = "{alias}"\n')
        assert resolve_storage(target=tmp_path).backend == "embedded"

    @pytest.mark.parametrize("alias", ["redis", "falkordb", "central", "centralized"])
    def test_redis_aliases(self, tmp_path, alias):
        write_project_config(tmp_path, f'[storage]\nbackend = "{alias}"\n')
        assert resolve_storage(target=tmp_path).backend == "redis"

    def test_describe_names_the_source(self, tmp_path):
        write_project_config(tmp_path, '[storage]\nbackend = "redis"\n')
        described = resolve_storage(target=tmp_path).describe()
        assert DEFAULT_REDIS_URL in described
        assert "project config" in described


class TestConfigDiscovery:
    def test_find_project_config_returns_none_without_one(self, tmp_path):
        assert find_project_config(tmp_path) is None

    def test_find_project_config_walks_up(self, tmp_path):
        expected = write_project_config(tmp_path, '[storage]\nbackend = "sqlite"\n')
        nested = tmp_path / "a" / "b" / "c"
        nested.mkdir(parents=True)
        assert find_project_config(nested) == expected

    def test_find_project_config_accepts_a_file_target(self, tmp_path):
        expected = write_project_config(tmp_path, '[storage]\nbackend = "sqlite"\n')
        f = tmp_path / "main.py"
        f.write_text("x = 1", encoding="utf-8")
        assert find_project_config(f) == expected

    def test_user_config_path_honours_override(self, monkeypatch):
        monkeypatch.setenv("NAVEGADOR_CONFIG", "/tmp/somewhere/config.toml")
        assert user_config_path() == Path("/tmp/somewhere/config.toml")

    def test_user_config_path_honours_xdg(self, monkeypatch):
        monkeypatch.delenv("NAVEGADOR_CONFIG", raising=False)
        monkeypatch.setenv("XDG_CONFIG_HOME", "/tmp/xdg")
        assert user_config_path() == Path("/tmp/xdg/navegador/config.toml")


class TestInitProjectRoundTrip:
    """init_project writes config that resolve_storage must actually honour."""

    def test_redis_init_resolves_to_redis(self, tmp_path):
        init_project(tmp_path, storage="redis", redis_url="redis://localhost:6379")
        cfg = resolve_storage(target=tmp_path)
        assert cfg.backend == "redis"
        assert cfg.redis_url == "redis://localhost:6379"

    def test_sqlite_init_resolves_to_embedded(self, tmp_path):
        init_project(tmp_path, storage="sqlite")
        cfg = resolve_storage(target=tmp_path)
        assert cfg.backend == "embedded"
        assert Path(cfg.db_path) == tmp_path / ".navegador" / "graph.db"


class TestInitProject:
    def test_creates_navegador_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            from navegador.config import init_project

            nav_dir = init_project(tmpdir)
            assert nav_dir.exists()
            assert nav_dir.name == ".navegador"

    def test_creates_env_example(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            from navegador.config import init_project

            nav_dir = init_project(tmpdir)
            env_example = nav_dir / ".env.example"
            assert env_example.exists()
            content = env_example.read_text()
            assert "NAVEGADOR_DB" in content
            assert "NAVEGADOR_REDIS_URL" in content

    def test_does_not_overwrite_existing_env_example(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            from navegador.config import init_project

            nav_dir = Path(tmpdir) / ".navegador"
            nav_dir.mkdir()
            env_example = nav_dir / ".env.example"
            env_example.write_text("custom content")
            init_project(tmpdir)
            assert env_example.read_text() == "custom content"

    def test_creates_gitignore_if_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            from navegador.config import init_project

            init_project(tmpdir)
            gitignore = Path(tmpdir) / ".gitignore"
            assert gitignore.exists()
            assert ".navegador/" in gitignore.read_text()

    def test_appends_to_existing_gitignore(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            gitignore = Path(tmpdir) / ".gitignore"
            gitignore.write_text("*.pyc\n__pycache__/\n")
            from navegador.config import init_project

            init_project(tmpdir)
            content = gitignore.read_text()
            assert "*.pyc" in content
            assert ".navegador/" in content

    def test_does_not_duplicate_gitignore_entry(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            gitignore = Path(tmpdir) / ".gitignore"
            gitignore.write_text(".navegador/\n")
            from navegador.config import init_project

            init_project(tmpdir)
            content = gitignore.read_text()
            assert content.count(".navegador/") == 1

    def test_returns_nav_dir_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            from navegador.config import init_project

            result = init_project(tmpdir)
            assert isinstance(result, Path)
            assert result == Path(tmpdir).resolve() / ".navegador"

    def test_creates_config_toml(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            from navegador.config import init_project

            nav_dir = init_project(tmpdir)
            config = nav_dir / "config.toml"
            assert config.exists()
            content = config.read_text()
            assert "[storage]" in content
            assert "[llm]" in content
            assert "[cluster]" in content

    def test_config_toml_sqlite_defaults(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            from navegador.config import init_project

            nav_dir = init_project(tmpdir)
            content = (nav_dir / "config.toml").read_text()
            assert 'backend = "sqlite"' in content
            assert "db_path" in content

    def test_config_toml_redis_mode(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            from navegador.config import init_project

            nav_dir = init_project(tmpdir, storage="redis", redis_url="redis://host:6379")
            content = (nav_dir / "config.toml").read_text()
            assert 'backend = "redis"' in content
            assert 'redis_url = "redis://host:6379"' in content

    def test_config_toml_llm_settings(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            from navegador.config import init_project

            nav_dir = init_project(tmpdir, llm_provider="anthropic", llm_model="claude-sonnet-4-6")
            content = (nav_dir / "config.toml").read_text()
            assert 'provider = "anthropic"' in content
            assert 'model = "claude-sonnet-4-6"' in content

    def test_config_toml_cluster_enabled(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            from navegador.config import init_project

            nav_dir = init_project(tmpdir, cluster=True)
            content = (nav_dir / "config.toml").read_text()
            assert "enabled = true" in content

    def test_config_toml_cluster_disabled_by_default(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            from navegador.config import init_project

            nav_dir = init_project(tmpdir)
            content = (nav_dir / "config.toml").read_text()
            assert "enabled = false" in content
