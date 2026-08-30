"""Tests for ingredient recognition during nutrition resolution."""

from unittest.mock import patch

import pytest

from app.schemas import DraftIngredientItem
from app.services.ingredients import resolve_item_nutrition
from app.services.receipt_analyzer import ParsedReceiptItem, ReceiptAnalysisError


def _estimated_item(
    *,
    recognized: bool,
    name: str = "Test",
    unit_warning: str | None = None,
) -> ParsedReceiptItem:
    return ParsedReceiptItem(
        store_item_name=name,
        ingredient_name=name,
        recognized=recognized,
        quantity="1",
        unit="each",
        serving_size="1 cup" if recognized else None,
        servings_per_container=1 if recognized else None,
        calories=100 if recognized else None,
        protein_g=5 if recognized else None,
        carbs_g=10 if recognized else None,
        fat_g=2 if recognized else None,
        fiber_g=1 if recognized else None,
        sodium_mg=50 if recognized else None,
        nutrition_notes="USDA estimate" if recognized else "Unrecognized item",
        unit_warning=unit_warning,
    )


class TestResolveItemNutrition:
    def test_accepts_recognized_manual_item(self):
        item = DraftIngredientItem(
            ingredient_name="Chicken breast",
            store_item_name="Chicken breast",
            is_manual=True,
        )

        with patch(
            "app.services.ingredients.estimate_ingredient_nutrition",
            return_value=_estimated_item(recognized=True, name="Chicken breast"),
        ):
            resolved = resolve_item_nutrition(item)

        assert resolved.calories == 100
        assert resolved.ingredient_name == "Chicken breast"

    def test_rejects_unrecognized_manual_item(self):
        item = DraftIngredientItem(
            ingredient_name="po",
            store_item_name="po",
            is_manual=True,
        )

        with patch(
            "app.services.ingredients.estimate_ingredient_nutrition",
            return_value=_estimated_item(recognized=False, name="po"),
        ):
            with pytest.raises(ReceiptAnalysisError) as exc_info:
                resolve_item_nutrition(item)

        assert "Could not recognize" in str(exc_info.value)
        assert "po" in str(exc_info.value)

    def test_rejects_unrecognized_receipt_item(self):
        item = DraftIngredientItem(
            ingredient_name="po",
            store_item_name="po",
            quantity="2",
            unit="lb",
            is_manual=False,
        )

        with patch(
            "app.services.ingredients.estimate_ingredient_nutrition",
            return_value=_estimated_item(recognized=False, name="po"),
        ):
            with pytest.raises(ReceiptAnalysisError):
                resolve_item_nutrition(item)

    def test_accepts_recognized_receipt_item(self):
        item = DraftIngredientItem(
            ingredient_name="Bananas",
            store_item_name="Bananas",
            quantity="2",
            unit="lb",
            is_manual=False,
        )

        with patch(
            "app.services.ingredients.estimate_ingredient_nutrition",
            return_value=_estimated_item(recognized=True, name="Bananas"),
        ) as estimate:
            resolved = resolve_item_nutrition(item)

        estimate.assert_called_once()
        assert resolved.calories == 100
        assert resolved.quantity == "2"
        assert resolved.unit == "lb"

    def test_rejects_implausible_unit(self):
        item = DraftIngredientItem(
            ingredient_name="watermelon",
            store_item_name="watermelon",
            quantity="1",
            unit="gallon",
            is_manual=True,
        )

        with patch(
            "app.services.ingredients.estimate_ingredient_nutrition",
            return_value=_estimated_item(
                recognized=True,
                name="watermelon",
                unit_warning="Use each or lb for whole watermelon.",
            ),
        ):
            with pytest.raises(ReceiptAnalysisError) as exc_info:
                resolve_item_nutrition(item)

        assert "Use each or lb" in str(exc_info.value)
