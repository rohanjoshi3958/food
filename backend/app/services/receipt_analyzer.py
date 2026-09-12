import base64
import json
import mimetypes
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import anthropic
from pydantic import BaseModel, Field

from app.config import RECEIPT_ANTHROPIC_MODEL, settings
from app.services.anthropic_cache import create_cached_message
from app.services.model_router import RouteDecision, escalation_for, route_model


def _anthropic_error_message(exc: Exception) -> str:
    response = getattr(exc, "response", None)
    if response is not None:
        try:
            payload = response.json()
            error = payload.get("error", {})
            message = error.get("message")
            if message:
                return message
        except Exception:
            pass

    message = str(exc)
    if "not_found_error" in message or "model:" in message:
        return (
            f"The Anthropic model ({RECEIPT_ANTHROPIC_MODEL}) is unavailable. "
            "Receipt analysis failed."
        )

    return "Receipt analysis failed. Please try again."


SUPPORTED_MEDIA_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".pdf": "application/pdf",
}

RECEIPT_ANALYSIS_PROMPT = """Analyze this grocery store receipt image.

Focus ONLY on reading what is printed on the receipt. Do NOT estimate nutrition, serving sizes, or servings per container.

Extract every line item visible on the receipt. For each item return:
- store_item_name: the exact text printed on the receipt
- ingredient_name: a clear plain-English grocery name. Expand receipt abbreviations
  (e.g. "CHKN BRST" → "Chicken Breast", "ORG BNNAS" → "Organic Bananas",
  "GRND BF" → "Ground Beef"). Prefer singular common food names when natural
  ("Bananas" → "Banana" is fine as "Bananas" or "Banana"; be consistent and readable).
  Keep useful qualifiers like Organic/Fresh/Frozen when they are part of the product.
  Do not leave shorthand in ingredient_name.
- is_food: true for groceries/food, false for non-food (tax, bags, coupons, fees, etc.)
- quantity: quantity from the receipt when shown; if missing, make an educated guess (often "1")
- unit: unit from the receipt when shown (e.g. lb, oz, each). If missing, make an educated guess based on the item (packaged goods usually "each"; produce often "lb" or "each"; liquids often "fl oz" or "each" for bottles). Never leave unit null for food items.

Also identify the store name if visible.

Respond with ONLY valid JSON in this exact shape:
{
  "store_name": "Store Name or null",
  "items": [
    {
      "store_item_name": "ORGANIC BANANAS",
      "ingredient_name": "Organic Bananas",
      "is_food": true,
      "quantity": "2.5",
      "unit": "lb"
    }
  ]
}

Include non-food receipt lines (tax, bags, coupons, etc.) with is_food set to false.
Prefer accurate extraction from the receipt; only guess quantity/unit when they are not printed."""

# Prompt layout for caching: each *_PROMPT below is a byte-stable prefix
# (task framing + rules + JSON response shape) sent as a cached system block.
# The matching *_USER_PROMPT carries only per-request values and is sent in
# the user turn, after the cache breakpoint. See backend/PROMPT_CACHING.md.

