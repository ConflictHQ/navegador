# Installation

## Requirements

- Python **3.12 or later** — required by `falkordblite`, the embedded SQLite backend
- pip 23+

## Install

```bash
pip install navegador
```

This installs the core package with the SQLite backend (`falkordblite`) included. No external services are required for local use.

## Optional extras

=== "[sqlite]"

    The default. `falkordblite` is bundled and requires no configuration. This is what `pip install navegador` already gives you.

    ```bash
    pip install "navegador[sqlite]"   # explicit, same as above
    ```

    !!! note
        `falkordblite` requires Python 3.12+. Its embedded SQLite graph engine uses features not available in earlier Python versions.

=== "[redis]"

    For production deployments backed by a Redis instance running FalkorDB.

    ```bash
    pip install "navegador[redis]"
    ```

    Then point navegador at your Redis instance:

    ```bash
    export NAVEGADOR_DB=redis://localhost:6379
    navegador ingest ./repo
    ```

    See [Configuration](configuration.md) for full Redis setup details.

=== "[languages]"

    Additional tree-sitter grammars for Kotlin, C#, PHP, Ruby, Swift, C, and C++. The default install includes Python, TypeScript, JavaScript, Go, Rust, and Java.

    ```bash
    pip install "navegador[languages]"
    ```

    After installing, all 13 languages are parsed automatically by `navegador ingest`. No additional configuration is required.

=== "[llm]"

    LLM provider integrations for Anthropic, OpenAI, and Ollama. Required for `navegador ask`, `navegador docs`, and `navegador semantic-search`.

    ```bash
    pip install "navegador[llm]"
    ```

    Configure the provider in `.navegador/config.toml` or via environment variables. See [Configuration](configuration.md) for details.

=== "all extras"

    Install everything at once:

    ```bash
    pip install "navegador[sqlite,redis,languages,llm]"
    ```

## Verify

```bash
navegador --version
```

Expected output:

```
navegador, version 0.7.0
```

## Shell completions

Install shell completions for tab-completion of commands and flags:

```bash
navegador completions bash >> ~/.bashrc
navegador completions zsh  >> ~/.zshrc
navegador completions fish > ~/.config/fish/completions/navegador.fish
```

## Python SDK

The Python SDK wraps all CLI functionality in a single `Navegador` class:

```python
from navegador import Navegador

nav = Navegador(".navegador/navegador.db")
nav.ingest("./src")
bundle = nav.explain("AuthService")
print(bundle.to_markdown())
```

The SDK is included in the base install — no extra is required.

## Development install

```bash
git clone https://github.com/ConflictHQ/navegador
cd navegador
pip install -e ".[sqlite,redis,languages,llm,dev]"
```

## Upgrading

```bash
pip install --upgrade navegador
```

After upgrading, run schema migrations first, then re-ingest to pick up new parser features:

```bash
navegador migrate          # apply any schema changes from the new version
navegador ingest ./repo    # re-ingest with incremental updates (preferred)
navegador ingest ./repo --clear  # full rebuild if you prefer a clean slate
```
