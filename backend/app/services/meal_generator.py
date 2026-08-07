import json
import re

import anthropic
from pydantic import BaseModel, Field

from app.config import MEAL_ANTHROPIC_MODEL, settings
from app.models import Ingredient
from app.services.ingredient_deduction import (
    clamp_meal_ingredients_to_pantry,
    remaining_servings,
    scale_meal_ingredients_for_one_person,
    serialize_meal_ingredients,
)
from app.services.meal_nutrition import calculate_meal_macros

MEAL_CALORIE_MIN = 500
MEAL_CALORIE_MAX = 800
MEAL_GENERATION_ATTEMPTS = 4

MEAL_GENERATION_PROMPT = """You are a helpful home chef. Given the ingredients available in the user's kitchen, suggest ONE practical meal for a single person (one plate / one bowl).

Available ingredients:
{ingredients}

HARD REQUIREMENT — calories: the finished meal MUST land between {calorie_min} and {calorie_max} kcal (estimated from the ingredients_used amounts and each item's kcal-per-serving).
- This is a filling main meal, not a snack. Do not propose meals under {calorie_min} kcal.
- Use enough volume and calorie-dense pantry items (grains, starches, proteins, nut butters, cheese, oils, creamy soups, fruit, etc.) to hit the range.
- Prefer multiple substantial components on the plate (e.g. a base + protein + fat/sauce + side), not a thin soup with a tiny garnish.
- Before answering, roughly sum kcal from amounts × kcal-per-serving and adjust portions until you are inside {calorie_min}–{calorie_max}.
- Only if the pantry truly cannot support {calorie_min} kcal should you go lower — and only then get as close as possible without inventing ingredients.

Scale everything for ONE person only:
- ingredient amounts in ingredients_used must be a single-person portion (not a family batch)
- instructions should cook one plate / one bowl for one eater
- do not generate multi-serving recipes and do not say "serves 2+" or similar
- NEVER use a whole package for one person when the item is a jar/bottle/box/bag sold as "each"
  (e.g. do NOT use "1 each" almond butter). Use a practical cooking amount instead (e.g. "2–3 tbsp").
- Single-serve produce like one banana or one apple may use "1 each".
- Use more than one nutrition-label serving of an item whenever needed to reach {calorie_min}–{calorie_max} kcal.

Use each selected ingredient's quantity, unit, serving size, servings remaining, and kcal per serving when deciding how much to use. Prefer amounts in the same units as the serving size when the item is sold as a package (e.g. tablespoons from a jar). You do NOT need to use every available ingredient — choose a sensible subset that makes one cohesive, practical, filling single-person meal. Only include ingredients you actually use in ingredients_used. You may assume basic pantry staples (salt, pepper, cooking oil, butter, water) are available if needed; if you rely on oil or butter for cooking, still keep the meal itself substantial from the listed ingredients.

CRITICAL: For every ingredient you include, the amount in ingredients_used must be less than or equal to the maximum available quantity shown for that item. Never require more than the user has on hand. For example, if they only have 1 g of tomatoes, use at most 1 g of tomatoes.

Respond with ONLY valid JSON in this exact shape:
{{
  "name": "Meal Name",
  "description": "One or two sentence summary of the dish for one person.",
  "ingredients_used": [
    {{
      "name": "Ingredient name from the list",
      "amount": "Numeric amount with unit matching the pantry item, e.g. 2 g or 1 cup"
    }}
  ],
  "instructions": [
    "First step written as a complete sentence.",
    "Second step written as a complete sentence."
  ]
}}

Put each instruction step in its own array element. Do not combine multiple steps into one string."""

FOLLOW_UP_PROMPT = """Suggest a different single-person meal ({calorie_min}–{calorie_max} kcal) than the one you just proposed.
Keep using only the available ingredients and the same JSON response format.
Ingredient amounts must still be sized for one person and MUST land in the {calorie_min}–{calorie_max} kcal range.
Do not repeat the same dish name or essentially the same recipe."""


class MealIngredientUse(BaseModel):
    name: str
    amount: str


class GeneratedMeal(BaseModel):
    name: str
    description: str
    ingredients_used: list[MealIngredientUse] = Field(default_factory=list)
    instructions: str


class PreviousMealTurn(BaseModel):
    name: str
    description: str | None = None
    ingredients_used: str | None = None
    instructions: str | None = None


