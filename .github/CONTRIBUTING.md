# Contributing to Navegador

Thanks for your interest in contributing!

## Development setup

```bash
git clone https://github.com/ConflictHQ/navegador
cd navegador
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pre-commit install
```

## Running tests

```bash
pytest tests/ -v
```

## Code style

We use `ruff` for linting and formatting. Pre-commit hooks run automatically on commit.

```bash
ruff check navegador/
ruff format navegador/
```

## Pull requests

1. Fork the repo and create a branch from `main`
2. Add tests for new behaviour
3. Ensure CI passes
4. Open a PR with a clear description of the change

## Commit messages

Use the imperative mood: `add X`, `fix Y`, `update Z`. Keep the first line under 72 characters.
