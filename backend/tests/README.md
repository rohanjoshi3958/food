# Backend Tests

## Overview

This directory contains automated backend tests for core inventory operations, including:

- Ingredient deduction logic (unit conversions, quantity calculations, serving sizes)
- Ingredient merging (combining duplicate entries)
- Unit normalization and parsing
- Name matching and fuzzy search

## Running Tests

### Prerequisites

Install test dependencies:

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
# Test ingredient deduction
pytest tests/test_ingredient_deduction.py

# Test ingredient merge
pytest tests/test_ingredient_merge.py
```

### Run with Coverage

```bash
pytest --cov=app --cov-report=html
```

This will generate a coverage report in `htmlcov/index.html`.

### Run Specific Test Classes or Functions

```bash
# Run a specific test class
pytest tests/test_ingredient_deduction.py::TestParseNumber

# Run a specific test function
pytest tests/test_ingredient_deduction.py::TestParseNumber::test_parse_fraction
```

### Verbose Output

```bash
pytest -v
```

### Show Print Statements

```bash
pytest -s
```

## Test Coverage

The test suite covers the following acceptance criteria from FOOD-13:

### ✅ Exact unit matches
- `TestDeductionScenarios::test_sufficient_inventory_partial_depletion`
- `TestConvertAmount::test_exact_unit_match_returns_same_quantity`

### ✅ Weight conversion
- `TestConvertAmount::test_weight_conversion_*` (multiple tests)
- `TestDeductionScenarios::test_weight_to_weight_different_units`

### ✅ Volume conversion
- `TestConvertAmount::test_volume_conversion_*` (multiple tests)
- `TestDeductionScenarios::test_volume_to_volume_different_units`

### ✅ Fractional quantities
- `TestParseNumber::test_parse_fraction`
- `TestDeductionScenarios::test_fractional_quantity_deduction`

### ✅ Package units
- `TestDeductionScenarios::test_package_unit_deduction`
- `TestUnitAliases::test_package_units_covered`

### ✅ Insufficient inventory
- `TestDeductionScenarios::test_insufficient_inventory`

### ✅ Full depletion
- `TestDeductionScenarios::test_full_depletion`

### ✅ Partial depletion
- `TestDeductionScenarios::test_sufficient_inventory_partial_depletion`

### ✅ Unknown units
- `TestDeductionScenarios::test_unknown_unit_deduction`
- `TestNormalizeUnit::test_unknown_unit`

### ✅ Ingredient name matching
- `TestFindMatchingIngredient::*` (comprehensive test class)
- Tests cover exact, case-insensitive, partial, and fuzzy matching

## Test Structure

### `test_ingredient_deduction.py`
Tests for the deduction service (`app/services/ingredient_deduction.py`):
- Unit parsing and normalization
- Weight and volume conversions
- Quantity formatting
- Ingredient name matching
- Deduction scenarios (partial, full, insufficient inventory)

### `test_ingredient_merge.py`
Tests for the merge service (`app/services/ingredient_merge.py`):
- Merging duplicate ingredients
- Quantity summation
- Unit normalization during merge
- Preservation of nutrition data

## CI Integration

These tests should be run in your CI pipeline:

```yaml
# Example GitHub Actions
- name: Run backend tests
  run: |
    cd backend
    pip install -r requirements.txt
    pytest --cov=app --cov-report=xml
```

## Writing New Tests

When adding new inventory features:

1. Create test cases that cover edge cases
2. Use the `MockIngredient` class for simple unit tests
3. Test both success and failure scenarios
4. Include tests for unit conversions if applicable
5. Follow the existing test naming conventions
