from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models import Ingredient, User
from app.schemas import IngredientResponse

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
