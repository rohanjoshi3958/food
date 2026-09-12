"""
End-to-end tests for the FOOD-55 OCR-first receipt path (flag ON).

    receipt image → Tesseract (mocked text fixture) → rules parser → gates
      → nutrition enrichment (Anthropic mocked) → review → confirm → inventory

Plus the content-hash cache and the gate-failure escalations to the mocked
Haiku (OCR-text cleanup) and Sonnet (vision) rungs. No live OCR or Anthropic
calls.
"""
from unittest.mock import Mock, patch

import pytest
from sqlalchemy.orm import Session

from app.config import (
    RECEIPT_ANTHROPIC_MODEL,
    RECEIPT_OCR_CLEANUP_MODEL,
    RECEIPT_OCR_VISION_FALLBACK_MODEL,
    settings,
)
from app.models import Ingredient, Receipt, User
from app.services.receipt_ocr import OcrResult
from app.services.receipt_preprocess import content_hash

from tests.conftest import build_anthropic_router

PIPELINE = "app.services.receipt_pipeline"


@pytest.fixture
def ocr_first_env(tmp_path):
    with patch.object(settings, "upload_dir", str(tmp_path / "uploads")), \
         patch.object(settings, "anthropic_api_key", "test-api-key"), \
         patch.object(settings, "receipt_ocr_first", True), \
         patch.object(settings, "receipt_analysis_cache", False):
        yield


def _upload(client, image_path, name="receipt.png"):
    with open(image_path, "rb") as handle:
        return client.post(
            "/api/receipts/upload",
            files={"file": (name, handle, "image/png")},
            data={"manual_items": "[]"},
        )


