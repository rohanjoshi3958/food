"""Tests for ingredient deduction service."""

import pytest
from app.services.ingredient_deduction import (
    _amount_used_in_pantry_units,
    _convert_amount,
    _find_matching_ingredient,
    _format_quantity,
    normalize_unit,
    parse_amount,
    parse_number,
    servings_per_pantry_unit,
    UNIT_ALIASES,
    WEIGHT_TO_GRAMS,
    VOLUME_TO_ML,
)


class TestParseNumber:
    """Tests for parse_number function."""

    def test_parse_integer(self):
        assert parse_number("5") == 5.0

    def test_parse_float(self):
        assert parse_number("3.5") == 3.5

    def test_parse_fraction(self):
        assert parse_number("1/2") == 0.5
        assert parse_number("1/4") == 0.25
        assert parse_number("3/4") == 0.75

    def test_parse_mixed_fraction(self):
        # Note: "1 1/2" won't parse without special handling
        # but "3/2" works
        assert parse_number("3/2") == 1.5

    def test_parse_with_whitespace(self):
        assert parse_number("  5  ") == 5.0
        assert parse_number("  1/2  ") == 0.5

    def test_parse_empty_string(self):
        assert parse_number("") is None
        assert parse_number("   ") is None

    def test_parse_invalid_string(self):
        assert parse_number("abc") is None
        assert parse_number("not a number") is None

    def test_parse_division_by_zero(self):
        assert parse_number("1/0") is None


class TestNormalizeUnit:
    """Tests for normalize_unit function."""

    def test_exact_unit_match(self):
        assert normalize_unit("g") == "g"
        assert normalize_unit("kg") == "kg"
        assert normalize_unit("oz") == "oz"
        assert normalize_unit("cup") == "cup"
        assert normalize_unit("each") == "each"

    def test_unit_aliases(self):
        assert normalize_unit("gram") == "g"
        assert normalize_unit("grams") == "g"
        assert normalize_unit("kilogram") == "kg"
        assert normalize_unit("kilograms") == "kg"
        assert normalize_unit("ounce") == "oz"
        assert normalize_unit("ounces") == "oz"
        assert normalize_unit("pound") == "lb"
        assert normalize_unit("pounds") == "lb"

    def test_volume_units(self):
        cases = [
            ("ml", "ml"),
            ("milliliter", "ml"),
            ("milliliters", "ml"),
            ("ML", "ml"),
            ("l", "l"),
            ("liter", "l"),
            ("liters", "l"),
            ("litre", "l"),
            ("litres", "l"),
            ("cup", "cup"),
            ("cups", "cup"),
            ("tbsp", "tbsp"),
            ("tablespoon", "tbsp"),
            ("tablespoons", "tbsp"),
            ("tsp", "tsp"),
            ("teaspoon", "tsp"),
            ("teaspoons", "tsp"),
            ("pint", "pint"),
            ("pints", "pint"),
            ("quart", "quart"),
            ("quarts", "quart"),
            ("gallon", "gallon"),
            ("gallons", "gallon"),
            ("fl oz", "fl oz"),
            ("FL OZ", "fl oz"),
        ]

        for raw, expected in cases:
            assert normalize_unit(raw) == expected

    def test_package_units(self):
        cases = [
            ("each", "each"),
            ("item", "each"),
            ("items", "each"),
            ("bunch", "bunch"),
            ("bunches", "bunch"),
            ("bag", "bag"),
            ("bags", "bag"),
            ("box", "box"),
            ("boxes", "box"),
            ("can", "can"),
            ("cans", "can"),
            ("bottle", "bottle"),
            ("bottles", "bottle"),
            ("pack", "pack"),
            ("packs", "pack"),
            ("slice", "slice"),
            ("slices", "slice"),
            ("head", "head"),
            ("heads", "head"),
            ("clove", "clove"),
            ("cloves", "clove"),
        ]

        for raw, expected in cases:
            assert normalize_unit(raw) == expected

    def test_case_insensitive(self):
        assert normalize_unit("G") == "g"
        assert normalize_unit("KG") == "kg"
        assert normalize_unit("Kilogram") == "kg"
        assert normalize_unit("OZ") == "oz"
        assert normalize_unit("CUP") == "cup"

    def test_with_whitespace(self):
        assert normalize_unit("  g  ") == "g"
        assert normalize_unit("  kilograms  ") == "kg"
        assert normalize_unit("fl oz") == "fl oz"

    def test_with_descriptors(self):
        # Should extract the unit from "cup dry"
        result = normalize_unit("cup dry")
        assert result == "cup"

    def test_none_input(self):
        assert normalize_unit(None) is None

    def test_empty_string(self):
        assert normalize_unit("") is None

    def test_unknown_unit(self):
        result = normalize_unit("unknown_unit")
        assert result == "unknown_unit"


