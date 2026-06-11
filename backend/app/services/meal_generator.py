import json
import re

import anthropic
from pydantic import BaseModel, Field

from app.config import settings
from app.models import Ingredient

MEAL_GENERATION_PROMPT = """You are a helpful home chef. Given the ingredients available in the user's kitchen, suggest ONE practical meal they can make right now.

Available ingredients:
{ingredients}

Use each selected ingredient's quantity, unit, and serving size when deciding how much to use in the recipe. You do NOT need to use every available ingredient — choose a sensible subset that makes one cohesive, practical meal. Only include ingredients you actually use in ingredients_used. You may assume basic pantry staples (salt, pepper, cooking oil, butter, water) are available if needed.

Respond with ONLY valid JSON in this exact shape:
{{
  "name": "Meal Name",
  "description": "One or two sentence summary of the dish.",
  "ingredients_used": [
    {{
      "name": "Ingredient name from the list",
      "amount": "How much to use, e.g. 2 cups"
    }}
  ],
  "instructions": [
    "First step written as a complete sentence.",
    "Second step written as a complete sentence."
  ]
}}

Put each instruction step in its own array element. Do not combine multiple steps into one string."""


class MealIngredientUse(BaseModel):
    name: str
    amount: str


class GeneratedMeal(BaseModel):
    name: str
    description: str
    ingredients_used: list[MealIngredientUse] = Field(default_factory=list)
    instructions: str


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
            details.append(f"quantity on hand: {quantity_label}")
        if ingredient.serving_size:
            details.append(f"serving size: {ingredient.serving_size}")

        lines.append(", ".join(details))

    return "\n".join(lines)


def format_ingredients_used(items: list[MealIngredientUse]) -> str:
    return "\n".join(f"- {item.name}: {item.amount}" for item in items)


def generate_meal_from_ingredients(ingredients: list[Ingredient]) -> GeneratedMeal:
    if not settings.anthropic_api_key:
        raise MealGenerationError(
            "Anthropic API key is not configured. Add ANTHROPIC_API_KEY to your .env file."
        )

    if not ingredients:
        raise MealGenerationError(
            "Add ingredients to your kitchen before generating a meal."
        )

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    message = client.messages.create(
        model=settings.anthropic_model,
        max_tokens=4096,
        messages=[
            {
                "role": "user",
                "content": MEAL_GENERATION_PROMPT.format(
                    ingredients=_format_ingredients(ingredients),
                ),
            }
        ],
    )

    text_blocks = [block.text for block in message.content if block.type == "text"]
    if not text_blocks:
        raise MealGenerationError("Anthropic returned an empty response.")

    try:
        payload = _extract_json(text_blocks[-1])
        instructions = normalize_instructions(payload.get("instructions", ""))
        payload["instructions"] = instructions
        parsed = GeneratedMeal.model_validate(payload)
    except (json.JSONDecodeError, ValueError) as exc:
        raise MealGenerationError(
            "Could not parse meal suggestion from the AI response."
        ) from exc

    if not parsed.name.strip() or not parsed.instructions.strip():
        raise MealGenerationError("The AI response did not include a complete meal.")

    return parsed
