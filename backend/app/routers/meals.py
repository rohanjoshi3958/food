import logging

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models import Ingredient, Meal, User
from app.schemas import GenerateMealRequest, MealResponse, meal_response
from app.services.cookbook import add_meal_to_cookbook
from app.services.ingredient_deduction import serialize_meal_ingredients
from app.services.meal_image import MealImageError, generate_meal_image
from app.services.meal_nutrition import calculate_meal_macros
from app.services.meal_generator import (
    MealGenerationError,
    PreviousMealTurn,
    format_ingredients_used,
    generate_meal_from_ingredients,
)
from app.storage import (
    MEALS_PREFIX,
    StorageError,
    build_object_key,
    delete_quietly,
    get_storage,
    resolve_photo_key,
    serve_object,
)

logger = logging.getLogger(__name__)

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


def _meal_photo_key(user_id: str, stored_value: str) -> str:
    return resolve_photo_key(MEALS_PREFIX, user_id, stored_value)


def _store_meal_photo_bytes(
    current_user: User,
    meal: Meal,
    contents: bytes,
    filename: str,
    content_type: str | None = None,
) -> None:
    object_key = build_object_key(MEALS_PREFIX, current_user.id, filename)
    try:
        get_storage().put(object_key, contents, content_type=content_type)
    except StorageError as exc:
        logger.exception("Failed to store meal photo %s", object_key)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to store the photo right now. Please try again.",
        ) from exc

    if meal.photo_filename:
        delete_quietly(_meal_photo_key(current_user.id, meal.photo_filename))

    meal.photo_filename = object_key


def _finalize_meal_to_cookbook(
    db: Session,
    meal: Meal,
    current_user: User,
) -> MealResponse:
    add_meal_to_cookbook(db, meal, current_user)

    response = meal_response(meal)

    if meal.photo_filename:
        delete_quietly(_meal_photo_key(current_user.id, meal.photo_filename))

    db.delete(meal)
    db.commit()

    return response


@router.get("", response_model=list[MealResponse])
def list_meals(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[MealResponse]:
    meals = (
        db.query(Meal)
        .filter(Meal.user_id == current_user.id, Meal.photo_filename.is_(None))
        .order_by(Meal.created_at.desc())
        .all()
    )
    return [meal_response(meal) for meal in meals]


@router.post("/generate", response_model=MealResponse, status_code=status.HTTP_201_CREATED)
def generate_meal(
    payload: GenerateMealRequest = GenerateMealRequest(),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MealResponse:
    ingredients = (
        db.query(Ingredient)
        .filter(Ingredient.user_id == current_user.id)
        .order_by(Ingredient.created_at.desc())
        .all()
    )

    previous = None
    if payload.previous_meal:
        previous = PreviousMealTurn(
            name=payload.previous_meal.name,
            description=payload.previous_meal.description,
            ingredients_used=payload.previous_meal.ingredients_used,
            instructions=payload.previous_meal.instructions,
        )

    try:
        suggestion = generate_meal_from_ingredients(
            ingredients,
            previous_meal=previous,
        )
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


@router.post("/{meal_id}/complete", response_model=MealResponse)
async def complete_meal(
    meal_id: str,
    file: UploadFile | None = File(None),
    skip_photo: bool = Query(False),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MealResponse:
    meal = _get_meal_for_user(meal_id, current_user, db)

    if file is not None and file.filename:
        if file.content_type not in ALLOWED_PHOTO_TYPES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Upload a JPG, PNG, WEBP, or GIF image.",
            )
        contents = await file.read()
        if not contents:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Upload a valid image file.",
            )
        _store_meal_photo_bytes(
            current_user,
            meal,
            contents,
            file.filename,
            content_type=file.content_type,
        )
    elif not skip_photo:
        try:
            image_bytes = generate_meal_image(meal)
        except MealImageError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc),
            ) from exc
        _store_meal_photo_bytes(
            current_user,
            meal,
            image_bytes,
            f"{meal.name.replace(' ', '_').lower()[:40] or 'meal'}.png",
            content_type="image/png",
        )

    db.commit()
    db.refresh(meal)

    return _finalize_meal_to_cookbook(db, meal, current_user)


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

    return await complete_meal(
        meal_id=meal_id,
        file=file,
        current_user=current_user,
        db=db,
    )


@router.get("/{meal_id}/photo")
def get_meal_photo(
    meal_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    meal = _get_meal_for_user(meal_id, current_user, db)

    if not meal.photo_filename:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Photo not found.")

    return serve_object(
        _meal_photo_key(current_user.id, meal.photo_filename),
        not_found_detail="Photo not found.",
    )
