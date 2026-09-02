import shutil
import uuid
from pathlib import Path

from sqlalchemy.exc import IntegrityError
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


def remove_cookbook_entry(db: Session, entry: CookbookEntry, user: User) -> None:
    _remove_cookbook_photo(user.id, entry.photo_filename)
    db.delete(entry)
    db.commit()


def _copy_meal_macros(meal: Meal) -> dict[str, float | None]:
    return {
        "calories": meal.calories,
        "protein_g": meal.protein_g,
        "carbs_g": meal.carbs_g,
        "fat_g": meal.fat_g,
        "fiber_g": meal.fiber_g,
        "sodium_mg": meal.sodium_mg,
    }


def _find_entry_for_meal(db: Session, meal: Meal, user: User) -> CookbookEntry | None:
    return (
        db.query(CookbookEntry)
        .filter(CookbookEntry.user_id == user.id, CookbookEntry.meal_id == meal.id)
        .order_by(CookbookEntry.created_at)
        .first()
    )


def _copy_meal_photo(meal: Meal, user: User, upload_root: Path) -> str | None:
    if not meal.photo_filename:
        return None

    source = _meal_photo_path(user.id, meal.photo_filename)
    if not source.exists():
        return None

    photo_filename = f"{uuid.uuid4().hex}_{Path(meal.photo_filename).name}"
    shutil.copy2(source, upload_root / photo_filename)
    return photo_filename


def _apply_meal_to_entry(
    entry: CookbookEntry,
    meal: Meal,
    user: User,
    photo_filename: str | None,
) -> None:
    if entry.photo_filename and entry.photo_filename != photo_filename:
        _remove_cookbook_photo(user.id, entry.photo_filename)

    entry.title = meal.name
    entry.description = meal.description
    entry.ingredients = meal.ingredients_used
    entry.instructions = meal.instructions
    entry.photo_filename = photo_filename
    for field, value in _copy_meal_macros(meal).items():
        setattr(entry, field, value)


def add_meal_to_cookbook(db: Session, meal: Meal, user: User) -> CookbookEntry:
    upload_root = Path(settings.cookbook_upload_dir) / user.id
    upload_root.mkdir(parents=True, exist_ok=True)

    entry = _find_entry_for_meal(db, meal, user)
    photo_filename = _copy_meal_photo(meal, user, upload_root)

    try:
        if entry is None:
            try:
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
                deduct_meal_ingredients(db, user, meal)
                db.commit()
            except IntegrityError:
                # Another completion of this meal committed first. Reuse its entry
                # instead of consuming the inventory a second time.
                db.rollback()
                entry = _find_entry_for_meal(db, meal, user)
                if entry is None:
                    raise
                _apply_meal_to_entry(entry, meal, user, photo_filename)
                db.commit()
        else:
            _apply_meal_to_entry(entry, meal, user, photo_filename)
            db.commit()

        db.refresh(entry)
        return entry
    except Exception:
        db.rollback()
        if photo_filename:
            _remove_cookbook_photo(user.id, photo_filename)
        raise
