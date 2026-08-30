# Backend Tests

## Overview

This directory contains automated backend tests for:

- **Receipt → inventory E2E flow** (`test_receipt_to_inventory_e2e.py`)
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

```yaml
- name: Run backend tests
  run: |
    cd backend
    pip install -r requirements.txt
    pytest --cov=app --cov-report=xml
```
