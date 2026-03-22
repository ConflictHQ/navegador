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

## Verify

```bash
navegador --version
```

Expected output:

```
navegador, version 0.x.y
```

## Development install

```bash
git clone https://github.com/ConflictHQ/navegador
cd navegador
pip install -e ".[sqlite,redis]"
```

## Upgrading

```bash
pip install --upgrade navegador
```

After upgrading, re-ingest any existing repos to pick up new parser features or schema changes:

```bash
navegador ingest ./repo --clear
```
