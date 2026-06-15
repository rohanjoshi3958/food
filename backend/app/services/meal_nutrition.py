from dataclasses import dataclass

from app.models import Ingredient
from app.services.ingredient_deduction import (
    _convert_amount,
    _find_matching_ingredient,
    _ingredient_on_hand,
    meal_ingredients_data,
    normalize_unit,
)

MACRO_FIELDS = (
    "calories",
    "protein_g",
    "carbs_g",
    "fat_g",
    "fiber_g",
    "sodium_mg",
)


@dataclass
class MealMacros:
    calories: float | None = None
    protein_g: float | None = None
    carbs_g: float | None = None
    fat_g: float | None = None
    fiber_g: float | None = None
    sodium_mg: float | None = None

    def as_dict(self) -> dict[str, float | None]:
        return {
            "calories": self.calories,
            "protein_g": self.protein_g,
            "carbs_g": self.carbs_g,
            "fat_g": self.fat_g,
            "fiber_g": self.fiber_g,
            "sodium_mg": self.sodium_mg,
        }


def _usage_fraction(ingredient: Ingredient, used: dict) -> float | None:
    on_hand_quantity, on_hand_unit = _ingredient_on_hand(ingredient)
    used_quantity = used.get("quantity")

    if on_hand_quantity is None or used_quantity is None or on_hand_quantity <= 0:
        return None

    used_unit = normalize_unit(used.get("unit")) or on_hand_unit
    converted_used = _convert_amount(used_quantity, used_unit, on_hand_unit)

    if converted_used is None and used_unit == on_hand_unit:
        converted_used = used_quantity

    if converted_used is None:
        return None

    return min(converted_used / on_hand_quantity, 1.0)


def calculate_meal_macros(
    pantry: list[Ingredient],
    used_items: list[dict],
) -> MealMacros:
    totals = {field: 0.0 for field in MACRO_FIELDS}
    has_value = {field: False for field in MACRO_FIELDS}

    for used in used_items:
        ingredient = _find_matching_ingredient(pantry, used.get("name", ""))
        if ingredient is None:
            continue

        fraction = _usage_fraction(ingredient, used)
        if fraction is None:
            continue

        for field in MACRO_FIELDS:
            value = getattr(ingredient, field)
            if value is not None:
                totals[field] += value * fraction
                has_value[field] = True

    def round_value(field: str) -> float | None:
        if not has_value[field]:
            return None
        value = totals[field]
        if field == "calories":
            return round(value)
        if field == "sodium_mg":
            return round(value, 1)
        return round(value, 1)

    return MealMacros(
        calories=round_value("calories"),
        protein_g=round_value("protein_g"),
        carbs_g=round_value("carbs_g"),
        fat_g=round_value("fat_g"),
        fiber_g=round_value("fiber_g"),
        sodium_mg=round_value("sodium_mg"),
    )


def calculate_meal_macros_from_meal(pantry: list[Ingredient], meal) -> MealMacros:
    return calculate_meal_macros(pantry, meal_ingredients_data(meal))