NUTRITION_ESTIMATE_PROMPT = """Estimate nutritional facts per standard serving for the grocery item described in the user message (item name, quantity purchased, and unit).

If quantity or unit is missing/unknown, guess a typical grocery purchase quantity and unit for this item
(e.g. almond butter jar → quantity "1", unit "each"; bananas → quantity "1", unit "lb" or "each").

Also estimate how many of those standard servings are in ONE unit of the purchase unit.
Examples:
- unit "each" for a jar of almond butter → servings_per_container ≈ 15 (servings in one jar)
- unit "oz" for yogurt → servings_per_container ≈ servings per 1 oz
- unit "lb" for bananas → servings_per_container ≈ servings per 1 lb

Set "recognized" to true ONLY when the item name clearly identifies a real grocery food or beverage
you can match to USDA-style nutrition data (e.g. "chicken breast", "bananas", "greek yogurt").
Set "recognized" to false when the name is empty, too vague, ambiguous, profane, gibberish,
not food, or cannot be matched to a plausible grocery product (e.g. "po", "food", "item").

When recognized is false, set every nutrition field to null and explain briefly in nutrition_notes.

Respond with ONLY valid JSON:
{
  "recognized": true,
  "quantity": "1",
  "unit": "each",
  "serving_size": "1 serving (describe size)",
  "servings_per_container": 15,
  "calories": 100,
  "protein_g": 5,
  "carbs_g": 12,
  "fat_g": 3,
  "fiber_g": 2,
  "sodium_mg": 150,
  "nutrition_notes": "Brief note on data source"
}

For quantity and unit: echo the provided values when known; otherwise fill in your educated guess.
Use null for unknown nutrition values. Base estimates on standard USDA or nutrition database / typical package sizes."""

NUTRITION_ESTIMATE_USER_PROMPT = """- Item: {ingredient_name}
- Quantity purchased: {quantity}
- Unit: {unit}"""

UNIT_CHECK_PROMPT = """Assess whether the grocery purchase unit given in the user message is plausible for the item.

Set "unit_plausible" to true when the unit fits how this is normally bought or measured.
Set "unit_plausible" to false when the unit is a poor fit (e.g. whole produce in gallon, dry spice in ml).
When false, set "unit_warning" to one short sentence suggesting better units; otherwise null.
The app rejects ingredients that fail this check.

Respond with ONLY valid JSON:
{
  "unit_plausible": true,
  "unit_warning": null
}"""

UNIT_CHECK_USER_PROMPT = """- Item: {ingredient_name}
- Unit: {unit}"""

PANTRY_MATCH_PROMPT = """Match an incoming grocery item to the user's existing pantry.

The user message provides the incoming item (name and unit) and the existing pantry items as JSON.

Rules:
- Expand receipt abbreviations when interpreting the incoming name
  (e.g. CHKN BRST → Chicken Breast, ORG BNNAS → Organic Bananas).
- Treat singular/plural forms as the same food (Chicken Breasts ≈ Chicken Breast,
  Tomatoes ≈ Tomato) when deciding matches.
- Treat common product qualifiers (organic, fresh, frozen, brand/store prefixes)
  as the same core food when clearly equivalent
  (Organic Chicken Breast ≈ Chicken Breast), unless the qualifier changes the
  product identity (Brown Rice ≠ White Rice, Skim Milk ≠ Whole Milk).
- Set match_id to an existing pantry item id ONLY when it is clearly the same food
  AND the unit is compatible for merging (same measurement family / same package unit).
- Set ambiguous to true when more than one pantry item could reasonably match
  (e.g. "Rice" vs both "Brown Rice" and "White Rice"), or when the match is only partial.
  When ambiguous, match_id must be null.
- Set match_id to null when nothing clearly matches.
- Never invent pantry ids; only use ids from the existing pantry items list.
- canonical_name should be a clear plain-English display name for the incoming item
  (expanded abbreviations, sensible singular/plural, title-friendly).

Respond with ONLY valid JSON:
{
  "match_id": null,
  "ambiguous": false,
  "canonical_name": "Chicken Breast"
}"""

PANTRY_MATCH_USER_PROMPT = """Incoming item:
- name: {ingredient_name}
- unit: {unit}

Existing pantry items (JSON):
{pantry_json}"""


class ParsedReceiptItem(BaseModel):
    store_item_name: str
    ingredient_name: str
    is_food: bool = True
    recognized: bool = False
    quantity: str | None = None
    unit: str | None = None
    serving_size: str | None = None
    servings_per_container: float | None = None
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


def _get_client() -> anthropic.Anthropic:
    if not settings.anthropic_api_key:
        raise ReceiptAnalysisError(
            "Anthropic API key is not configured. Add ANTHROPIC_API_KEY to your .env file."
        )
    return anthropic.Anthropic(api_key=settings.anthropic_api_key)


