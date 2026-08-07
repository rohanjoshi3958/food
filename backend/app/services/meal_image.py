import base64

import anthropic
from openai import OpenAI

from app.config import MEAL_ANTHROPIC_MODEL, OPENAI_IMAGE_MODEL, settings
from app.models import Meal

PROMPT_SYSTEM = """You write short prompts for photorealistic food photography.
Respond with ONLY the image prompt text — no quotes, labels, or explanation."""


class MealImageError(Exception):
    pass


def _fallback_prompt(meal: Meal) -> str:
    parts = [
        f"Photorealistic overhead food photography of {meal.name}",
        "plated and ready to eat on a simple ceramic plate",
        "natural soft lighting, shallow depth of field, appetizing, no text, no watermark",
    ]
    if meal.description:
        parts.insert(1, meal.description.strip())
    if meal.ingredients_used:
        parts.insert(1, f"featuring: {meal.ingredients_used.strip()[:400]}")
    return ". ".join(parts)


def _build_image_prompt(meal: Meal) -> str:
    if not settings.anthropic_api_key:
        return _fallback_prompt(meal)

    user_prompt = (
        f"Meal name: {meal.name}\n"
        f"Description: {meal.description or 'N/A'}\n"
        f"Ingredients:\n{meal.ingredients_used or 'N/A'}\n\n"
        "Write one concise prompt (under 80 words) for a realistic photo of this finished dish."
    )

    try:
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        response = client.messages.create(
            model=MEAL_ANTHROPIC_MODEL,
            max_tokens=200,
            system=PROMPT_SYSTEM,
            messages=[{"role": "user", "content": user_prompt}],
        )
        text = "".join(
            block.text for block in response.content if getattr(block, "type", None) == "text"
        ).strip()
        return text or _fallback_prompt(meal)
    except Exception:
        return _fallback_prompt(meal)


def generate_meal_image(meal: Meal) -> bytes:
    if not settings.openai_api_key:
        raise MealImageError(
            "OpenAI API key is not configured. Add OPENAI_API_KEY to your .env file."
        )

    prompt = _build_image_prompt(meal)
    client = OpenAI(api_key=settings.openai_api_key)

    try:
        result = client.images.generate(
            model=OPENAI_IMAGE_MODEL,
            prompt=prompt,
            size="1024x1024",
            quality="medium",
        )
    except Exception as exc:
        raise MealImageError(
            "Unable to generate a meal image. Please try again or upload your own photo."
        ) from exc

    if not result.data or not result.data[0].b64_json:
        raise MealImageError(
            "Unable to generate a meal image. Please try again or upload your own photo."
        )

    return base64.b64decode(result.data[0].b64_json)
