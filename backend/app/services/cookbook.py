import logging
import re

from sqlalchemy.orm import Session

from app.models import CookbookEntry, Meal, User
from app.services.ingredient_deduction import deduct_meal_ingredients
from app.storage import (
    COOKBOOK_PREFIX,
    MEALS_PREFIX,
    StorageError,
    StorageObjectNotFound,
    build_object_key,
    delete_quietly,
    get_storage,
    resolve_photo_key,
    safe_filename,
)

logger = logging.getLogger(__name__)

_UUID_PREFIX = re.compile(r"^[0-9a-f]{32}_(?P<name>.+)$")


def cookbook_photo_key(user_id: str, stored_value: str) -> str:
    return resolve_photo_key(COOKBOOK_PREFIX, user_id, stored_value)


def _meal_photo_key(user_id: str, stored_value: str) -> str:
    return resolve_photo_key(MEALS_PREFIX, user_id, stored_value)


def _remove_cookbook_photo(user_id: str, stored_value: str | None) -> None:
    if not stored_value:
        return
    delete_quietly(cookbook_photo_key(user_id, stored_value))


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


def _copy_meal_photo_to_cookbook(user: User, meal: Meal) -> str | None:
    """Copy the meal photo under ``cookbook/`` and return the new object key."""
    if not meal.photo_filename:
        return None

    source_key = _meal_photo_key(user.id, meal.photo_filename)
    basename = source_key.rsplit("/", 1)[-1]
    match = _UUID_PREFIX.match(basename)
    original_name = safe_filename(match.group("name") if match else basename)
    destination_key = build_object_key(COOKBOOK_PREFIX, user.id, original_name)

    storage = get_storage()
    try:
        storage.copy(source_key, destination_key)
    except StorageObjectNotFound:
        # Photo went missing between upload and cookbook save; keep the entry
        # without a photo rather than failing the whole flow.
        return None
    except StorageError:
        logger.exception("Failed to copy meal photo %s to %s", source_key, destination_key)
        raise
    return destination_key


def add_meal_to_cookbook(db: Session, meal: Meal, user: User) -> CookbookEntry:
    entry = (
        db.query(CookbookEntry)
        .filter(CookbookEntry.user_id == user.id, CookbookEntry.meal_id == meal.id)
        .first()
    )

    photo_filename = _copy_meal_photo_to_cookbook(user, meal)

    is_new_entry = entry is None

    try:
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
    except Exception:
        db.rollback()
        if is_new_entry and photo_filename:
            _remove_cookbook_photo(user.id, photo_filename)
        raise
