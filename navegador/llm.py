"""
LLM backend abstraction — unified provider interface with auto-discovery.

Provides a common interface for multiple LLM providers (Anthropic, OpenAI,
Ollama). SDK imports are lazy and guarded so that missing optional dependencies
produce a clear, actionable ImportError rather than a confusing traceback.

Usage::

    from navegador.llm import get_provider, auto_provider, discover_providers

    # Explicit provider
    provider = get_provider("anthropic", model="claude-opus-5")
    response = provider.complete("Explain this function: ...")

    # Auto-detect the first available SDK
    provider = auto_provider()

    # See what is installed
    available = discover_providers()  # e.g. ["anthropic", "openai"]
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod

# ── Abstract base ─────────────────────────────────────────────────────────────


class LLMProvider(ABC):
    """Abstract interface that every concrete LLM provider must satisfy."""

    @abstractmethod
    def complete(self, prompt: str, **kwargs) -> str:
        """
        Send *prompt* to the model and return the completion as a string.

        Args:
            prompt: The user/system prompt text.
            **kwargs: Provider-specific options (temperature, max_tokens, …).

        Returns:
            The model's text response.
        """

    @abstractmethod
    def embed(self, text: str) -> list[float]:
        """
        Return an embedding vector for *text*.

        Args:
            text: The input string to embed.

        Returns:
            A list of floats representing the embedding.
        """

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier for the provider, e.g. ``"anthropic"``."""

    @property
    @abstractmethod
    def model(self) -> str:
        """Model identifier used for API calls, e.g. ``"claude-opus-5"``."""


# ── Concrete providers ────────────────────────────────────────────────────────


class AnthropicProvider(LLMProvider):
    """
    LLM provider backed by the ``anthropic`` Python SDK.

    Install::

        pip install anthropic

    Args:
        model: Anthropic model ID (default ``"claude-opus-5"``).
    """

    #: Current model alias. Model IDs are complete as written — never append a
    #: date suffix. The previous default, `claude-3-5-haiku-20241022`, was
    #: retired on 2026-02-19 and returns 404, so every `ask` that reached the
    #: Anthropic default failed on a model the user never chose (#164).
    _DEFAULT_MODEL = "claude-opus-5"

    def __init__(self, model: str = "") -> None:
        try:
            import anthropic  # noqa: F401
        except ImportError as exc:
            raise ImportError(
                "The 'anthropic' package is required to use AnthropicProvider. "
                "Install it with:  pip install anthropic"
            ) from exc

        self._model = model or self._DEFAULT_MODEL

        import anthropic

        self._client = anthropic.Anthropic()

    @property
    def name(self) -> str:
        return "anthropic"

    @property
    def model(self) -> str:
        return self._model

    def complete(self, prompt: str, **kwargs) -> str:
        """Call the Anthropic Messages API and return the first text block."""
        max_tokens = kwargs.pop("max_tokens", 1024)
        message = self._client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
            **kwargs,
        )
        return message.content[0].text

    def embed(self, text: str) -> list[float]:
        """
        Anthropic does not currently expose a public embeddings API.

        Raises:
            NotImplementedError: Always.
        """
        raise NotImplementedError(
            "Anthropic does not provide a public embeddings API. "
            "Use OpenAIProvider or OllamaProvider for embeddings."
        )


class OpenAIProvider(LLMProvider):
    """
    LLM provider backed by the ``openai`` Python SDK.

    Install::

        pip install openai

    Args:
        model: OpenAI model ID (default ``"gpt-4o-mini"``).
    """

    _DEFAULT_MODEL = "gpt-4o-mini"

    def __init__(self, model: str = "") -> None:
        try:
            import openai  # noqa: F401
        except ImportError as exc:
            raise ImportError(
                "The 'openai' package is required to use OpenAIProvider. "
                "Install it with:  pip install openai"
            ) from exc

        self._model = model or self._DEFAULT_MODEL

        import openai

        self._client = openai.OpenAI()

    @property
    def name(self) -> str:
        return "openai"

    @property
    def model(self) -> str:
        return self._model

    def complete(self, prompt: str, **kwargs) -> str:
        """Call the OpenAI Chat Completions API and return the assistant message."""
        response = self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": "user", "content": prompt}],
            **kwargs,
        )
        return response.choices[0].message.content

    def embed(self, text: str) -> list[float]:
        """Call the OpenAI Embeddings API and return the embedding vector."""
        embed_model = "text-embedding-3-small"
        response = self._client.embeddings.create(input=text, model=embed_model)
        return response.data[0].embedding


class OllamaProvider(LLMProvider):
    """
    LLM provider backed by the ``ollama`` Python SDK (local models via Ollama).

    Install::

        pip install ollama

    The Ollama server must be running locally (``ollama serve``).

    Args:
        model: Ollama model tag (default ``"llama3.2"``).
    """

    _DEFAULT_MODEL = "llama3.2"

    def __init__(self, model: str = "") -> None:
        try:
            import ollama  # noqa: F401
        except ImportError as exc:
            raise ImportError(
                "The 'ollama' package is required to use OllamaProvider. "
                "Install it with:  pip install ollama"
            ) from exc

        self._model = model or self._DEFAULT_MODEL

        import ollama

        self._client = ollama.Client()

    @property
    def name(self) -> str:
        return "ollama"

    @property
    def model(self) -> str:
        return self._model

    def complete(self, prompt: str, **kwargs) -> str:
        """Call the Ollama chat API and return the assistant message content."""
        response = self._client.chat(
            model=self._model,
            messages=[{"role": "user", "content": prompt}],
            **kwargs,
        )
        return response["message"]["content"]

    def embed(self, text: str) -> list[float]:
        """Call the Ollama embeddings API and return the embedding vector."""
        response = self._client.embeddings(model=self._model, prompt=text)
        return response["embedding"]


