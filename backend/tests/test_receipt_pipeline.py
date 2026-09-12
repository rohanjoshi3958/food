"""Unit tests for the receipt extraction facade: cache, flag routing, escalation (FOOD-55)."""
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from app.config import RECEIPT_ANTHROPIC_MODEL, settings
from app.models import Receipt, User
from app.services.receipt_analyzer import ParsedReceipt, ParsedReceiptItem, ReceiptAnalysisError
from app.services.receipt_ocr import OcrResult, OcrUnavailableError
from app.services.receipt_pipeline import (
    PATH_CACHE,
    PATH_HAIKU,
    PATH_OCR,
    PATH_OPUS_BASELINE,
    PATH_SONNET,
    analyze_receipt,
    find_cached_analysis,
    path_for_model,
)
from app.services.receipt_preprocess import content_hash
from app.services.receipt_telemetry import (
    clear_receipt_path_listeners,
    emit_receipt_path,
    register_receipt_path_listener,
)

PIPELINE = "app.services.receipt_pipeline"


def _parsed(*names: str) -> ParsedReceipt:
    return ParsedReceipt(
        store_name="Cached Store",
        items=[
            ParsedReceiptItem(store_item_name=n, ingredient_name=n.title(), quantity="1", unit="each", calories=42)
            for n in names
        ],
    )


def _receipt_row(user: User, digest: str, status: str = "completed", result=None, **kwargs) -> Receipt:
    return Receipt(
        user_id=user.id,
        filename="/tmp/x.png",
        original_name="x.png",
        analysis_status=status,
        content_hash=digest,
        analysis_result=result,
        **kwargs,
    )


@pytest.fixture
def other_user(test_db):
    from app.auth_utils import hash_password

    user = User(email="other@example.com", name="Other", password=hash_password("pw12345678"))
    test_db.add(user)
    test_db.commit()
    test_db.refresh(user)
    return user


@pytest.fixture
def ocr_ok(ocr_receipt_text):
    return OcrResult(text=ocr_receipt_text, confidence=92.0, word_count=40)


@pytest.fixture
def flags():
    """Reset every FOOD-55 flag for a test, then restore."""
    with patch.object(settings, "receipt_analysis_cache", False), \
         patch.object(settings, "receipt_ocr_first", False), \
         patch.object(settings, "receipt_ocr_text_fallback_model", ""), \
         patch.object(settings, "receipt_ocr_vision_fallback_model", ""), \
         patch.object(settings, "anthropic_api_key", "test-api-key"):
        yield


class TestFindCachedAnalysis:
    def test_miss_when_no_matching_hash(self, test_db, test_user):
        test_db.add(_receipt_row(test_user, "aaa", result=_parsed("MILK").model_dump()))
        test_db.commit()
        assert find_cached_analysis(test_db, test_user.id, "bbb") is None

    def test_hit_returns_most_recent_successful_parse(self, test_db, test_user):
        digest = "same"
        test_db.add(_receipt_row(test_user, digest, result=_parsed("OLD").model_dump()))
        test_db.commit()
        test_db.add(_receipt_row(test_user, digest, status="pending_review", result=_parsed("NEW").model_dump()))
        test_db.commit()

        cached = find_cached_analysis(test_db, test_user.id, digest)
        assert cached is not None
        assert [item.store_item_name for item in cached.items] == ["NEW"]
        assert cached.items[0].calories == 42  # enrichment is reused too

    def test_ignores_other_users_and_unsuccessful_rows(self, test_db, test_user, other_user):
        digest = "shared"
        test_db.add(_receipt_row(other_user, digest, result=_parsed("THEIRS").model_dump()))
        test_db.add(_receipt_row(test_user, digest, status="processing", result=None))
        test_db.add(_receipt_row(test_user, digest, status="failed", result=_parsed("BAD").model_dump()))
        test_db.commit()
        assert find_cached_analysis(test_db, test_user.id, digest) is None

    def test_excludes_current_receipt_and_skips_corrupt_json(self, test_db, test_user):
        digest = "d"
        current = _receipt_row(test_user, digest, status="pending_review", result=_parsed("ME").model_dump())
        corrupt = _receipt_row(test_user, digest, result={"items": "not-a-list"})
        test_db.add_all([current, corrupt])
        test_db.commit()
        assert find_cached_analysis(test_db, test_user.id, digest, exclude_receipt_id=current.id) is None


