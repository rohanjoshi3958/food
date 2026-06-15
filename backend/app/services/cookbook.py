import shutil
import uuid
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import settings
from app.models import CookbookEntry, Meal, User
from app.services.ingredient_deduction import deduct_meal_ingredients


def _cookbook_photo_path(user_id: str, filename: str) -> Path:
    return Path(settings.cookbook_upload_dir) / user_id / filename


def _meal_photo_path(user_id: str, filename: str) -> Path:
    return Path(settings.meal_upload_dir) / user_id / filename


def _remove_cookbook_photo(user_id: str, filename: str | None) -> None:
    if not filename:
        return

    photo_path = _cookbook_photo_path(user_id, filename)
    if photo_path.exists():
        photo_path.unlink()


def _copy_meal_macros(meal: Meal) -> dict[str, float | None]:
    return {
        "calories": meal.calories,
        "protein_g": meal.protein_g,
        "carbs_g": meal.carbs_g,
        "fat_g": meal.fat_g,
        "fiber_g": meal.fiber_g,
        "sodium_mg": meal.sodium_mg,
    }


def add_meal_to_cookbook(db: Session, meal: Meal, user: User) -> CookbookEntry:
    upload_root = Path(settings.cookbook_upload_dir) / user.id
    upload_root.mkdir(parents=True, exist_ok=True)

    entry = (
        db.query(CookbookEntry)
        .filter(CookbookEntry.user_id == user.id, CookbookEntry.meal_id == meal.id)
        .first()
    )

    photo_filename = None
    if meal.photo_filename:
        source = _meal_photo_path(user.id, meal.photo_filename)
        if source.exists():
            photo_filename = f"{uuid.uuid4().hex}_{Path(meal.photo_filename).name}"
            shutil.copy2(source, upload_root / photo_filename)

    is_new_entry = entry is None

    if is_new_entry:
        entry = CookbookEntry(
            user_id=user.id,
            meal_id=meal.id,
            title=meal.name,
            description=meal.description,
            ingredients=meal.ingredients_used,
            instructions=meal.instructions,
            photo_filename=photo_filename,
            **_copy_meal_macros(meal),
        )
        db.add(entry)
    else:
        if entry.photo_filename and entry.photo_filename != photo_filename:
            _remove_cookbook_photo(user.id, entry.photo_filename)

        entry.title = meal.name
        entry.description = meal.description
        entry.ingredients = meal.ingredients_used
        entry.instructions = meal.instructions
        entry.photo_filename = photo_filename
        for field, value in _copy_meal_macros(meal).items():
            setattr(entry, field, value)

    if is_new_entry:
        deduct_meal_ingredients(db, user, meal)

    db.commit()
    db.refresh(entry)
    return entry
