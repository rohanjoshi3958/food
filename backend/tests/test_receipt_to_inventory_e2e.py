"""
End-to-end tests for receipt → inventory flow.

Tests the complete flow:
    receipt
     ↓
    Claude-shaped response
     ↓
    review
     ↓
    confirm
     ↓
    inventory
"""
import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Ingredient, Receipt, User

from tests.conftest import create_mock_anthropic_response


class TestReceiptToInventoryE2E:
    """End-to-end tests for the receipt scanning and inventory flow."""

    @patch("app.services.receipt_analyzer.anthropic.Anthropic")
    @patch("app.services.receipt_analyzer.settings.anthropic_api_key", "test-api-key")
    @patch("app.config.settings.anthropic_api_key", "test-api-key")
    def test_complete_receipt_flow(
        self,
        mock_anthropic_class,
        client,
        test_db: Session,
        test_user: User,
        auth_headers,
        mock_receipt_image,
        sample_receipt_response,
        sample_nutrition_estimates,
        tmp_path,
    ):
        """Test the complete flow from receipt upload to inventory confirmation."""
        
        # Override the upload directory setting
        with patch.object(settings, "upload_dir", str(tmp_path / "uploads")):
            
            # Step 1: Mock Claude API responses
            mock_client = Mock()
            mock_anthropic_class.return_value = mock_client
            
            # Mock receipt analysis response
            receipt_response_json = json.dumps(sample_receipt_response)
            mock_client.messages.create.return_value = create_mock_anthropic_response(
                receipt_response_json
            )
            
            # Mock nutrition estimation responses (called in parallel for each item)
            nutrition_responses = [
                create_mock_anthropic_response(json.dumps(sample_nutrition_estimates["Organic Bananas"])),
                create_mock_anthropic_response(json.dumps(sample_nutrition_estimates["Almond Butter"])),
                create_mock_anthropic_response(json.dumps(sample_nutrition_estimates["Greek Yogurt"])),
            ]
            mock_client.messages.create.side_effect = [
                create_mock_anthropic_response(receipt_response_json),
                *nutrition_responses
            ]
            
            # Step 2: Upload receipt
            with open(mock_receipt_image, "rb") as f:
                response = client.post(
                    "/api/receipts/upload",
                    files={"file": ("receipt.jpg", f, "image/jpeg")},
                    data={"manual_items": "[]"},
                    headers=auth_headers,
                )
            
            assert response.status_code == 201
            receipt_data = response.json()
            
            # Verify receipt was created with correct status
            assert receipt_data["analysis_status"] == "pending_review"
            assert receipt_data["store_name"] == "Whole Foods Market"
            assert len(receipt_data["draft_items"]) == 3
            
            receipt_id = receipt_data["id"]
            
            # Verify draft items contain expected data
            draft_items = {item["ingredient_name"]: item for item in receipt_data["draft_items"]}
            
            assert "Organic Bananas" in draft_items
            bananas = draft_items["Organic Bananas"]
            assert bananas["quantity"] == "2.5"
            assert bananas["unit"] == "lb"
            assert bananas["calories"] == 105
            assert bananas["protein_g"] == 1.3
            
            assert "Almond Butter" in draft_items
            almond_butter = draft_items["Almond Butter"]
            assert almond_butter["quantity"] == "1"
            assert almond_butter["unit"] == "each"
            assert almond_butter["servings_per_container"] == 15
            
            # Verify receipt exists in database
            db_receipt = test_db.query(Receipt).filter(Receipt.id == receipt_id).first()
            assert db_receipt is not None
            assert db_receipt.analysis_status == "pending_review"
            assert db_receipt.user_id == test_user.id
            
            # Step 3: Review/Edit draft items (optional step in real flow)
            # User might edit quantities, names, etc.
            edited_items = receipt_data["draft_items"].copy()
            # Edit banana quantity as an example
            for item in edited_items:
                if item["ingredient_name"] == "Organic Bananas":
                    item["quantity"] = "3.0"
            
            response = client.patch(
                f"/api/receipts/{receipt_id}/draft",
                json={"items": edited_items},
                headers=auth_headers,
            )
            
            assert response.status_code == 200
            updated_receipt = response.json()
            
            # Verify the edit was applied
            updated_bananas = next(
                item for item in updated_receipt["draft_items"]
                if item["ingredient_name"] == "Organic Bananas"
            )
            assert updated_bananas["quantity"] == "3.0"
            
            # Step 4: Confirm receipt and save to inventory
            response = client.post(
                f"/api/receipts/{receipt_id}/confirm",
                json={"items": updated_receipt["draft_items"]},
                headers=auth_headers,
            )
            
            assert response.status_code == 200
            confirmed_data = response.json()
            
            # Verify receipt status changed to completed
            assert confirmed_data["analysis_status"] == "completed"
            assert confirmed_data["draft_items"] is None or len(confirmed_data["draft_items"]) == 0
            
            # Verify ingredients were created in inventory
            ingredients = confirmed_data["ingredients"]
            assert len(ingredients) == 3
            
            # Step 5: Verify ingredients in database
            db_ingredients = (
                test_db.query(Ingredient)
                .filter(Ingredient.user_id == test_user.id)
                .all()
            )
            
            assert len(db_ingredients) == 3
            
            ingredient_names = {ing.name for ing in db_ingredients}
            assert "Organic Bananas" in ingredient_names
            assert "Almond Butter" in ingredient_names
            assert "Greek Yogurt" in ingredient_names
            
            # Verify the edited quantity was saved
            bananas_ingredient = next(
                ing for ing in db_ingredients if ing.name == "Organic Bananas"
            )
            assert bananas_ingredient.quantity == "3.0"
            assert bananas_ingredient.unit == "lb"
            assert bananas_ingredient.receipt_id == receipt_id
            
            # Step 6: Verify ingredients appear in inventory endpoint
            response = client.get("/api/ingredients", headers=auth_headers)
            assert response.status_code == 200
            
            inventory_data = response.json()
            assert len(inventory_data) == 3
            
            inventory_names = {item["name"] for item in inventory_data}
            assert "Organic Bananas" in inventory_names
            assert "Almond Butter" in inventory_names
            assert "Greek Yogurt" in inventory_names

    # @patch("app.services.receipt_analyzer.anthropic.Anthropic")
    # @patch("app.services.receipt_analyzer.settings.anthropic_api_key", "test-api-key")
    # @patch("app.config.settings.anthropic_api_key", "test-api-key")
    # def test_receipt_flow_with_manual_additions(
    #     self,
    #     mock_anthropic_class,
    #     client,
    #     test_db: Session,
    #     test_user: User,
    #     auth_headers,
    #     mock_receipt_image,
    #     sample_receipt_response,
    #     sample_nutrition_estimates,
    #     tmp_path,
    # ):
    #     """Test receipt flow with manual ingredient additions during review."""
    #     
    #     # TODO: This test is currently disabled due to mock setup complexity
    #     # The manual ingredient nutrition estimation requires additional mock setup
    #     pass

    @patch("app.services.receipt_analyzer.anthropic.Anthropic")
    @patch("app.services.receipt_analyzer.settings.anthropic_api_key", "test-api-key")
    @patch("app.config.settings.anthropic_api_key", "test-api-key")
    def test_receipt_cancellation(
        self,
        mock_anthropic_class,
        client,
        test_db: Session,
        test_user: User,
        auth_headers,
        mock_receipt_image,
        sample_receipt_response,
        sample_nutrition_estimates,
        tmp_path,
    ):
        """Test cancelling a receipt review doesn't create inventory items."""
        
        with patch.object(settings, "upload_dir", str(tmp_path / "uploads")):
            # Mock Claude API responses
            mock_client = Mock()
            mock_anthropic_class.return_value = mock_client
            
            receipt_response_json = json.dumps(sample_receipt_response)
            nutrition_responses = [
                create_mock_anthropic_response(json.dumps(sample_nutrition_estimates["Organic Bananas"])),
                create_mock_anthropic_response(json.dumps(sample_nutrition_estimates["Almond Butter"])),
                create_mock_anthropic_response(json.dumps(sample_nutrition_estimates["Greek Yogurt"])),
            ]
            mock_client.messages.create.side_effect = [
                create_mock_anthropic_response(receipt_response_json),
                *nutrition_responses
            ]
            
            # Upload receipt
            with open(mock_receipt_image, "rb") as f:
                response = client.post(
                    "/api/receipts/upload",
                    files={"file": ("receipt.jpg", f, "image/jpeg")},
                    data={"manual_items": "[]"},
                    headers=auth_headers,
                )
            
            assert response.status_code == 201
            receipt_data = response.json()
            receipt_id = receipt_data["id"]
            
            # Cancel the receipt review
            response = client.post(
                f"/api/receipts/{receipt_id}/cancel",
                headers=auth_headers,
            )
            
            assert response.status_code == 200
            cancelled_data = response.json()
            
            # Verify receipt status is cancelled
            assert cancelled_data["analysis_status"] == "cancelled"
            
            # Verify no ingredients were created
            db_ingredients = (
                test_db.query(Ingredient)
                .filter(Ingredient.user_id == test_user.id)
                .all()
            )
            
            assert len(db_ingredients) == 0

    @patch("app.services.receipt_analyzer.anthropic.Anthropic")
    @patch("app.services.receipt_analyzer.settings.anthropic_api_key", "test-api-key")
    @patch("app.config.settings.anthropic_api_key", "test-api-key")
    def test_receipt_with_item_removal(
        self,
        mock_anthropic_class,
        client,
        test_db: Session,
        test_user: User,
        auth_headers,
        mock_receipt_image,
        sample_receipt_response,
        sample_nutrition_estimates,
        tmp_path,
    ):
        """Test removing items during review before confirmation."""
        
        with patch.object(settings, "upload_dir", str(tmp_path / "uploads")):
            # Mock Claude API responses
            mock_client = Mock()
            mock_anthropic_class.return_value = mock_client
            
            receipt_response_json = json.dumps(sample_receipt_response)
            nutrition_responses = [
                create_mock_anthropic_response(json.dumps(sample_nutrition_estimates["Organic Bananas"])),
                create_mock_anthropic_response(json.dumps(sample_nutrition_estimates["Almond Butter"])),
                create_mock_anthropic_response(json.dumps(sample_nutrition_estimates["Greek Yogurt"])),
            ]
            mock_client.messages.create.side_effect = [
                create_mock_anthropic_response(receipt_response_json),
                *nutrition_responses
            ]
            
            # Upload receipt
            with open(mock_receipt_image, "rb") as f:
                response = client.post(
                    "/api/receipts/upload",
                    files={"file": ("receipt.jpg", f, "image/jpeg")},
                    data={"manual_items": "[]"},
                    headers=auth_headers,
                )
            
            assert response.status_code == 201
            receipt_data = response.json()
            receipt_id = receipt_data["id"]
            
            # Remove Greek Yogurt from items
            filtered_items = [
                item for item in receipt_data["draft_items"]
                if item["ingredient_name"] != "Greek Yogurt"
            ]
            
            assert len(filtered_items) == 2
            
            # Confirm with filtered items
            response = client.post(
                f"/api/receipts/{receipt_id}/confirm",
                json={"items": filtered_items},
                headers=auth_headers,
            )
            
            assert response.status_code == 200
            confirmed_data = response.json()
            
            # Should only have 2 ingredients
            assert len(confirmed_data["ingredients"]) == 2
            
            # Verify in database
            db_ingredients = (
                test_db.query(Ingredient)
                .filter(Ingredient.user_id == test_user.id)
                .all()
            )
            
            assert len(db_ingredients) == 2
            
            ingredient_names = {ing.name for ing in db_ingredients}
            assert "Organic Bananas" in ingredient_names
            assert "Almond Butter" in ingredient_names
            assert "Greek Yogurt" not in ingredient_names

    @patch("app.services.receipt_analyzer.anthropic.Anthropic")
    @patch("app.services.receipt_analyzer.settings.anthropic_api_key", "test-api-key")
    @patch("app.config.settings.anthropic_api_key", "test-api-key")
    def test_multiple_receipts_flow(
        self,
        mock_anthropic_class,
        client,
        test_db: Session,
        test_user: User,
        auth_headers,
        mock_receipt_image,
        tmp_path,
    ):
        """Test uploading multiple receipts sequentially."""
        
        with patch.object(settings, "upload_dir", str(tmp_path / "uploads")):
            # Mock Claude API
            mock_client = Mock()
            mock_anthropic_class.return_value = mock_client
            
            # First receipt
            first_receipt_response = {
                "store_name": "Trader Joe's",
                "items": [
                    {
                        "store_item_name": "EGGS",
                        "ingredient_name": "Eggs",
                        "is_food": True,
                        "quantity": "12",
                        "unit": "each"
                    }
                ]
            }
            
            first_nutrition = {
                "quantity": "12",
                "unit": "each",
                "serving_size": "1 large egg (50g)",
                "servings_per_container": 12,
                "calories": 70,
                "protein_g": 6,
                "carbs_g": 0,
                "fat_g": 5,
                "fiber_g": 0,
                "sodium_mg": 70,
                "nutrition_notes": "USDA data"
            }
            
            mock_client.messages.create.side_effect = [
                create_mock_anthropic_response(json.dumps(first_receipt_response)),
                create_mock_anthropic_response(json.dumps(first_nutrition)),
            ]
            
            # Upload first receipt
            with open(mock_receipt_image, "rb") as f:
                response = client.post(
                    "/api/receipts/upload",
                    files={"file": ("receipt1.jpg", f, "image/jpeg")},
                    data={"manual_items": "[]"},
                    headers=auth_headers,
                )
            
            assert response.status_code == 201
            receipt1_data = response.json()
            receipt1_id = receipt1_data["id"]
            
            # Confirm first receipt
            response = client.post(
                f"/api/receipts/{receipt1_id}/confirm",
                json={"items": receipt1_data["draft_items"]},
                headers=auth_headers,
            )
            assert response.status_code == 200
            
            # Second receipt
            second_receipt_response = {
                "store_name": "Whole Foods",
                "items": [
                    {
                        "store_item_name": "MILK",
                        "ingredient_name": "Milk",
                        "is_food": True,
                        "quantity": "1",
                        "unit": "gallon"
                    }
                ]
            }
            
            second_nutrition = {
                "quantity": "1",
                "unit": "gallon",
                "serving_size": "1 cup (240ml)",
                "servings_per_container": 16,
                "calories": 150,
                "protein_g": 8,
                "carbs_g": 12,
                "fat_g": 8,
                "fiber_g": 0,
                "sodium_mg": 120,
                "nutrition_notes": "Whole milk"
            }
            
            mock_client.messages.create.side_effect = [
                create_mock_anthropic_response(json.dumps(second_receipt_response)),
                create_mock_anthropic_response(json.dumps(second_nutrition)),
            ]
            
            # Upload second receipt
            with open(mock_receipt_image, "rb") as f:
                response = client.post(
                    "/api/receipts/upload",
                    files={"file": ("receipt2.jpg", f, "image/jpeg")},
                    data={"manual_items": "[]"},
                    headers=auth_headers,
                )
            
            assert response.status_code == 201
            receipt2_data = response.json()
            receipt2_id = receipt2_data["id"]
            
            # Confirm second receipt
            response = client.post(
                f"/api/receipts/{receipt2_id}/confirm",
                json={"items": receipt2_data["draft_items"]},
                headers=auth_headers,
            )
            assert response.status_code == 200
            
            # Verify both ingredients exist in inventory
            db_ingredients = (
                test_db.query(Ingredient)
                .filter(Ingredient.user_id == test_user.id)
                .all()
            )
            
            assert len(db_ingredients) == 2
            
            ingredient_names = {ing.name for ing in db_ingredients}
            assert "Eggs" in ingredient_names
            assert "Milk" in ingredient_names
