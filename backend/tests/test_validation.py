"""Tests for ingredient validation."""

import pytest

from app.validation import validate_quantity, validate_unit, validate_ingredient_input


class TestQuantityValidation:
    """Test quantity validation logic."""
    
    def test_valid_positive_integer(self):
        """Positive integers should be valid."""
        is_valid, error = validate_quantity("5")
        assert is_valid is True
        assert error is None
    
    def test_valid_positive_decimal(self):
        """Positive decimals should be valid."""
        is_valid, error = validate_quantity("1.5")
        assert is_valid is True
        assert error is None
        
        is_valid, error = validate_quantity("2.75")
        assert is_valid is True
        assert error is None

    def test_valid_comma_separated_thousands(self):
        """Correctly grouped comma separators should be valid."""
        for quantity in ("1,000", "1,000,000", "10,000", "1,000.5"):
            is_valid, error = validate_quantity(quantity)
            assert is_valid is True, quantity
            assert error is None
    
    def test_empty_quantity_is_valid(self):
        """Empty/None quantity should be valid (defaults to 1)."""
        is_valid, error = validate_quantity(None)
        assert is_valid is True
        assert error is None
        
        is_valid, error = validate_quantity("")
        assert is_valid is True
        assert error is None
        
        is_valid, error = validate_quantity("  ")
        assert is_valid is True
        assert error is None
    
    def test_reject_zero(self):
        """Zero quantity should be rejected."""
        is_valid, error = validate_quantity("0")
        assert is_valid is False
        assert "greater than zero" in error.lower()
    
    def test_reject_negative(self):
        """Negative quantities should be rejected."""
        is_valid, error = validate_quantity("-5")
        assert is_valid is False
        assert "cannot be negative" in error.lower()
        
        is_valid, error = validate_quantity("-1.5")
        assert is_valid is False
        assert "cannot be negative" in error.lower()
    
    def test_reject_non_numeric(self):
        """Non-numeric quantities should be rejected."""
        is_valid, error = validate_quantity("abc")
        assert is_valid is False
        assert "must be a number" in error.lower()
        
        is_valid, error = validate_quantity("five")
        assert is_valid is False
        assert "must be a number" in error.lower()

    def test_reject_scientific_notation(self):
        """Scientific notation should be rejected."""
        for quantity in ("14e6", "1e-3", "1E6", "2.5e2"):
            is_valid, error = validate_quantity(quantity)
            assert is_valid is False, quantity
            assert "must be a number" in error.lower()

    def test_reject_expressions_and_symbols(self):
        """Parentheses, fractions, and operators should be rejected."""
        invalid = [
            "(48)",
            "1/2",
            "1+2",
            "3-1",
            "1.2.3",
            "+5",
            "1 2",
        ]
        for quantity in invalid:
            is_valid, error = validate_quantity(quantity)
            assert is_valid is False, quantity
            assert "must be a number" in error.lower()

    def test_reject_invalid_comma_grouping(self):
        """Misplaced comma separators should be rejected."""
        invalid = [
            "1,00,0,000",
            "1,0000",
            "12,34",
            "1,23",
            ",000",
            "1,",
        ]
        for quantity in invalid:
            is_valid, error = validate_quantity(quantity)
            assert is_valid is False, quantity
            assert "must be a number" in error.lower()


class TestPackageQuantityValidation:
    """Package units (bags, boxes, etc.) require whole-number quantities."""

    PACKAGE_UNITS = (
        "each",
        "bag",
        "box",
        "can",
        "bottle",
        "pack",
        "bunch",
        "head",
    )

    def test_package_units_reject_decimals(self):
        for unit in self.PACKAGE_UNITS:
            is_valid, error = validate_quantity("1.5", unit)
            assert is_valid is False, unit
            assert "whole number" in error.lower()

    def test_package_units_accept_whole_numbers(self):
        for unit in self.PACKAGE_UNITS:
            for quantity in ("1", "2", "10", "1,000"):
                is_valid, error = validate_quantity(quantity, unit)
                assert is_valid is True, (unit, quantity)
                assert error is None

    def test_non_package_units_allow_decimals(self):
        for unit in ("lb", "oz", "g", "cup", "slice", "clove"):
            is_valid, error = validate_quantity("1.5", unit)
            assert is_valid is True, unit
            assert error is None

    def test_default_unit_rejects_decimal_quantity(self):
        """Missing unit defaults to each, which requires whole numbers."""
        is_valid, error = validate_quantity("1.5", assume_default_unit=True)
        assert is_valid is False
        assert "whole number" in error.lower()

        is_valid, error = validate_ingredient_input("1.5", None)
        assert is_valid is False
        assert "whole number" in error.lower()

        is_valid, error = validate_ingredient_input("1.5", "")
        assert is_valid is False
        assert "whole number" in error.lower()

    def test_decimal_allowed_for_weight_units(self):
        is_valid, error = validate_ingredient_input("1.5", "lb")
        assert is_valid is True
        assert error is None

    def test_whole_package_quantity_with_unit(self):
        is_valid, error = validate_ingredient_input("2", "box")
        assert is_valid is True
        assert error is None


