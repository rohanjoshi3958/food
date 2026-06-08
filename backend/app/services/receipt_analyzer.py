import base64
import json
import mimetypes
import re
from pathlib import Path

import anthropic
from pydantic import BaseModel, Field

from app.config import settings

SUPPORTED_MEDIA_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".pdf": "application/pdf",
}

RECEIPT_ANALYSIS_PROMPT = """Analyze this grocery store receipt image.

Extract every food/grocery line item visible on the receipt. For each item return:
- store_item_name: the exact text printed on the receipt
- ingredient_name: a clear normalized name (e.g. "Organic Bananas")
- quantity and unit from the receipt when shown
- serving_size: a reasonable standard serving (e.g. "1 medium banana (118g)")
- nutritional facts PER serving: calories, protein_g, carbs_g, fat_g, fiber_g, sodium_mg
- nutrition_notes: brief note if values are estimated from standard USDA/database data

Also identify the store name if visible.

Respond with ONLY valid JSON in this exact shape:
{
  "store_name": "Store Name or null",
  "items": [
    {
      "store_item_name": "ORGANIC BANANAS",
      "ingredient_name": "Organic Bananas",
      "quantity": "2.5",
      "unit": "lb",
      "serving_size": "1 medium (118g)",
      "calories": 105,
      "protein_g": 1.3,
      "carbs_g": 27,
      "fat_g": 0.4,
      "fiber_g": 3.1,
      "sodium_mg": 1,
      "nutrition_notes": "Estimated per USDA generic banana"
    }
  ]
}

Skip non-food items like tax, bags, or coupons. Use null for unknown numeric nutrition values."""

NUTRITION_ESTIMATE_PROMPT = """Estimate nutritional facts per standard serving for this grocery item:
- Item: {ingredient_name}
- Quantity purchased: {quantity} {unit}

Respond with ONLY valid JSON:
{{
  "serving_size": "1 serving (describe size)",
  "calories": 100,
  "protein_g": 5,
  "carbs_g": 12,
  "fat_g": 3,
  "fiber_g": 2,
  "sodium_mg": 150,
  "nutrition_notes": "Brief note on data source"
}}

Use null for unknown values. Base estimates on standard USDA or nutrition database values."""


class ParsedReceiptItem(BaseModel):
    store_item_name: str
    ingredient_name: str
    quantity: str | None = None
    unit: str | None = None
    serving_size: str | None = None
    calories: float | None = None
    protein_g: float | None = None
    carbs_g: float | None = None
    fat_g: float | None = None
    fiber_g: float | None = None
    sodium_mg: float | None = None
    nutrition_notes: str | None = None


class ParsedReceipt(BaseModel):
    store_name: str | None = None
    items: list[ParsedReceiptItem] = Field(default_factory=list)


class ReceiptAnalysisError(Exception):
    pass


def _media_type_for_path(path: Path) -> tuple[str, str]:
    suffix = path.suffix.lower()
    media_type = SUPPORTED_MEDIA_TYPES.get(suffix)

    if not media_type:
        guessed, _ = mimetypes.guess_type(path.name)
        media_type = guessed

    if not media_type or media_type not in SUPPORTED_MEDIA_TYPES.values():
        supported = ", ".join(sorted(SUPPORTED_MEDIA_TYPES))
        raise ReceiptAnalysisError(
            f"Unsupported file type. Upload a receipt image or PDF ({supported})."
        )

    content_type = "document" if media_type == "application/pdf" else "image"
    return media_type, content_type


def _extract_json(text: str) -> dict:
    cleaned = text.strip()

    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)

    return json.loads(cleaned)


def analyze_receipt_image(file_path: Path) -> ParsedReceipt:
    if not settings.anthropic_api_key:
        raise ReceiptAnalysisError(
            "Anthropic API key is not configured. Add ANTHROPIC_API_KEY to your .env file."
        )

    media_type, content_type = _media_type_for_path(file_path)
    encoded = base64.standard_b64encode(file_path.read_bytes()).decode("utf-8")

    client = _get_client()

    content_block = {
        "type": content_type,
        "source": {
            "type": "base64",
            "media_type": media_type,
            "data": encoded,
        },
    }

    message = client.messages.create(
        model=settings.anthropic_model,
        max_tokens=4096,
        messages=[
            {
                "role": "user",
                "content": [
                    content_block,
                    {"type": "text", "text": RECEIPT_ANALYSIS_PROMPT},
                ],
            }
        ],
    )

    text_blocks = [block.text for block in message.content if block.type == "text"]
    if not text_blocks:
        raise ReceiptAnalysisError("Anthropic returned an empty response.")

    try:
        payload = _extract_json(text_blocks[-1])
        parsed = ParsedReceipt.model_validate(payload)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ReceiptAnalysisError(
            "Could not parse ingredient data from the receipt analysis."
        ) from exc

    if not parsed.items:
        raise ReceiptAnalysisError(
            "No food items were found on this receipt. Try a clearer photo."
        )

    return parsed


def _get_client() -> anthropic.Anthropic:
    if not settings.anthropic_api_key:
        raise ReceiptAnalysisError(
            "Anthropic API key is not configured. Add ANTHROPIC_API_KEY to your .env file."
        )
    return anthropic.Anthropic(api_key=settings.anthropic_api_key)


def estimate_ingredient_nutrition(
    ingredient_name: str,
    quantity: str | None = None,
    unit: str | None = None,
) -> ParsedReceiptItem:
    client = _get_client()
    qty = quantity or "unknown"
    unit_label = unit or ""

    message = client.messages.create(
        model=settings.anthropic_model,
        max_tokens=1024,
        messages=[
            {
                "role": "user",
                "content": NUTRITION_ESTIMATE_PROMPT.format(
                    ingredient_name=ingredient_name,
                    quantity=qty,
                    unit=unit_label,
                ),
            }
        ],
    )

    text_blocks = [block.text for block in message.content if block.type == "text"]
    if not text_blocks:
        raise ReceiptAnalysisError(
            f"Could not estimate nutrition for {ingredient_name}."
        )

    try:
        payload = _extract_json(text_blocks[-1])
        return ParsedReceiptItem(
            store_item_name=ingredient_name,
            ingredient_name=ingredient_name,
            quantity=quantity,
            unit=unit,
            serving_size=payload.get("serving_size"),
            calories=payload.get("calories"),
            protein_g=payload.get("protein_g"),
            carbs_g=payload.get("carbs_g"),
            fat_g=payload.get("fat_g"),
            fiber_g=payload.get("fiber_g"),
            sodium_mg=payload.get("sodium_mg"),
            nutrition_notes=payload.get("nutrition_notes"),
        )
    except (json.JSONDecodeError, ValueError) as exc:
        raise ReceiptAnalysisError(
            f"Could not parse nutrition estimate for {ingredient_name}."
        ) from exc
