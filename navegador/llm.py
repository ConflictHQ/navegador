"""
LLM backend abstraction — unified provider interface with auto-discovery.

Provides a common interface for multiple LLM providers (Anthropic, OpenAI,
Ollama). SDK imports are lazy and guarded so that missing optional dependencies
produce a clear, actionable ImportError rather than a confusing traceback.

Usage::

    from navegador.llm import get_provider, auto_provider, discover_providers

    # Explicit provider
    provider = get_provider("anthropic", model="claude-3-5-haiku-20241022")
    response = provider.complete("Explain this function: ...")

    # Auto-detect the first available SDK
    provider = auto_provider()

    # See what is installed
    available = discover_providers()  # e.g. ["anthropic", "openai"]
"""

from __future__ import annotations

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
        """Model identifier used for API calls, e.g. ``"claude-3-5-haiku-20241022"``."""


# ── Concrete providers ────────────────────────────────────────────────────────


class AnthropicProvider(LLMProvider):
    """
    LLM provider backed by the ``anthropic`` Python SDK.

    Install::

        pip install anthropic

    Args:
        model: Anthropic model ID (default ``"claude-3-5-haiku-20241022"``).
    """

    _DEFAULT_MODEL = "claude-3-5-haiku-20241022"

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


def auto_provider(model: str = "") -> LLMProvider:
    """
    Return the first available LLM provider based on installed SDKs.

    Priority order: anthropic → openai → ollama.

    Args:
        model: Optional model ID forwarded to the provider constructor.

    Returns:
        An :class:`LLMProvider` instance for the first available SDK.

    Raises:
        RuntimeError: If no supported LLM SDK is installed.
    """
    for provider_name in _PROVIDER_NAMES:
        sdk_name = _PROVIDER_SDK_MAP[provider_name]
        try:
            __import__(sdk_name)
        except ImportError:
            continue
        return get_provider(provider_name, model=model)

    raise RuntimeError(
        "No LLM SDK is installed. Install at least one of: "
        "anthropic, openai, ollama.\n"
        "  pip install anthropic   # Anthropic Claude\n"
        "  pip install openai      # OpenAI GPT\n"
        "  pip install ollama      # Ollama (local models)"
    )
