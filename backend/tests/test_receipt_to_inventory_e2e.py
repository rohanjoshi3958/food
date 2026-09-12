"""
End-to-end tests for receipt → inventory flow.

Tests the complete flow:
    receipt
     ↓
    upload accepted (202, processing) → poll until analyzed
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
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Ingredient, Receipt, User
from app.services.receipt_jobs import (
    ANALYSIS_TIMEOUT_MESSAGE,
    GENERIC_FAILURE_MESSAGE,
    run_receipt_analysis,
)

from tests.conftest import (
    build_receipt_flow_side_effect,
    create_mock_anthropic_response,
)

POLL_TIMEOUT_SECONDS = 5.0


def upload_receipt(client, image_path, *, name="receipt.jpg", manual_items="[]"):
    """POST the receipt and assert the accept → job_id contract."""
    with open(image_path, "rb") as f:
        response = client.post(
            "/api/receipts/upload",
            files={"file": (name, f, "image/jpeg")},
            data={"manual_items": manual_items},
        )

    assert response.status_code == 202
    accepted = response.json()
    assert accepted["analysis_status"] == "processing"
    assert accepted["analysis_stage"] == "queued"
    assert accepted["draft_items"] == []
    assert accepted["id"]
    return accepted


def wait_for_analysis(client, receipt_id, *, timeout=POLL_TIMEOUT_SECONDS):
    """Poll GET /api/receipts/{id} until analysis leaves ``processing``."""
    deadline = time.monotonic() + timeout
    while True:
        response = client.get(f"/api/receipts/{receipt_id}")
        assert response.status_code == 200
        data = response.json()
        if data["analysis_status"] != "processing":
            return data
        if time.monotonic() > deadline:
            pytest.fail(f"Receipt {receipt_id} still processing after {timeout}s")
        time.sleep(0.05)


def upload_and_analyze(client, image_path, *, name="receipt.jpg", manual_items="[]"):
    accepted = upload_receipt(client, image_path, name=name, manual_items=manual_items)
    return wait_for_analysis(client, accepted["id"])


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

            upload_nutrition = [
                sample_nutrition_estimates["Organic Bananas"],
                sample_nutrition_estimates["Almond Butter"],
                sample_nutrition_estimates["Greek Yogurt"],
            ]
            mock_client.messages.create.side_effect = build_receipt_flow_side_effect(
                sample_receipt_response,
                upload_nutrition,
            )
            
            # Step 2: Upload receipt — accepted immediately, analyzed in the background
            accepted = upload_receipt(client, mock_receipt_image)
            receipt_id = accepted["id"]

            # Poll until Claude's result lands on the receipt
            receipt_data = wait_for_analysis(client, receipt_id)

            # Verify receipt finished with the review-ready result
            assert receipt_data["id"] == receipt_id
            assert receipt_data["analysis_status"] == "pending_review"
            assert receipt_data["analysis_stage"] is None
            assert receipt_data["analysis_error"] is None
            assert receipt_data["store_name"] == "Whole Foods Market"
            assert len(receipt_data["draft_items"]) == 3
            
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

            upload_nutrition = [
                sample_nutrition_estimates["Organic Bananas"],
                sample_nutrition_estimates["Almond Butter"],
                sample_nutrition_estimates["Greek Yogurt"],
            ]
            mock_client.messages.create.side_effect = build_receipt_flow_side_effect(
                sample_receipt_response,
                upload_nutrition,
            )
            
            # Upload receipt and wait for analysis
            receipt_data = upload_and_analyze(client, mock_receipt_image)
            receipt_id = receipt_data["id"]
            assert receipt_data["analysis_status"] == "pending_review"
            
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

            upload_nutrition = [
                sample_nutrition_estimates["Organic Bananas"],
                sample_nutrition_estimates["Almond Butter"],
                sample_nutrition_estimates["Greek Yogurt"],
            ]
            confirm_nutrition = [
                sample_nutrition_estimates["Organic Bananas"],
                sample_nutrition_estimates["Almond Butter"],
            ]
            mock_client.messages.create.side_effect = build_receipt_flow_side_effect(
                sample_receipt_response,
                upload_nutrition,
                confirm_nutrition,
            )
            
            # Upload receipt and wait for analysis
            receipt_data = upload_and_analyze(client, mock_receipt_image)
            receipt_id = receipt_data["id"]
            assert receipt_data["analysis_status"] == "pending_review"
            
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
                "recognized": True,
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
                "nutrition_notes": "USDA data",
            }

            mock_client.messages.create.side_effect = build_receipt_flow_side_effect(
                first_receipt_response,
                [first_nutrition],
            )
            
            # Upload first receipt
            receipt1_data = upload_and_analyze(
                client, mock_receipt_image, name="receipt1.jpg"
            )
            receipt1_id = receipt1_data["id"]
            assert receipt1_data["analysis_status"] == "pending_review"
            
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
                "recognized": True,
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
                "nutrition_notes": "Whole milk",
            }

            mock_client.messages.create.side_effect = build_receipt_flow_side_effect(
                second_receipt_response,
                [second_nutrition],
                existing_pantry_items=1,
            )
            
            # Upload second receipt
            receipt2_data = upload_and_analyze(
                client, mock_receipt_image, name="receipt2.jpg"
            )
            receipt2_id = receipt2_data["id"]
            assert receipt2_data["analysis_status"] == "pending_review"
            
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


class TestAsyncReceiptAnalysis:
    """Upload must return before Claude runs; the poll carries progress + result."""

    def test_upload_returns_before_analysis_runs(
        self,
        client,
        test_db: Session,
        test_user: User,
        auth_headers,
        mock_receipt_image,
        tmp_path,
    ):
        upload_dir = tmp_path / "uploads"
        with patch.object(settings, "upload_dir", str(upload_dir)), patch(
            "app.routers.receipts.run_receipt_analysis"
        ) as run_analysis:
            accepted = upload_receipt(client, mock_receipt_image)

            # The job was handed to the background runner with the receipt id as job id.
            run_analysis.assert_called_once()
            job_receipt_id, job_file, job_manual_items = run_analysis.call_args.args
            assert job_receipt_id == accepted["id"]
            assert job_file.startswith(str(upload_dir / test_user.id))
            assert job_manual_items == []

            # Nothing ran, so the poll still reports processing (not an error).
            polled = client.get(f"/api/receipts/{accepted['id']}")
            assert polled.status_code == 200
            assert polled.json()["analysis_status"] == "processing"
            assert polled.json()["analysis_stage"] == "queued"

            # Processing receipts stay out of the uploaded-receipts list.
            listed = client.get("/api/receipts")
            assert listed.status_code == 200
            assert listed.json() == []

    @patch("app.services.receipt_analyzer.anthropic.Anthropic")
    @patch("app.services.receipt_analyzer.settings.anthropic_api_key", "test-api-key")
    @patch("app.config.settings.anthropic_api_key", "test-api-key")
    def test_stage_transitions_are_written_during_analysis(
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
        from app import database

        stages_seen_by_claude: list[str | None] = []
        receipt_ids: list[str] = []

        responses = build_receipt_flow_side_effect(
            sample_receipt_response,
            [sample_nutrition_estimates["Organic Bananas"]] * 3,
        )

        def create(*args, **kwargs):
            session = database.SessionLocal()
            try:
                row = session.get(Receipt, receipt_ids[0]) if receipt_ids else None
                stages_seen_by_claude.append(row.analysis_stage if row else None)
            finally:
                session.close()
            return responses.pop(0)

        with patch.object(settings, "upload_dir", str(tmp_path / "uploads")), patch(
            "app.routers.receipts.run_receipt_analysis"
        ) as deferred:
            mock_client = Mock()
            mock_anthropic_class.return_value = mock_client
            mock_client.messages.create.side_effect = create

            accepted = upload_receipt(client, mock_receipt_image)
            receipt_ids.append(accepted["id"])

            # Run the deferred job now that we know the receipt id.
            run_receipt_analysis(*deferred.call_args.args)

            result = client.get(f"/api/receipts/{accepted['id']}").json()

        assert result["analysis_status"] == "pending_review"
        # First Claude call is the receipt read; the nutrition calls follow.
        assert stages_seen_by_claude[0] == "reading_receipt"
        assert set(stages_seen_by_claude[1:]) == {"estimating_nutrition"}

    @patch("app.services.receipt_analyzer.anthropic.Anthropic")
    @patch("app.services.receipt_analyzer.settings.anthropic_api_key", "test-api-key")
    @patch("app.config.settings.anthropic_api_key", "test-api-key")
    def test_poll_reports_failure_and_list_discards_it(
        self,
        mock_anthropic_class,
        client,
        test_db: Session,
        test_user: User,
        auth_headers,
        mock_receipt_image,
        tmp_path,
    ):
        with patch.object(settings, "upload_dir", str(tmp_path / "uploads")):
            mock_client = Mock()
            mock_anthropic_class.return_value = mock_client
            mock_client.messages.create.return_value = create_mock_anthropic_response(
                "this is not json"
            )

            accepted = upload_receipt(client, mock_receipt_image)
            result = wait_for_analysis(client, accepted["id"])

            assert result["analysis_status"] == "failed"
            assert result["analysis_stage"] is None
            assert result["analysis_error"] == (
                "Could not parse ingredient data from the receipt analysis."
            )
            assert result["draft_items"] == []

            # The upload itself is gone; the row survives only until the next listing.
            test_db.expire_all()
            db_receipt = test_db.get(Receipt, accepted["id"])
            assert db_receipt is not None
            assert not Path(db_receipt.filename).exists()

            listed = client.get("/api/receipts")
            assert listed.status_code == 200
            assert listed.json() == []

            test_db.expire_all()
            assert test_db.get(Receipt, accepted["id"]) is None
            assert client.get(f"/api/receipts/{accepted['id']}").status_code == 404

    @patch("app.services.receipt_analyzer.anthropic.Anthropic")
    @patch("app.services.receipt_analyzer.settings.anthropic_api_key", "test-api-key")
    @patch("app.config.settings.anthropic_api_key", "test-api-key")
    def test_unexpected_errors_fail_with_generic_message(
        self,
        mock_anthropic_class,
        client,
        test_db: Session,
        test_user: User,
        auth_headers,
        mock_receipt_image,
        tmp_path,
    ):
        with patch.object(settings, "upload_dir", str(tmp_path / "uploads")):
            mock_client = Mock()
            mock_anthropic_class.return_value = mock_client
            mock_client.messages.create.side_effect = RuntimeError("boom")

            accepted = upload_receipt(client, mock_receipt_image)
            result = wait_for_analysis(client, accepted["id"])

        assert result["analysis_status"] == "failed"
        assert result["analysis_error"] == GENERIC_FAILURE_MESSAGE

    @patch("app.services.receipt_analyzer.anthropic.Anthropic")
    @patch("app.services.receipt_analyzer.settings.anthropic_api_key", "test-api-key")
    @patch("app.config.settings.anthropic_api_key", "test-api-key")
    def test_manual_items_are_merged_into_analysis_result(
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
        with patch.object(settings, "upload_dir", str(tmp_path / "uploads")):
            mock_client = Mock()
            mock_anthropic_class.return_value = mock_client
            mock_client.messages.create.side_effect = build_receipt_flow_side_effect(
                sample_receipt_response,
                [
                    sample_nutrition_estimates["Organic Bananas"],
                    sample_nutrition_estimates["Almond Butter"],
                    sample_nutrition_estimates["Greek Yogurt"],
                ],
            )

            manual_items = json.dumps(
                [{"ingredient_name": "Olive Oil", "quantity": "1", "unit": "bottle"}]
            )
            result = upload_and_analyze(
                client, mock_receipt_image, manual_items=manual_items
            )

        assert result["analysis_status"] == "pending_review"
        drafts = {item["ingredient_name"]: item for item in result["draft_items"]}
        assert set(drafts) == {
            "Olive Oil",
            "Organic Bananas",
            "Almond Butter",
            "Greek Yogurt",
        }
        assert drafts["Olive Oil"]["is_manual"] is True
        assert drafts["Organic Bananas"]["is_manual"] is False

    def test_invalid_manual_items_are_rejected_before_queuing(
        self,
        client,
        test_db: Session,
        test_user: User,
        auth_headers,
        mock_receipt_image,
        tmp_path,
    ):
        with patch.object(settings, "upload_dir", str(tmp_path / "uploads")), patch(
            "app.routers.receipts.run_receipt_analysis"
        ) as run_analysis, open(mock_receipt_image, "rb") as f:
            response = client.post(
                "/api/receipts/upload",
                files={"file": ("receipt.jpg", f, "image/jpeg")},
                data={"manual_items": "not json"},
            )

        assert response.status_code == 400
        run_analysis.assert_not_called()
        assert test_db.query(Receipt).count() == 0

    def test_stale_processing_receipt_times_out_on_poll(
        self,
        client,
        test_db: Session,
        test_user: User,
        auth_headers,
        tmp_path,
    ):
        stale_file = tmp_path / "stale.jpg"
        stale_file.write_bytes(b"fake image data")
        receipt = Receipt(
            user_id=test_user.id,
            filename=str(stale_file),
            original_name="stale.jpg",
            analysis_status="processing",
            analysis_stage="reading_receipt",
            uploaded_at=datetime.now(UTC) - timedelta(minutes=11),
        )
        test_db.add(receipt)
        test_db.commit()

        polled = client.get(f"/api/receipts/{receipt.id}")

        assert polled.status_code == 200
        assert polled.json()["analysis_status"] == "failed"
        assert polled.json()["analysis_stage"] is None
        assert polled.json()["analysis_error"] == ANALYSIS_TIMEOUT_MESSAGE
        assert not stale_file.exists()

    def test_fresh_processing_receipt_is_not_timed_out(
        self,
        client,
        test_db: Session,
        test_user: User,
        auth_headers,
    ):
        receipt = Receipt(
            user_id=test_user.id,
            filename="receipt.jpg",
            original_name="receipt.jpg",
            analysis_status="processing",
            analysis_stage="reading_receipt",
        )
        test_db.add(receipt)
        test_db.commit()

        polled = client.get(f"/api/receipts/{receipt.id}")

        assert polled.status_code == 200
        assert polled.json()["analysis_status"] == "processing"
        assert polled.json()["analysis_stage"] == "reading_receipt"

    def test_result_is_dropped_when_receipt_was_discarded_mid_analysis(
        self,
        client,
        test_db: Session,
        test_user: User,
        auth_headers,
        mock_receipt_image,
        sample_receipt_response,
    ):
        from app.services.receipt_analyzer import ParsedReceipt

        parsed = ParsedReceipt.model_validate(sample_receipt_response)

        cancelled = Receipt(
            user_id=test_user.id,
            filename=str(mock_receipt_image),
            original_name="receipt.jpg",
            analysis_status="cancelled",
        )
        test_db.add(cancelled)
        test_db.commit()

        with patch(
            "app.services.receipt_jobs.analyze_receipt_image", return_value=parsed
        ):
            # Receipt deleted while Claude was running: nothing to write, no error.
            run_receipt_analysis("missing-receipt", str(mock_receipt_image), [])
            # Receipt no longer processing: the late result must not resurrect it.
            run_receipt_analysis(cancelled.id, str(mock_receipt_image), [])

        test_db.expire_all()
        refreshed = test_db.get(Receipt, cancelled.id)
        assert refreshed.analysis_status == "cancelled"
        assert refreshed.draft_items is None
        assert refreshed.store_name is None

    def test_poll_requires_owner(
        self,
        client,
        test_db: Session,
        test_user: User,
        auth_headers,
    ):
        other = User(email="other@example.com", name="Other", password="x")
        test_db.add(other)
        test_db.commit()
        receipt = Receipt(
            user_id=other.id,
            filename="receipt.jpg",
            original_name="receipt.jpg",
            analysis_status="processing",
        )
        test_db.add(receipt)
        test_db.commit()

        assert client.get(f"/api/receipts/{receipt.id}").status_code == 404
