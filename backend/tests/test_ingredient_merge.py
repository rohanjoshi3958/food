"""Tests for ingredient merge service."""

import pytest
from app.services.ingredient_merge import (
    _merge_key,
    _sum_quantities,
    merge_draft_items,
)


class TestMergeKey:
    """Tests for _merge_key function."""

    def test_same_name_and_unit(self):
        key1 = _merge_key("Chicken Breast", "oz")
        key2 = _merge_key("Chicken Breast", "oz")
        assert key1 == key2

    def test_case_insensitive_name(self):
        key1 = _merge_key("Chicken Breast", "oz")
        key2 = _merge_key("chicken breast", "oz")
        assert key1 == key2

    def test_whitespace_normalized(self):
        key1 = _merge_key("Chicken  Breast", "oz")
        key2 = _merge_key("Chicken Breast", "oz")
        assert key1 == key2

    def test_unit_normalized(self):
        key1 = _merge_key("Rice", "gram")
        key2 = _merge_key("Rice", "g")
        assert key1 == key2

    def test_different_names_different_keys(self):
        key1 = _merge_key("Chicken", "oz")
        key2 = _merge_key("Rice", "oz")
        assert key1 != key2

    def test_different_units_different_keys(self):
        key1 = _merge_key("Rice", "g")
        key2 = _merge_key("Rice", "oz")
        assert key1 != key2

    def test_none_values(self):
        key = _merge_key(None, None)
        assert key == ("", "")

    def test_none_unit(self):
        key1 = _merge_key("Chicken", None)
        key2 = _merge_key("Chicken", None)
        assert key1 == key2


class TestSumQuantities:
    """Tests for _sum_quantities function."""

    def test_sum_two_integers(self):
        result = _sum_quantities("5", "3")
        assert result == "8"

    def test_sum_two_floats(self):
        result = _sum_quantities("2.5", "1.5")
        assert result == "4"

    def test_sum_with_none_left(self):
        result = _sum_quantities(None, "5")
        assert result == "5"

    def test_sum_with_none_right(self):
        result = _sum_quantities("5", None)
        assert result == "5"

    def test_sum_both_none(self):
        result = _sum_quantities(None, None)
        assert result is None

    def test_sum_with_empty_string(self):
        result = _sum_quantities("", "5")
        assert result == "5"

    def test_sum_fractional_quantities(self):
        result = _sum_quantities("1.5", "2.5")
        assert result == "4"

    def test_sum_with_zero(self):
        result = _sum_quantities("0", "5")
        assert result == "5"

    def test_sum_identical_non_numeric_strings(self):
        # When both are the same non-parseable string, return one
        result = _sum_quantities("some text", "some text")
        assert result == "some text"

    def test_sum_different_non_numeric_strings(self):
        # When different non-numeric, prefer left or just return something
        result = _sum_quantities("text1", "text2")
        # Implementation returns left when both are unparseable
        assert result in ["text1", "text2", "text1 text2"]


