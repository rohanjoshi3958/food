from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models import Ingredient, Meal, User
from app.schemas import MealResponse
from app.services.meal_generator import (
    MealGenerationError,
    format_ingredients_used,
    generate_meal_from_ingredients,
)

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


@router.post("/generate", response_model=MealResponse, status_code=status.HTTP_201_CREATED)
def generate_meal(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MealResponse:
    ingredients = (
        db.query(Ingredient)
        .filter(Ingredient.user_id == current_user.id)
        .order_by(Ingredient.created_at.desc())
        .all()
    )

    try:
        suggestion = generate_meal_from_ingredients(ingredients)
    except MealGenerationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Meal generation failed. Please try again.",
        ) from exc

    db.query(Meal).filter(Meal.user_id == current_user.id).delete()

    meal = Meal(
        user_id=current_user.id,
        name=suggestion.name.strip(),
        description=suggestion.description.strip(),
        ingredients_used=format_ingredients_used(suggestion.ingredients_used),
        instructions=suggestion.instructions.strip(),
    )
    db.add(meal)
    db.commit()
    db.refresh(meal)

    return MealResponse.model_validate(meal)
