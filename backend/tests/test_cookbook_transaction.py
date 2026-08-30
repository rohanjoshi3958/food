"""Integration tests for atomic cookbook and inventory transactions."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from unittest.mock import patch

from app.database import Base
from app.models import CookbookEntry, Ingredient, Meal, User
from app.services.cookbook import add_meal_to_cookbook


@pytest.fixture
def db_session():
    """Create an in-memory SQLite database for testing."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture
def test_user(db_session: Session):
    """Create a test user."""
    user = User(
        id="test-user-id",
        email="test@example.com",
        name="Test User",
    )
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture
def test_ingredients(db_session: Session, test_user: User):
    """Create test ingredients in the pantry."""
    ingredients = [
        Ingredient(
            id="ingredient-1",
            user_id=test_user.id,
            name="Chicken Breast",
            quantity="500",
            unit="g",
        ),
        Ingredient(
            id="ingredient-2",
            user_id=test_user.id,
            name="Rice",
            quantity="2",
            unit="cup",
        ),
        Ingredient(
            id="ingredient-3",
            user_id=test_user.id,
            name="Olive Oil",
            quantity="500",
            unit="ml",
        ),
    ]
    for ingredient in ingredients:
        db_session.add(ingredient)
    db_session.commit()
    return ingredients


