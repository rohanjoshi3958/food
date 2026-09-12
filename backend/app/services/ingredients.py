from sqlalchemy.orm import Session

from app.models import Ingredient, User
from app.schemas import DraftIngredientItem, IngredientResponse
from app.services.ingredient_merge import _merge_key, _sum_quantities
from app.services.ingredient_deduction import normalize_unit, servings_per_pantry_unit
from app.services.ingredient_normalization import clean_display_name
from app.services.receipt_analyzer import (
    ReceiptAnalysisError,
    check_ingredient_unit,
    estimate_ingredient_nutrition,
    match_ingredient_to_pantry,
)


def resolve_item_nutrition(item: DraftIngredientItem) -> DraftIngredientItem:
    if not item.is_food:
        return item.model_copy(
            update={
                "quantity": item.quantity or "1",
                "unit": item.unit or "each",
            }
        )

    effective_unit = (item.unit or "").strip() or "each"
    unit_warning = check_ingredient_unit(item.ingredient_name, effective_unit)
    if unit_warning:
        raise ReceiptAnalysisError(unit_warning.strip())

    estimated = estimate_ingredient_nutrition(
        item.ingredient_name,
        item.quantity,
        item.unit,
    )
    if not estimated.recognized:
        raise ReceiptAnalysisError(
            f'Could not recognize "{item.ingredient_name.strip()}" as a food ingredient. '
            "Use a specific grocery item name."
        )

    pantry_unit = item.unit or estimated.unit or "each"
    computed_servings_per = servings_per_pantry_unit(
        estimated.serving_size,
        pantry_unit,
    )

    return DraftIngredientItem(
        store_item_name=item.store_item_name or item.ingredient_name,
        ingredient_name=item.ingredient_name,
        quantity=item.quantity or estimated.quantity or "1",
        unit=pantry_unit,
        serving_size=estimated.serving_size,
        servings_per_container=(
            computed_servings_per
            if computed_servings_per is not None
            else estimated.servings_per_container
        ),
        calories=estimated.calories,
        protein_g=estimated.protein_g,
        carbs_g=estimated.carbs_g,
        fat_g=estimated.fat_g,
        fiber_g=estimated.fiber_g,
        sodium_mg=estimated.sodium_mg,
        nutrition_notes=estimated.nutrition_notes,
        is_manual=item.is_manual,
        is_food=item.is_food,
    )


def _find_matching_pantry_item(
    db: Session,
    user: User,
    name: str,
    unit: str | None,
) -> tuple[Ingredient | None, bool, str | None]:
    """Find a matching pantry item.

    Returns (matched_ingredient, is_ambiguous, canonical_name).
    - Cheap canonical key match first (no LLM).
    - If that fails and the pantry is non-empty, ask the LLM whether the
      incoming name matches an existing row (handles abbreviations).
    - Ambiguous matches are not auto-merged.
    """
    target_key = _merge_key(name, unit)
    pantry = db.query(Ingredient).filter(Ingredient.user_id == user.id).all()

    for ingredient in pantry:
        if _merge_key(ingredient.name, ingredient.unit) == target_key:
            return (ingredient, False, None)

    if not pantry:
        return (None, False, None)

    # Only send unit-compatible pantry rows to the LLM so incompatible
    # units cannot win a name match.
    target_unit = normalize_unit(unit) or ""
    unit_compatible = [
        ingredient
        for ingredient in pantry
        if (normalize_unit(ingredient.unit) or "") == target_unit
    ]
    if not unit_compatible:
        return (None, False, None)

    pantry_payload = [
        {
            "id": ingredient.id,
            "name": ingredient.name,
            "unit": ingredient.unit or "",
        }
        for ingredient in unit_compatible
    ]

    try:
        llm_match = match_ingredient_to_pantry(name, unit, pantry_payload)
    except ReceiptAnalysisError:
        return (None, False, None)

    if llm_match.ambiguous:
        return (None, True, llm_match.canonical_name)

    if llm_match.match_id:
        for ingredient in unit_compatible:
            if ingredient.id == llm_match.match_id:
                return (ingredient, False, llm_match.canonical_name)

    return (None, False, llm_match.canonical_name)


def create_ingredient(
    db: Session,
    user: User,
    item: DraftIngredientItem,
    receipt_id: str | None = None,
) -> IngredientResponse:
    """Create or merge an ingredient into the user's inventory.

    Uses cheap local keys for exact matches, then an LLM pantry match for
    abbreviation / naming variants. Ambiguous matches create new entries.
    """
    resolved = resolve_item_nutrition(item)
    name = resolved.ingredient_name.strip()
    existing, is_ambiguous, canonical_name = _find_matching_pantry_item(
        db, user, name, resolved.unit
    )

    if existing is not None and not is_ambiguous:
        existing.original_quantity = _sum_quantities(
            existing.original_quantity or existing.quantity,
            resolved.quantity,
        )
        existing.quantity = _sum_quantities(existing.quantity, resolved.quantity)
        if receipt_id is not None:
            existing.receipt_id = receipt_id
        if not existing.serving_size and resolved.serving_size:
            existing.serving_size = resolved.serving_size
        if (
            existing.servings_per_container is None
            and resolved.servings_per_container is not None
        ):
            existing.servings_per_container = resolved.servings_per_container
        for field in (
            "calories",
            "protein_g",
            "carbs_g",
            "fat_g",
            "fiber_g",
            "sodium_mg",
            "nutrition_notes",
        ):
            if getattr(existing, field) is None and getattr(resolved, field) is not None:
                setattr(existing, field, getattr(resolved, field))

        db.commit()
        db.refresh(existing)
        return IngredientResponse.model_validate(existing)

    display_name = (canonical_name or "").strip() or clean_display_name(name)

    ingredient = Ingredient(
        user_id=user.id,
        receipt_id=receipt_id,
        name=display_name,
        store_item_name=resolved.store_item_name or resolved.ingredient_name,
        quantity=resolved.quantity,
        original_quantity=resolved.quantity,
        unit=resolved.unit,
        serving_size=resolved.serving_size,
        servings_per_container=resolved.servings_per_container,
        calories=resolved.calories,
        protein_g=resolved.protein_g,
        carbs_g=resolved.carbs_g,
        fat_g=resolved.fat_g,
        fiber_g=resolved.fiber_g,
        sodium_mg=resolved.sodium_mg,
        nutrition_notes=resolved.nutrition_notes,
    )
    db.add(ingredient)
    db.commit()
    db.refresh(ingredient)
    return IngredientResponse.model_validate(ingredient)
