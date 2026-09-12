# Backend Tests

## Overview

This directory contains automated backend tests for:

- **Golden-path smoke / cost baseline** (`test_smoke_golden_path.py`) — signup → upload receipt → confirm → generate meal → cookbook through the HTTP API, with per-stage Claude call counts and models pinned. This is the QA gate for cost-optimization PRs; see `docs/qa.md`.
- **Meal generation call budget** (`test_meal_generator.py`) — parsing, calorie retry loop, attempt cap, and fallback behaviour
- **Authentication lifecycle** (`test_auth.py`) — login, logout, password reset, expired sessions, and cross-user access
- **Receipt → inventory E2E flow** (`test_receipt_to_inventory_e2e.py`)
- **OCR-first receipt pipeline (FOOD-55)** — `test_receipt_preprocess.py` (hash, downsample), `test_receipt_ocr.py` (Tesseract wrapper), `test_receipt_parser.py` (deterministic parser), `test_receipt_gates.py` (confidence gates), `test_receipt_pipeline.py` (cache hit/miss, escalation ladder, outcome fields), `test_receipt_ocr_first_e2e.py` (flag ON end-to-end with mocked OCR text), `test_receipt_evals.py` (eval scaffold + scoring)
- **Ingredient deduction** — unit conversions, serving sizes, pantry updates (`test_ingredient_deduction.py`)
- **Ingredient merging** — combining duplicate entries (`test_ingredient_merge.py`)

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

Fixtures in `conftest.py`: `test_db`, `client`, `test_user`, `auth_headers`, `mock_receipt_image`, `real_receipt_image`, `ocr_receipt_text`, `sample_receipt_response`, `sample_nutrition_estimates`, `create_mock_anthropic_response`, `build_anthropic_router`

### OCR / Tesseract in tests

No test needs a live Anthropic key. Tesseract is **not** required either: the
OCR-first tests patch `run_tesseract` with text fixtures. The one live smoke
test (`test_receipt_ocr.py::TestLiveTesseract`) is skipped automatically when
the `tesseract` binary is not on `PATH`. Install it locally with
`sudo apt-get install tesseract-ocr` / `brew install tesseract` to run it.

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
    pytest
```