def _as_optional_float(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    return number


def _unit_warning_from_payload(payload: dict) -> str | None:
    if payload.get("unit_plausible") is True:
        return None
    return _as_optional_str(payload.get("unit_warning"))


def check_ingredient_unit(
    ingredient_name: str,
    unit: str | None = None,
) -> str | None:
    name = ingredient_name.strip()
    unit_label = (unit or "").strip()
    if not name or not unit_label:
        return None

    client = _get_client()

    def ask(decision: RouteDecision) -> str | None:
        message = create_cached_message(
            client,
            call_site="receipt.unit_check",
            model=decision.model,
            max_tokens=256,
            system_prefix=UNIT_CHECK_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": UNIT_CHECK_USER_PROMPT.format(
                        ingredient_name=name,
                        unit=unit_label,
                    ),
                }
            ],
        )

        text_blocks = [block.text for block in message.content if block.type == "text"]
        if not text_blocks:
            return None

        try:
            payload = _extract_json(text_blocks[-1])
        except (json.JSONDecodeError, ValueError):
            return None

        return _unit_warning_from_payload(payload)

    decision = route_model("receipt.unit_check")
    warning = ask(decision)
    # A rejection blocks the user, so a cheap tier's "implausible" is treated
    # as low confidence and confirmed on the escalation tier (routing on only).
    if warning is not None:
        escalated = escalation_for(decision)
        if escalated is not None:
            warning = ask(escalated)
    return warning


class PantryMatchResult(BaseModel):
    match_id: str | None = None
    ambiguous: bool = False
    canonical_name: str | None = None


def match_ingredient_to_pantry(
    ingredient_name: str,
    unit: str | None,
    pantry_items: list[dict],
) -> PantryMatchResult:
    """Use the LLM to match an incoming ingredient against existing pantry rows."""
    name = ingredient_name.strip()
    if not name:
        return PantryMatchResult()

    unit_label = (unit or "").strip() or "each"
    valid_ids = {
        str(item.get("id"))
        for item in pantry_items
        if item.get("id") is not None
    }

    # Always ask the LLM for a canonical display name, even when the pantry is
    # empty. match_id must stay null when there are no candidates.
    client = _get_client()

    def ask(decision: RouteDecision) -> tuple[PantryMatchResult, bool]:
        """Return (result, low_confidence)."""
        message = create_cached_message(
            client,
            call_site="receipt.pantry_match",
            model=decision.model,
            max_tokens=256,
            system_prefix=PANTRY_MATCH_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": PANTRY_MATCH_USER_PROMPT.format(
                        ingredient_name=name,
                        unit=unit_label,
                        pantry_json=json.dumps(pantry_items, ensure_ascii=True),
                    ),
                }
            ],
        )

        text_blocks = [block.text for block in message.content if block.type == "text"]
        if not text_blocks:
            return PantryMatchResult(), True

        try:
            payload = _extract_json(text_blocks[-1])
        except (json.JSONDecodeError, ValueError):
            return PantryMatchResult(), True

        if not isinstance(payload, dict):
            return PantryMatchResult(), True

        match_id = _as_optional_str(payload.get("match_id"))
        invented_id = match_id is not None and match_id not in valid_ids
        if not valid_ids or invented_id:
            match_id = None

        ambiguous = payload.get("ambiguous") is True
        if ambiguous:
            match_id = None

        canonical_name = _as_optional_str(payload.get("canonical_name"))
        result = PantryMatchResult(
            match_id=match_id,
            ambiguous=ambiguous,
            canonical_name=canonical_name,
        )
        # Low confidence = the model could not commit (ambiguous), pointed at
        # an id it was never offered, or failed to answer in the JSON shape.
        return result, ambiguous or invented_id

    decision = route_model("receipt.pantry_match")
    result, low_confidence = ask(decision)
    if low_confidence:
        escalated = escalation_for(decision)
        if escalated is not None:
            result, _ = ask(escalated)
    return result