class TestMergeDraftItems:
    """Tests for merge_draft_items function."""

    def test_merge_same_ingredient_same_unit(self):
        items = [
            {
                "ingredient_name": "Rice",
                "quantity": "100",
                "unit": "g",
                "is_food": True,
            },
            {
                "ingredient_name": "Rice",
                "quantity": "50",
                "unit": "g",
                "is_food": True,
            },
        ]
        result = merge_draft_items(items)
        assert len(result) == 1
        assert result[0]["ingredient_name"] == "Rice"
        assert result[0]["quantity"] == "150"
        assert result[0]["unit"] == "g"

    def test_merge_different_ingredients(self):
        items = [
            {
                "ingredient_name": "Rice",
                "quantity": "100",
                "unit": "g",
                "is_food": True,
            },
            {
                "ingredient_name": "Chicken",
                "quantity": "200",
                "unit": "g",
                "is_food": True,
            },
        ]
        result = merge_draft_items(items)
        assert len(result) == 2

    def test_merge_same_ingredient_different_units(self):
        items = [
            {
                "ingredient_name": "Rice",
                "quantity": "100",
                "unit": "g",
                "is_food": True,
            },
            {
                "ingredient_name": "Rice",
                "quantity": "2",
                "unit": "oz",
                "is_food": True,
            },
        ]
        result = merge_draft_items(items)
        # Different units should NOT merge
        assert len(result) == 2

    def test_merge_with_unit_aliases(self):
        items = [
            {
                "ingredient_name": "Rice",
                "quantity": "100",
                "unit": "gram",
                "is_food": True,
            },
            {
                "ingredient_name": "Rice",
                "quantity": "50",
                "unit": "g",
                "is_food": True,
            },
        ]
        result = merge_draft_items(items)
        # Should merge because gram -> g
        assert len(result) == 1
        assert result[0]["quantity"] == "150"

    def test_merge_filters_non_food_items(self):
        items = [
            {
                "ingredient_name": "Rice",
                "quantity": "100",
                "unit": "g",
                "is_food": True,
            },
            {
                "ingredient_name": "Paper Towels",
                "quantity": "1",
                "unit": "each",
                "is_food": False,
            },
        ]
        result = merge_draft_items(items)
        assert len(result) == 1
        assert result[0]["ingredient_name"] == "Rice"

    def test_merge_preserves_nutrition_info(self):
        items = [
            {
                "ingredient_name": "Rice",
                "quantity": "100",
                "unit": "g",
                "is_food": True,
                "calories": 130,
                "protein_g": 3,
            },
            {
                "ingredient_name": "Rice",
                "quantity": "50",
                "unit": "g",
                "is_food": True,
            },
        ]
        result = merge_draft_items(items)
        assert len(result) == 1
        assert result[0]["calories"] == 130
        assert result[0]["protein_g"] == 3

    def test_merge_combines_store_item_names(self):
        items = [
            {
                "ingredient_name": "Rice",
                "store_item_name": "Brand A Rice",
                "quantity": "100",
                "unit": "g",
                "is_food": True,
            },
            {
                "ingredient_name": "Rice",
                "store_item_name": "Brand B Rice",
                "quantity": "50",
                "unit": "g",
                "is_food": True,
            },
        ]
        result = merge_draft_items(items)
        assert len(result) == 1
        # Store names should be combined
        assert "Brand A Rice" in result[0]["store_item_name"]
        assert "Brand B Rice" in result[0]["store_item_name"]

    def test_merge_preserves_serving_info(self):
        items = [
            {
                "ingredient_name": "Rice",
                "quantity": "100",
                "unit": "g",
                "is_food": True,
                "serving_size": "1/2 cup",
                "servings_per_container": 4,
            },
            {
                "ingredient_name": "Rice",
                "quantity": "50",
                "unit": "g",
                "is_food": True,
            },
        ]
        result = merge_draft_items(items)
        assert len(result) == 1
        assert result[0]["serving_size"] == "1/2 cup"
        assert result[0]["servings_per_container"] == 4

    def test_merge_empty_list(self):
        result = merge_draft_items([])
        assert result == []

    def test_merge_preserves_order(self):
        items = [
            {
                "ingredient_name": "Chicken",
                "quantity": "100",
                "unit": "g",
                "is_food": True,
            },
            {
                "ingredient_name": "Rice",
                "quantity": "50",
                "unit": "g",
                "is_food": True,
            },
            {
                "ingredient_name": "Beans",
                "quantity": "75",
                "unit": "g",
                "is_food": True,
            },
        ]
        result = merge_draft_items(items)
        assert len(result) == 3
        assert result[0]["ingredient_name"] == "Chicken"
        assert result[1]["ingredient_name"] == "Rice"
        assert result[2]["ingredient_name"] == "Beans"

    def test_merge_case_insensitive_names(self):
        items = [
            {
                "ingredient_name": "Chicken Breast",
                "quantity": "100",
                "unit": "g",
                "is_food": True,
            },
            {
                "ingredient_name": "chicken breast",
                "quantity": "50",
                "unit": "g",
                "is_food": True,
            },
        ]
        result = merge_draft_items(items)
        assert len(result) == 1
        assert result[0]["quantity"] == "150"

    def test_merge_whitespace_normalized(self):
        items = [
            {
                "ingredient_name": "Chicken  Breast",
                "quantity": "100",
                "unit": "g",
                "is_food": True,
            },
            {
                "ingredient_name": "Chicken Breast",
                "quantity": "50",
                "unit": "g",
                "is_food": True,
            },
        ]
        result = merge_draft_items(items)
        assert len(result) == 1
        assert result[0]["quantity"] == "150"

    def test_merge_multiple_duplicates(self):
        items = [
            {
                "ingredient_name": "Rice",
                "quantity": "100",
                "unit": "g",
                "is_food": True,
            },
            {
                "ingredient_name": "Rice",
                "quantity": "50",
                "unit": "g",
                "is_food": True,
            },
            {
                "ingredient_name": "Rice",
                "quantity": "25",
                "unit": "g",
                "is_food": True,
            },
        ]
        result = merge_draft_items(items)
        assert len(result) == 1
        assert result[0]["quantity"] == "175"

    def test_merge_handles_missing_quantity(self):
        items = [
            {
                "ingredient_name": "Rice",
                "quantity": "100",
                "unit": "g",
                "is_food": True,
            },
            {
                "ingredient_name": "Rice",
                "quantity": None,
                "unit": "g",
                "is_food": True,
            },
        ]
        result = merge_draft_items(items)
        assert len(result) == 1
        assert result[0]["quantity"] == "100"

    def test_merge_skips_empty_names(self):
        items = [
            {
                "ingredient_name": "",
                "quantity": "100",
                "unit": "g",
                "is_food": True,
            },
            {
                "ingredient_name": "Rice",
                "quantity": "50",
                "unit": "g",
                "is_food": True,
            },
        ]
        result = merge_draft_items(items)
        assert len(result) == 1
        assert result[0]["ingredient_name"] == "Rice"

    def test_merge_handles_fractional_quantities(self):
        items = [
            {
                "ingredient_name": "Rice",
                "quantity": "1.5",
                "unit": "cup",
                "is_food": True,
            },
            {
                "ingredient_name": "Rice",
                "quantity": "0.5",
                "unit": "cup",
                "is_food": True,
            },
        ]
        result = merge_draft_items(items)
        assert len(result) == 1
        assert result[0]["quantity"] == "2"

    def test_merge_is_manual_flag(self):
        items = [
            {
                "ingredient_name": "Rice",
                "quantity": "100",
                "unit": "g",
                "is_food": True,
                "is_manual": True,
            },
            {
                "ingredient_name": "Rice",
                "quantity": "50",
                "unit": "g",
                "is_food": True,
                "is_manual": True,
            },
        ]
        result = merge_draft_items(items)
        assert len(result) == 1
        assert result[0]["is_manual"] is True

    def test_merge_is_manual_mixed(self):
        items = [
            {
                "ingredient_name": "Rice",
                "quantity": "100",
                "unit": "g",
                "is_food": True,
                "is_manual": True,
            },
            {
                "ingredient_name": "Rice",
                "quantity": "50",
                "unit": "g",
                "is_food": True,
                "is_manual": False,
            },
        ]
        result = merge_draft_items(items)
        assert len(result) == 1
        # When mixed, should be False
        assert result[0]["is_manual"] is False
