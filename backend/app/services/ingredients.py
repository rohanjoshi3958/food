from sqlalchemy.orm import Session

from app.models import Ingredient, User
from app.schemas import DraftIngredientItem, IngredientResponse
from app.services.ingredient_merge import _merge_key, _sum_quantities
from app.services.receipt_analyzer import ReceiptAnalysisError, estimate_ingredient_nutrition


def resolve_item_nutrition(item: DraftIngredientItem) -> DraftIngredientItem:
    if not item.is_food or not item.is_manual:
        return item.model_copy(
            update={
                "quantity": item.quantity or "1",
                "unit": item.unit or "each",
            }
        )

    try:
        estimated = estimate_ingredient_nutrition(
            item.ingredient_name,
            item.quantity,
            item.unit,
        )
    except ReceiptAnalysisError:
        return item.model_copy(
            update={
                "quantity": item.quantity or "1",
                "unit": item.unit or "each",
            }
        )

    return DraftIngredientItem(
        store_item_name=item.store_item_name or item.ingredient_name,
        ingredient_name=item.ingredient_name,
        quantity=item.quantity or estimated.quantity or "1",
        unit=item.unit or estimated.unit or "each",
        serving_size=estimated.serving_size,
        servings_per_container=estimated.servings_per_container,
        calories=estimated.calories,
        protein_g=estimated.protein_g,
        carbs_g=estimated.carbs_g,
        fat_g=estimated.fat_g,
        fiber_g=estimated.fiber_g,
        sodium_mg=estimated.sodium_mg,
        nutrition_notes=estimated.nutrition_notes,
        is_manual=True,
        is_food=item.is_food,
    )


def _find_matching_pantry_item(
    db: Session,
    user: User,
    name: str,
    unit: str | None,
) -> Ingredient | None:
    target_key = _merge_key(name, unit)
    pantry = db.query(Ingredient).filter(Ingredient.user_id == user.id).all()

    for ingredient in pantry:
        if _merge_key(ingredient.name, ingredient.unit) == target_key:
            return ingredient

    return None


def create_ingredient(
    db: Session,
    user: User,
    item: DraftIngredientItem,
    receipt_id: str | None = None,
) -> IngredientResponse:
    resolved = resolve_item_nutrition(item)
    name = resolved.ingredient_name.strip()
    existing = _find_matching_pantry_item(db, user, name, resolved.unit)

    if existing is not None:
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

    ingredient = Ingredient(
        user_id=user.id,
        receipt_id=receipt_id,
        name=name,
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
