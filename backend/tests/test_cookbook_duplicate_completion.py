"""Integration tests for duplicate protection when completing a meal.

Completing a meal writes a cookbook entry and consumes the pantry ingredients it
used. Doing that twice for the same meal must never consume the inventory twice
or leave more than one cookbook entry behind.
"""
from unittest.mock import patch

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth_utils import hash_password
from app.config import settings
from app.models import CookbookEntry, Ingredient, Meal, User
from app.services import cookbook as cookbook_service
from app.services.cookbook import add_meal_to_cookbook

from tests.conftest import get_test_engine


@pytest.fixture(autouse=True)
def upload_dirs(tmp_path):
    """Keep photo copies inside the test's temporary directory."""
    with patch.object(settings, "meal_upload_dir", str(tmp_path / "meals")), \
         patch.object(settings, "cookbook_upload_dir", str(tmp_path / "cookbook")):
        yield tmp_path


@pytest.fixture
def pantry(test_db: Session, test_user: User):
    ingredients = [
        Ingredient(
            id="pantry-chicken",
            user_id=test_user.id,
            name="Chicken Breast",
            quantity="500",
            unit="g",
        ),
        Ingredient(
            id="pantry-rice",
            user_id=test_user.id,
            name="Rice",
            quantity="2",
            unit="cup",
        ),
    ]
    test_db.add_all(ingredients)
    test_db.commit()
    return ingredients


@pytest.fixture
def meal(test_db: Session, test_user: User):
    meal = Meal(
        id="meal-to-complete",
        user_id=test_user.id,
        name="Chicken and Rice",
        description="A simple and delicious meal",
        ingredients_used="- Chicken Breast: 200 g\n- Rice: 1 cup",
        ingredients_used_data=[
            {"name": "Chicken Breast", "quantity": 200.0, "unit": "g"},
            {"name": "Rice", "quantity": 1.0, "unit": "cup"},
        ],
        instructions="1. Cook chicken\n2. Cook rice\n3. Serve together",
    )
    test_db.add(meal)
    test_db.commit()
    return meal


def quantities_by_id(db: Session) -> dict[str, str | None]:
    db.expire_all()
    return {item.id: item.quantity for item in db.query(Ingredient).all()}


class TestDuplicateCookbookConstraint:
    """The database rejects a second cookbook entry for the same meal."""

    def test_duplicate_entry_for_same_meal_is_rejected(
        self, test_db: Session, test_user: User, meal: Meal
    ):
        test_db.add(CookbookEntry(user_id=test_user.id, meal_id=meal.id, title="First"))
        test_db.commit()

        _engine, SessionLocal = get_test_engine()
        other_session = SessionLocal()
        try:
            other_session.add(
                CookbookEntry(user_id=test_user.id, meal_id=meal.id, title="Duplicate")
            )
            with pytest.raises(IntegrityError):
                other_session.commit()
        finally:
            other_session.rollback()
            other_session.close()

        assert test_db.query(CookbookEntry).count() == 1

    def test_entries_without_a_meal_are_not_constrained(
        self, test_db: Session, test_user: User
    ):
        """Completing a meal clears meal_id, so unlinked entries may repeat."""
        test_db.add_all(
            [
                CookbookEntry(user_id=test_user.id, meal_id=None, title="Toast"),
                CookbookEntry(user_id=test_user.id, meal_id=None, title="Toast"),
            ]
        )
        test_db.commit()

        assert test_db.query(CookbookEntry).count() == 2

    def test_constraint_is_scoped_per_user(
        self, test_db: Session, test_user: User, meal: Meal
    ):
        other_user = User(
            email="other@example.com",
            name="Other User",
            password=hash_password("testpassword123"),
        )
        test_db.add(other_user)
        test_db.commit()

        test_db.add_all(
            [
                CookbookEntry(user_id=test_user.id, meal_id=meal.id, title="Mine"),
                CookbookEntry(user_id=other_user.id, meal_id=meal.id, title="Theirs"),
            ]
        )
        test_db.commit()

        assert test_db.query(CookbookEntry).count() == 2


class TestDuplicateCompletionService:
    """add_meal_to_cookbook consumes inventory at most once per meal."""

    def test_second_call_reuses_entry_without_deducting_again(
        self,
        test_db: Session,
        test_user: User,
        meal: Meal,
        pantry: list[Ingredient],
    ):
        first_entry = add_meal_to_cookbook(test_db, meal, test_user)
        after_first = quantities_by_id(test_db)
        assert after_first == {"pantry-chicken": "300", "pantry-rice": "1"}

        second_entry = add_meal_to_cookbook(test_db, meal, test_user)

        assert second_entry.id == first_entry.id
        assert test_db.query(CookbookEntry).count() == 1
        assert quantities_by_id(test_db) == after_first

    def test_losing_a_completion_race_does_not_deduct_again(
        self,
        test_db: Session,
        test_user: User,
        meal: Meal,
        pantry: list[Ingredient],
    ):
        """A request that reads the cookbook before a concurrent request commits
        still ends up with a single entry and a single deduction."""
        first_entry = add_meal_to_cookbook(test_db, meal, test_user)
        after_first = quantities_by_id(test_db)

        real_find = cookbook_service._find_entry_for_meal
        lookups = []

        def stale_first_lookup(db, meal_, user):
            lookups.append(meal_.id)
            if len(lookups) == 1:
                return None
            return real_find(db, meal_, user)

        with patch.object(
            cookbook_service, "_find_entry_for_meal", side_effect=stale_first_lookup
        ):
            second_entry = add_meal_to_cookbook(test_db, meal, test_user)

        assert len(lookups) == 2  # the insert failed and the entry was re-read
        assert second_entry.id == first_entry.id
        assert test_db.query(CookbookEntry).count() == 1
        assert quantities_by_id(test_db) == after_first


class TestDuplicateCompletionEndpoint:
    """POST /api/meals/{meal_id}/complete cannot be applied twice."""

    def test_completing_the_same_meal_twice_is_rejected(
        self,
        client,
        test_db: Session,
        test_user: User,
        auth_headers,
        meal: Meal,
        pantry: list[Ingredient],
    ):
        first = client.post(
            f"/api/meals/{meal.id}/complete",
            params={"skip_photo": "true"},
            headers=auth_headers,
        )
        assert first.status_code == 200

        entries = client.get("/api/cookbook", headers=auth_headers).json()
        assert len(entries) == 1
        assert entries[0]["title"] == "Chicken and Rice"

        after_first = quantities_by_id(test_db)
        assert after_first == {"pantry-chicken": "300", "pantry-rice": "1"}

        second = client.post(
            f"/api/meals/{meal.id}/complete",
            params={"skip_photo": "true"},
            headers=auth_headers,
        )
        assert second.status_code == 404

        assert len(client.get("/api/cookbook", headers=auth_headers).json()) == 1
        assert quantities_by_id(test_db) == after_first