class TestAnalyzeReceiptRouting:
    def test_flag_off_uses_baseline_and_labels_opus(self, flags, test_db, test_user, real_receipt_image):
        contents = real_receipt_image.read_bytes()
        with patch(f"{PIPELINE}.analyze_receipt_image", return_value=_parsed("EGGS")) as baseline:
            outcome = analyze_receipt(real_receipt_image, contents, db=test_db, user_id=test_user.id)
        baseline.assert_called_once_with(real_receipt_image)
        assert outcome.path == PATH_OPUS_BASELINE
        assert outcome.content_hash == content_hash(contents)
        assert outcome.escalations == []
        assert outcome.latency_ms >= 0

    def test_cache_flag_off_ignores_prior_parse(self, flags, test_db, test_user, real_receipt_image):
        contents = real_receipt_image.read_bytes()
        test_db.add(_receipt_row(test_user, content_hash(contents), result=_parsed("CACHED").model_dump()))
        test_db.commit()
        with patch(f"{PIPELINE}.analyze_receipt_image", return_value=_parsed("FRESH")) as baseline:
            outcome = analyze_receipt(real_receipt_image, contents, db=test_db, user_id=test_user.id)
        baseline.assert_called_once()
        assert outcome.path == PATH_OPUS_BASELINE

    def test_cache_hit_skips_all_analysis(self, flags, test_db, test_user, real_receipt_image):
        contents = real_receipt_image.read_bytes()
        test_db.add(_receipt_row(test_user, content_hash(contents), result=_parsed("CACHED").model_dump()))
        test_db.commit()
        with patch.object(settings, "receipt_analysis_cache", True), \
             patch.object(settings, "anthropic_api_key", ""), \
             patch(f"{PIPELINE}.analyze_receipt_image") as baseline, \
             patch(f"{PIPELINE}.run_tesseract") as ocr:
            outcome = analyze_receipt(real_receipt_image, contents, db=test_db, user_id=test_user.id)
        baseline.assert_not_called()
        ocr.assert_not_called()
        assert outcome.path == PATH_CACHE
        assert outcome.confidence == 1.0
        assert outcome.parsed.items[0].store_item_name == "CACHED"

    def test_missing_api_key_still_errors_like_before(self, flags, test_db, test_user, real_receipt_image):
        with patch.object(settings, "anthropic_api_key", ""):
            with pytest.raises(ReceiptAnalysisError):
                analyze_receipt(real_receipt_image, b"x", db=test_db, user_id=test_user.id)