def normalize_instructions(raw: list[str] | str) -> str:
    if isinstance(raw, list):
        steps = [step.strip() for step in raw if step.strip()]
        return "\n".join(f"{index}. {step}" for index, step in enumerate(steps, start=1))

    text = raw.strip()
    parts = re.split(r"\s+(?=\d+\.\s)", text)
    if len(parts) > 1:
        return "\n".join(part.strip() for part in parts)

    return text


class MealGenerationError(Exception):
    pass


def _extract_json(text: str) -> dict:
    cleaned = text.strip()

    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)

    return json.loads(cleaned)


def _format_ingredients(ingredients: list[Ingredient]) -> str:
    lines: list[str] = []

    for ingredient in ingredients:
        quantity_label = " ".join(
            part for part in [ingredient.quantity, ingredient.unit] if part
        )
        details = [f"- {ingredient.name}"]

        if quantity_label:
            details.append(f"maximum available: {quantity_label} (do not exceed)")
        if ingredient.serving_size:
            details.append(f"serving size: {ingredient.serving_size}")
        servings_left = remaining_servings(ingredient)
        if servings_left is not None:
            details.append(f"~{servings_left:g} servings remaining")
        elif ingredient.servings_per_container:
            details.append(
                f"~{ingredient.servings_per_container:g} servings per {ingredient.unit or 'unit'}"
            )
        if ingredient.calories is not None:
            details.append(f"~{ingredient.calories:g} kcal per serving")

        lines.append(", ".join(details))

    return "\n".join(lines)


def format_ingredients_used(items: list[MealIngredientUse]) -> str:
    return "\n".join(f"- {item.name}: {item.amount}" for item in items)


def _normalize_meal_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.casefold())


def _is_same_meal(candidate: str, previous: str | None) -> bool:
    if not previous:
        return False
    left = _normalize_meal_name(candidate)
    right = _normalize_meal_name(previous)
    return bool(left) and left == right


def _assistant_turn_content(previous: PreviousMealTurn) -> str:
    return json.dumps(
        {
            "name": previous.name,
            "description": previous.description or "",
            "ingredients_used": previous.ingredients_used or "",
            "instructions": previous.instructions or "",
        },
        ensure_ascii=True,
    )


def _parse_generated_meal(text: str) -> GeneratedMeal:
    payload = _extract_json(text)
    instructions = normalize_instructions(payload.get("instructions", ""))
    payload["instructions"] = instructions
    parsed = GeneratedMeal.model_validate(payload)

    if not parsed.name.strip() or not parsed.instructions.strip():
        raise MealGenerationError("The AI response did not include a complete meal.")

    return parsed


def _finalize_meal_amounts(
    pantry: list[Ingredient],
    items: list[MealIngredientUse],
) -> list[MealIngredientUse]:
    return scale_meal_ingredients_for_one_person(
        pantry,
        clamp_meal_ingredients_to_pantry(pantry, items),
    )


def _estimate_meal_calories(
    pantry: list[Ingredient],
    items: list[MealIngredientUse],
) -> float | None:
    macros = calculate_meal_macros(pantry, serialize_meal_ingredients(items))
    return macros.calories


def _calorie_retry_prompt(calories: float) -> str:
    if calories < MEAL_CALORIE_MIN:
        return (
            f"That meal is only about {calories:.0f} kcal — too light. "
            f"Propose a NEW or revised JSON meal that lands between "
            f"{MEAL_CALORIE_MIN} and {MEAL_CALORIE_MAX} kcal. "
            "Increase portions and/or add more calorie-dense ingredients from the "
            "available list (grains, proteins, cheese, nut butters, creamy soups, fruit). "
            "Do not invent ingredients or exceed pantry maximums."
        )

    return (
        f"That meal is about {calories:.0f} kcal — above the "
        f"{MEAL_CALORIE_MAX} kcal target. "
        f"Propose a revised JSON meal closer to {MEAL_CALORIE_MIN}–{MEAL_CALORIE_MAX} kcal "
        "by reducing portions slightly while keeping it a filling single-person meal."
    )


def _calories_in_target(calories: float | None) -> bool:
    if calories is None:
        # Can't verify — accept rather than looping forever.
        return True
    return MEAL_CALORIE_MIN <= calories <= MEAL_CALORIE_MAX


