# Receipt to Inventory End-to-End Tests

This directory contains comprehensive end-to-end tests for the receipt scanning → inventory flow.

## Test Coverage

The tests cover the complete workflow:

```
receipt
 ↓
Claude-shaped response (mocked)
 ↓
review  
 ↓
confirm
 ↓
inventory
```

## Test Cases

1. **`test_complete_receipt_flow`** - Tests the full happy path:
   - Upload a receipt image
   - Mock Claude API to return parsed items and nutrition
   - Review and edit draft items
   - Confirm receipt to save to inventory
   - Verify ingredients appear in the inventory

2. **`test_receipt_cancellation`** - Tests cancelling a receipt review:
   - Upload receipt
   - Cancel before confirming
   - Verify no ingredients were created

3. **`test_receipt_with_item_removal`** - Tests removing items during review:
   - Upload receipt with multiple items
   - Remove some items before confirming
   - Verify only kept items are in inventory

4. **`test_multiple_receipts_flow`** - Tests sequential receipt processing:
   - Upload and confirm first receipt
   - Upload and confirm second receipt
   - Verify all ingredients from both receipts are in inventory

## Running the Tests

```bash
cd backend
python3 -m pytest tests/test_receipt_to_inventory_e2e.py -v
```

Run a single test:

```bash
python3 -m pytest tests/test_receipt_to_inventory_e2e.py::TestReceiptToInventoryE2E::test_complete_receipt_flow -v
```

## Key Features

- **Mocked Claude API**: Tests use mocked Anthropic API responses instead of making real API calls
- **Isolated Database**: Each test uses a fresh SQLite database  
- **No External Dependencies**: Tests run completely offline
- **Fast Execution**: Full test suite runs in under 5 seconds

## Architecture

The tests use:
- `pytest` for test framework
- `unittest.mock` to mock the Anthropic Claude API
- SQLite in-memory database for test isolation
- FastAPI TestClient for HTTP requests

## Test Fixtures

- `test_db`: Creates a fresh SQLite database for each test
- `client`: FastAPI test client with mocked dependencies
- `test_user`: Pre-created test user with authentication
- `auth_headers`: Authentication headers for API requests
- `mock_receipt_image`: Fake receipt image file
- `sample_receipt_response`: Sample Claude API response for receipt parsing
- `sample_nutrition_estimates`: Sample Claude API responses for nutrition estimation
