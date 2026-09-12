# Backend Tests

## Overview

This directory contains automated backend tests for:

- **Authentication lifecycle** (`test_auth.py`) — login, logout, password reset, expired sessions, and cross-user access
- **Receipt → inventory E2E flow** (`test_receipt_to_inventory_e2e.py`)
- **Ingredient deduction** — unit conversions, serving sizes, pantry updates (`test_ingredient_deduction.py`)
- **Ingredient merging** — combining duplicate entries (`test_ingredient_merge.py`)
- **Prompt caching breakpoints** on every Claude call site (`test_prompt_caching.py`)
- **Model routing** policy, flag-off guarantee, and escalation wiring (`test_model_router.py`)
- **Quality evals** — receipt accuracy, ingredient match rate, meal-plan acceptability against documented baselines (`evals/`, see `evals/README.md`)

## Running Tests

### Prerequisites

```bash
cd backend
pip install -r requirements.txt
```

### Run All Tests

```bash
cd backend
pytest
```

### Run Specific Test Files

```bash
# Receipt upload → review → confirm → inventory
pytest tests/test_receipt_to_inventory_e2e.py

# Ingredient deduction and unit logic
pytest tests/test_ingredient_deduction.py

# Ingredient merge logic
pytest tests/test_ingredient_merge.py

# Auth sessions, password reset, and authorization
pytest tests/test_auth.py

# Quality evals (mocked; prints a metrics table against baselines.json)
pytest tests/evals

# Quality evals against a real model tier (paid, opt-in)
FOOD_EVAL_LIVE=1 FOOD_EVAL_MODEL=claude-haiku-4-5 pytest tests/evals -m live -s
```

### Run with Coverage

```bash
pytest --cov=app --cov-report=html
```

Coverage report: `htmlcov/index.html`

### Verbose / Single Test

```bash
pytest -v
pytest tests/test_receipt_to_inventory_e2e.py::TestReceiptToInventoryE2E::test_complete_receipt_flow -v
pytest tests/test_ingredient_deduction.py::TestParseNumber::test_parse_fraction -v
```

## Receipt E2E Tests

End-to-end flow:

```
receipt → Claude-shaped response (mocked) → review → confirm → inventory
```

Test cases:

1. **`test_complete_receipt_flow`** — upload, review, confirm, verify inventory
2. **`test_receipt_cancellation`** — cancel before confirm, no ingredients created
3. **`test_receipt_with_item_removal`** — remove items during review
4. **`test_multiple_receipts_flow`** — sequential receipt processing

Key features:

- Mocked Anthropic API (no real API calls)
- Isolated SQLite database per test
- FastAPI TestClient for HTTP requests

Fixtures in `conftest.py`: `test_db`, `client`, `test_user`, `auth_headers`, `mock_receipt_image`, `sample_receipt_response`, `sample_nutrition_estimates`, `create_mock_anthropic_response`

## Inventory Unit Tests

### `test_ingredient_deduction.py`

- Unit parsing and normalization
- Weight and volume conversions
- Package units and serving-based deduction
- Ingredient name matching
- Deduction scenarios (partial, full, insufficient inventory)

### `test_ingredient_merge.py`

- Merging duplicate ingredients
- Quantity summation
- Unit normalization during merge

## CI Integration

GitHub Actions runs this suite on every pull request and push to `main`
(see `.github/workflows/ci.yml`). Anthropic/OpenAI keys are cleared in CI, and
tests mock provider clients so no real AI calls are made.

```yaml
- name: Run backend tests
  working-directory: backend
  env:
    ANTHROPIC_API_KEY: ""
    OPENAI_API_KEY: ""
  run: |
    pip install -r requirements.txt
    pytest --ignore=tests/evals

- name: Quality evals (mocked)
  working-directory: backend
  run: pytest tests/evals
```

The evals step fails the job when a metric in `tests/evals/baselines.json`
regresses and appends the metrics table to the GitHub step summary. Live
(paid) evals never run in CI; they are gated behind `FOOD_EVAL_LIVE=1`.