class TestParseAmount:
    """Tests for parse_amount function."""

    def test_parse_with_unit(self):
        quantity, unit = parse_amount("2 cups")
        assert quantity == 2.0
        assert unit == "cup"

    def test_parse_fraction_with_unit(self):
        quantity, unit = parse_amount("1/2 cup")
        assert quantity == 0.5
        assert unit == "cup"

    def test_parse_with_decimal(self):
        quantity, unit = parse_amount("1.5 cups")
        assert quantity == 1.5
        assert unit == "cup"

        quantity, unit = parse_amount("0.25 tsp")
        assert quantity == 0.25
        assert unit == "tsp"

        quantity, unit = parse_amount("2.5oz")
        assert quantity == 2.5
        assert unit == "oz"

    def test_parse_with_unit_alias(self):
        quantity, unit = parse_amount("3 ounces")
        assert quantity == 3.0
        assert unit == "oz"

    def test_parse_no_space_between_number_and_unit(self):
        quantity, unit = parse_amount("500ml")
        assert quantity == 500.0
        assert unit == "ml"

    def test_parse_with_parenthetical(self):
        quantity, unit = parse_amount("1/2 cup (48g)")
        assert quantity == 0.5
        assert unit == "cup"

    def test_parse_empty_string(self):
        quantity, unit = parse_amount("")
        assert quantity is None
        assert unit is None

    def test_parse_unit_only(self):
        quantity, unit = parse_amount("cups")
        assert quantity is None
        assert unit == "cup"


class TestConvertAmount:
    """Tests for weight and volume conversions."""

    def test_weight_conversion_g_to_kg(self):
        result = _convert_amount(1000.0, "g", "kg")
        assert result == pytest.approx(1.0)

    def test_weight_conversion_kg_to_g(self):
        result = _convert_amount(1.0, "kg", "g")
        assert result == pytest.approx(1000.0)

    def test_weight_conversion_oz_to_g(self):
        result = _convert_amount(1.0, "oz", "g")
        assert result == pytest.approx(28.3495)

    def test_weight_conversion_lb_to_oz(self):
        result = _convert_amount(1.0, "lb", "oz")
        assert result == pytest.approx(16.0, rel=0.01)

    def test_weight_conversion_g_to_oz(self):
        result = _convert_amount(28.3495, "g", "oz")
        assert result == pytest.approx(1.0)

    def test_volume_conversion_ml_to_l(self):
        result = _convert_amount(1000.0, "ml", "l")
        assert result == pytest.approx(1.0)

    def test_volume_conversion_l_to_ml(self):
        result = _convert_amount(1.0, "l", "ml")
        assert result == pytest.approx(1000.0)

    def test_volume_conversion_cup_to_ml(self):
        result = _convert_amount(1.0, "cup", "ml")
        assert result == pytest.approx(236.588)

    def test_volume_conversion_tbsp_to_tsp(self):
        result = _convert_amount(1.0, "tbsp", "tsp")
        assert result == pytest.approx(3.0, rel=0.01)

    def test_volume_conversion_gallon_to_quart(self):
        result = _convert_amount(1.0, "gallon", "quart")
        assert result == pytest.approx(4.0, rel=0.01)

    def test_volume_conversion_pint_to_cup(self):
        result = _convert_amount(1.0, "pint", "cup")
        assert result == pytest.approx(2.0, rel=0.01)

    def test_exact_unit_match_returns_same_quantity(self):
        result = _convert_amount(5.0, "g", "g")
        assert result == 5.0

    def test_convert_with_unit_aliases(self):
        # Should work with aliases
        result = _convert_amount(1.0, "gram", "grams")
        assert result == 1.0

    def test_convert_incompatible_units_returns_none(self):
        # Can't convert weight to volume
        result = _convert_amount(100.0, "g", "ml")
        assert result is None

    def test_convert_incompatible_weight_to_count(self):
        result = _convert_amount(100.0, "g", "each")
        assert result is None

    def test_convert_with_none_units(self):
        result = _convert_amount(5.0, None, "g")
        assert result is None

        result = _convert_amount(5.0, "g", None)
        assert result is None

    def test_fractional_conversion(self):
        # 0.5 cups to ml
        result = _convert_amount(0.5, "cup", "ml")
        assert result == pytest.approx(118.294)

    def test_package_amounts_same_unit_or_alias(self):
        # Package units never convert across types (can ≠ bottle). Only exact
        # matches or aliases to the same canonical unit are valid.
        assert _convert_amount(2.0, "each", "each") == 2.0
        assert _convert_amount(1.0, "can", "can") == 1.0
        assert _convert_amount(3.0, "bag", "bag") == 3.0
        assert _convert_amount(2.0, "item", "each") == 2.0
        assert _convert_amount(3.0, "cans", "can") == 3.0

    def test_package_amounts_cannot_convert_across_types(self):
        incompatible_pairs = [
            ("can", "bottle"),
            ("bag", "box"),
            ("bunch", "head"),
            ("each", "can"),
            ("slice", "clove"),
            ("can", "oz"),
            ("bag", "cup"),
            ("each", "g"),
        ]

        for from_unit, to_unit in incompatible_pairs:
            assert _convert_amount(1.0, from_unit, to_unit) is None


