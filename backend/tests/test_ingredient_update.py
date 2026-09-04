"""Tests for the inventory quantity/unit update endpoint (PATCH /api/ingredients/{id})."""

from unittest.mock import patch

import pytest

from app.models import Ingredient, User
from app.services.ingredient_deduction import _convert_amount, _format_quantity


# ---------------------------------------------------------------------------
# API-level tests (via FastAPI TestClient)
# ---------------------------------------------------------------------------

class TestUpdateIngredientAPI:
    """PATCH /api/ingredients/{id} endpoint tests."""

    @pytest.fixture(autouse=True)
    def setup(self, client, test_user, auth_headers, test_db):
        self.client = client
        self.user = test_user
        self.headers = auth_headers
        self.db = test_db
        # Avoid live AI unit checks for happy-path / validation tests.
        with patch(
            "app.routers.ingredients.check_ingredient_unit",
            return_value=None,
        ):
            yield

    def _create_ingredient(self, **overrides):
        defaults = dict(
            id="ing-api-1",
            user_id=self.user.id,
            name="Olive Oil",
            quantity="500",
            unit="ml",
        )
        defaults.update(overrides)
        item = Ingredient(**defaults)
        self.db.add(item)
        self.db.commit()
        return item

    def test_update_quantity_only(self):
        self._create_ingredient()
        resp = self.client.patch(
            "/api/ingredients/ing-api-1",
            json={"quantity": "250", "unit": "ml"},
            headers=self.headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["quantity"] == "250"
        assert data["unit"] == "ml"

    def test_update_unit_change(self):
        self._create_ingredient()
        resp = self.client.patch(
            "/api/ingredients/ing-api-1",
            json={"quantity": "0.5", "unit": "l"},
            headers=self.headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["quantity"] == "0.5"
        assert data["unit"] == "l"

    def test_update_fractional_quantity(self):
        self._create_ingredient()
        resp = self.client.patch(
            "/api/ingredients/ing-api-1",
            json={"quantity": "1.75", "unit": "l"},
            headers=self.headers,
        )
        assert resp.status_code == 200
        assert resp.json()["quantity"] == "1.75"

    def test_reject_zero_quantity(self):
        self._create_ingredient()
        resp = self.client.patch(
            "/api/ingredients/ing-api-1",
            json={"quantity": "0", "unit": "ml"},
            headers=self.headers,
        )
        assert resp.status_code == 400
        assert "greater than zero" in resp.json()["detail"].lower()

    def test_reject_negative_quantity(self):
        self._create_ingredient()
        resp = self.client.patch(
            "/api/ingredients/ing-api-1",
            json={"quantity": "-5", "unit": "ml"},
            headers=self.headers,
        )
        assert resp.status_code == 400
        assert "negative" in resp.json()["detail"].lower()

    def test_reject_non_numeric_quantity(self):
        self._create_ingredient()
        resp = self.client.patch(
            "/api/ingredients/ing-api-1",
            json={"quantity": "abc", "unit": "ml"},
            headers=self.headers,
        )
        assert resp.status_code == 400

    def test_reject_slash_fraction_quantity(self):
        self._create_ingredient()
        resp = self.client.patch(
            "/api/ingredients/ing-api-1",
            json={"quantity": "1/2", "unit": "ml"},
            headers=self.headers,
        )
        assert resp.status_code == 400
        assert "must be a number" in resp.json()["detail"].lower()

        self.db.expire_all()
        item = self.db.query(Ingredient).filter(Ingredient.id == "ing-api-1").first()
        assert item.quantity == "500"
        assert item.unit == "ml"

    def test_reject_negative_slash_fraction_quantity(self):
        self._create_ingredient()
        resp = self.client.patch(
            "/api/ingredients/ing-api-1",
            json={"quantity": "-1/2", "unit": "ml"},
            headers=self.headers,
        )
        assert resp.status_code == 400

        self.db.expire_all()
        item = self.db.query(Ingredient).filter(Ingredient.id == "ing-api-1").first()
        assert item.quantity == "500"
        assert item.unit == "ml"

    def test_reject_invalid_unit(self):
        self._create_ingredient()
        resp = self.client.patch(
            "/api/ingredients/ing-api-1",
            json={"quantity": "500", "unit": "bushel"},
            headers=self.headers,
        )
        assert resp.status_code == 400
        assert "unit" in resp.json()["detail"].lower()

    def test_reject_decimal_for_package_unit(self):
        self._create_ingredient(id="ing-pkg", quantity="2", unit="can")
        resp = self.client.patch(
            "/api/ingredients/ing-pkg",
            json={"quantity": "1.5", "unit": "can"},
            headers=self.headers,
        )
        assert resp.status_code == 400
        assert "whole" in resp.json()["detail"].lower()

    def test_reject_implausible_unit_match(self):
        self._create_ingredient(
            id="ing-bananas",
            name="Bananas",
            quantity="6",
            unit="each",
        )
        warning = (
            "Bananas are sold by count or weight, so use pieces (each), kg, or lb "
            "instead of liters."
        )
        with patch(
            "app.routers.ingredients.check_ingredient_unit",
            return_value=warning,
        ):
            resp = self.client.patch(
                "/api/ingredients/ing-bananas",
                json={"quantity": "1", "unit": "l"},
                headers=self.headers,
            )

        assert resp.status_code == 400
        assert resp.json()["detail"] == warning

        self.db.expire_all()
        item = self.db.query(Ingredient).filter(Ingredient.id == "ing-bananas").first()
        assert item.quantity == "6"
        assert item.unit == "each"

    def test_depleted_edit_deletes_ingredient(self):
        """Editing down to effectively no servings removes the pantry item."""
        self._create_ingredient(
            id="ing-tuna",
            name="365 Light Chunk Tuna",
            quantity="5",
            unit="oz",
            servings_per_container=0.5,  # 1 serving = 2 oz
            original_quantity="5",
        )
        resp = self.client.patch(
            "/api/ingredients/ing-tuna",
            json={"quantity": "0.01", "unit": "oz"},
            headers=self.headers,
        )
        assert resp.status_code == 204
        self.db.expire_all()
        assert (
            self.db.query(Ingredient).filter(Ingredient.id == "ing-tuna").first()
            is None
        )

    def test_edit_keeps_ingredient_when_enough_servings_remain(self):
        self._create_ingredient(
            id="ing-tuna-keep",
            name="365 Light Chunk Tuna",
            quantity="5",
            unit="oz",
            servings_per_container=0.5,
            original_quantity="5",
        )
        resp = self.client.patch(
            "/api/ingredients/ing-tuna-keep",
            json={"quantity": "2", "unit": "oz"},
            headers=self.headers,
        )
        assert resp.status_code == 200
        assert resp.json()["quantity"] == "2"
        self.db.expire_all()
        assert (
            self.db.query(Ingredient).filter(Ingredient.id == "ing-tuna-keep").first()
            is not None
        )

    def test_not_found_returns_404(self):
        resp = self.client.patch(
            "/api/ingredients/nonexistent",
            json={"quantity": "5", "unit": "g"},
            headers=self.headers,
        )
        assert resp.status_code == 404

    def test_cannot_update_other_users_ingredient(self):
        other = User(id="user-other", email="other@test.com", name="Other")
        self.db.add(other)
        self.db.commit()
        item = Ingredient(
            id="ing-other",
            user_id=other.id,
            name="Secret Sauce",
            quantity="1",
            unit="bottle",
        )
        self.db.add(item)
        self.db.commit()

        resp = self.client.patch(
            "/api/ingredients/ing-other",
            json={"quantity": "5", "unit": "bottle"},
            headers=self.headers,
        )
        assert resp.status_code == 404

    def test_existing_value_not_corrupted_on_invalid_input(self):
        self._create_ingredient()
        resp = self.client.patch(
            "/api/ingredients/ing-api-1",
            json={"quantity": "-1", "unit": "ml"},
            headers=self.headers,
        )
        assert resp.status_code == 400

        self.db.expire_all()
        item = self.db.query(Ingredient).filter(Ingredient.id == "ing-api-1").first()
        assert item.quantity == "500"
        assert item.unit == "ml"

    def test_persisted_immediately(self):
        self._create_ingredient()
        resp = self.client.patch(
            "/api/ingredients/ing-api-1",
            json={"quantity": "100", "unit": "ml"},
            headers=self.headers,
        )
        assert resp.status_code == 200

        self.db.expire_all()
        item = self.db.query(Ingredient).filter(Ingredient.id == "ing-api-1").first()
        assert item.quantity == "100"
        assert item.unit == "ml"

    def test_comma_thousand_separator(self):
        self._create_ingredient()
        resp = self.client.patch(
            "/api/ingredients/ing-api-1",
            json={"quantity": "1,000", "unit": "ml"},
            headers=self.headers,
        )
        assert resp.status_code == 200
        assert resp.json()["quantity"] == "1000"

    def test_weight_to_weight_accepted(self):
        self._create_ingredient(id="ing-w", quantity="16", unit="oz")
        resp = self.client.patch(
            "/api/ingredients/ing-w",
            json={"quantity": "1", "unit": "lb"},
            headers=self.headers,
        )
        assert resp.status_code == 200
        assert resp.json()["unit"] == "lb"

    def test_volume_to_volume_accepted(self):
        self._create_ingredient()
        resp = self.client.patch(
            "/api/ingredients/ing-api-1",
            json={"quantity": "2", "unit": "cup"},
            headers=self.headers,
        )
        assert resp.status_code == 200
        assert resp.json()["unit"] == "cup"

    def test_response_includes_all_fields(self):
        self._create_ingredient(
            calories=120.0,
            serving_size="1 tbsp",
            servings_per_container=33.0,
        )
        resp = self.client.patch(
            "/api/ingredients/ing-api-1",
            json={"quantity": "250", "unit": "ml"},
            headers=self.headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Olive Oil"
        assert data["calories"] == 120.0
        assert data["quantity"] == "250"
        assert data["unit"] == "ml"


# ---------------------------------------------------------------------------
# Unit conversion service-level tests
# ---------------------------------------------------------------------------

class TestConvertAmountForEdit:
    """Verify _convert_amount works for inventory edit scenarios."""

    def test_weight_conversion_oz_to_g(self):
        result = _convert_amount(16, "oz", "g")
        assert result is not None
        assert abs(result - 453.592) < 0.01

    def test_weight_conversion_lb_to_kg(self):
        result = _convert_amount(1, "lb", "kg")
        assert result is not None
        assert abs(result - 0.4536) < 0.01

    def test_volume_conversion_cup_to_ml(self):
        result = _convert_amount(1, "cup", "ml")
        assert result is not None
        assert abs(result - 236.588) < 0.01

    def test_volume_conversion_tbsp_to_tsp(self):
        result = _convert_amount(1, "tbsp", "tsp")
        assert result is not None
        assert abs(result - 3.0) < 0.1

    def test_volume_conversion_gallon_to_quart(self):
        result = _convert_amount(1, "gallon", "quart")
        assert result is not None
        assert abs(result - 4.0) < 0.1

    def test_cross_family_returns_none(self):
        assert _convert_amount(1, "g", "cup") is None
        assert _convert_amount(1, "ml", "lb") is None

    def test_package_to_weight_returns_none(self):
        assert _convert_amount(1, "each", "g") is None
        assert _convert_amount(1, "can", "oz") is None

    def test_same_unit_identity(self):
        assert _convert_amount(5, "g", "g") == 5
        assert _convert_amount(2.5, "cup", "cup") == 2.5

    def test_fractional_conversion(self):
        result = _convert_amount(0.5, "lb", "oz")
        assert result is not None
        assert abs(result - 8.0) < 0.1


class TestFormatQuantity:
    """Verify _format_quantity produces clean display strings."""

    def test_integer_result(self):
        assert _format_quantity(5.0) == "5"
        assert _format_quantity(100.0) == "100"

    def test_decimal_result(self):
        result = _format_quantity(1.75)
        assert "1.75" in result

    def test_near_integer_rounds(self):
        assert _format_quantity(2.0000001) == "2"

    def test_small_decimal(self):
        result = _format_quantity(0.5)
        assert result == "0.5"
