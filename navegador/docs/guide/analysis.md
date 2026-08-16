# Structural Analysis

Navegador's analysis commands answer questions about how code fits together: what breaks if this function changes, where does data flow, which code is never called, and which tests cover what.

All analysis commands work against the live graph. Run `navegador ingest` first to populate it.

---

## Impact analysis

`navegador impact` traces the downstream effect of changing a function, class, or file. It follows `CALLS`, `INHERITS`, and `IMPORTS` edges to find everything that depends on the target — directly or transitively.

```bash
navegador impact validate_token
navegador impact PaymentProcessor --depth 3
navegador impact src/auth/service.py --format json
```

### Options

| Flag | Effect |
|---|---|
| `--depth N` | How many hops to follow (default: unlimited) |
| `--format json` | Machine-readable output |
| `--include-tests` | Include test files in the impact set |

### Output

```
validate_token (Function — src/auth/service.py:42)
  Direct dependents (3):
    check_permissions   src/auth/permissions.py:18
    require_auth        src/auth/decorators.py:7
    middleware_auth     src/middleware/auth.py:31

  Transitive dependents (11):
    process_payment     src/payments/processor.py:56
    create_order        src/orders/service.py:23
    ... (8 more)

  Affected files (5):
    src/auth/permissions.py
    src/auth/decorators.py
    src/middleware/auth.py
    src/payments/processor.py
    src/orders/service.py
```

### Use cases

- Before refactoring: understand the blast radius before changing a shared utility
- Code review: verify a PR's changes are limited to the expected scope
- Dependency triage: identify high-fan-out functions that deserve extra test coverage

---

## Flow tracing

`navegador flow` traces the execution path from one function to another, returning every call chain that connects them.

```bash
navegador flow create_order process_payment
navegador flow handle_request save_to_db --max-paths 5
```

### Options

| Flag | Effect |
|---|---|
| `--max-paths N` | Maximum number of paths to return (default: 3) |
| `--format json` | Machine-readable output |

### Output

```
Paths from create_order to process_payment:

Path 1 (3 hops):
  create_order  →  validate_cart  →  charge_card  →  process_payment

Path 2 (4 hops):
  create_order  →  apply_discount  →  charge_card  →  process_payment
```

### Use cases

- Debugging: find all code paths that reach a problematic function
- Security review: trace every path to a sensitive operation (e.g., `delete_user`, `transfer_funds`)
- Onboarding: understand how a high-level action maps to low-level implementation

---

## Dead code detection

`navegador dead-code` finds functions and classes that are never called, never imported, and not decorated as entry points.

```bash
navegador dead-code ./src
navegador dead-code ./src --exclude-tests --format json
```

### Options

| Flag | Effect |
|---|---|
| `--exclude-tests` | Skip test files |
| `--min-age-days N` | Only report code not called in the last N days (requires git history) |
| `--format json` | Machine-readable output |
| `--threshold N` | Minimum confidence score to report (0–100, default: 80) |

### Output

```
Potentially dead code (12 items):

  [Function] legacy_hash_password        src/auth/legacy.py:14
  [Function] _format_receipt_v1          src/payments/receipt.py:88
  [Class]    OldPaymentAdapter           src/payments/adapters.py:201
  ...
```

!!! note
    Navegador performs static call graph analysis. Dynamic dispatch, `getattr`, and string-based imports are not traced. Review candidates before deleting them.

### Use cases

- Codebase cleanup: identify safe-to-delete code before a release
- Migration audits: find old adapter classes after a library upgrade

---

## Cycle detection

`navegador cycles` finds circular dependency chains in the call graph and import graph.

```bash
navegador cycles ./src
navegador cycles ./src --type imports
navegador cycles ./src --type calls --format json
```

### Options

| Flag | Effect |
|---|---|
| `--type calls` | Find circular call chains (default) |
| `--type imports` | Find circular import chains |
| `--type both` | Find both |
| `--min-length N` | Only report cycles with at least N nodes (default: 2) |
| `--format json` | Machine-readable output |

### Output

```
Import cycles (2 found):

  Cycle 1 (length 3):
    src/payments/processor.py
    → src/payments/validators.py
    → src/payments/utils.py
    → src/payments/processor.py

  Cycle 2 (length 2):
    src/auth/service.py
    → src/auth/models.py
    → src/auth/service.py
```

### Use cases

- CI gate: fail builds that introduce new circular imports
- Refactoring prep: identify modules to split before a large restructure

---

## Test mapping

`navegador test-map` maps test functions to the production code they exercise, using call graph analysis.

```bash
navegador test-map ./src ./tests
navegador test-map ./src ./tests --target process_payment
navegador test-map ./src ./tests --format json
```

### Options

| Flag | Effect |
|---|---|
| `--target <name>` | Only show tests that cover a specific function |
| `--uncovered` | Show production functions with no covering tests |
| `--format json` | Machine-readable output |

### Output

```
Test coverage map:

  process_payment (src/payments/processor.py:56)
    tests/payments/test_processor.py::test_process_payment_success
    tests/payments/test_processor.py::test_process_payment_duplicate
    tests/integration/test_checkout.py::test_full_checkout_flow

  validate_token (src/auth/service.py:42)
    tests/auth/test_service.py::test_validate_token_valid
    tests/auth/test_service.py::test_validate_token_expired

Uncovered functions (4):
  legacy_hash_password    src/auth/legacy.py:14
  _format_receipt_v1      src/payments/receipt.py:88
  ...
```

### Use cases

- Coverage by semantics, not just lines: see which tests actually call a function
- Regression targeting: when a function changes, which tests should run?
- Review prep: check that new code has corresponding tests before merging

---

## Combining analysis with knowledge

All analysis commands understand the knowledge layer. Add `--include-knowledge` to see rules, concepts, and decisions linked to the affected nodes:

```bash
navegador impact process_payment --include-knowledge
```

Output will include knowledge nodes like:

```
  Governed by:
    Rule: RequireIdempotencyKey (critical)
    Concept: Idempotency
  Decisions:
    UseStripeForPayments (accepted, 2025-01-15)
```

---

## Python API

```python
from navegador.graph import GraphStore
from navegador.analysis import (
    ImpactAnalyzer,
    FlowTracer,
    DeadCodeDetector,
    CycleDetector,
    TestMapper,
)

store = GraphStore.sqlite(".navegador/navegador.db")

# impact analysis
analyzer = ImpactAnalyzer(store)
result = analyzer.analyze("validate_token", depth=3)
print(result.direct_dependents)
print(result.transitive_dependents)

# flow tracing
tracer = FlowTracer(store)
paths = tracer.trace("create_order", "process_payment", max_paths=5)
for path in paths:
    print(" -> ".join(path.nodes))

# dead code
detector = DeadCodeDetector(store)
candidates = detector.find("./src", exclude_tests=True)
for item in candidates:
    print(f"{item.label}: {item.name}  {item.file}:{item.line}")

# cycle detection
cycle_detector = CycleDetector(store)
cycles = cycle_detector.find_import_cycles("./src")
for cycle in cycles:
    print(" -> ".join(cycle.path))

# test mapping
mapper = TestMapper(store)
coverage = mapper.map("./src", "./tests")
for fn, tests in coverage.items():
    print(f"{fn}: {len(tests)} tests")
```

See the [Analysis API reference](../api/analysis.md) for full method signatures.
