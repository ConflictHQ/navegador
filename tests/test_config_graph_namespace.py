"""
Overriding the server must not discard the namespace (#178).

`redis_url` says *which server*; `graph` says *which namespace on it*. They
were resolved as one decision, so `NAVEGADOR_REDIS_URL` returned before project
config was ever read and a project with a configured graph landed on the
default one.

The reason this is worth its own test file: for an MCP client the failure is
invisible. The server starts, every tool responds, and the graph is empty —
which reads as "this codebase has nothing in it" rather than as a
misconfiguration. Same symptom class as #169 and #170, one layer up.
"""

import pytest

from navegador.config import resolve_storage


@pytest.fixture(autouse=True)
def no_ambient_env(monkeypatch):
    """
    Storage resolution reads the environment, so a developer's own exports
    would otherwise decide the result. This is the mistake that made two 1.5
    LLM tests pass locally and fail in CI.
    """
    for name in ("NAVEGADOR_REDIS_URL", "NAVEGADOR_DB", "NAVEGADOR_GRAPH"):
        monkeypatch.delenv(name, raising=False)


def project(tmp_path, **storage):
    root = tmp_path / "proj"
    (root / ".navegador").mkdir(parents=True, exist_ok=True)
    body = "[storage]\n" + "".join(f'{k} = "{v}"\n' for k, v in storage.items())
    (root / ".navegador" / "config.toml").write_text(body)
    return root


class TestNamespaceSurvivesAConnectionOverride:
    def test_env_url_keeps_the_configured_graph(self, tmp_path, monkeypatch):
        """The reported bug, exactly."""
        root = project(
            tmp_path,
            backend="redis",
            redis_url="redis://127.0.0.1:6379",
            graph="navegador_myproj",
        )
        monkeypatch.setenv("NAVEGADOR_REDIS_URL", "redis://127.0.0.1:6379")

        config = resolve_storage(target=root)
        assert config.graph_name == "navegador_myproj"
        assert "env var" in config.source, "the URL should still come from the environment"

    def test_explicit_redis_url_keeps_the_configured_graph(self, tmp_path):
        root = project(
            tmp_path, backend="redis", redis_url="redis://a:6379", graph="navegador_myproj"
        )
        config = resolve_storage(redis_url="redis://elsewhere:6379", target=root)
        assert config.graph_name == "navegador_myproj"
        assert config.redis_url == "redis://elsewhere:6379"

    def test_env_db_keeps_the_configured_graph(self, tmp_path, monkeypatch):
        root = project(tmp_path, backend="redis", graph="navegador_myproj")
        monkeypatch.setenv("NAVEGADOR_DB", str(tmp_path / "other.db"))

        config = resolve_storage(target=root)
        assert config.graph_name == "navegador_myproj"
        assert config.backend == "embedded"


class TestPrecedence:
    def test_explicit_graph_beats_configuration(self, tmp_path):
        root = project(tmp_path, backend="redis", graph="from_config")
        assert resolve_storage(target=root, graph_name="from_flag").graph_name == "from_flag"

    def test_env_graph_beats_configuration(self, tmp_path, monkeypatch):
        root = project(tmp_path, backend="redis", graph="from_config")
        monkeypatch.setenv("NAVEGADOR_GRAPH", "from_env")
        assert resolve_storage(target=root).graph_name == "from_env"

    def test_flag_beats_env(self, tmp_path, monkeypatch):
        root = project(tmp_path, backend="redis", graph="from_config")
        monkeypatch.setenv("NAVEGADOR_GRAPH", "from_env")
        assert resolve_storage(target=root, graph_name="from_flag").graph_name == "from_flag"

    def test_config_is_used_when_nothing_overrides_it(self, tmp_path):
        root = project(tmp_path, backend="redis", graph="from_config")
        assert resolve_storage(target=root).graph_name == "from_config"


class TestNoNamespaceConfigured:
    def test_absent_graph_key_stays_absent(self, tmp_path):
        """No configured namespace must not invent one."""
        root = project(tmp_path, backend="redis", redis_url="redis://127.0.0.1:6379")
        assert resolve_storage(target=root).graph_name == ""

    def test_no_project_config_at_all(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NAVEGADOR_REDIS_URL", "redis://127.0.0.1:6379")
        config = resolve_storage(target=tmp_path)
        assert config.backend == "redis"
        assert config.graph_name == ""

    def test_provenance_still_names_the_deciding_layer(self, tmp_path, monkeypatch):
        """
        The namespace is merged in, but `source` must still describe where the
        *connection* came from, or doctor's explanation becomes a fiction.
        """
        root = project(tmp_path, backend="redis", graph="navegador_myproj")
        monkeypatch.setenv("NAVEGADOR_REDIS_URL", "redis://127.0.0.1:6379")
        assert "NAVEGADOR_REDIS_URL" in resolve_storage(target=root).source
