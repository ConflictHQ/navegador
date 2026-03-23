"""
Tests for navegador/llm.py — LLM backend abstraction.

All tests are fully offline. SDK imports are patched to avoid requiring
any LLM SDK to be installed in the test environment.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest


# ── Helpers ───────────────────────────────────────────────────────────────────


def _block_import(name: str):
    """
    Context manager that makes ``import <name>`` raise ImportError for the
    duration of the block, even if the package is installed.
    """

    class _Blocker:
        def __enter__(self):
            self._original = sys.modules.get(name, None)
            sys.modules[name] = None  # type: ignore[assignment]
            return self

        def __exit__(self, *_):
            if self._original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = self._original

    return _Blocker()


def _fake_anthropic_module():
    """Return a minimal mock that satisfies AnthropicProvider's usage."""
    mod = MagicMock()
    client = MagicMock()
    message = MagicMock()
    message.content = [MagicMock(text="hello from anthropic")]
    client.messages.create.return_value = message
    mod.Anthropic.return_value = client
    return mod, client


def _fake_openai_module():
    """Return a minimal mock that satisfies OpenAIProvider's usage."""
    mod = MagicMock()
    client = MagicMock()
    choice = MagicMock()
    choice.message.content = "hello from openai"
    response = MagicMock()
    response.choices = [choice]
    client.chat.completions.create.return_value = response
    embed_data = MagicMock()
    embed_data.embedding = [0.1, 0.2, 0.3]
    embed_response = MagicMock()
    embed_response.data = [embed_data]
    client.embeddings.create.return_value = embed_response
    mod.OpenAI.return_value = client
    return mod, client


def _fake_ollama_module():
    """Return a minimal mock that satisfies OllamaProvider's usage."""
    mod = MagicMock()
    client = MagicMock()
    client.chat.return_value = {"message": {"content": "hello from ollama"}}
    client.embeddings.return_value = {"embedding": [0.4, 0.5, 0.6]}
    mod.Client.return_value = client
    return mod, client


# ── AnthropicProvider ─────────────────────────────────────────────────────────


class TestAnthropicProvider:
    def test_raises_import_error_when_sdk_missing(self):
        with _block_import("anthropic"):
            # Remove cached module from navegador.llm so the guard re-runs
            import importlib

            import navegador.llm as llm_mod

            importlib.reload(llm_mod)
            with pytest.raises(ImportError, match="pip install anthropic"):
                llm_mod.AnthropicProvider()

    def test_name_is_anthropic(self):
        fake_mod, _ = _fake_anthropic_module()
        with patch.dict(sys.modules, {"anthropic": fake_mod}):
            import importlib

            import navegador.llm as llm_mod

            importlib.reload(llm_mod)
            p = llm_mod.AnthropicProvider()
            assert p.name == "anthropic"

    def test_default_model(self):
        fake_mod, _ = _fake_anthropic_module()
        with patch.dict(sys.modules, {"anthropic": fake_mod}):
            import importlib

            import navegador.llm as llm_mod

            importlib.reload(llm_mod)
            p = llm_mod.AnthropicProvider()
            assert p.model == "claude-3-5-haiku-20241022"

    def test_custom_model(self):
        fake_mod, _ = _fake_anthropic_module()
        with patch.dict(sys.modules, {"anthropic": fake_mod}):
            import importlib

            import navegador.llm as llm_mod

            importlib.reload(llm_mod)
            p = llm_mod.AnthropicProvider(model="claude-opus-4")
            assert p.model == "claude-opus-4"

    def test_complete_returns_text(self):
        fake_mod, client = _fake_anthropic_module()
        with patch.dict(sys.modules, {"anthropic": fake_mod}):
            import importlib

            import navegador.llm as llm_mod

            importlib.reload(llm_mod)
            p = llm_mod.AnthropicProvider()
            result = p.complete("say hello")
            assert result == "hello from anthropic"
            client.messages.create.assert_called_once()

    def test_complete_passes_max_tokens(self):
        fake_mod, client = _fake_anthropic_module()
        with patch.dict(sys.modules, {"anthropic": fake_mod}):
            import importlib

            import navegador.llm as llm_mod

            importlib.reload(llm_mod)
            p = llm_mod.AnthropicProvider()
            p.complete("hi", max_tokens=512)
            _, kwargs = client.messages.create.call_args
            assert kwargs["max_tokens"] == 512

    def test_embed_raises_not_implemented(self):
        fake_mod, _ = _fake_anthropic_module()
        with patch.dict(sys.modules, {"anthropic": fake_mod}):
            import importlib

            import navegador.llm as llm_mod

            importlib.reload(llm_mod)
            p = llm_mod.AnthropicProvider()
            with pytest.raises(NotImplementedError):
                p.embed("text")


# ── OpenAIProvider ────────────────────────────────────────────────────────────


