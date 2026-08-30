from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models import Ingredient, User
from app.schemas import (
    CheckUnitRequest,
    CheckUnitResponse,
    CreateManualIngredientRequest,
    DraftIngredientItem,
    IngredientResponse,
)
from app.services.ingredient_merge import _merge_key, _sum_quantities
from app.services.ingredients import create_ingredient
from app.services.receipt_analyzer import ReceiptAnalysisError, check_ingredient_unit
from app.validation import validate_ingredient_input

router = APIRouter(prefix="/ingredients", tags=["ingredients"])


def _consolidate_pantry(db: Session, user: User) -> list[Ingredient]:
    pantry = (
        db.query(Ingredient)
        .filter(Ingredient.user_id == user.id)
        .order_by(Ingredient.created_at.asc())
        .all()
    )

    keepers: dict[tuple[str, str], Ingredient] = {}
    changed = False

    for ingredient in pantry:
        key = _merge_key(ingredient.name, ingredient.unit)
        existing = keepers.get(key)
        if existing is None:
            keepers[key] = ingredient
            continue

        existing.original_quantity = _sum_quantities(
            existing.original_quantity or existing.quantity,
            ingredient.original_quantity or ingredient.quantity,
        )
        existing.quantity = _sum_quantities(existing.quantity, ingredient.quantity)
        if not existing.serving_size and ingredient.serving_size:
            existing.serving_size = ingredient.serving_size
        if (
            existing.servings_per_container is None
            and ingredient.servings_per_container is not None
        ):
            existing.servings_per_container = ingredient.servings_per_container
        for field in (
            "calories",
            "protein_g",
            "carbs_g",
            "fat_g",
            "fiber_g",
            "sodium_mg",
            "nutrition_notes",
        ):
            if getattr(existing, field) is None and getattr(ingredient, field) is not None:
                setattr(existing, field, getattr(ingredient, field))

        db.delete(ingredient)
        changed = True

    if changed:
        db.commit()

    return (
        db.query(Ingredient)
        .filter(Ingredient.user_id == user.id)
        .order_by(Ingredient.created_at.desc())
        .all()
    )


@router.get("", response_model=list[IngredientResponse])
def list_ingredients(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[IngredientResponse]:
    ingredients = _consolidate_pantry(db, current_user)
    return [IngredientResponse.model_validate(item) for item in ingredients]


@router.post("/unit-check", response_model=CheckUnitResponse)
def check_unit(
    payload: CheckUnitRequest,
    current_user: User = Depends(get_current_user),
) -> CheckUnitResponse:
    del current_user
    try:
        warning = check_ingredient_unit(
            payload.ingredient_name.strip(),
            payload.unit.strip(),
        )
    except ReceiptAnalysisError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    return CheckUnitResponse(warning=warning)


@router.post("/manual", response_model=IngredientResponse, status_code=status.HTTP_201_CREATED)
def create_manual_ingredient(
    payload: CreateManualIngredientRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> IngredientResponse:
    if not payload.ingredient_name.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Enter an ingredient name.",
        )

    is_valid, error_message = validate_ingredient_input(payload.quantity, payload.unit)
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error_message,
        )

    item = DraftIngredientItem(
        ingredient_name=payload.ingredient_name.strip(),
        store_item_name=payload.ingredient_name.strip(),
        quantity=payload.quantity,
        unit=payload.unit,
        is_manual=True,
    )

    try:
        return create_ingredient(db, current_user, item)
    except ReceiptAnalysisError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.delete("/{ingredient_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_ingredient(
    ingredient_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    ingredient = (
        db.query(Ingredient)
        .filter(Ingredient.id == ingredient_id, Ingredient.user_id == current_user.id)
        .first()
    )

    if ingredient is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Ingredient not found.",
        )

    db.delete(ingredient)
    db.commit()
