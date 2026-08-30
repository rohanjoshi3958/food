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

# Must match app.services.ingredient_deduction.PACKAGE_UNITS
PACKAGE_UNITS = {
    "each",
    "bag",
    "box",
    "can",
    "bottle",
    "pack",
    "bunch",
    "head",
}


def _effective_unit(unit: str | None) -> str:
    if unit is None or unit.strip() == "":
        return "each"
    return unit.strip()


def _is_valid_comma_grouped_digits(value: str) -> bool:
    """Return True when commas separate thousands correctly (e.g. 1,000,000)."""
    if not value.isascii():
        return False
    if "," not in value:
        return value.isdigit()

    parts = value.split(",")
    if not parts[0].isdigit() or not 1 <= len(parts[0]) <= 3:
        return False
    for part in parts[1:]:
        if len(part) != 3 or not part.isdigit():
            return False
    return True


def _parse_quantity_value(quantity_str: str) -> float | None:
    """Parse a plain decimal quantity, allowing comma thousand separators."""
    if "." in quantity_str:
        integer_part, fractional_part = quantity_str.split(".", 1)
        if "." in fractional_part:
            return None
    else:
        integer_part = quantity_str
        fractional_part = ""

    if not integer_part:
        return None
    if not _is_valid_comma_grouped_digits(integer_part):
        return None
    if fractional_part and not fractional_part.isdigit():
        return None

    normalized = integer_part.replace(",", "")
    if fractional_part:
        normalized += f".{fractional_part}"
    return float(normalized)


def validate_quantity(
    quantity: str | None,
    unit: str | None = None,
    *,
    assume_default_unit: bool = False,
) -> tuple[Literal[True], None] | tuple[Literal[False], str]:
    """
    Validate ingredient quantity.
    
    Valid behavior:
    - None/empty: valid (defaults to "1")
    - Positive plain decimals: valid (e.g., "1", "1.5", "1,000,000")
    - Package units: whole numbers only (e.g., "1", "2", "1,000")
    - Zero: invalid
    - Negative: invalid
    - Scientific notation, fractions, expressions: invalid
    - Commas: valid only as thousand separators (e.g. "1,000" not "1,00,0,000")
    
    Returns:
        (True, None) if valid
        (False, error_message) if invalid
    """
    # Missing quantity is valid (will default to "1")
    if quantity is None or quantity.strip() == "":
        return True, None
    
    quantity_str = quantity.strip()

    if assume_default_unit:
        effective_unit = _effective_unit(unit)
    elif unit is not None and unit.strip():
        effective_unit = unit.strip()
    else:
        effective_unit = None

    if quantity_str.startswith("-"):
        return False, "Quantity cannot be negative."

    value = _parse_quantity_value(quantity_str)
    if value is None:
        return False, "Quantity must be a number."

    if effective_unit in PACKAGE_UNITS and "." in quantity_str:
        return False, "Package quantities must be whole numbers."

    # Reject zero
    if value == 0:
        return False, "Quantity must be greater than zero."
    
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
    # Validate quantity (missing unit defaults to "each", a package unit)
    quantity_valid, quantity_error = validate_quantity(
        quantity,
        unit,
        assume_default_unit=True,
    )
    if not quantity_valid:
        return False, quantity_error
    
    # Validate unit
    unit_valid, unit_error = validate_unit(unit)
    if not unit_valid:
        return False, unit_error
    
    return True, None
