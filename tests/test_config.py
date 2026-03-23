"""Tests for navegador.config — get_store() env var resolution and init_project()."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch


class TestGetStore:
    def test_explicit_path_returns_sqlite(self):
        with patch("navegador.graph.GraphStore") as mock_gs:
            mock_gs.sqlite.return_value = MagicMock()
            from navegador.config import get_store

            get_store("/tmp/test.db")
            mock_gs.sqlite.assert_called_once_with("/tmp/test.db")

    def test_redis_url_env_returns_redis(self, monkeypatch):
        monkeypatch.setenv("NAVEGADOR_REDIS_URL", "redis://localhost:6379")
        monkeypatch.delenv("NAVEGADOR_DB", raising=False)
        with patch("navegador.graph.GraphStore") as mock_gs:
            mock_gs.redis.return_value = MagicMock()
            # Re-import to pick up env changes
            import importlib

            import navegador.config as cfg
            importlib.reload(cfg)
            cfg.get_store()
            mock_gs.redis.assert_called_once_with("redis://localhost:6379")

    def test_db_env_returns_sqlite(self, monkeypatch):
        monkeypatch.delenv("NAVEGADOR_REDIS_URL", raising=False)
        monkeypatch.setenv("NAVEGADOR_DB", "/tmp/custom.db")
        with patch("navegador.graph.GraphStore") as mock_gs:
            mock_gs.sqlite.return_value = MagicMock()
            import importlib

            import navegador.config as cfg
            importlib.reload(cfg)
            cfg.get_store()
            mock_gs.sqlite.assert_called_once_with("/tmp/custom.db")

    def test_default_sqlite_path(self, monkeypatch):
        monkeypatch.delenv("NAVEGADOR_REDIS_URL", raising=False)
        monkeypatch.delenv("NAVEGADOR_DB", raising=False)
        with patch("navegador.graph.GraphStore") as mock_gs:
            mock_gs.sqlite.return_value = MagicMock()
            import importlib

            import navegador.config as cfg
            importlib.reload(cfg)
            cfg.get_store()
            mock_gs.sqlite.assert_called_once_with(".navegador/graph.db")

    def test_redis_takes_precedence_over_db_env(self, monkeypatch):
        monkeypatch.setenv("NAVEGADOR_REDIS_URL", "redis://myhost:6379")
        monkeypatch.setenv("NAVEGADOR_DB", "/tmp/other.db")
        with patch("navegador.graph.GraphStore") as mock_gs:
            mock_gs.redis.return_value = MagicMock()
            import importlib

            import navegador.config as cfg
            importlib.reload(cfg)
            cfg.get_store()
            mock_gs.redis.assert_called_once_with("redis://myhost:6379")
            mock_gs.sqlite.assert_not_called()


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