class TestOcrFirstReceiptFlow:
    @patch("app.services.receipt_analyzer.anthropic.Anthropic")
    def test_ocr_path_upload_review_confirm(
        self,
        mock_anthropic_class,
        ocr_first_env,
        client,
        test_db: Session,
        test_user: User,
        auth_headers,
        real_receipt_image,
        ocr_receipt_text,
        sample_nutrition_estimates,
    ):
        mock_client = Mock()
        mock_anthropic_class.return_value = mock_client
        # No receipt_response: any vision/text extraction call fails the test.
        mock_client.messages.create.side_effect = build_anthropic_router(None, sample_nutrition_estimates)

        ocr = OcrResult(text=ocr_receipt_text, confidence=93.0, word_count=40)
        with patch(f"{PIPELINE}.run_tesseract", return_value=ocr) as tesseract:
            response = _upload(client, real_receipt_image)
        tesseract.assert_called_once()

        assert response.status_code == 201, response.text
        data = response.json()
        assert data["analysis_status"] == "pending_review"
        assert data["store_name"] == "Whole Foods Market"

        drafts = {item["ingredient_name"]: item for item in data["draft_items"]}
        assert set(drafts) == {"Organic Bananas", "Almond Butter", "Greek Yogurt"}
        assert drafts["Organic Bananas"]["store_item_name"] == "ORG BNNAS"
        assert (drafts["Organic Bananas"]["quantity"], drafts["Organic Bananas"]["unit"]) == ("2.14", "lb")
        assert (drafts["Almond Butter"]["quantity"], drafts["Almond Butter"]["unit"]) == ("16", "oz")
        # Nutrition enrichment (Opus, out of scope for FOOD-55) still ran.
        assert drafts["Organic Bananas"]["calories"] == 105
        assert drafts["Almond Butter"]["servings_per_container"] == 15
        # Non-food lines never reach the review screen.
        assert "Paper Bag" not in drafts

        receipt = test_db.query(Receipt).filter(Receipt.id == data["id"]).one()
        assert receipt.analysis_path == "ocr"
        assert receipt.content_hash == content_hash(real_receipt_image.read_bytes())
        assert receipt.analysis_result["store_name"] == "Whole Foods Market"
        assert len(receipt.analysis_result["items"]) == 4

        response = client.post(
            f"/api/receipts/{data['id']}/confirm",
            json={"items": data["draft_items"]},
            headers=auth_headers,
        )
        assert response.status_code == 200, response.text
        assert response.json()["analysis_status"] == "completed"

        names = {row.name for row in test_db.query(Ingredient).filter(Ingredient.user_id == test_user.id)}
        assert names == {"Organic Bananas", "Almond Butter", "Greek Yogurt"}

    @patch("app.services.receipt_analyzer.anthropic.Anthropic")
    def test_reupload_hits_hash_cache_without_ocr_or_llm(
        self,
        mock_anthropic_class,
        ocr_first_env,
        client,
        test_db: Session,
        test_user: User,
        auth_headers,
        real_receipt_image,
        ocr_receipt_text,
        sample_nutrition_estimates,
    ):
        mock_client = Mock()
        mock_anthropic_class.return_value = mock_client
        mock_client.messages.create.side_effect = build_anthropic_router(None, sample_nutrition_estimates)
        ocr = OcrResult(text=ocr_receipt_text, confidence=93.0)

        with patch.object(settings, "receipt_analysis_cache", True), \
             patch(f"{PIPELINE}.run_tesseract", return_value=ocr) as tesseract:
            first = _upload(client, real_receipt_image, name="first.png")
            assert first.status_code == 201, first.text
            llm_calls_after_first = mock_client.messages.create.call_count
            assert llm_calls_after_first == 3  # one nutrition estimate per food item

            second = _upload(client, real_receipt_image, name="second.png")

        assert second.status_code == 201, second.text
        assert tesseract.call_count == 1
        assert mock_client.messages.create.call_count == llm_calls_after_first

        first_items = {i["ingredient_name"]: i for i in first.json()["draft_items"]}
        second_items = {i["ingredient_name"]: i for i in second.json()["draft_items"]}
        assert second_items == first_items
        assert second.json()["id"] != first.json()["id"]

        paths = {
            row.original_name: row.analysis_path
            for row in test_db.query(Receipt).filter(Receipt.user_id == test_user.id)
        }
        assert paths == {"first.png": "ocr", "second.png": "cache"}

    @patch("app.services.receipt_analyzer.anthropic.Anthropic")
    def test_soft_gate_failure_escalates_to_haiku_cleanup(
        self,
        mock_anthropic_class,
        ocr_first_env,
        client,
        test_db: Session,
        auth_headers,
        real_receipt_image,
        sample_receipt_response,
        sample_nutrition_estimates,
    ):
        mock_client = Mock()
        mock_anthropic_class.return_value = mock_client
        # Vision must not be called: receipt_response=None makes it fail loudly.
        mock_client.messages.create.side_effect = build_anthropic_router(
            None, sample_nutrition_estimates, text_response=sample_receipt_response
        )

        garbage = OcrResult(text="~~~ !! ##\n1lI|\nMLK 3.49\n", confidence=21.0)
        with patch(f"{PIPELINE}.run_tesseract", return_value=garbage):
            response = _upload(client, real_receipt_image)

        assert response.status_code == 201, response.text
        data = response.json()
        assert data["store_name"] == "Whole Foods Market"
        assert len(data["draft_items"]) == 3

        text_calls = [c for c in mock_client.messages.create.call_args_list if c.kwargs["model"] == RECEIPT_OCR_CLEANUP_MODEL]
        assert len(text_calls) == 1
        prompt = text_calls[0].kwargs["messages"][0]["content"][0]["text"]
        assert "MLK 3.49" in prompt  # the raw OCR text went to Haiku

        receipt = test_db.query(Receipt).filter(Receipt.id == data["id"]).one()
        assert receipt.analysis_path == "haiku"

    @patch("app.services.receipt_analyzer.anthropic.Anthropic")
    def test_hard_gate_failure_escalates_to_sonnet_vision(
        self,
        mock_anthropic_class,
        ocr_first_env,
        client,
        test_db: Session,
        auth_headers,
        real_receipt_image,
        sample_receipt_response,
        sample_nutrition_estimates,
    ):
        mock_client = Mock()
        mock_anthropic_class.return_value = mock_client
        haiku_finds_nothing = {"store_name": None, "items": []}
        mock_client.messages.create.side_effect = build_anthropic_router(
            sample_receipt_response, sample_nutrition_estimates, text_response=haiku_finds_nothing
        )

        garbage = OcrResult(text="~~~ !! ##\n1lI|\n", confidence=21.0)
        with patch(f"{PIPELINE}.run_tesseract", return_value=garbage):
            response = _upload(client, real_receipt_image)

        assert response.status_code == 201, response.text
        data = response.json()
        assert data["store_name"] == "Whole Foods Market"
        assert len(data["draft_items"]) == 3

        vision_calls = [
            call
            for call in mock_client.messages.create.call_args_list
            if isinstance(call.kwargs["messages"][0]["content"], list)
            and call.kwargs["messages"][0]["content"][0]["type"] == "image"
        ]
        assert len(vision_calls) == 1
        assert vision_calls[0].kwargs["model"] == RECEIPT_OCR_VISION_FALLBACK_MODEL
        assert vision_calls[0].kwargs["model"] != RECEIPT_ANTHROPIC_MODEL
        image_block = vision_calls[0].kwargs["messages"][0]["content"][0]
        assert image_block["source"]["media_type"] == "image/jpeg"  # downsampled re-encode

        # Nutrition enrichment still went to the Opus constant (FOOD-58 owns routing).
        nutrition_models = {
            c.kwargs["model"]
            for c in mock_client.messages.create.call_args_list
            if isinstance(c.kwargs["messages"][0]["content"], str)
            and "Estimate nutritional facts" in c.kwargs["messages"][0]["content"]
        }
        assert nutrition_models == {RECEIPT_ANTHROPIC_MODEL}

        receipt = test_db.query(Receipt).filter(Receipt.id == data["id"]).one()
        assert receipt.analysis_path == "sonnet"

    @patch("app.services.receipt_analyzer.anthropic.Anthropic")
    def test_flag_off_keeps_baseline_even_with_tesseract_present(
        self,
        mock_anthropic_class,
        ocr_first_env,
        client,
        test_db: Session,
        auth_headers,
        real_receipt_image,
        sample_receipt_response,
        sample_nutrition_estimates,
    ):
        mock_client = Mock()
        mock_anthropic_class.return_value = mock_client
        mock_client.messages.create.side_effect = build_anthropic_router(
            sample_receipt_response, sample_nutrition_estimates
        )

        with patch.object(settings, "receipt_ocr_first", False), \
             patch(f"{PIPELINE}.run_tesseract") as tesseract:
            response = _upload(client, real_receipt_image)

        assert response.status_code == 201, response.text
        tesseract.assert_not_called()
        receipt = test_db.query(Receipt).filter(Receipt.id == response.json()["id"]).one()
        assert receipt.analysis_path == "opus_baseline"
        assert receipt.content_hash == content_hash(real_receipt_image.read_bytes())
