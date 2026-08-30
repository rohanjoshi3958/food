import re
from fractions import Fraction

from sqlalchemy.orm import Session

from app.models import Ingredient, Meal, User

UNIT_ALIASES: dict[str, str] = {
    "each": "each",
    "item": "each",
    "items": "each",
    "g": "g",
    "gram": "g",
    "grams": "g",
    "kg": "kg",
    "kilogram": "kg",
    "kilograms": "kg",
    "oz": "oz",
    "ounce": "oz",
    "ounces": "oz",
    "lb": "lb",
    "lbs": "lb",
    "pound": "lb",
    "pounds": "lb",
    "ml": "ml",
    "milliliter": "ml",
    "milliliters": "ml",
    "l": "l",
    "liter": "l",
    "liters": "l",
    "litre": "l",
    "litres": "l",
    "cup": "cup",
    "cups": "cup",
    "tbsp": "tbsp",
    "tablespoon": "tbsp",
    "tablespoons": "tbsp",
    "tsp": "tsp",
    "teaspoon": "tsp",
    "teaspoons": "tsp",
    "pint": "pint",
    "pints": "pint",
    "quart": "quart",
    "quarts": "quart",
    "gallon": "gallon",
    "gallons": "gallon",
    "fl oz": "fl oz",
    "bunch": "bunch",
    "bunches": "bunch",
    "bag": "bag",
    "bags": "bag",
    "box": "box",
    "boxes": "box",
    "can": "can",
    "cans": "can",
    "bottle": "bottle",
    "bottles": "bottle",
    "pack": "pack",
    "packs": "pack",
    "slice": "slice",
    "slices": "slice",
    "head": "head",
    "heads": "head",
    "clove": "clove",
    "cloves": "clove",
}

WEIGHT_TO_GRAMS = {
    "g": 1.0,
    "kg": 1000.0,
    "oz": 28.3495,
    "lb": 453.592,
}

VOLUME_TO_ML = {
    "ml": 1.0,
    "l": 1000.0,
    "cup": 236.588,
    "tbsp": 14.787,
    "tsp": 4.929,
    "pint": 473.176,
    "quart": 946.353,
    "gallon": 3785.41,
    "fl oz": 29.5735,
}


def parse_number(value: str) -> float | None:
    cleaned = value.strip()
    if not cleaned:
        return None

    try:
        if "/" in cleaned:
            return float(Fraction(cleaned))
        return float(cleaned)
    except (ValueError, ZeroDivisionError):
        return None


def normalize_unit(unit: str | None) -> str | None:
    if not unit:
        return None

    normalized = unit.strip().lower()
    if normalized in UNIT_ALIASES:
        return UNIT_ALIASES[normalized]

    compact = normalized.replace(" ", "")
    for alias, canonical in UNIT_ALIASES.items():
        if alias == normalized or alias.replace(" ", "") == compact:
            return canonical

    # Serving sizes often add descriptors: "cup dry", "tbsp creamy", "oz drained".
    parts = normalized.split()
    for length in (2, 1):
        if len(parts) >= length:
            candidate = " ".join(parts[:length])
            if candidate in UNIT_ALIASES:
                return UNIT_ALIASES[candidate]
            candidate_compact = candidate.replace(" ", "")
            for alias, canonical in UNIT_ALIASES.items():
                if alias.replace(" ", "") == candidate_compact:
                    return canonical

    return normalized


def parse_amount(amount: str) -> tuple[float | None, str | None]:
    cleaned = amount.strip()
    if not cleaned:
        return None, None

    # Drop parenthetical notes: "1/2 cup dry (48g)" → "1/2 cup dry"
    cleaned = re.sub(r"\([^)]*\)", "", cleaned).strip()

    match = re.match(r"^([\d./]+)\s*(.*)$", cleaned)
    if not match:
        return None, normalize_unit(cleaned)

    quantity = parse_number(match.group(1))
    unit = normalize_unit(match.group(2) or None)
    return quantity, unit