def estimate_ingredient_nutrition(
    ingredient_name: str,
    quantity: str | None = None,
    unit: str | None = None,
) -> ParsedReceiptItem:
    client = _get_client()
    qty = (quantity or "").strip() or "unknown"
    unit_label = (unit or "").strip() or "unknown"

    message = create_cached_message(
        client,
        call_site="receipt.nutrition_estimate",
        model=route_model("receipt.nutrition_estimate").model,
        max_tokens=1024,
        system_prefix=NUTRITION_ESTIMATE_PROMPT,
        messages=[
            {
                "role": "user",
                "content": NUTRITION_ESTIMATE_USER_PROMPT.format(
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
        guessed_quantity = quantity or _as_optional_str(payload.get("quantity")) or "1"
        guessed_unit = unit or _as_optional_str(payload.get("unit")) or "each"

        return ParsedReceiptItem(
            store_item_name=ingredient_name,
            ingredient_name=ingredient_name,
            recognized=payload.get("recognized") is True,
            quantity=guessed_quantity,
            unit=guessed_unit,
            serving_size=payload.get("serving_size"),
            servings_per_container=_as_optional_float(
                payload.get("servings_per_container")
            ),
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


def _as_optional_str(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"unknown", "null", "none", "n/a"}:
        return None
    return text


def _enrich_item_with_nutrition(item: ParsedReceiptItem) -> ParsedReceiptItem:
    if not item.is_food:
        return item

    try:
        estimated = estimate_ingredient_nutrition(
            item.ingredient_name,
            item.quantity,
            item.unit,
        )
    except Exception:
        return item.model_copy(
            update={
                "quantity": item.quantity or "1",
                "unit": item.unit or "each",
            }
        )

    return item.model_copy(
        update={
            "quantity": item.quantity or estimated.quantity or "1",
            "unit": item.unit or estimated.unit or "each",
            "serving_size": estimated.serving_size,
            "servings_per_container": estimated.servings_per_container,
            "calories": estimated.calories,
            "protein_g": estimated.protein_g,
            "carbs_g": estimated.carbs_g,
            "fat_g": estimated.fat_g,
            "fiber_g": estimated.fiber_g,
            "sodium_mg": estimated.sodium_mg,
            "nutrition_notes": estimated.nutrition_notes,
        }
    )


def _enrich_receipt_nutrition(parsed: ParsedReceipt) -> ParsedReceipt:
    food_indexes = [index for index, item in enumerate(parsed.items) if item.is_food]
    if not food_indexes:
        return parsed

    enriched_items = list(parsed.items)

    with ThreadPoolExecutor(max_workers=min(6, len(food_indexes))) as executor:
        futures = {
            executor.submit(_enrich_item_with_nutrition, parsed.items[index]): index
            for index in food_indexes
        }
        for future in as_completed(futures):
            index = futures[future]
            enriched_items[index] = future.result()

    return ParsedReceipt(store_name=parsed.store_name, items=enriched_items)


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

    # The receipt image/PDF is unique per request, so it must stay in the
    # user turn after the cached instruction prefix.
    try:
        message = create_cached_message(
            client,
            call_site="receipt.analyze_image",
            model=route_model("receipt.analyze_image").model,
            max_tokens=4096,
            system_prefix=RECEIPT_ANALYSIS_PROMPT,
            messages=[{"role": "user", "content": [content_block]}],
        )
    except anthropic.APIError as exc:
        raise ReceiptAnalysisError(_anthropic_error_message(exc)) from exc

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

    food_items = [item for item in parsed.items if item.is_food]
    if not food_items:
        raise ReceiptAnalysisError(
            "No food items were found on this receipt. Try a clearer photo."
        )

    return _enrich_receipt_nutrition(parsed)