class TestFormatQuantity:
    """Tests for _format_quantity function."""

    def test_format_integer(self):
        assert _format_quantity(5.0) == "5"

    def test_format_float(self):
        result = _format_quantity(3.5)
        assert "3.5" in result

    def test_format_very_small_number(self):
        result = _format_quantity(0.0001)
        assert result == "0.0001"

    def test_format_rounds_near_integer(self):
        # Should round very close to integer
        assert _format_quantity(4.9999999) == "5"

    def test_format_zero(self):
        assert _format_quantity(0.0) == "0"


class MockIngredient:
    """Mock ingredient for testing without database."""

    def __init__(
        self,
        id="test-id",
        name="Test Ingredient",
        quantity="100",
        unit="g",
        serving_size=None,
        servings_per_container=None,
    ):
        self.id = id
        self.name = name
        self.quantity = quantity
        self.unit = unit
        self.serving_size = serving_size
        self.servings_per_container = servings_per_container


class TestFindMatchingIngredient:
    """Tests for ingredient name matching."""

    def test_exact_name_match(self):
        ingredients = [
            MockIngredient(id="1", name="Chicken Breast"),
            MockIngredient(id="2", name="Brown Rice"),
        ]
        result = _find_matching_ingredient(ingredients, "Chicken Breast")
        assert result is not None
        assert result.id == "1"

    def test_case_insensitive_match(self):
        ingredients = [MockIngredient(name="Chicken Breast")]
        result = _find_matching_ingredient(ingredients, "chicken breast")
        assert result is not None

    def test_whitespace_normalized_match(self):
        ingredients = [MockIngredient(name="Chicken  Breast")]
        result = _find_matching_ingredient(ingredients, "Chicken Breast")
        assert result is not None

    def test_partial_match_substring(self):
        ingredients = [MockIngredient(id="1", name="Organic Chicken Breast")]
        result = _find_matching_ingredient(ingredients, "Chicken Breast")
        assert result is not None

    def test_partial_match_single_result(self):
        ingredients = [MockIngredient(id="1", name="Brown Rice")]
        result = _find_matching_ingredient(ingredients, "Rice")
        assert result is not None
        assert result.id == "1"

    def test_partial_match_multiple_results_returns_none(self):
        ingredients = [
            MockIngredient(id="1", name="Brown Rice"),
            MockIngredient(id="2", name="White Rice"),
        ]
        result = _find_matching_ingredient(ingredients, "Rice")
        # Should return None when multiple partial matches
        assert result is None

    def test_no_match_returns_none(self):
        ingredients = [MockIngredient(name="Chicken Breast")]
        result = _find_matching_ingredient(ingredients, "Tofu")
        assert result is None

    def test_empty_name_returns_none(self):
        ingredients = [MockIngredient(name="Chicken Breast")]
        result = _find_matching_ingredient(ingredients, "")
        assert result is None

    def test_empty_list_returns_none(self):
        result = _find_matching_ingredient([], "Chicken")
        assert result is None