def serialize_meal_ingredients(items: list) -> list[dict]:
    serialized: list[dict] = []

    for item in items:
        if isinstance(item, dict):
            name = str(item.get("name", "")).strip()
            amount = str(item.get("amount", "")).strip()
        else:
            name = item.name.strip()
            amount = item.amount.strip()

        quantity, unit = parse_amount(amount)
        serialized.append(
            {
                "name": name,
                "quantity": quantity,
                "unit": unit,
            }
        )

    return serialized


def parse_ingredients_used_text(text: str | None) -> list[dict]:
    if not text:
        return []

    items: list[dict] = []
    for line in text.splitlines():
        cleaned = line.strip()
        if not cleaned:
            continue

        if cleaned.startswith("- "):
            cleaned = cleaned[2:].strip()

        if ":" not in cleaned:
            continue

        name, amount = cleaned.split(":", 1)
        quantity, unit = parse_amount(amount.strip())
        items.append({"name": name.strip(), "quantity": quantity, "unit": unit})

    return items


def meal_ingredients_data(meal: Meal) -> list[dict]:
    if meal.ingredients_used_data:
        return meal.ingredients_used_data

    return parse_ingredients_used_text(meal.ingredients_used)


def _normalize_name(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip().lower())


def _find_matching_ingredient(
    ingredients: list[Ingredient],
    used_name: str,
) -> Ingredient | None:
    target = _normalize_name(used_name)
    if not target:
        return None

    exact_matches = [
        ingredient
        for ingredient in ingredients
        if _normalize_name(ingredient.name) == target
    ]
    if exact_matches:
        return exact_matches[0]

    partial_matches = [
        ingredient
        for ingredient in ingredients
        if target in _normalize_name(ingredient.name)
        or _normalize_name(ingredient.name) in target
    ]
    if len(partial_matches) == 1:
        return partial_matches[0]

    return None


def _format_quantity(value: float) -> str:
    rounded = round(value, 4)
    if abs(rounded - round(rounded)) < 1e-6:
        return str(int(round(rounded)))
    return f"{rounded:g}"


def _convert_amount(
    quantity: float,
    from_unit: str | None,
    to_unit: str | None,
) -> float | None:
    if from_unit is None or to_unit is None:
        return None

    from_unit = normalize_unit(from_unit) or from_unit
    to_unit = normalize_unit(to_unit) or to_unit

    if from_unit == to_unit:
        return quantity

    if from_unit in WEIGHT_TO_GRAMS and to_unit in WEIGHT_TO_GRAMS:
        grams = quantity * WEIGHT_TO_GRAMS[from_unit]
        return grams / WEIGHT_TO_GRAMS[to_unit]

    if from_unit in VOLUME_TO_ML and to_unit in VOLUME_TO_ML:
        milliliters = quantity * VOLUME_TO_ML[from_unit]
        return milliliters / VOLUME_TO_ML[to_unit]

    return None


def _ingredient_on_hand(ingredient: Ingredient) -> tuple[float | None, str | None]:
    quantity = parse_number(ingredient.quantity or "")
    unit = normalize_unit(ingredient.unit)
    return quantity, unit


def _format_amount(quantity: float, unit: str | None) -> str:
    formatted = _format_quantity(quantity)
    if unit:
        return f"{formatted} {unit}"
    return formatted


def clamp_meal_ingredients_to_pantry(pantry: list[Ingredient], items: list) -> list:
    clamped: list = []

    for item in items:
        name = item.name.strip()
        used_quantity, used_unit = parse_amount(item.amount)
        pantry_item = _find_matching_ingredient(pantry, name)

        if pantry_item is None:
            clamped.append(item)
            continue

        on_hand_quantity, on_hand_unit = _ingredient_on_hand(pantry_item)
        if on_hand_quantity is None:
            clamped.append(item)
            continue

        output_unit = on_hand_unit or used_unit

        if used_quantity is None:
            # No parseable amount — prefer one labeled serving over the whole package.
            serving_quantity, serving_unit = _serving_amount_from_ingredient(pantry_item)
            if serving_quantity and serving_unit:
                clamped.append(
                    _clone_meal_item(
                        item,
                        name,
                        _format_amount(serving_quantity, serving_unit),
                    )
                )
            else:
                clamped.append(item)
            continue

        compare_unit = used_unit or on_hand_unit
        used_in_pantry_units = _convert_amount(used_quantity, compare_unit, on_hand_unit)

        if used_in_pantry_units is None and compare_unit == on_hand_unit:
            used_in_pantry_units = used_quantity

        if used_in_pantry_units is None:
            # Can't convert (e.g. tbsp vs each). Keep the portion amount; never
            # escalate to the entire package for a meal.
            clamped.append(item)
            continue

        capped_quantity = min(used_in_pantry_units, on_hand_quantity)
        clamped.append(
            _clone_meal_item(item, name, _format_amount(capped_quantity, output_unit))
        )

    return clamped