def _calorie_distance(calories: float | None) -> float:
    if calories is None:
        return float("inf")
    if MEAL_CALORIE_MIN <= calories <= MEAL_CALORIE_MAX:
        return 0.0
    if calories < MEAL_CALORIE_MIN:
        return MEAL_CALORIE_MIN - calories
    return calories - MEAL_CALORIE_MAX


def generate_meal_from_ingredients(
    ingredients: list[Ingredient],
    *,
    previous_meal: PreviousMealTurn | None = None,
) -> GeneratedMeal:
    if not settings.anthropic_api_key:
        raise MealGenerationError(
            "Anthropic API key is not configured. Add ANTHROPIC_API_KEY to your .env file."
        )

    if not ingredients:
        raise MealGenerationError(
            "Add ingredients to your kitchen before generating a meal."
        )

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    base_prompt = MEAL_GENERATION_PROMPT.format(
        ingredients=_format_ingredients(ingredients),
        calorie_min=MEAL_CALORIE_MIN,
        calorie_max=MEAL_CALORIE_MAX,
    )

    messages: list[dict] = [{"role": "user", "content": base_prompt}]

    if previous_meal and previous_meal.name.strip():
        messages.append(
            {
                "role": "assistant",
                "content": _assistant_turn_content(previous_meal),
            }
        )
        messages.append(
            {
                "role": "user",
                "content": FOLLOW_UP_PROMPT.format(
                    calorie_min=MEAL_CALORIE_MIN,
                    calorie_max=MEAL_CALORIE_MAX,
                ),
            }
        )

    last_error: Exception | None = None
    avoid_name = previous_meal.name if previous_meal else None
    best_meal: GeneratedMeal | None = None
    best_distance = float("inf")

    for attempt in range(MEAL_GENERATION_ATTEMPTS):
        conversation = list(messages)
        if attempt > 0 and avoid_name and best_meal is None:
            conversation.append(
                {
                    "role": "user",
                    "content": (
                        f'That was still too similar to "{avoid_name.strip()}". '
                        f"Suggest a clearly different single-person meal "
                        f"({MEAL_CALORIE_MIN}–{MEAL_CALORIE_MAX} kcal) in the same JSON format."
                    ),
                }
            )

        try:
            message = client.messages.create(
                model=MEAL_ANTHROPIC_MODEL,
                max_tokens=4096,
                messages=conversation,
            )
        except Exception as exc:
            last_error = exc
            continue

        text_blocks = [block.text for block in message.content if block.type == "text"]
        if not text_blocks:
            last_error = MealGenerationError("Anthropic returned an empty response.")
            continue

        raw_text = text_blocks[-1]
        try:
            parsed = _parse_generated_meal(raw_text)
        except (json.JSONDecodeError, ValueError, MealGenerationError) as exc:
            if isinstance(exc, MealGenerationError):
                last_error = exc
            else:
                err = MealGenerationError(
                    "Could not parse meal suggestion from the AI response."
                )
                err.__cause__ = exc
                last_error = err
            continue

        if _is_same_meal(parsed.name, avoid_name):
            messages.append({"role": "assistant", "content": raw_text})
            last_error = MealGenerationError(
                "Generated the same meal as last time; retrying."
            )
            continue

        parsed.ingredients_used = _finalize_meal_amounts(
            ingredients,
            parsed.ingredients_used,
        )
        calories = _estimate_meal_calories(ingredients, parsed.ingredients_used)
        distance = _calorie_distance(calories)
        if distance < best_distance:
            best_distance = distance
            best_meal = parsed

        if _calories_in_target(calories):
            return parsed

        messages.append({"role": "assistant", "content": raw_text})
        if calories is not None:
            messages.append(
                {
                    "role": "user",
                    "content": _calorie_retry_prompt(calories),
                }
            )
            last_error = MealGenerationError(
                f"Meal was about {calories:.0f} kcal; outside "
                f"{MEAL_CALORIE_MIN}–{MEAL_CALORIE_MAX} target; retrying."
            )
        else:
            last_error = MealGenerationError(
                "Could not estimate meal calories; retrying."
            )

    if best_meal is not None:
        # Pantry may not support the full range; return the closest attempt.
        return best_meal

    if isinstance(last_error, MealGenerationError):
        if "same meal as last time" in str(last_error):
            raise MealGenerationError(
                "Could not generate a different meal. Please try again."
            )
        raise last_error
    if last_error is not None:
        raise MealGenerationError("Meal generation failed. Please try again.") from last_error
    raise MealGenerationError("Meal generation failed. Please try again.")
