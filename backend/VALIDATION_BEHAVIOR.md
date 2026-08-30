# Ingredient Quantity Validation Behavior

This document defines the validation rules for ingredient quantity and unit inputs when users manually add ingredients to their inventory.

## Validation Rules

### Quantity Validation

#### ✅ Valid (Accepted)
- **Positive integers**: `1`, `5`, `10`
- **Positive decimals/fractional**: `1.5`, `2.75`, `0.5`
- **Missing/empty quantity**: `null`, `""`, `"  "` → defaults to `"1"`

#### ❌ Invalid (Rejected)
- **Zero**: `0` → Error: "Quantity must be greater than zero."
- **Negative numbers**: `-5`, `-1.5` → Error: "Quantity cannot be negative."
- **Non-numeric values**: `abc`, `five` → Error: "Quantity must be a number."

### Unit Validation

#### ✅ Valid (Accepted)
- **Known units**: Any unit from the predefined list:
  - Weight: `lb`, `oz`, `g`, `kg`
  - Volume: `ml`, `l`, `fl oz`, `cup`, `pint`, `quart`, `gallon`
  - Other: `tbsp`, `tsp`, `each`, `bunch`, `bag`, `box`, `can`, `bottle`, `pack`, `slice`, `head`, `clove`
- **Missing/empty unit**: `null`, `""`, `"  "` → defaults to `"each"`

#### ❌ Invalid (Rejected)
- **Unknown units**: `nonsense`, `foobar` → Error: "Invalid unit. Please select a valid unit from the list."

## Edge Cases

| Case | Behavior | Example |
|------|----------|---------|
| Zero quantity | Rejected | `0 lb` → "Quantity must be greater than zero." |
| Negative quantity | Rejected | `-5 lb` → "Quantity cannot be negative." |
| Fractional quantity | Accepted | `1.5 lb` → Valid |
| Missing quantity | Accepted | `null, "lb"` → Valid (defaults to `"1"`) |
| Missing unit | Accepted | `"5", null` → Valid (defaults to `"each"`) |
| Both missing | Accepted | `null, null` → Valid (defaults to `"1 each"`) |

## Acceptance Criteria (FOOD-25)

All requirements from the Linear issue FOOD-25 have been implemented:

- [x] Reject `-5 lb` (negative quantity)
- [x] Reject `abc lb` (non-numeric quantity)  
- [x] Reject `0 lb` (zero quantity)
- [x] Reject `1 nonsense` (invalid unit)

## Implementation

### Backend
- `backend/app/validation.py`: Core validation logic
- `backend/app/routers/ingredients.py`: API endpoint integration
- `backend/tests/test_validation.py`: Comprehensive test suite (21 tests, all passing)

### Frontend
- Frontend unit select already restricts to valid units via dropdown
- Error messages from backend validation are displayed in the UI automatically
- No frontend changes required

## Testing

Run validation tests:
```bash
cd backend
pytest tests/test_validation.py -v
```

All 21 tests pass, including specific tests for each acceptance criteria.