class TestOpenAIProvider:
    def test_raises_import_error_when_sdk_missing(self):
        with _block_import("openai"):
            import importlib

            import navegador.llm as llm_mod

            importlib.reload(llm_mod)
            with pytest.raises(ImportError, match="pip install openai"):
                llm_mod.OpenAIProvider()

    def test_name_is_openai(self):
        fake_mod, _ = _fake_openai_module()
        with patch.dict(sys.modules, {"openai": fake_mod}):
            import importlib

            import navegador.llm as llm_mod

            importlib.reload(llm_mod)
            p = llm_mod.OpenAIProvider()
            assert p.name == "openai"

    def test_default_model(self):
        fake_mod, _ = _fake_openai_module()
        with patch.dict(sys.modules, {"openai": fake_mod}):
            import importlib

            import navegador.llm as llm_mod

            importlib.reload(llm_mod)
            p = llm_mod.OpenAIProvider()
            assert p.model == "gpt-4o-mini"

    def test_custom_model(self):
        fake_mod, _ = _fake_openai_module()
        with patch.dict(sys.modules, {"openai": fake_mod}):
            import importlib

            import navegador.llm as llm_mod

            importlib.reload(llm_mod)
            p = llm_mod.OpenAIProvider(model="gpt-4o")
            assert p.model == "gpt-4o"

    def test_complete_returns_text(self):
        fake_mod, client = _fake_openai_module()
        with patch.dict(sys.modules, {"openai": fake_mod}):
            import importlib

            import navegador.llm as llm_mod

            importlib.reload(llm_mod)
            p = llm_mod.OpenAIProvider()
            result = p.complete("say hello")
            assert result == "hello from openai"
            client.chat.completions.create.assert_called_once()

    def test_embed_returns_list_of_floats(self):
        fake_mod, client = _fake_openai_module()
        with patch.dict(sys.modules, {"openai": fake_mod}):
            import importlib

            import navegador.llm as llm_mod

            importlib.reload(llm_mod)
            p = llm_mod.OpenAIProvider()
            result = p.embed("hello world")
            assert result == [0.1, 0.2, 0.3]
            client.embeddings.create.assert_called_once()


# ── OllamaProvider ────────────────────────────────────────────────────────────


class TestOllamaProvider:
    def test_raises_import_error_when_sdk_missing(self):
        with _block_import("ollama"):
            import importlib

            import navegador.llm as llm_mod

            importlib.reload(llm_mod)
            with pytest.raises(ImportError, match="pip install ollama"):
                llm_mod.OllamaProvider()

    def test_name_is_ollama(self):
        fake_mod, _ = _fake_ollama_module()
        with patch.dict(sys.modules, {"ollama": fake_mod}):
            import importlib

            import navegador.llm as llm_mod

            importlib.reload(llm_mod)
            p = llm_mod.OllamaProvider()
            assert p.name == "ollama"

    def test_default_model(self):
        fake_mod, _ = _fake_ollama_module()
        with patch.dict(sys.modules, {"ollama": fake_mod}):
            import importlib

            import navegador.llm as llm_mod

            importlib.reload(llm_mod)
            p = llm_mod.OllamaProvider()
            assert p.model == "llama3.2"

    def test_custom_model(self):
        fake_mod, _ = _fake_ollama_module()
        with patch.dict(sys.modules, {"ollama": fake_mod}):
            import importlib

            import navegador.llm as llm_mod

            importlib.reload(llm_mod)
            p = llm_mod.OllamaProvider(model="mistral")
            assert p.model == "mistral"

    def test_complete_returns_text(self):
        fake_mod, client = _fake_ollama_module()
        with patch.dict(sys.modules, {"ollama": fake_mod}):
            import importlib

            import navegador.llm as llm_mod

            importlib.reload(llm_mod)
            p = llm_mod.OllamaProvider()
            result = p.complete("say hello")
            assert result == "hello from ollama"
            client.chat.assert_called_once()

    def test_embed_returns_list_of_floats(self):
        fake_mod, client = _fake_ollama_module()
        with patch.dict(sys.modules, {"ollama": fake_mod}):
            import importlib

            import navegador.llm as llm_mod

            importlib.reload(llm_mod)
            p = llm_mod.OllamaProvider()
            result = p.embed("hello world")
            assert result == [0.4, 0.5, 0.6]
            client.embeddings.assert_called_once()


# ── discover_providers ────────────────────────────────────────────────────────


class TestDiscoverProviders:
    def _reload(self, modules: dict):
        import importlib

        import navegador.llm as llm_mod

        importlib.reload(llm_mod)
        return llm_mod

    def test_all_available(self):
        fake_a, _ = _fake_anthropic_module()
        fake_o, _ = _fake_openai_module()
        fake_ol, _ = _fake_ollama_module()
        with patch.dict(
            sys.modules,
            {"anthropic": fake_a, "openai": fake_o, "ollama": fake_ol},
        ):
            llm_mod = self._reload({})
            result = llm_mod.discover_providers()
            assert result == ["anthropic", "openai", "ollama"]

    def test_only_openai_available(self):
        fake_o, _ = _fake_openai_module()
        with (
            _block_import("anthropic"),
            patch.dict(sys.modules, {"openai": fake_o}),
            _block_import("ollama"),
        ):
            llm_mod = self._reload({})
            result = llm_mod.discover_providers()
            assert result == ["openai"]

    def test_none_available(self):
        with _block_import("anthropic"), _block_import("openai"), _block_import("ollama"):
            llm_mod = self._reload({})
            result = llm_mod.discover_providers()
            assert result == []

    def test_preserves_priority_order(self):
        fake_a, _ = _fake_anthropic_module()
        fake_ol, _ = _fake_ollama_module()
        with (
            patch.dict(sys.modules, {"anthropic": fake_a, "ollama": fake_ol}),
            _block_import("openai"),
        ):
            llm_mod = self._reload({})
            result = llm_mod.discover_providers()
            assert result == ["anthropic", "ollama"]


