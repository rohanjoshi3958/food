from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models import Meal, User
from app.schemas import MealResponse

router = APIRouter(prefix="/meals", tags=["meals"])


@router.get("", response_model=list[MealResponse])
def list_meals(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[MealResponse]:
    meals = (
        db.query(Meal)
        .filter(Meal.user_id == current_user.id)
        .order_by(Meal.created_at.desc())
        .all()
    )
    return [MealResponse.model_validate(meal) for meal in meals]
