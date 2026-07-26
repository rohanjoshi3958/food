from dataclasses import dataclass

from app.models import Ingredient
from app.services.ingredient_deduction import (
    _convert_amount,
    _find_matching_ingredient,
    _ingredient_on_hand,
    meal_ingredients_data,
    normalize_unit,
    parse_amount,
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


def _serving_amount(ingredient: Ingredient) -> tuple[float | None, str | None]:
    if not ingredient.serving_size:
        return None, None
    # Prefer the primary measure before any parenthetical weight, e.g. "2 tbsp (32g)".
    primary = ingredient.serving_size.split("(", 1)[0].strip()
    return parse_amount(primary)


def _servings_used(ingredient: Ingredient, used: dict) -> float | None:
    """Return how many standard servings the meal uses from this pantry item."""
    used_quantity = used.get("quantity")
    if used_quantity is None:
        return None

    used_unit = normalize_unit(used.get("unit"))
    on_hand_quantity, on_hand_unit = _ingredient_on_hand(ingredient)
    servings_per_unit = ingredient.servings_per_container

    serving_quantity, serving_unit = _serving_amount(ingredient)
    if serving_quantity and serving_quantity > 0:
        converted_to_serving = _convert_amount(
            used_quantity,
            used_unit,
            serving_unit,
        )
        if converted_to_serving is not None:
            return converted_to_serving / serving_quantity
        if used_unit == serving_unit:
            return used_quantity / serving_quantity

    if (
        servings_per_unit
        and servings_per_unit > 0
        and on_hand_quantity
        and on_hand_quantity > 0
    ):
        converted_to_on_hand = _convert_amount(
            used_quantity,
            used_unit,
            on_hand_unit,
        )
        if converted_to_on_hand is None and used_unit == on_hand_unit:
            converted_to_on_hand = used_quantity

        if converted_to_on_hand is not None:
            # servings_per_container is defined per 1 unit of the pantry unit.
            return converted_to_on_hand * servings_per_unit

    return None


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

        servings = _servings_used(ingredient, used)
        if servings is None or servings <= 0:
            continue

        for field in MACRO_FIELDS:
            value = getattr(ingredient, field)
            if value is not None:
                totals[field] += value * servings
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
