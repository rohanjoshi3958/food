from sqlalchemy.orm import Session

from app.models import Ingredient, User
from app.schemas import DraftIngredientItem, IngredientResponse
from app.services.receipt_analyzer import ReceiptAnalysisError, estimate_ingredient_nutrition


def resolve_item_nutrition(item: DraftIngredientItem) -> DraftIngredientItem:
    if not item.is_food or not item.is_manual:
        return item

    try:
        estimated = estimate_ingredient_nutrition(
            item.ingredient_name,
            item.quantity,
            item.unit,
        )
    except ReceiptAnalysisError:
        return item

    return DraftIngredientItem(
        store_item_name=item.store_item_name or item.ingredient_name,
        ingredient_name=item.ingredient_name,
        quantity=item.quantity,
        unit=item.unit,
        serving_size=estimated.serving_size,
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


def create_ingredient(
    db: Session,
    user: User,
    item: DraftIngredientItem,
    receipt_id: str | None = None,
) -> IngredientResponse:
    resolved = resolve_item_nutrition(item)

    ingredient = Ingredient(
        user_id=user.id,
        receipt_id=receipt_id,
        name=resolved.ingredient_name.strip(),
        store_item_name=resolved.store_item_name or resolved.ingredient_name,
        quantity=resolved.quantity,
        unit=resolved.unit,
        serving_size=resolved.serving_size,
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