@pytest.fixture
def test_meal(db_session: Session, test_user: User):
    """Create a test meal."""
    meal = Meal(
        id="meal-1",
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
    db_session.add(meal)
    db_session.commit()
    return meal


class TestAtomicCookbookTransaction:
    """Test that cookbook entry and inventory deduction are atomic."""

    def test_successful_cookbook_creation_with_inventory_deduction(
        self, db_session: Session, test_user: User, test_meal: Meal, test_ingredients: list[Ingredient]
    ):
        """Test successful atomic transaction of cookbook entry and inventory deduction."""
        # Get initial ingredient quantities
        initial_chicken = db_session.query(Ingredient).filter_by(id="ingredient-1").first()
        initial_rice = db_session.query(Ingredient).filter_by(id="ingredient-2").first()
        assert initial_chicken.quantity == "500"
        assert initial_rice.quantity == "2"

        # Add meal to cookbook
        entry = add_meal_to_cookbook(db_session, test_meal, test_user)

        # Verify cookbook entry was created
        assert entry is not None
        assert entry.title == "Chicken and Rice"
        assert entry.user_id == test_user.id

        # Verify the entry is in the database
        db_entry = db_session.query(CookbookEntry).filter_by(id=entry.id).first()
        assert db_entry is not None
        assert db_entry.title == "Chicken and Rice"

        # Verify inventory was deducted
        updated_chicken = db_session.query(Ingredient).filter_by(id="ingredient-1").first()
        updated_rice = db_session.query(Ingredient).filter_by(id="ingredient-2").first()

        # Chicken: 500g - 200g = 300g
        assert updated_chicken is not None
        assert updated_chicken.quantity == "300"

        # Rice: 2 cups - 1 cup = 1 cup
        assert updated_rice is not None
        assert updated_rice.quantity == "1"

        # Verify olive oil was not touched
        olive_oil = db_session.query(Ingredient).filter_by(id="ingredient-3").first()
        assert olive_oil.quantity == "500"

    def test_rollback_on_commit_failure(
        self, db_session: Session, test_user: User, test_meal: Meal, test_ingredients: list[Ingredient]
    ):
        """Test that both cookbook and inventory changes rollback on commit failure."""
        # Get initial ingredient quantities
        initial_chicken_qty = db_session.query(Ingredient).filter_by(id="ingredient-1").first().quantity
        initial_rice_qty = db_session.query(Ingredient).filter_by(id="ingredient-2").first().quantity
        initial_cookbook_count = db_session.query(CookbookEntry).count()

        # Mock db.commit to raise an exception
        with patch.object(db_session, "commit", side_effect=Exception("Database commit failed")):
            with pytest.raises(Exception, match="Database commit failed"):
                add_meal_to_cookbook(db_session, test_meal, test_user)

        # Verify rollback occurred - no cookbook entry was created
        final_cookbook_count = db_session.query(CookbookEntry).count()
        assert final_cookbook_count == initial_cookbook_count

        # Verify rollback occurred - inventory was not deducted
        final_chicken = db_session.query(Ingredient).filter_by(id="ingredient-1").first()
        final_rice = db_session.query(Ingredient).filter_by(id="ingredient-2").first()
        assert final_chicken.quantity == initial_chicken_qty
        assert final_rice.quantity == initial_rice_qty

    def test_rollback_on_inventory_deduction_error(
        self, db_session: Session, test_user: User, test_meal: Meal, test_ingredients: list[Ingredient]
    ):
        """Test that cookbook creation rolls back when inventory deduction fails."""
        initial_cookbook_count = db_session.query(CookbookEntry).count()

        # Create a meal with valid data
        valid_meal = Meal(
            id="meal-invalid",
            user_id=test_user.id,
            name="Test Meal",
            description="This will fail during deduction",
            ingredients_used="- Chicken Breast: 100 g",
            ingredients_used_data=[
                {"name": "Chicken Breast", "quantity": 100.0, "unit": "g"},
            ],
            instructions="Cook chicken",
        )
        db_session.add(valid_meal)
        db_session.commit()

        # Mock the _format_quantity function to raise an exception during deduction
        from app.services import ingredient_deduction

        def failing_format_quantity(value: float) -> str:
            raise ValueError("Inventory deduction failed")

        with patch.object(ingredient_deduction, "_format_quantity", side_effect=failing_format_quantity):
            with pytest.raises(ValueError, match="Inventory deduction failed"):
                add_meal_to_cookbook(db_session, valid_meal, test_user)

        # Verify no cookbook entry was created
        final_cookbook_count = db_session.query(CookbookEntry).count()
        assert final_cookbook_count == initial_cookbook_count

        # Verify inventory was not modified
        chicken = db_session.query(Ingredient).filter_by(id="ingredient-1").first()
        rice = db_session.query(Ingredient).filter_by(id="ingredient-2").first()
        assert chicken.quantity == "500"
        assert rice.quantity == "2"

    def test_rollback_on_cookbook_entry_creation_error(
        self, db_session: Session, test_user: User, test_meal: Meal, test_ingredients: list[Ingredient]
    ):
        """Test that transaction rolls back when cookbook entry creation fails."""
        initial_chicken_qty = db_session.query(Ingredient).filter_by(id="ingredient-1").first().quantity
        initial_rice_qty = db_session.query(Ingredient).filter_by(id="ingredient-2").first().quantity
        initial_cookbook_count = db_session.query(CookbookEntry).count()

        # Mock db.add to fail when adding a CookbookEntry
        original_add = db_session.add

        def failing_add(instance):
            if isinstance(instance, CookbookEntry):
                raise ValueError("Failed to create cookbook entry")
            original_add(instance)

        with patch.object(db_session, "add", side_effect=failing_add):
            with pytest.raises(ValueError, match="Failed to create cookbook entry"):
                add_meal_to_cookbook(db_session, test_meal, test_user)

        # Verify no cookbook entry was created
        final_cookbook_count = db_session.query(CookbookEntry).count()
        assert final_cookbook_count == initial_cookbook_count

        # Verify inventory was not deducted
        chicken = db_session.query(Ingredient).filter_by(id="ingredient-1").first()
        rice = db_session.query(Ingredient).filter_by(id="ingredient-2").first()
        assert chicken.quantity == initial_chicken_qty
        assert rice.quantity == initial_rice_qty

    def test_update_existing_cookbook_entry_does_not_deduct_inventory(
        self, db_session: Session, test_user: User, test_meal: Meal, test_ingredients: list[Ingredient]
    ):
        """Test that updating an existing cookbook entry doesn't deduct inventory again."""
        # First, add the meal to cookbook
        entry = add_meal_to_cookbook(db_session, test_meal, test_user)
        assert entry is not None

        # Get ingredient quantities after first addition
        chicken_after_first = db_session.query(Ingredient).filter_by(id="ingredient-1").first()
        rice_after_first = db_session.query(Ingredient).filter_by(id="ingredient-2").first()
        chicken_qty_1 = chicken_after_first.quantity
        rice_qty_1 = rice_after_first.quantity

        # Update the meal
        test_meal.name = "Updated Chicken and Rice"
        test_meal.description = "Updated description"
        db_session.commit()

        # Add the same meal again (should update, not create new)
        updated_entry = add_meal_to_cookbook(db_session, test_meal, test_user)

        # Verify it's the same entry
        assert updated_entry.id == entry.id
        assert updated_entry.title == "Updated Chicken and Rice"

        # Verify inventory was NOT deducted again
        chicken_after_second = db_session.query(Ingredient).filter_by(id="ingredient-1").first()
        rice_after_second = db_session.query(Ingredient).filter_by(id="ingredient-2").first()
        assert chicken_after_second.quantity == chicken_qty_1
        assert rice_after_second.quantity == rice_qty_1

    def test_full_inventory_depletion(
        self, db_session: Session, test_user: User, test_ingredients: list[Ingredient]
    ):
        """Test that ingredients are removed when fully depleted."""
        # Create a meal that uses all the rice
        meal = Meal(
            id="meal-deplete",
            user_id=test_user.id,
            name="Rice Bowl",
            description="Uses all rice",
            ingredients_used="- Rice: 2 cup",
            ingredients_used_data=[
                {"name": "Rice", "quantity": 2.0, "unit": "cup"},
            ],
            instructions="Cook all the rice",
        )
        db_session.add(meal)
        db_session.commit()

        # Add meal to cookbook
        entry = add_meal_to_cookbook(db_session, meal, test_user)
        assert entry is not None

        # Verify rice ingredient was removed (fully depleted)
        rice = db_session.query(Ingredient).filter_by(id="ingredient-2").first()
        assert rice is None

        # Verify other ingredients are still there
        chicken = db_session.query(Ingredient).filter_by(id="ingredient-1").first()
        olive_oil = db_session.query(Ingredient).filter_by(id="ingredient-3").first()
        assert chicken is not None
        assert olive_oil is not None


class TestCookbookTransactionIsolation:
    """Test transaction isolation for concurrent operations."""

    def test_multiple_meals_in_sequence(
        self, db_session: Session, test_user: User, test_ingredients: list[Ingredient]
    ):
        """Test that multiple meals can be added sequentially with proper inventory tracking."""
        # Meal 1: Uses some chicken and rice
        meal1 = Meal(
            id="meal-seq-1",
            user_id=test_user.id,
            name="Meal 1",
            description="First meal",
            ingredients_used="- Chicken Breast: 150 g\n- Rice: 0.5 cup",
            ingredients_used_data=[
                {"name": "Chicken Breast", "quantity": 150.0, "unit": "g"},
                {"name": "Rice", "quantity": 0.5, "unit": "cup"},
            ],
            instructions="Cook meal 1",
        )
        db_session.add(meal1)
        db_session.commit()

        entry1 = add_meal_to_cookbook(db_session, meal1, test_user)
        assert entry1 is not None

        # Check quantities after meal 1
        chicken = db_session.query(Ingredient).filter_by(id="ingredient-1").first()
        rice = db_session.query(Ingredient).filter_by(id="ingredient-2").first()
        assert chicken.quantity == "350"  # 500 - 150
        assert rice.quantity == "1.5"  # 2 - 0.5

        # Meal 2: Uses more chicken and rice
        meal2 = Meal(
            id="meal-seq-2",
            user_id=test_user.id,
            name="Meal 2",
            description="Second meal",
            ingredients_used="- Chicken Breast: 200 g\n- Rice: 1 cup",
            ingredients_used_data=[
                {"name": "Chicken Breast", "quantity": 200.0, "unit": "g"},
                {"name": "Rice", "quantity": 1.0, "unit": "cup"},
            ],
            instructions="Cook meal 2",
        )
        db_session.add(meal2)
        db_session.commit()

        entry2 = add_meal_to_cookbook(db_session, meal2, test_user)
        assert entry2 is not None

        # Check quantities after meal 2
        chicken = db_session.query(Ingredient).filter_by(id="ingredient-1").first()
        rice = db_session.query(Ingredient).filter_by(id="ingredient-2").first()
        assert chicken.quantity == "150"  # 350 - 200
        assert rice.quantity == "0.5"  # 1.5 - 1

        # Verify both cookbook entries exist
        entries = db_session.query(CookbookEntry).filter_by(user_id=test_user.id).all()
        assert len(entries) == 2
