# Copyright CONFLICT LLC 2026 (weareconflict.com)
"""
Regression tests for #164 and #165 — LLM selection and generated Cypher.

#164: `navegador init` wrote `[llm] provider` and `model` and nothing read
them; provider discovery selected Anthropic whenever the SDK was importable,
regardless of credentials; and the Anthropic default was pinned to
`claude-3-5-haiku-20241022`, retired 2026-02-19, so the fallback path called a
model that returns 404.

#165: generated Cypher used `(n:Class|Function)`, which FalkorDB rejects — one
label per node pattern. Alternative *relationship* types are valid and must be
left alone.
"""

import sys
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from navegador.config import resolve_llm
from navegador.intelligence.nlp import NLPEngine
from navegador.llm import AnthropicProvider, auto_provider, provider_available


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch, tmp_path):
    for var in (
        "NAVEGADOR_LLM_PROVIDER",
        "NAVEGADOR_LLM_MODEL",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "OPENAI_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("NAVEGADOR_CONFIG", str(tmp_path / "absent.toml"))


@contextmanager
def sdk(**present):
    """
    State which provider SDKs are importable.

    The SDKs live in the optional `[llm]` extra, so CI has none of them and a
    developer machine usually has several. Availability tests that inherit that
    difference test the machine rather than the code.
    """
    stubs = {name: (MagicMock() if ok else None) for name, ok in present.items()}
    with patch.dict(sys.modules, stubs):
        yield


@contextmanager
def ollama_server(running: bool):
    """
    State whether a local Ollama server answers.

    Ollama needs no credential, so reachability is the whole of its
    availability — and a developer running `ollama serve` would otherwise see
    different provider selection than CI.
    """

    def urlopen(*_args, **_kwargs):
        if not running:
            raise OSError("connection refused")
        return MagicMock()

    with patch("urllib.request.urlopen", urlopen):
        yield


def write_config(root, body):
    nav = root / ".navegador"
    nav.mkdir(parents=True, exist_ok=True)
    (nav / "config.toml").write_text(body, encoding="utf-8")
    return root


# ── #164: configuration is read ────────────────────────────────────────────


class TestLLMConfigResolution:
    def test_project_config_is_honoured(self, tmp_path):
        """The regression: [llm] was written by init and never read."""
        write_config(tmp_path, '[llm]\nprovider = "openai"\nmodel = "gpt-4o"\n')
        cfg = resolve_llm(target=tmp_path)
        assert cfg.provider == "openai"
        assert cfg.model == "gpt-4o"
        assert "project config" in cfg.source

    def test_provider_only_is_enough(self, tmp_path):
        write_config(tmp_path, '[llm]\nprovider = "ollama"\nmodel = ""\n')
        assert resolve_llm(target=tmp_path).provider == "ollama"

    def test_empty_section_falls_through(self, tmp_path):
        write_config(tmp_path, '[llm]\nprovider = ""\nmodel = ""\n')
        assert resolve_llm(target=tmp_path).source == "default"

    def test_command_line_wins(self, tmp_path):
        write_config(tmp_path, '[llm]\nprovider = "openai"\n')
        cfg = resolve_llm(provider="anthropic", target=tmp_path)
        assert cfg.provider == "anthropic"
        assert cfg.source == "command line"

    def test_environment_beats_config(self, tmp_path, monkeypatch):
        write_config(tmp_path, '[llm]\nprovider = "openai"\n')
        monkeypatch.setenv("NAVEGADOR_LLM_PROVIDER", "ollama")
        assert resolve_llm(target=tmp_path).provider == "ollama"

    def test_user_config_used_when_no_project_config(self, tmp_path, monkeypatch):
        user = tmp_path / "user.toml"
        user.write_text('[llm]\nprovider = "openai"\nmodel = "gpt-4o"\n', encoding="utf-8")
        monkeypatch.setenv("NAVEGADOR_CONFIG", str(user))
        bare = tmp_path / "bare"
        bare.mkdir()
        cfg = resolve_llm(target=bare)
        assert cfg.provider == "openai"
        assert "user config" in cfg.source

    def test_nothing_configured_is_auto(self, tmp_path):
        bare = tmp_path / "bare"
        bare.mkdir()
        cfg = resolve_llm(target=bare)
        assert cfg.provider == ""
        assert cfg.source == "default"


# ── #164: availability means credentials, not imports ──────────────────────


class TestProviderAvailability:
    def test_sdk_without_credential_is_unavailable(self):
        """
        The reported failure: the SDK is installed, no key is exported, and
        Anthropic was selected anyway — failing later with a raw auth error.
        """
        with sdk(anthropic=True):
            available, why = provider_available("anthropic")
        assert available is False
        assert "ANTHROPIC_API_KEY" in why

    def test_credential_makes_it_available(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        with sdk(anthropic=True):
            assert provider_available("anthropic")[0] is True

    def test_auth_token_also_counts(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "token")
        with sdk(anthropic=True):
            assert provider_available("anthropic")[0] is True

    def test_missing_sdk_is_reported_as_such(self):
        """A missing package and a missing key are different problems."""
        with sdk(anthropic=False):
            available, why = provider_available("anthropic")
        assert available is False
        assert "pip install anthropic" in why

    def test_a_local_provider_needs_a_running_server(self):
        """Ollama takes no credential, so reachability is its availability."""
        with sdk(ollama=True), ollama_server(running=False):
            available, why = provider_available("ollama")
        assert available is False
        assert "no Ollama server" in why

    def test_auto_provider_skips_credential_less_providers(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        with sdk(anthropic=True, openai=True, ollama=False):
            provider = auto_provider()
        assert provider.name == "openai"

    def test_auto_provider_error_explains_each_provider(self):
        with sdk(anthropic=True, openai=True, ollama=True), ollama_server(running=False):
            with pytest.raises(RuntimeError) as excinfo:
                auto_provider()
        message = str(excinfo.value)
        assert "ANTHROPIC_API_KEY" in message
        assert "config.toml" in message


class TestAnthropicDefaultModel:
    def test_default_is_not_the_retired_model(self):
        """`claude-3-5-haiku-20241022` was retired 2026-02-19 and returns 404."""
        assert AnthropicProvider._DEFAULT_MODEL != "claude-3-5-haiku-20241022"

    def test_default_is_a_current_alias(self):
        import re

        model = AnthropicProvider._DEFAULT_MODEL
        assert model == "claude-opus-5"
        # Aliases are complete as written. An appended 8-digit date turns the
        # alias into a dated snapshot ID — a different model, and the shape the
        # retired default had.
        assert not re.search(r"-\d{8}$", model)


# ── #165: generated Cypher must be executable ──────────────────────────────


class TestAlternativeLabelRewriting:
    rewrite = staticmethod(NLPEngine._rewrite_alternative_labels)

    def test_the_reported_query_is_repaired(self):
        original = (
            "MATCH (f:File)-[:CONTAINS]->(c:Class|Function)"
            "-[:REFERENCES]->(concept:Concept) RETURN f.path"
        )
        result = self.rewrite(original)
        assert "Class|Function" not in result
        assert "(c:Class OR c:Function)" in result
        assert "WHERE" in result

    def test_three_labels(self):
        result = self.rewrite("MATCH (n:Class|Function|Method) RETURN count(n)")
        assert "(n:Class OR n:Function OR n:Method)" in result

    def test_existing_where_is_preserved(self):
        result = self.rewrite("MATCH (n:Class|Function) WHERE n.name = 'x' RETURN n")
        assert "n.name = 'x'" in result
        assert "AND" in result

    def test_anonymous_node_gets_a_variable(self):
        result = self.rewrite("MATCH (:Class|Function) RETURN 1")
        assert "|" not in result
        assert " OR " in result

    def test_single_label_is_untouched(self):
        query = "MATCH (n:Function) RETURN n"
        assert self.rewrite(query) == query

    def test_relationship_alternatives_are_untouched(self):
        """FalkorDB accepts `-[:A|B]->`; rewriting it would be wrong."""
        query = "MATCH ()-[r:CALLS|CONTAINS]->() RETURN count(r)"
        assert self.rewrite(query) == query

    def test_mixed_node_and_relationship_alternatives(self):
        result = self.rewrite("MATCH (n:Class|Function)-[r:CALLS|CONTAINS]->() RETURN n")
        assert "r:CALLS|CONTAINS" in result
        assert "(n:Class OR n:Function)" in result

    def test_property_map_after_labels(self):
        result = self.rewrite("MATCH (n:Class|Function {name: 'x'}) RETURN n")
        assert "|" not in result
        assert "name: 'x'" in result


class TestRewrittenQueriesActuallyRun:
    """The rewrite is only correct if FalkorDB accepts the result."""

    @pytest.fixture()
    def store(self, tmp_path_factory):
        from navegador.graph.store import GraphStore

        s = GraphStore.sqlite(str(tmp_path_factory.mktemp("cypher") / "graph.db"))
        s.query("CREATE (:Class {name: 'A'})")
        s.query("CREATE (:Function {name: 'b'})")
        yield s
        s.close()

    @pytest.mark.parametrize(
        "query",
        [
            "MATCH (n:Class|Function) RETURN count(n)",
            "MATCH (n:Class|Function) WHERE n.name = 'A' RETURN count(n)",
            "MATCH (:Class|Function) RETURN count(*)",
            "MATCH (n:Class|Function|Method) RETURN count(n)",
        ],
    )
    def test_rewritten_query_executes(self, store, query):
        rewritten = NLPEngine._rewrite_alternative_labels(query)
        store.query(rewritten)  # raises if FalkorDB rejects it

    def test_original_query_is_rejected(self, store):
        """Confirms the rewrite is addressing a real syntax error."""
        with pytest.raises(Exception, match=r"\|"):
            store.query("MATCH (n:Class|Function) RETURN count(n)")

    def test_rewrite_preserves_semantics(self, store):
        rewritten = NLPEngine._rewrite_alternative_labels(
            "MATCH (n:Class|Function) RETURN count(n)"
        )
        assert store.query(rewritten).result_set[0][0] == 2