class TestOcrFirstLadder:
    @pytest.fixture(autouse=True)
    def _ocr_first(self, flags):
        with patch.object(settings, "receipt_ocr_first", True), \
             patch(f"{PIPELINE}.enrich_receipt_nutrition", side_effect=lambda parsed: parsed):
            yield

    def test_ocr_pass_never_touches_a_vision_model(self, test_db, test_user, real_receipt_image, ocr_ok):
        contents = real_receipt_image.read_bytes()
        with patch(f"{PIPELINE}.run_tesseract", return_value=ocr_ok), \
             patch(f"{PIPELINE}.extract_receipt_vision") as vision, \
             patch(f"{PIPELINE}.extract_receipt_text") as text_model:
            outcome = analyze_receipt(real_receipt_image, contents, db=test_db, user_id=test_user.id)

        vision.assert_not_called()
        text_model.assert_not_called()
        assert outcome.path == PATH_OCR
        assert outcome.gate_reasons == []
        assert outcome.ocr_confidence == 92.0
        assert outcome.confidence == pytest.approx(0.92)
        assert outcome.parsed.store_name == "Whole Foods Market"
        names = {item.ingredient_name for item in outcome.parsed.items if item.is_food}
        assert names == {"Organic Bananas", "Almond Butter", "Greek Yogurt"}

    def test_low_confidence_escalates_to_opus_on_downsampled_image(self, test_db, test_user, real_receipt_image, ocr_receipt_text):
        contents = real_receipt_image.read_bytes()
        weak = OcrResult(text=ocr_receipt_text, confidence=30.0)
        with patch(f"{PIPELINE}.run_tesseract", return_value=weak), \
             patch(f"{PIPELINE}.extract_receipt_vision", return_value=_parsed("EGGS")) as vision:
            outcome = analyze_receipt(real_receipt_image, contents, db=test_db, user_id=test_user.id)

        assert outcome.path == PATH_OPUS_BASELINE
        assert outcome.escalations == [PATH_OCR]
        assert outcome.gate_reasons == ["low_ocr_confidence"]
        data, media_type = vision.call_args.args
        assert vision.call_args.kwargs["model"] == RECEIPT_ANTHROPIC_MODEL
        assert media_type == "image/jpeg"
        assert data != contents  # re-encoded / downsampled, not the raw PNG

    def test_ocr_unavailable_escalates(self, test_db, test_user, real_receipt_image):
        contents = real_receipt_image.read_bytes()
        with patch(f"{PIPELINE}.run_tesseract", side_effect=OcrUnavailableError("no binary")), \
             patch(f"{PIPELINE}.extract_receipt_vision", return_value=_parsed("EGGS")):
            outcome = analyze_receipt(real_receipt_image, contents, db=test_db, user_id=test_user.id)
        assert outcome.path == PATH_OPUS_BASELINE
        assert outcome.gate_reasons == ["ocr_unavailable"]
        assert outcome.ocr_confidence is None

    def test_pdf_skips_ocr_and_sends_original_bytes(self, test_db, test_user, tmp_path):
        pdf = tmp_path / "receipt.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake")
        with patch(f"{PIPELINE}.run_tesseract") as ocr, \
             patch(f"{PIPELINE}.extract_receipt_vision", return_value=_parsed("EGGS")) as vision:
            outcome = analyze_receipt(pdf, pdf.read_bytes(), db=test_db, user_id=test_user.id)
        ocr.assert_not_called()
        assert vision.call_args.args == (b"%PDF-1.4 fake", "application/pdf")
        assert outcome.path == PATH_OPUS_BASELINE

    def test_zero_food_lines_gate(self, test_db, test_user, real_receipt_image):
        contents = real_receipt_image.read_bytes()
        bags_only = OcrResult(text="MARKET\nPAPER BAG 0.10\nTOTAL 0.10\n", confidence=95.0)
        with patch(f"{PIPELINE}.run_tesseract", return_value=bags_only), \
             patch(f"{PIPELINE}.extract_receipt_vision", return_value=_parsed("EGGS")):
            outcome = analyze_receipt(real_receipt_image, contents, db=test_db, user_id=test_user.id)
        assert outcome.path == PATH_OPUS_BASELINE
        assert "zero_food_lines" in outcome.gate_reasons

    def test_text_fallback_rung_when_configured(self, test_db, test_user, real_receipt_image, ocr_receipt_text):
        contents = real_receipt_image.read_bytes()
        weak = OcrResult(text=ocr_receipt_text, confidence=30.0)
        with patch.object(settings, "receipt_ocr_text_fallback_model", "claude-haiku-test"), \
             patch(f"{PIPELINE}.run_tesseract", return_value=weak), \
             patch(f"{PIPELINE}.extract_receipt_text", return_value=_parsed("EGGS")) as text_model, \
             patch(f"{PIPELINE}.extract_receipt_vision") as vision:
            outcome = analyze_receipt(real_receipt_image, contents, db=test_db, user_id=test_user.id)

        vision.assert_not_called()
        assert text_model.call_args.args == (ocr_receipt_text,)
        assert text_model.call_args.kwargs == {"model": "claude-haiku-test"}
        assert outcome.path == PATH_HAIKU
        assert outcome.escalations == [PATH_OCR]

    def test_text_fallback_failure_continues_to_vision(self, test_db, test_user, real_receipt_image, ocr_receipt_text):
        contents = real_receipt_image.read_bytes()
        weak = OcrResult(text=ocr_receipt_text, confidence=30.0)
        no_food = ParsedReceipt(items=[ParsedReceiptItem(store_item_name="BAG", ingredient_name="Bag", is_food=False)])
        with patch.object(settings, "receipt_ocr_text_fallback_model", "claude-haiku-test"), \
             patch.object(settings, "receipt_ocr_vision_fallback_model", "claude-sonnet-5"), \
             patch(f"{PIPELINE}.run_tesseract", return_value=weak), \
             patch(f"{PIPELINE}.extract_receipt_text", return_value=no_food), \
             patch(f"{PIPELINE}.extract_receipt_vision", return_value=_parsed("EGGS")) as vision:
            outcome = analyze_receipt(real_receipt_image, contents, db=test_db, user_id=test_user.id)

        assert vision.call_args.kwargs["model"] == "claude-sonnet-5"
        assert outcome.path == PATH_SONNET
        assert outcome.escalations == [PATH_OCR, PATH_HAIKU]
        assert outcome.gate_reasons == ["low_ocr_confidence", "zero_food_lines"]

    def test_final_rung_errors_propagate(self, test_db, test_user, real_receipt_image):
        contents = real_receipt_image.read_bytes()
        with patch(f"{PIPELINE}.run_tesseract", side_effect=OcrUnavailableError("x")), \
             patch(f"{PIPELINE}.extract_receipt_vision", side_effect=ReceiptAnalysisError("boom")):
            with pytest.raises(ReceiptAnalysisError, match="boom"):
                analyze_receipt(real_receipt_image, contents, db=test_db, user_id=test_user.id)