class TestAmountUsedInPantryUnits:
    """Tests for converting used amounts to pantry units."""

    def test_exact_unit_match(self):
        ingredient = MockIngredient(quantity="100", unit="g")
        result = _amount_used_in_pantry_units(ingredient, 50.0, "g")
        assert result == 50.0

    def test_weight_conversion(self):
        ingredient = MockIngredient(quantity="1", unit="kg")
        result = _amount_used_in_pantry_units(ingredient, 500.0, "g")
        assert result == pytest.approx(0.5)

    def test_volume_conversion(self):
        ingredient = MockIngredient(quantity="1000", unit="ml")
        result = _amount_used_in_pantry_units(ingredient, 1.0, "l")
        # 1 liter = 1000 ml in pantry units
        assert result == pytest.approx(1000.0)

    def test_incompatible_units_with_servings(self):
        # Using tbsp when pantry is "each" with serving info
        ingredient = MockIngredient(
            quantity="1",
            unit="each",
            serving_size="2 tbsp",
            servings_per_container=16,
        )
        result = _amount_used_in_pantry_units(ingredient, 2.0, "tbsp")
        # Should convert via servings: 2 tbsp = 1 serving, 1 serving / 16 servings = 0.0625 units
        assert result == pytest.approx(0.0625)

    def test_incompatible_units_without_servings_returns_none(self):
        ingredient = MockIngredient(quantity="1", unit="each")
        result = _amount_used_in_pantry_units(ingredient, 50.0, "g")
        assert result is None

    def test_package_same_unit(self):
        ingredient = MockIngredient(quantity="2", unit="can")
        result = _amount_used_in_pantry_units(ingredient, 1.0, "can")
        assert result == 1.0

    def test_package_unit_alias(self):
        ingredient = MockIngredient(quantity="3", unit="each")
        result = _amount_used_in_pantry_units(ingredient, 1.0, "item")
        assert result == 1.0

    def test_package_slice_via_servings(self):
        ingredient = MockIngredient(
            quantity="1",
            unit="each",
            serving_size="1 slice",
            servings_per_container=8,
        )
        result = _amount_used_in_pantry_units(ingredient, 2.0, "slice")
        assert result == pytest.approx(0.25)

    def test_package_different_types_returns_none(self):
        ingredient = MockIngredient(quantity="2", unit="bottle")
        result = _amount_used_in_pantry_units(ingredient, 1.0, "can")
        assert result is None