def _serving_amount_from_ingredient(
    ingredient: Ingredient,
) -> tuple[float | None, str | None]:
    if not ingredient.serving_size:
        return None, None
    primary = ingredient.serving_size.split("(", 1)[0].strip()
    return parse_amount(primary)


def servings_per_pantry_unit(
    serving_size: str | None,
    pantry_unit: str | None,
) -> float | None:
    """How many standard servings fit in one unit of the pantry unit."""
    if not serving_size or not pantry_unit:
        return None

    primary = serving_size.split("(", 1)[0].strip()
    serving_quantity, serving_unit = parse_amount(primary)
    if not serving_quantity or serving_quantity <= 0 or not serving_unit:
        return None

    normalized_pantry_unit = normalize_unit(pantry_unit)
    if not normalized_pantry_unit:
        return None

    converted = _convert_amount(1.0, normalized_pantry_unit, serving_unit)
    if converted is None and normalized_pantry_unit == serving_unit:
        converted = 1.0
    if converted is None:
        return None

    return converted / serving_quantity


def servings_used_from_amount(
    ingredient: Ingredient,
    used_quantity: float,
    used_unit: str | None,
) -> float | None:
    """How many labeled servings a used amount represents."""
    used_unit = normalize_unit(used_unit)
    on_hand_quantity, on_hand_unit = _ingredient_on_hand(ingredient)
    servings_per_unit = ingredient.servings_per_container

    serving_quantity, serving_unit = _serving_amount_from_ingredient(ingredient)
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


def remaining_servings(ingredient: Ingredient) -> float | None:
    """Servings still available from this pantry item."""
    quantity, _unit = _ingredient_on_hand(ingredient)
    servings_per = ingredient.servings_per_container
    if quantity is None or servings_per is None or servings_per <= 0:
        return None
    return quantity * servings_per


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


# Allow a few labeled servings in one plate (e.g. 2 tbsp creamer) without
# forcing every item down to exactly one nutrition-label serving.
MAX_REASONABLE_ONE_PERSON_SERVINGS = 4.0


