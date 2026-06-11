from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models import Ingredient, User
from app.schemas import CreateManualIngredientRequest, DraftIngredientItem, IngredientResponse
from app.services.ingredients import create_ingredient

router = APIRouter(prefix="/ingredients", tags=["ingredients"])


@router.get("", response_model=list[IngredientResponse])
def list_ingredients(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[IngredientResponse]:
    ingredients = (
        db.query(Ingredient)
        .filter(Ingredient.user_id == current_user.id)
        .order_by(Ingredient.created_at.desc())
        .all()
    )
    return [IngredientResponse.model_validate(item) for item in ingredients]


@router.post("/manual", response_model=IngredientResponse, status_code=status.HTTP_201_CREATED)
def create_manual_ingredient(
    payload: CreateManualIngredientRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> IngredientResponse:
    if not payload.unit:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Select a unit.",
        )

    item = DraftIngredientItem(
        ingredient_name=payload.ingredient_name.strip(),
        store_item_name=payload.ingredient_name.strip(),
        quantity=payload.quantity,
        unit=payload.unit,
        is_manual=True,
    )

    return create_ingredient(db, current_user, item)


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
