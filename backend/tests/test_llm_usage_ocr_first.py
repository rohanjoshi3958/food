"""FOOD-54 × FOOD-55: OCR-first receipt paths land in the usage table with route/confidence."""

from unittest.mock import Mock, patch

import pytest

from app.config import RECEIPT_ANTHROPIC_MODEL, RECEIPT_OCR_CLEANUP_MODEL, settings
from app.llm_usage import set_sinks
from app.llm_usage.recorder import DatabaseSink
from app.models import LlmUsageEvent, LlmWorkflowRun
from app.services.receipt_ocr import OcrResult, OcrUnavailableError

from tests.conftest import build_anthropic_router

PIPELINE = "app.services.receipt_pipeline"


@pytest.fixture(autouse=True)
def db_only_sink():
    set_sinks([DatabaseSink()])
    try:
        yield
    finally:
        set_sinks(None)


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


def _events(db):
    return db.query(LlmUsageEvent).order_by(LlmUsageEvent.created_at).all()


class TestOcrFirstUsageEvents:
    @patch("app.services.receipt_analyzer.anthropic.Anthropic")
    def test_ocr_success_records_ocr_step_and_nutrition_calls_in_one_run(
        self, mock_anthropic_class, ocr_first_env, client, test_db, auth_headers,
        real_receipt_image, ocr_receipt_text, sample_nutrition_estimates,
    ):
        mock_client = Mock()
        mock_anthropic_class.return_value = mock_client
        mock_client.messages.create.side_effect = build_anthropic_router(None, sample_nutrition_estimates)

        ocr = OcrResult(text=ocr_receipt_text, confidence=93.0, word_count=40)
        with patch(f"{PIPELINE}.run_tesseract", return_value=ocr):
            response = _upload(client, real_receipt_image)
        assert response.status_code == 201, response.text

        events = _events(test_db)
        [run] = test_db.query(LlmWorkflowRun).all()
        assert run.workflow == "receipt_parse"
        assert run.status == "succeeded"
        assert {event.run_id for event in events} == {run.id}

        ocr_events = [event for event in events if event.step == "receipt_ocr"]
        [ocr_event] = ocr_events
        assert ocr_event.route == "ocr"
        assert ocr_event.provider == "local"
        assert ocr_event.status == "ok"
        assert ocr_event.confidence is not None and 0.0 < ocr_event.confidence <= 1.0
        assert ocr_event.estimated_cost_usd == 0.0
        assert ocr_event.latency_ms >= 0

        nutrition = [event for event in events if event.step == "nutrition_estimate"]
        assert len(nutrition) == 3
        assert {event.model for event in nutrition} == {RECEIPT_ANTHROPIC_MODEL}
        assert {event.route for event in nutrition} == {"opus"}
        assert {event.call_site for event in nutrition} == {"receipt.nutrition_estimate"}
        # No vision or text-cleanup LLM rung ran on the OCR-success path.
        assert not [event for event in events if event.step in {"receipt_scan", "ocr_text_cleanup"}]

        summary = client.get("/api/metrics/llm/summary", headers=auth_headers).json()
        receipt = next(row for row in summary["workflows"] if row["workflow"] == "receipt_parse")
        routes = {row["route"]: row for row in receipt["routes"]}
        assert set(routes) == {"ocr", "opus"}
        assert routes["ocr"]["calls"] == 1
        assert receipt["successful_runs"] == 1
        assert receipt["vision"]["image_count"] == 0

    @patch("app.services.receipt_analyzer.anthropic.Anthropic")
    def test_cache_hit_records_zero_cost_cache_step(
        self, mock_anthropic_class, ocr_first_env, client, test_db, auth_headers,
        real_receipt_image, ocr_receipt_text, sample_nutrition_estimates,
    ):
        mock_client = Mock()
        mock_anthropic_class.return_value = mock_client
        mock_client.messages.create.side_effect = build_anthropic_router(None, sample_nutrition_estimates)
        ocr = OcrResult(text=ocr_receipt_text, confidence=93.0)

        with patch.object(settings, "receipt_analysis_cache", True), \
             patch(f"{PIPELINE}.run_tesseract", return_value=ocr):
            first = _upload(client, real_receipt_image, name="first.png")
            assert first.status_code == 201, first.text
            second = _upload(client, real_receipt_image, name="second.png")
        assert second.status_code == 201, second.text

        runs = {run.receipt_id: run for run in test_db.query(LlmWorkflowRun).all()}
        assert len(runs) == 2
        second_run = runs[second.json()["id"]]
        assert second_run.status == "succeeded"

        second_events = [event for event in _events(test_db) if event.run_id == second_run.id]
        [cache_event] = second_events
        assert cache_event.step == "receipt_cache_hit"
        assert cache_event.route == "cache"
        assert cache_event.confidence == 1.0
        assert cache_event.estimated_cost_usd == 0.0
        assert cache_event.uncached_input_tokens == 0

        summary = client.get("/api/metrics/llm/summary", headers=auth_headers).json()
        receipt = next(row for row in summary["workflows"] if row["workflow"] == "receipt_parse")
        assert receipt["successful_runs"] == 2
        routes = {row["route"] for row in receipt["routes"]}
        assert routes == {"cache", "ocr", "opus"}

    @patch("app.services.receipt_analyzer.anthropic.Anthropic")
    def test_soft_gate_failure_shows_ocr_then_haiku_escalation(
        self, mock_anthropic_class, ocr_first_env, client, test_db, auth_headers,
        real_receipt_image, sample_receipt_response, sample_nutrition_estimates,
    ):
        mock_client = Mock()
        mock_anthropic_class.return_value = mock_client
        mock_client.messages.create.side_effect = build_anthropic_router(
            None, sample_nutrition_estimates, text_response=sample_receipt_response
        )

        garbage = OcrResult(text="~~~ !! ##\n1lI|\nMLK 3.49\n", confidence=21.0)
        with patch(f"{PIPELINE}.run_tesseract", return_value=garbage):
            response = _upload(client, real_receipt_image)
        assert response.status_code == 201, response.text

        events = _events(test_db)
        steps = [(event.step, event.route) for event in events if event.step != "nutrition_estimate"]
        assert steps == [("receipt_ocr", "ocr"), ("ocr_text_cleanup", "haiku")]
        ocr_event, haiku_event = (events[0], events[1])
        assert ocr_event.status == "ok"  # the step ran; the gate (not the step) failed
        assert ocr_event.confidence is not None and ocr_event.confidence < 0.5
        assert haiku_event.call_site == "receipt.ocr_text_cleanup"
        assert haiku_event.workflow == "receipt_parse"
        assert haiku_event.model == RECEIPT_OCR_CLEANUP_MODEL
        assert haiku_event.pricing_known is True

        summary = client.get("/api/metrics/llm/summary", headers=auth_headers).json()
        receipt = next(row for row in summary["workflows"] if row["workflow"] == "receipt_parse")
        # Haiku cleanup + Opus nutrition inside one run → counted as an escalation.
        assert receipt["escalation_rate_pct"] == 100.0
        assert {row["route"] for row in receipt["routes"]} == {"ocr", "haiku", "opus"}

    @patch("app.services.receipt_analyzer.anthropic.Anthropic")
    def test_ocr_unavailable_records_error_status_then_sonnet(
        self, mock_anthropic_class, ocr_first_env, client, test_db, auth_headers,
        real_receipt_image, sample_receipt_response, sample_nutrition_estimates,
    ):
        mock_client = Mock()
        mock_anthropic_class.return_value = mock_client
        mock_client.messages.create.side_effect = build_anthropic_router(
            sample_receipt_response, sample_nutrition_estimates
        )
        with patch(f"{PIPELINE}.run_tesseract", side_effect=OcrUnavailableError("no binary")):
            response = _upload(client, real_receipt_image)
        assert response.status_code == 201, response.text

        events = _events(test_db)
        ocr_event = next(event for event in events if event.step == "receipt_ocr")
        assert ocr_event.status == "error"
        assert ocr_event.error_type == "OcrUnavailableError"
        assert ocr_event.route == "ocr"
        assert ocr_event.confidence == 0.0
        assert not [event for event in events if event.step == "ocr_text_cleanup"]
        [scan] = [event for event in events if event.step == "receipt_scan"]
        assert scan.call_site == "receipt.analyze_image"
        assert scan.status == "ok"
