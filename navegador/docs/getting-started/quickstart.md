# Quick Start

This guide walks from a fresh install to a fully wired agent integration in five steps.

---

## Step 1: Install

```bash
pip install navegador
navegador --version
```

Python 3.12+ is required. For additional languages (Kotlin, C#, PHP, Ruby, Swift, C, C++) install the `[languages]` extra. See [Installation](installation.md) for all extras and Redis setup.

---

## Step 2: Ingest a repo

Point navegador at any local source tree:

```bash
navegador ingest ./my-repo
```

On first run this builds the graph from scratch. Re-run anytime to pick up changes. Use `--incremental` to skip files that haven't changed (based on content hashing — much faster on large repos):

```bash
navegador ingest ./my-repo --incremental
```

Use `--watch` to keep the graph in sync as files change:

```bash
navegador ingest ./my-repo --watch
```

Use `--clear` to wipe and rebuild from scratch:

```bash
navegador ingest ./my-repo --clear
```

Use `--json` to get a machine-readable summary of what was indexed:

```bash
navegador ingest ./my-repo --json
```

Navegador walks the tree, parses source files in 13 languages with tree-sitter, and writes nodes and edges for: files, modules, classes, functions, methods, imports, decorators, and call relationships. Framework enrichers automatically detect and annotate Django models, FastAPI routes, React components, Rails controllers, Spring Boot beans, and more.

---

## Step 3: Query the graph

**Explain anything by name** — works for functions, classes, files, concepts, rules, and decisions:

```bash
navegador explain AuthService
navegador explain validate_token
navegador explain src/payments/processor.py
```

**Search across code and knowledge together:**

```bash
navegador search "rate limit" --all
navegador search "authentication" --docs --limit 10
```

**Inspect a function** (callers, callees, decorators, source):

```bash
navegador function validate_token
navegador function validate_token --depth 2 --format json
```

**Inspect a class** (hierarchy, methods, references):

```bash
navegador class PaymentProcessor
navegador class PaymentProcessor --format json
```

---

## Step 4: Add business knowledge

Code alone doesn't capture *why*. Add concepts, rules, and decisions and link them to code.

**Add a concept:**

```bash
navegador add concept "Idempotency" \
  --desc "Operations that can be retried safely without side effects" \
  --domain Payments
```

**Add a rule:**

```bash
navegador add rule "PaymentsMustBeIdempotent" \
  --desc "All payment endpoints must handle duplicate submissions" \
  --domain Payments \
  --severity critical \
  --rationale "Card networks retry on timeout; double-charging causes chargebacks"
```

**Annotate code with a concept or rule:**

```bash
navegador annotate process_payment \
  --type Function \
  --concept Idempotency \
  --rule PaymentsMustBeIdempotent
```

**Add a decision:**

```bash
navegador add decision "UseStripeForPayments" \
  --desc "Stripe is the primary payment processor" \
  --domain Payments \
  --rationale "Best fraud tooling for SaaS" \
  --alternatives "Braintree, Adyen" \
  --date 2025-01-15 \
  --status accepted
```

Now `navegador explain process_payment` returns code structure *and* the rules that govern it.

---

## Step 5: Wire an agent hook

Use the bootstrap script to ingest your repo and install the hook for your AI coding assistant in one command:

```bash
./bootstrap.sh --repo owner/repo --wiki --agent claude
```

Options:

| Flag | Effect |
|---|---|
| `--repo owner/repo` | GitHub repo to clone + ingest |
| `--wiki` | Also ingest the GitHub wiki |
| `--agent claude` | Install `.claude/hooks/claude-hook.py` |
| `--agent gemini` | Install `.gemini/hooks/gemini-hook.py` |
| `--agent openai` | Install `openai-hook.py` + `openai-tools.json` |

After bootstrap, every file the agent edits triggers a re-ingest so the graph stays in sync. See [Agent Hooks](../guide/agent-hooks.md) for manual setup and the `NAVEGADOR.md` template.

---

## Step 6 (optional): SDK quick start

All CLI functionality is available through the Python SDK:

```python
from navegador import Navegador

nav = Navegador(".navegador/navegador.db")

# ingest (incremental by default in SDK)
nav.ingest("./my-repo", incremental=True)

# query
bundle = nav.explain("AuthService")
print(bundle.to_markdown())

# analysis
impact = nav.impact("validate_token")
churn = nav.churn(days=30)
cycles = nav.cycles()
```

The `Navegador` class wraps `GraphStore`, `ContextLoader`, all ingesters, and the analysis commands into one interface.

---

## Step 7 (optional): Code analysis

Once the graph is populated, use the analysis commands to understand your codebase:

```bash
# impact of changing a function
navegador impact validate_token

# trace execution flow
navegador trace process_payment --depth 3

# find dead code
navegador deadcode

# detect dependency cycles
navegador cycles

# map tests to source files
navegador testmap

# code churn (files that change most)
navegador churn --days 30

# diff — what changed between two refs
navegador diff HEAD~1 HEAD
```
