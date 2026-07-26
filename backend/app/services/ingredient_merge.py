from __future__ import annotations

from app.services.ingredient_deduction import (
    _format_quantity,
    _normalize_name,
    normalize_unit,
    parse_number,
)


def _merge_key(name: str | None, unit: str | None) -> tuple[str, str]:
    return (_normalize_name(name or ""), normalize_unit(unit) or "")


def _sum_quantities(left: str | None, right: str | None) -> str | None:
    left_value = parse_number(left or "")
    right_value = parse_number(right or "")

    if left_value is None and right_value is None:
        if left and right and left.strip() == right.strip():
            return left.strip()
        if left and not right:
            return left
        if right and not left:
            return right
        return left or right

    total = (left_value or 0.0) + (right_value or 0.0)
    return _format_quantity(total)


def merge_draft_items(items: list[dict]) -> list[dict]:
    """Combine draft rows that share the same ingredient name and unit."""
    merged: dict[tuple[str, str], dict] = {}
    order: list[tuple[str, str]] = []

    for raw in items:
        if raw.get("is_food") is False:
            continue

        name = (raw.get("ingredient_name") or "").strip()
        if not name:
            continue

        key = _merge_key(name, raw.get("unit"))
        if key not in merged:
            merged[key] = {
                **raw,
                "ingredient_name": name,
                "store_item_name": raw.get("store_item_name") or name,
                "quantity": raw.get("quantity"),
                "unit": raw.get("unit"),
            }
            order.append(key)
            continue

        existing = merged[key]
        existing["quantity"] = _sum_quantities(
            existing.get("quantity"),
            raw.get("quantity"),
        )
        existing["is_manual"] = bool(existing.get("is_manual")) and bool(
            raw.get("is_manual")
        )

        if not existing.get("serving_size") and raw.get("serving_size"):
            existing["serving_size"] = raw.get("serving_size")
        if (
            existing.get("servings_per_container") is None
            and raw.get("servings_per_container") is not None
        ):
            existing["servings_per_container"] = raw.get("servings_per_container")
        for field in (
            "calories",
            "protein_g",
            "carbs_g",
            "fat_g",
            "fiber_g",
            "sodium_mg",
            "nutrition_notes",
        ):
            if existing.get(field) is None and raw.get(field) is not None:
                existing[field] = raw.get(field)

        store_label = raw.get("store_item_name")
        if (
            store_label
            and existing.get("store_item_name")
            and store_label != existing.get("store_item_name")
            and store_label not in str(existing.get("store_item_name"))
        ):
            existing["store_item_name"] = (
                f"{existing['store_item_name']}; {store_label}"
            )

    return [merged[key] for key in order]
