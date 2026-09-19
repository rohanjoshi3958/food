# Backend Tests

## Overview

This directory contains automated backend tests for:

- **Authentication lifecycle** (`test_auth.py`) — login, logout, password reset, expired sessions, and cross-user access
- **Receipt → inventory E2E flow** (`test_receipt_to_inventory_e2e.py`)
- **Ingredient deduction** — unit conversions, serving sizes, pantry updates (`test_ingredient_deduction.py`)
- **Ingredient merging** — combining duplicate entries (`test_ingredient_merge.py`)
- **Upload storage** — object keys, local + S3 backends, presigned-URL serving (`test_storage.py`) and the receipt / meal → cookbook flows in S3 mode and the local fallback (`test_uploads_flow.py`). S3 is exercised through `FakeS3Client` in `conftest.py` (patched `boto3.client`), so no AWS credentials or network are needed.

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

1. **`test_complete_receipt_flow`** — upload (202 `processing`), poll until `pending_review`, review, confirm, verify inventory
2. **`test_receipt_cancellation`** — cancel before confirm, no ingredients created
3. **`test_receipt_with_item_removal`** — remove items during review
4. **`test_multiple_receipts_flow`** — sequential receipt processing

`TestAsyncReceiptAnalysis` covers the accept → job id → poll contract: upload
returns before analysis runs, `GET /api/receipts/{id}` reports
`analysis_stage` progress and the final result, failures surface as `failed`
with `analysis_error`, stale `processing` receipts time out on poll, and late
results are dropped for receipts the user already discarded.

Key features:

- Mocked Anthropic API (no real API calls)
- Isolated SQLite database per test
- FastAPI TestClient for HTTP requests

Fixtures in `conftest.py`: `test_db`, `client`, `test_user`, `auth_headers`, `mock_receipt_image`, `sample_receipt_response`, `sample_nutrition_estimates`, `create_mock_anthropic_response`, `local_uploads` (per-test local upload dirs), `fake_s3` (sets `UPLOADS_BUCKET` and swaps in an in-memory S3 client)

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