def scale_meal_ingredients_for_one_person(pantry: list[Ingredient], items: list) -> list:
    """Rewrite absurd whole-package amounts; keep ordinary single-plate portions."""
    scaled: list = []

    for item in items:
        name = item.name.strip()
        pantry_item = _find_matching_ingredient(pantry, name)
        if pantry_item is None:
            scaled.append(item)
            continue

        used_quantity, used_unit = parse_amount(item.amount)
        serving_quantity, serving_unit = _serving_amount_from_ingredient(pantry_item)
        on_hand_quantity, on_hand_unit = _ingredient_on_hand(pantry_item)

        servings = None
        if (
            used_quantity is not None
            and serving_quantity
            and serving_quantity > 0
        ):
            converted = _convert_amount(used_quantity, used_unit, serving_unit)
            if converted is None and used_unit == serving_unit:
                converted = used_quantity
            if converted is not None:
                servings = converted / serving_quantity

        uses_whole_package = (
            used_quantity is not None
            and on_hand_unit in PACKAGE_UNITS
            and (used_unit == on_hand_unit or used_unit in PACKAGE_UNITS)
            and used_quantity >= 0.5
            and (
                serving_unit is None
                or serving_unit not in PACKAGE_UNITS
                or (
                    pantry_item.servings_per_container is not None
                    and pantry_item.servings_per_container > 1
                )
            )
        )

        # Whole jar/bag/bottle counted as "1 each" → one labeled serving.
        if uses_whole_package and serving_quantity and serving_unit:
            scaled.append(
                _clone_meal_item(
                    item,
                    name,
                    _format_amount(serving_quantity, serving_unit),
                )
            )
            continue

        if (
            uses_whole_package
            and used_quantity is not None
            and pantry_item.servings_per_container
            and pantry_item.servings_per_container > 1
            and on_hand_quantity is not None
        ):
            portion = used_quantity / pantry_item.servings_per_container
            if portion < on_hand_quantity:
                scaled.append(
                    _clone_meal_item(
                        item,
                        name,
                        _format_amount(portion, on_hand_unit),
                    )
                )
                continue

        # Family-batch amounts only: scale down to a reasonable single-plate size.
        if (
            servings is not None
            and servings > MAX_REASONABLE_ONE_PERSON_SERVINGS
            and serving_quantity
            and serving_unit
        ):
            scaled.append(
                _clone_meal_item(
                    item,
                    name,
                    _format_amount(
                        serving_quantity * MAX_REASONABLE_ONE_PERSON_SERVINGS,
                        serving_unit,
                    ),
                )
            )
            continue

        scaled.append(item)

    return scaled


def _clone_meal_item(item, name: str, amount: str):
    return type(item)(name=name, amount=amount)


def _amount_used_in_pantry_units(
    ingredient: Ingredient,
    used_quantity: float,
    used_unit: str | None,
) -> float | None:
    """Convert a meal amount into the pantry item's storage unit."""
    on_hand_quantity, on_hand_unit = _ingredient_on_hand(ingredient)
    used_unit = normalize_unit(used_unit) or on_hand_unit

    converted = _convert_amount(used_quantity, used_unit, on_hand_unit)
    if converted is None and used_unit == on_hand_unit:
        converted = used_quantity

    if converted is not None:
        return converted

    # e.g. meal used "2 tbsp" / "1/2 cup" but pantry is "1 each".
    servings_used = servings_used_from_amount(ingredient, used_quantity, used_unit)
    servings_per = ingredient.servings_per_container
    if (
        servings_used is not None
        and servings_per
        and servings_per > 0
        and on_hand_quantity is not None
    ):
        return servings_used / servings_per

    return None


def deduct_meal_ingredients(db: Session, user: User, meal: Meal) -> None:
    used_items = meal_ingredients_data(meal)
    if not used_items:
        return

    pantry = (
        db.query(Ingredient)
        .filter(Ingredient.user_id == user.id)
        .all()
    )

    for used in used_items:
        ingredient = _find_matching_ingredient(pantry, used["name"])
        if ingredient is None:
            continue

        used_quantity = used.get("quantity")
        if used_quantity is None:
            db.delete(ingredient)
            pantry = [item for item in pantry if item.id != ingredient.id]
            continue

        on_hand_quantity, _on_hand_unit = _ingredient_on_hand(ingredient)
        if on_hand_quantity is None:
            db.delete(ingredient)
            pantry = [item for item in pantry if item.id != ingredient.id]
            continue

        used_unit = normalize_unit(used.get("unit"))
        converted_used = _amount_used_in_pantry_units(
            ingredient,
            used_quantity,
            used_unit,
        )
        if converted_used is None:
            continue

        remaining = on_hand_quantity - converted_used
        remaining_servings_left = None
        if ingredient.servings_per_container and ingredient.servings_per_container > 0:
            remaining_servings_left = remaining * ingredient.servings_per_container

        # Remove when nothing useful is left (whole item or < ~1/4 serving).
        if remaining <= 1e-6 or (
            remaining_servings_left is not None and remaining_servings_left < 0.25
        ):
            db.delete(ingredient)
            pantry = [item for item in pantry if item.id != ingredient.id]
        else:
            ingredient.quantity = _format_quantity(remaining)