# ── get_provider ──────────────────────────────────────────────────────────────


class TestGetProvider:
    def _reload(self):
        import importlib

        import navegador.llm as llm_mod

        importlib.reload(llm_mod)
        return llm_mod

    def test_returns_anthropic_provider(self):
        fake_mod, _ = _fake_anthropic_module()
        with patch.dict(sys.modules, {"anthropic": fake_mod}):
            llm_mod = self._reload()
            p = llm_mod.get_provider("anthropic")
            assert p.name == "anthropic"

    def test_returns_openai_provider(self):
        fake_mod, _ = _fake_openai_module()
        with patch.dict(sys.modules, {"openai": fake_mod}):
            llm_mod = self._reload()
            p = llm_mod.get_provider("openai")
            assert p.name == "openai"

    def test_returns_ollama_provider(self):
        fake_mod, _ = _fake_ollama_module()
        with patch.dict(sys.modules, {"ollama": fake_mod}):
            llm_mod = self._reload()
            p = llm_mod.get_provider("ollama")
            assert p.name == "ollama"

    def test_passes_model_argument(self):
        fake_mod, _ = _fake_anthropic_module()
        with patch.dict(sys.modules, {"anthropic": fake_mod}):
            llm_mod = self._reload()
            p = llm_mod.get_provider("anthropic", model="claude-opus-4")
            assert p.model == "claude-opus-4"

    def test_unknown_provider_raises_value_error(self):
        import importlib

        import navegador.llm as llm_mod

        importlib.reload(llm_mod)
        with pytest.raises(ValueError, match="Unknown LLM provider"):
            llm_mod.get_provider("grok")

    def test_unknown_provider_message_includes_valid_options(self):
        import importlib

        import navegador.llm as llm_mod

        importlib.reload(llm_mod)
        with pytest.raises(ValueError, match="anthropic"):
            llm_mod.get_provider("nonexistent")


# ── auto_provider ─────────────────────────────────────────────────────────────


class TestAutoProvider:
    def _reload(self):
        import importlib

        import navegador.llm as llm_mod

        importlib.reload(llm_mod)
        return llm_mod

    def test_prefers_anthropic_when_all_available(self):
        fake_a, _ = _fake_anthropic_module()
        fake_o, _ = _fake_openai_module()
        fake_ol, _ = _fake_ollama_module()
        with patch.dict(
            sys.modules,
            {"anthropic": fake_a, "openai": fake_o, "ollama": fake_ol},
        ):
            llm_mod = self._reload()
            p = llm_mod.auto_provider()
            assert p.name == "anthropic"

    def test_falls_back_to_openai_when_anthropic_missing(self):
        fake_o, _ = _fake_openai_module()
        fake_ol, _ = _fake_ollama_module()
        with (
            _block_import("anthropic"),
            patch.dict(sys.modules, {"openai": fake_o, "ollama": fake_ol}),
        ):
            llm_mod = self._reload()
            p = llm_mod.auto_provider()
            assert p.name == "openai"

    def test_falls_back_to_ollama_when_anthropic_and_openai_missing(self):
        fake_ol, _ = _fake_ollama_module()
        with (
            _block_import("anthropic"),
            _block_import("openai"),
            patch.dict(sys.modules, {"ollama": fake_ol}),
        ):
            llm_mod = self._reload()
            p = llm_mod.auto_provider()
            assert p.name == "ollama"

    def test_raises_runtime_error_when_no_sdk_available(self):
        with _block_import("anthropic"), _block_import("openai"), _block_import("ollama"):
            llm_mod = self._reload()
            with pytest.raises(RuntimeError, match="No LLM SDK is installed"):
                llm_mod.auto_provider()

    def test_runtime_error_message_includes_install_hints(self):
        with _block_import("anthropic"), _block_import("openai"), _block_import("ollama"):
            llm_mod = self._reload()
            with pytest.raises(RuntimeError, match="pip install"):
                llm_mod.auto_provider()

    def test_passes_model_to_provider(self):
        fake_a, _ = _fake_anthropic_module()
        with patch.dict(sys.modules, {"anthropic": fake_a}):
            llm_mod = self._reload()
            p = llm_mod.auto_provider(model="claude-opus-4")
            assert p.model == "claude-opus-4"