class TestDeductionScenarios:
    """Integration tests for various deduction scenarios."""

    def test_sufficient_inventory_partial_depletion(self):
        """Test partial deduction from inventory."""
        ingredient = MockIngredient(quantity="100", unit="g")
        used_quantity = 25.0
        used_unit = "g"

        converted = _amount_used_in_pantry_units(ingredient, used_quantity, used_unit)
        assert converted == 25.0

        remaining = 100.0 - converted
        assert remaining == 75.0

    def test_insufficient_inventory(self):
        """Test when requested amount exceeds inventory."""
        ingredient = MockIngredient(quantity="50", unit="g")
        used_quantity = 100.0
        used_unit = "g"

        converted = _amount_used_in_pantry_units(ingredient, used_quantity, used_unit)
        assert converted == 100.0

        remaining = 50.0 - converted
        assert remaining < 0

    def test_full_depletion(self):
        """Test exact depletion of inventory."""
        ingredient = MockIngredient(quantity="100", unit="g")
        used_quantity = 100.0
        used_unit = "g"

        converted = _amount_used_in_pantry_units(ingredient, used_quantity, used_unit)
        assert converted == 100.0

        remaining = 100.0 - converted
        assert remaining == pytest.approx(0.0)

    def test_fractional_quantity_deduction(self):
        """Test deduction with fractional amounts."""
        ingredient = MockIngredient(quantity="2", unit="cup")
        # Using 1/2 cup
        used_quantity = 0.5
        used_unit = "cup"

        converted = _amount_used_in_pantry_units(ingredient, used_quantity, used_unit)
        assert converted == 0.5

        remaining = 2.0 - converted
        assert remaining == 1.5

    def test_unit_conversion_deduction(self):
        """Test deduction with unit conversion."""
        ingredient = MockIngredient(quantity="1", unit="kg")
        # Use 500g from 1kg
        used_quantity = 500.0
        used_unit = "g"

        converted = _amount_used_in_pantry_units(ingredient, used_quantity, used_unit)
        assert converted == pytest.approx(0.5)

        remaining = 1.0 - converted
        assert remaining == pytest.approx(0.5)

    def test_package_unit_deduction(self):
        """Test deduction of package units."""
        ingredient = MockIngredient(quantity="3", unit="can")
        used_quantity = 1.0
        used_unit = "can"

        converted = _amount_used_in_pantry_units(ingredient, used_quantity, used_unit)
        assert converted == 1.0

        remaining = 3.0 - converted
        assert remaining == 2.0

    def test_package_full_depletion(self):
        """Test using an entire package item."""
        ingredient = MockIngredient(quantity="1", unit="bag")
        converted = _amount_used_in_pantry_units(ingredient, 1.0, "bag")
        assert converted == 1.0

        remaining = 1.0 - converted
        assert remaining == pytest.approx(0.0)

    def test_package_alias_deduction(self):
        """Test deduction when meal uses an alias of the pantry package unit."""
        ingredient = MockIngredient(quantity="2", unit="each")
        converted = _amount_used_in_pantry_units(ingredient, 1.0, "item")
        assert converted == 1.0

        remaining = 2.0 - converted
        assert remaining == 1.0

    def test_package_insufficient_inventory(self):
        """Test using more package units than are on hand."""
        ingredient = MockIngredient(quantity="1", unit="box")
        converted = _amount_used_in_pantry_units(ingredient, 2.0, "box")
        assert converted == 2.0

        remaining = 1.0 - converted
        assert remaining < 0

    def test_package_deduction_with_servings(self):
        """Test package deduction via serving size (e.g. creamer bottle)."""
        ingredient = MockIngredient(
            quantity="1",
            unit="each",
            serving_size="2 tbsp",
            servings_per_container=16,
        )
        converted = _amount_used_in_pantry_units(ingredient, 4.0, "tbsp")
        assert converted == pytest.approx(0.125)

        remaining = 1.0 - converted
        assert remaining == pytest.approx(0.875)

        remaining_servings = remaining * ingredient.servings_per_container
        assert remaining_servings == pytest.approx(14.0)

    def test_package_slice_servings_deduction(self):
        """Test deducting slices from a package tracked as each."""
        ingredient = MockIngredient(
            quantity="1",
            unit="each",
            serving_size="1 slice",
            servings_per_container=8,
        )
        converted = _amount_used_in_pantry_units(ingredient, 2.0, "slice")
        assert converted == pytest.approx(0.25)

        remaining = 1.0 - converted
        assert remaining == pytest.approx(0.75)

        remaining_servings = remaining * ingredient.servings_per_container
        assert remaining_servings == pytest.approx(6.0)

    def test_unknown_unit_deduction(self):
        """Test handling of unknown units."""
        ingredient = MockIngredient(quantity="5", unit="unknown_unit")
        used_quantity = 2.0
        used_unit = "unknown_unit"

        converted = _amount_used_in_pantry_units(ingredient, used_quantity, used_unit)
        # Should still work with exact match
        assert converted == 2.0

    def test_deduction_with_serving_size(self):
        """Test deduction using serving size information."""
        ingredient = MockIngredient(
            quantity="1",
            unit="bottle",
            serving_size="1 cup",
            servings_per_container=4,
        )
        # Use 1 cup (1 serving)
        used_quantity = 1.0
        used_unit = "cup"

        converted = _amount_used_in_pantry_units(ingredient, used_quantity, used_unit)
        # 1 cup = 1 serving, 1 serving / 4 servings per bottle = 0.25 bottles
        assert converted == pytest.approx(0.25)

        remaining = 1.0 - converted
        assert remaining == pytest.approx(0.75)

    def test_volume_to_volume_different_units(self):
        """Test conversion between different volume units."""
        ingredient = MockIngredient(quantity="2", unit="l")
        # Use 500ml from 2L
        used_quantity = 500.0
        used_unit = "ml"

        converted = _amount_used_in_pantry_units(ingredient, used_quantity, used_unit)
        assert converted == pytest.approx(0.5)

        remaining = 2.0 - converted
        assert remaining == pytest.approx(1.5)

    def test_weight_to_weight_different_units(self):
        """Test conversion between different weight units."""
        ingredient = MockIngredient(quantity="32", unit="oz")
        # Use 1 lb (16 oz) from 32 oz
        used_quantity = 1.0
        used_unit = "lb"

        converted = _amount_used_in_pantry_units(ingredient, used_quantity, used_unit)
        assert converted == pytest.approx(16.0, rel=0.01)

        remaining = 32.0 - converted
        assert remaining == pytest.approx(16.0, rel=0.01)


