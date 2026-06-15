import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.dependencies import get_current_user
from app.models import Ingredient, Meal, User
from app.schemas import MealResponse, meal_response
from app.services.cookbook import add_meal_to_cookbook
from app.services.ingredient_deduction import serialize_meal_ingredients
from app.services.meal_nutrition import calculate_meal_macros
from app.services.meal_generator import (
    MealGenerationError,
    format_ingredients_used,
    generate_meal_from_ingredients,
)

router = APIRouter(prefix="/meals", tags=["meals"])

ALLOWED_PHOTO_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/gif",
}


def _get_meal_for_user(meal_id: str, current_user: User, db: Session) -> Meal:
    meal = (
        db.query(Meal)
        .filter(Meal.id == meal_id, Meal.user_id == current_user.id)
        .first()
    )

    if meal is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meal not found.")

    return meal


def _meal_photo_path(user_id: str, filename: str) -> Path:
    return Path(settings.meal_upload_dir) / user_id / filename


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
    return [meal_response(meal) for meal in meals]


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

    used_data = serialize_meal_ingredients(suggestion.ingredients_used)
    macros = calculate_meal_macros(ingredients, used_data)

    meal = Meal(
        user_id=current_user.id,
        name=suggestion.name.strip(),
        description=suggestion.description.strip(),
        ingredients_used=format_ingredients_used(suggestion.ingredients_used),
        ingredients_used_data=used_data,
        instructions=suggestion.instructions.strip(),
        **macros.as_dict(),
    )
    db.add(meal)
    db.commit()
    db.refresh(meal)

    return meal_response(meal)


@router.get("/{meal_id}", response_model=MealResponse)
def get_meal(
    meal_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MealResponse:
    meal = _get_meal_for_user(meal_id, current_user, db)
    return meal_response(meal)


@router.post("/{meal_id}/photo", response_model=MealResponse)
async def upload_meal_photo(
    meal_id: str,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MealResponse:
    if not file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A photo is required.",
        )

    if file.content_type not in ALLOWED_PHOTO_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Upload a JPG, PNG, WEBP, or GIF image.",
        )

    meal = _get_meal_for_user(meal_id, current_user, db)

    upload_root = Path(settings.meal_upload_dir) / current_user.id
    upload_root.mkdir(parents=True, exist_ok=True)

    if meal.photo_filename:
        existing = _meal_photo_path(current_user.id, meal.photo_filename)
        if existing.exists():
            existing.unlink()

    safe_name = Path(file.filename).name
    stored_name = f"{uuid.uuid4().hex}_{safe_name}"
    destination = upload_root / stored_name

    contents = await file.read()
    destination.write_bytes(contents)

    meal.photo_filename = stored_name
    db.commit()
    db.refresh(meal)

    add_meal_to_cookbook(db, meal, current_user)

    return meal_response(meal)


@router.get("/{meal_id}/photo")
def get_meal_photo(
    meal_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FileResponse:
    meal = _get_meal_for_user(meal_id, current_user, db)

    if not meal.photo_filename:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Photo not found.")

    photo_path = _meal_photo_path(current_user.id, meal.photo_filename)

    if not photo_path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Photo not found.")

    return FileResponse(photo_path)