# ── Discovery & factory ───────────────────────────────────────────────────────

# Ordered list of known providers — also defines auto_provider priority.
_PROVIDER_NAMES: list[str] = ["anthropic", "openai", "ollama"]

_PROVIDER_SDK_MAP: dict[str, str] = {
    "anthropic": "anthropic",
    "openai": "openai",
    "ollama": "ollama",
}

_PROVIDER_CLASS_MAP: dict[str, type[LLMProvider]] = {
    "anthropic": AnthropicProvider,
    "openai": OpenAIProvider,
    "ollama": OllamaProvider,
}


def discover_providers() -> list[str]:
    """
    Return a list of provider names whose SDKs are currently importable.

    The list preserves the canonical priority order:
    ``["anthropic", "openai", "ollama"]``.

    Returns:
        List of available provider name strings.
    """
    available: list[str] = []
    for provider_name in _PROVIDER_NAMES:
        sdk_name = _PROVIDER_SDK_MAP[provider_name]
        try:
            __import__(sdk_name)
            available.append(provider_name)
        except ImportError:
            pass
    return available


def get_provider(name: str, model: str = "") -> LLMProvider:
    """
    Instantiate and return the named LLM provider.

    Args:
        name: One of ``"anthropic"``, ``"openai"``, or ``"ollama"``.
        model: Optional model ID to pass to the provider constructor.
               Falls back to each provider's built-in default.

    Returns:
        An :class:`LLMProvider` instance.

    Raises:
        ValueError: If *name* does not correspond to a known provider.
        ImportError: If the underlying SDK is not installed.
    """
    if name not in _PROVIDER_CLASS_MAP:
        raise ValueError(
            f"Unknown LLM provider: {name!r}. Valid options are: {sorted(_PROVIDER_CLASS_MAP)}"
        )
    cls = _PROVIDER_CLASS_MAP[name]
    return cls(model=model)


#: Environment variables that carry a usable credential for each provider.
#: Ollama is local and needs none — reachability is the check instead.
_PROVIDER_CREDENTIALS = {
    "anthropic": ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"),
    "openai": ("OPENAI_API_KEY",),
    "ollama": (),
}


def provider_available(name: str) -> tuple[bool, str]:
    """
    Whether *name* can actually serve a request, and why not when it cannot.

    SDK importability is not availability: the Anthropic package installed
    without a key selected a provider that then failed at call time with a raw
    auth error (#164). Credentials are checked here, and a local provider is
    checked for reachability.
    """
    sdk = _PROVIDER_SDK_MAP.get(name, name)
    try:
        __import__(sdk)
    except ImportError:
        return False, f"the {sdk!r} package is not installed (pip install {sdk})"

    if name == "ollama":
        import urllib.error
        import urllib.request

        base = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
        try:
            urllib.request.urlopen(f"{base}/api/tags", timeout=1.5).close()  # noqa: S310
        except (urllib.error.URLError, TimeoutError, OSError):
            return False, f"no Ollama server is reachable at {base}"
        return True, ""

    env_names = _PROVIDER_CREDENTIALS.get(name, ())
    if env_names and not any(os.environ.get(var, "").strip() for var in env_names):
        return False, f"no credential in {' or '.join(env_names)}"
    return True, ""


def auto_provider(model: str = "") -> LLMProvider:
    """
    Return the first provider that is actually usable.

    Priority order: anthropic → openai → ollama. A provider whose SDK is
    installed but whose credential is missing is skipped rather than selected,
    so the failure names the missing credential instead of surfacing a raw
    auth error from whichever SDK happened to be present.

    Args:
        model: Optional model ID forwarded to the provider constructor.

    Returns:
        An :class:`LLMProvider` instance for the first usable provider.

    Raises:
        RuntimeError: If no provider is usable, listing why each was skipped.
    """
    reasons: list[str] = []
    for provider_name in _PROVIDER_NAMES:
        ok, why = provider_available(provider_name)
        if ok:
            return get_provider(provider_name, model=model)
        reasons.append(f"  {provider_name}: {why}")

    raise RuntimeError(
        "No usable LLM provider.\n"
        + "\n".join(reasons)
        + "\n\nCredentials are read from the environment. Set one of:\n"
        "  export ANTHROPIC_API_KEY=...     # Anthropic\n"
        "  export OPENAI_API_KEY=...        # OpenAI\n"
        "  ollama serve                     # Ollama (local, no key)\n"
        "Or pin one in .navegador/config.toml:\n"
        '  [llm]\n  provider = "anthropic"\n  model = "claude-opus-5"'
    )