class TestUnitAliases:
    """Test that all expected unit aliases are defined."""

    def test_weight_units_covered(self):
        """Ensure weight units have aliases."""
        assert "g" in UNIT_ALIASES
        assert "gram" in UNIT_ALIASES
        assert "kg" in UNIT_ALIASES
        assert "oz" in UNIT_ALIASES
        assert "lb" in UNIT_ALIASES

    def test_volume_units_covered(self):
        """Ensure volume units have aliases."""
        assert "ml" in UNIT_ALIASES
        assert "l" in UNIT_ALIASES
        assert "cup" in UNIT_ALIASES
        assert "tbsp" in UNIT_ALIASES
        assert "tsp" in UNIT_ALIASES

    def test_package_units_covered(self):
        """Ensure package units have aliases."""
        assert "each" in UNIT_ALIASES
        assert "bag" in UNIT_ALIASES
        assert "box" in UNIT_ALIASES
        assert "can" in UNIT_ALIASES
        assert "bottle" in UNIT_ALIASES


class TestConversionFactors:
    """Test conversion factor dictionaries."""

    def test_weight_conversion_factors_exist(self):
        """Ensure all weight units have conversion factors."""
        assert "g" in WEIGHT_TO_GRAMS
        assert "kg" in WEIGHT_TO_GRAMS
        assert "oz" in WEIGHT_TO_GRAMS
        assert "lb" in WEIGHT_TO_GRAMS

    def test_volume_conversion_factors_exist(self):
        """Ensure all volume units have conversion factors."""
        assert "ml" in VOLUME_TO_ML
        assert "l" in VOLUME_TO_ML
        assert "cup" in VOLUME_TO_ML
        assert "tbsp" in VOLUME_TO_ML
        assert "tsp" in VOLUME_TO_ML
        assert "fl oz" in VOLUME_TO_ML

    def test_weight_factors_are_positive(self):
        """All conversion factors should be positive."""
        for factor in WEIGHT_TO_GRAMS.values():
            assert factor > 0

    def test_volume_factors_are_positive(self):
        """All conversion factors should be positive."""
        for factor in VOLUME_TO_ML.values():
            assert factor > 0


class TestServingsPerPantryUnit:
    def test_ml_to_tsp_serving(self):
        servings = servings_per_pantry_unit(
            "1 tsp (about 2.3 g, ~5 ml) ground paprika",
            "ml",
        )
        assert servings == pytest.approx(1 / 4.929, rel=1e-3)

    def test_same_unit(self):
        servings = servings_per_pantry_unit("1 cup", "cup")
        assert servings == pytest.approx(1.0)

    def test_incompatible_units(self):
        servings = servings_per_pantry_unit("1 slice", "each")
        assert servings is None