class TestUnitValidation:
    """Test unit validation logic."""
    
    def test_valid_known_units(self):
        """Known units should be valid."""
        known_units = ["lb", "oz", "g", "kg", "each", "cup"]
        for unit in known_units:
            is_valid, error = validate_unit(unit)
            assert is_valid is True, f"Unit '{unit}' should be valid"
            assert error is None
    
    def test_empty_unit_is_valid(self):
        """Empty/None unit should be valid (defaults to 'each')."""
        is_valid, error = validate_unit(None)
        assert is_valid is True
        assert error is None
        
        is_valid, error = validate_unit("")
        assert is_valid is True
        assert error is None
        
        is_valid, error = validate_unit("  ")
        assert is_valid is True
        assert error is None
    
    def test_reject_unknown_unit(self):
        """Unknown units should be rejected."""
        is_valid, error = validate_unit("nonsense")
        assert is_valid is False
        assert "invalid unit" in error.lower()
        
        is_valid, error = validate_unit("foobar")
        assert is_valid is False
        assert "invalid unit" in error.lower()


class TestIngredientInputValidation:
    """Test combined ingredient input validation."""
    
    def test_valid_complete_input(self):
        """Valid quantity and unit should pass."""
        is_valid, error = validate_ingredient_input("5", "lb")
        assert is_valid is True
        assert error is None
    
    def test_valid_missing_both(self):
        """Missing both quantity and unit should be valid."""
        is_valid, error = validate_ingredient_input(None, None)
        assert is_valid is True
        assert error is None
    
    def test_valid_missing_quantity(self):
        """Missing quantity with valid unit should pass."""
        is_valid, error = validate_ingredient_input(None, "lb")
        assert is_valid is True
        assert error is None
    
    def test_valid_missing_unit(self):
        """Valid quantity with missing unit should pass."""
        is_valid, error = validate_ingredient_input("5", None)
        assert is_valid is True
        assert error is None
    
    def test_reject_negative_with_unit(self):
        """Negative quantity with unit should be rejected."""
        is_valid, error = validate_ingredient_input("-5", "lb")
        assert is_valid is False
        assert "cannot be negative" in error.lower()
    
    def test_reject_non_numeric_with_unit(self):
        """Non-numeric quantity with unit should be rejected."""
        is_valid, error = validate_ingredient_input("abc", "lb")
        assert is_valid is False
        assert "must be a number" in error.lower()
    
    def test_reject_zero_with_unit(self):
        """Zero quantity with unit should be rejected."""
        is_valid, error = validate_ingredient_input("0", "lb")
        assert is_valid is False
        assert "greater than zero" in error.lower()
    
    def test_reject_valid_quantity_with_invalid_unit(self):
        """Valid quantity with invalid unit should be rejected."""
        is_valid, error = validate_ingredient_input("1", "nonsense")
        assert is_valid is False
        assert "invalid unit" in error.lower()


class TestAcceptanceCriteria:
    """Test the specific acceptance criteria from the Linear issue."""
    
    def test_reject_negative_5_lb(self):
        """-5 lb should be rejected."""
        is_valid, error = validate_ingredient_input("-5", "lb")
        assert is_valid is False
    
    def test_reject_abc_lb(self):
        """abc lb should be rejected."""
        is_valid, error = validate_ingredient_input("abc", "lb")
        assert is_valid is False
    
    def test_reject_0_lb(self):
        """0 lb should be rejected."""
        is_valid, error = validate_ingredient_input("0", "lb")
        assert is_valid is False
    
    def test_reject_1_nonsense(self):
        """1 nonsense should be rejected."""
        is_valid, error = validate_ingredient_input("1", "nonsense")
        assert is_valid is False

    def test_reject_scientific_notation_with_unit(self):
        """14e6 lb should be rejected."""
        is_valid, error = validate_ingredient_input("14e6", "lb")
        assert is_valid is False
        assert "must be a number" in error.lower()
