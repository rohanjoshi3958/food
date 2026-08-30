"""Validation utilities for ingredient input."""

from typing import Literal

# Valid ingredient units - must match frontend units.ts
INGREDIENT_UNITS = [
    "each",
    "lb",
    "oz",
    "g",
    "kg",
    "ml",
    "l",
    "fl oz",
    "cup",
    "pint",
    "quart",
    "gallon",
    "tbsp",
    "tsp",
    "bunch",
    "bag",
    "box",
    "can",
    "bottle",
    "pack",
    "slice",
    "head",
    "clove",
]


def validate_quantity(quantity: str | None) -> tuple[Literal[True], None] | tuple[Literal[False], str]:
    """
    Validate ingredient quantity.
    
    Valid behavior:
    - None/empty: valid (defaults to "1")
    - Positive numbers (including fractional): valid (e.g., "1.5", "2.75")
    - Zero: invalid
    - Negative: invalid
    - Non-numeric: invalid
    
    Returns:
        (True, None) if valid
        (False, error_message) if invalid
    """
    # Missing quantity is valid (will default to "1")
    if quantity is None or quantity.strip() == "":
        return True, None
    
    quantity_str = quantity.strip()
    
    # Try to parse as float
    try:
        value = float(quantity_str)
    except ValueError:
        return False, "Quantity must be a number."
    
    # Reject zero
    if value == 0:
        return False, "Quantity must be greater than zero."
    
    # Reject negative
    if value < 0:
        return False, "Quantity cannot be negative."
    
    # Fractional numbers are valid
    return True, None


def validate_unit(unit: str | None) -> tuple[Literal[True], None] | tuple[Literal[False], str]:
    """
    Validate ingredient unit.
    
    Valid behavior:
    - None/empty: valid (defaults to "each")
    - Known unit: valid
    - Unknown unit: invalid
    
    Returns:
        (True, None) if valid
        (False, error_message) if invalid
    """
    # Missing unit is valid (will default to "each")
    if unit is None or unit.strip() == "":
        return True, None
    
    unit_str = unit.strip()
    
    # Check if unit is in the list of known units
    if unit_str not in INGREDIENT_UNITS:
        return False, f"Invalid unit. Please select a valid unit from the list."
    
    return True, None


def validate_ingredient_input(
    quantity: str | None,
    unit: str | None,
) -> tuple[Literal[True], None] | tuple[Literal[False], str]:
    """
    Validate both quantity and unit for ingredient input.
    
    Returns:
        (True, None) if valid
        (False, error_message) if invalid
    """
    # Validate quantity
    quantity_valid, quantity_error = validate_quantity(quantity)
    if not quantity_valid:
        return False, quantity_error
    
    # Validate unit
    unit_valid, unit_error = validate_unit(unit)
    if not unit_valid:
        return False, unit_error
    
    return True, None
