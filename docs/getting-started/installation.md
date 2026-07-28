# Installation

## Requirements

- Python **3.12 or later** — required by `falkordblite`, the embedded SQLite backend
- pip 23+
- A **POSIX** platform — Linux, macOS, or WSL2

## Platform support

| Platform | Supported | Verified by |
|----------|-----------|-------------|
| Linux | Yes | CI, `ubuntu-latest` |
| macOS (Apple Silicon and Intel) | Yes | CI, `macos-latest` |
| Windows via [WSL2](https://learn.microsoft.com/windows/wsl/install) | Yes | CI, `windows-latest` + WSL2 |
| Windows (native) | No | — |

Native Windows is not supported. `falkordblite` — the embedded graph backend, and a
required dependency — publishes no Windows wheel, and its source distribution declines
to build on `win32`. `pip install navegador` therefore fails on native Windows:

```
The redislite module is not supported on the 'win32' platform
ERROR: Failed to build 'falkordblite'
```

This is not a path-handling or line-ending problem that could be patched here. The
dependency chain ends at Redis C sources that require `fork()` and POSIX sockets.

### Windows via WSL2

Install and run navegador inside WSL2. It behaves exactly as it does on Linux — the
full test suite runs green under WSL2 on every release, in the same CI job that builds
the Linux binary.

Two practical notes:

**Ubuntu 22.04 needs a newer Python.** navegador requires Python 3.12+, and Ubuntu
22.04 ships 3.10, so `pip install navegador` fails there on `requires-python`. Use
Ubuntu 24.04 (which ships 3.12), or install a newer interpreter via `deadsnakes`,
`pyenv` or `uv`. Separately, `falkordblite` publishes only `manylinux_2_39` wheels, so
on glibc older than 2.39 pip falls back to its source distribution and compiles
FalkorDB — that works, but it is slow and needs `build-essential` present.

**The pre-commit hook must run under WSL.** If a repository's hook shells out to
`navegador`, committing from a Windows-side Git client or IDE runs that hook in an
environment where navegador does not exist, and the commit is rejected. Commit from
inside WSL, or point the tool at the WSL interpreter.

**Working from `/mnt/c` is fine.** The Windows drive is a 9p mount where binding a unix
socket fails with `EOPNOTSUPP`, but `flock` works and the embedded backend runs there
correctly — CI asserts a full graph round-trip with its database on `/mnt/c`, and
measures a full ingest on both filesystems: 30s on `/mnt/c` against 29s on the Linux
filesystem. Keep your repository wherever suits your workflow.

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

=== "[iac]"

    Infrastructure-as-Code parsers for HCL/Terraform, Puppet, Bash/Shell, Ansible, and Chef.

    ```bash
    pip install "navegador[iac]"
    ```

    After installing, `.tf`, `.hcl`, `.pp`, `.sh`, `.bash`, and `.zsh` files are parsed automatically. Ansible YAML files are detected heuristically by directory structure. Chef cookbooks are enriched via the Chef enricher on top of the Ruby parser.

=== "[llm]"

    LLM provider integrations for Anthropic, OpenAI, and Ollama. Required for `navegador ask`, `navegador docs`, and `navegador semantic-search`.

    ```bash
    pip install "navegador[llm]"
    ```

    Configure the provider in `.navegador/config.toml` or via environment variables. See [Configuration](configuration.md) for details.

=== "all extras"

    Install everything at once:

    ```bash
    pip install "navegador[sqlite,redis,languages,iac,llm]"
    ```

## Verify

```bash
navegador --version
```

Expected output:

```
navegador, version 1.1.0
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