class TestExtractors:
    """The real extraction helpers against a mocked Anthropic client (no patching of our own code)."""

    @patch("app.services.receipt_analyzer.anthropic.Anthropic")
    def test_extract_receipt_text_sends_ocr_text_and_parses_json(self, mock_anthropic_class, flags, ocr_receipt_text):
        from tests.conftest import create_mock_anthropic_response

        mock_client = Mock()
        mock_anthropic_class.return_value = mock_client
        mock_client.messages.create.return_value = create_mock_anthropic_response(
            '{"store_name": "Whole Foods Market", "items": [{"store_item_name": "ORG BNNAS", "ingredient_name": "Organic Bananas", "is_food": true, "quantity": "2.14", "unit": "lb"}]}'
        )
        from app.services.receipt_analyzer import extract_receipt_text

        parsed = extract_receipt_text(ocr_receipt_text, model="claude-haiku-test")

        kwargs = mock_client.messages.create.call_args.kwargs
        assert kwargs["model"] == "claude-haiku-test"
        prompt = kwargs["messages"][0]["content"][0]["text"]
        assert "ORG BNNAS" in prompt and "OCR TEXT START" in prompt
        assert '"store_name": "Store Name or null"' in prompt  # JSON braces survived
        assert parsed.store_name == "Whole Foods Market"
        assert parsed.items[0].ingredient_name == "Organic Bananas"

    @patch("app.services.receipt_analyzer.anthropic.Anthropic")
    def test_extract_receipt_vision_uses_document_block_for_pdf(self, mock_anthropic_class, flags):
        from tests.conftest import create_mock_anthropic_response

        mock_client = Mock()
        mock_anthropic_class.return_value = mock_client
        mock_client.messages.create.return_value = create_mock_anthropic_response('{"store_name": null, "items": []}')
        from app.services.receipt_analyzer import extract_receipt_vision

        extract_receipt_vision(b"%PDF-1.4", "application/pdf", model="claude-sonnet-5")
        kwargs = mock_client.messages.create.call_args.kwargs
        block = kwargs["messages"][0]["content"][0]
        assert kwargs["model"] == "claude-sonnet-5"
        assert block["type"] == "document"
        assert block["source"]["media_type"] == "application/pdf"


class TestTelemetry:
    def test_path_for_model(self):
        assert path_for_model("claude-haiku-4") == PATH_HAIKU
        assert path_for_model("claude-sonnet-5") == PATH_SONNET
        assert path_for_model("claude-opus-5") == PATH_OPUS_BASELINE

    def test_outcome_telemetry_fields(self, flags, test_db, test_user, real_receipt_image):
        contents = real_receipt_image.read_bytes()
        with patch(f"{PIPELINE}.analyze_receipt_image", return_value=_parsed("EGGS", "MILK")):
            event = analyze_receipt(real_receipt_image, contents, db=test_db, user_id=test_user.id).telemetry()
        assert event["path"] == PATH_OPUS_BASELINE
        assert set(event) >= {"path", "confidence", "ocr_confidence", "latency_ms", "content_hash", "gate_reasons", "escalations", "item_count", "food_item_count"}
        assert event["content_hash"] == content_hash(contents)[:12]
        assert event["item_count"] == 2

    def test_emit_logs_json_and_notifies_listeners(self, caplog):
        received: list[dict] = []
        register_receipt_path_listener(received.append)
        try:
            with caplog.at_level("INFO", logger="app.receipts.telemetry"):
                emit_receipt_path({"path": PATH_CACHE, "status": "ok"})
        finally:
            clear_receipt_path_listeners()
        assert received == [{"event": "receipt_analysis", "path": PATH_CACHE, "status": "ok"}]
        assert '"path": "cache"' in caplog.text

    def test_listener_errors_do_not_propagate(self):
        register_receipt_path_listener(Mock(side_effect=RuntimeError("listener down")))
        try:
            emit_receipt_path({"path": PATH_OCR})
        finally:
            clear_receipt_path_listeners()
