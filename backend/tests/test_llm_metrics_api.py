"""Cost dashboard API: persistence, 7-day aggregates, access control."""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
import pytest
from sqlalchemy.orm import Session

from app.config import settings
from app.llm_usage import set_sinks
from app.llm_usage.anthropic_admin import (
    clear_cache,
    reconciliation_report,
    summarize_cost_buckets,
    summarize_usage_buckets,
)
from app.llm_usage.recorder import DatabaseSink
from app.models import LlmUsageEvent, LlmWorkflowRun, User


@pytest.fixture(autouse=True)
def db_only_sink():
    """Persist to the SQLite test DB and skip stdout logging for these tests."""
    set_sinks([DatabaseSink()])
    try:
        yield
    finally:
        set_sinks(None)


def _response(text: str, *, input_tokens: int, output_tokens: int, cache_read: int = 0):
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=cache_read,
        ),
        stop_reason="end_turn",
        model="claude-opus-5",
        _request_id="req_x",
    )


def _seed_event(db: Session, **overrides) -> LlmUsageEvent:
    defaults = dict(
        workflow="meal_gen",
        step="meal_generate",
        model="claude-sonnet-5",
        status="ok",
        latency_ms=1000,
        attempt=1,
        uncached_input_tokens=1000,
        output_tokens=500,
        estimated_cost_usd=0.007,
        pricing_known=True,
    )
    defaults.update(overrides)
    event = LlmUsageEvent(**defaults)
    db.add(event)
    db.commit()
    return event


def _seed_run(db: Session, run_id: str, workflow: str, status: str, **overrides) -> LlmWorkflowRun:
    run = LlmWorkflowRun(id=run_id, workflow=workflow, status=status, **overrides)
    db.add(run)
    db.commit()
    return run


class TestPersistenceThroughProductFlows:
    @patch("app.services.receipt_analyzer.anthropic.Anthropic")
    @patch("app.services.receipt_analyzer.settings.anthropic_api_key", "test-api-key")
    @patch("app.config.settings.anthropic_api_key", "test-api-key")
    def test_receipt_upload_persists_events_and_run(
        self, mock_anthropic, client, test_db, test_user, auth_headers, mock_receipt_image, tmp_path
    ):
        scan = {
            "store_name": "Shop",
            "items": [
                {"store_item_name": "BANANA", "ingredient_name": "Banana", "is_food": True, "quantity": "1", "unit": "each"},
                {"store_item_name": "TAX", "ingredient_name": "Tax", "is_food": False},
            ],
        }
        nutrition = {"recognized": True, "quantity": "1", "unit": "each", "calories": 100}
        mock_client = Mock()
        mock_client.messages.create.side_effect = [
            _response(json.dumps(scan), input_tokens=3000, output_tokens=400),
            _response(json.dumps(nutrition), input_tokens=500, output_tokens=150),
        ]
        mock_anthropic.return_value = mock_client

        with patch.object(settings, "upload_dir", str(tmp_path / "uploads")):
            with open(mock_receipt_image, "rb") as handle:
                response = client.post(
                    "/api/receipts/upload",
                    files={"file": ("receipt.jpg", handle, "image/jpeg")},
                    data={"manual_items": "[]"},
                    headers=auth_headers,
                )
        assert response.status_code == 201
        receipt_id = response.json()["id"]

        events = test_db.query(LlmUsageEvent).order_by(LlmUsageEvent.created_at).all()
        assert [event.step for event in events] == ["receipt_scan", "nutrition_estimate"]
        assert all(event.workflow == "receipt_parse" for event in events)
        assert all(event.user_id == test_user.id for event in events)
        assert all(event.receipt_id == receipt_id for event in events)
        assert events[0].image_count == 1
        assert events[0].uncached_input_tokens == 3000
        assert events[0].estimated_cost_usd == pytest.approx((3000 * 5 + 400 * 25) / 1e6)

        [run] = test_db.query(LlmWorkflowRun).all()
        assert run.workflow == "receipt_parse"
        assert run.status == "succeeded"
        assert run.receipt_id == receipt_id
        assert {event.run_id for event in events} == {run.id}

        summary = client.get("/api/metrics/llm/summary?days=7").json()
        receipt_row = next(row for row in summary["workflows"] if row["workflow"] == "receipt_parse")
        assert receipt_row["calls"] == 2
        assert receipt_row["successful_runs"] == 1
        total_cost = sum(event.estimated_cost_usd for event in events)
        assert receipt_row["cost_per_successful_run_usd"] == pytest.approx(total_cost, abs=1e-6)
        assert receipt_row["input_tokens_per_successful_run"] == 3500
        assert receipt_row["output_tokens_per_successful_run"] == 550
        assert receipt_row["vision"]["image_count"] == 1

    @patch("app.services.receipt_analyzer.anthropic.Anthropic")
    @patch("app.services.receipt_analyzer.settings.anthropic_api_key", "test-api-key")
    @patch("app.config.settings.anthropic_api_key", "test-api-key")
    def test_failed_receipt_parse_is_a_failed_run(
        self, mock_anthropic, client, test_db, auth_headers, mock_receipt_image, tmp_path
    ):
        mock_client = Mock()
        mock_client.messages.create.side_effect = [
            _response(json.dumps({"store_name": None, "items": []}), input_tokens=1000, output_tokens=20),
        ]
        mock_anthropic.return_value = mock_client
        with patch.object(settings, "upload_dir", str(tmp_path / "uploads")):
            with open(mock_receipt_image, "rb") as handle:
                response = client.post(
                    "/api/receipts/upload",
                    files={"file": ("receipt.jpg", handle, "image/jpeg")},
                    data={"manual_items": "[]"},
                    headers=auth_headers,
                )
        assert response.status_code == 422

        [run] = test_db.query(LlmWorkflowRun).all()
        assert run.status == "failed"
        assert run.error_type == "ReceiptAnalysisError"
        assert test_db.query(LlmUsageEvent).count() == 1

    @patch("app.services.receipt_analyzer._get_client")
    def test_manual_ingredient_is_one_normalize_run(self, mock_get_client, client, test_db, test_user, auth_headers):
        mock_client = Mock()
        mock_client.messages.create.side_effect = [
            _response(json.dumps({"unit_plausible": True, "unit_warning": None}), input_tokens=300, output_tokens=20),
            _response(json.dumps({"recognized": True, "quantity": "2", "unit": "lb", "calories": 90}), input_tokens=600, output_tokens=200),
            _response(json.dumps({"match_id": None, "ambiguous": False, "canonical_name": "Apple"}), input_tokens=350, output_tokens=30),
        ]
        mock_get_client.return_value = mock_client

        response = client.post(
            "/api/ingredients/manual",
            json={"ingredient_name": "apples", "quantity": "2", "unit": "lb"},
            headers=auth_headers,
        )
        assert response.status_code == 201

        events = test_db.query(LlmUsageEvent).order_by(LlmUsageEvent.created_at).all()
        assert [event.step for event in events] == ["unit_check", "nutrition_estimate", "pantry_match"]
        assert all(event.workflow == "ingredient_normalize" for event in events)
        [run] = test_db.query(LlmWorkflowRun).all()
        assert run.workflow == "ingredient_normalize"
        assert run.status == "succeeded"
        assert run.user_id == test_user.id

    @patch("app.services.receipt_analyzer._get_client")
    def test_live_unit_check_counts_spend_but_not_a_run(self, mock_get_client, client, test_db, auth_headers):
        mock_client = Mock()
        mock_client.messages.create.return_value = _response(
            json.dumps({"unit_plausible": False, "unit_warning": "Use lb."}), input_tokens=300, output_tokens=20
        )
        mock_get_client.return_value = mock_client

        response = client.post(
            "/api/ingredients/unit-check",
            json={"ingredient_name": "watermelon", "unit": "gallon"},
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["warning"] == "Use lb."

        [event] = test_db.query(LlmUsageEvent).all()
        assert event.workflow == "ingredient_normalize"
        assert event.step == "unit_check"
        assert event.run_id is None
        assert test_db.query(LlmWorkflowRun).count() == 0

        summary = client.get("/api/metrics/llm/summary").json()
        row = next(r for r in summary["workflows"] if r["workflow"] == "ingredient_normalize")
        assert row["calls"] == 1
        assert row["runs"] == 0
        assert row["cost_outside_runs_usd"] == pytest.approx(event.estimated_cost_usd, abs=1e-9)
        assert row["cost_per_successful_run_usd"] is None


class TestSevenDayAggregates:
    def test_answers_cost_and_tokens_per_successful_workflow(self, client, test_db, auth_headers):
        # meal_gen: two runs, one succeeded (with a retry), one failed.
        _seed_run(test_db, "run-ok", "meal_gen", "succeeded")
        _seed_run(test_db, "run-fail", "meal_gen", "failed", error_type="MealGenerationError")
        _seed_event(test_db, run_id="run-ok", attempt=1, uncached_input_tokens=2000, output_tokens=600, estimated_cost_usd=0.010)
        _seed_event(test_db, run_id="run-ok", attempt=2, uncached_input_tokens=2500, output_tokens=700, estimated_cost_usd=0.012)
        _seed_event(test_db, run_id="run-fail", attempt=1, status="error", error_type="APIError", uncached_input_tokens=0, output_tokens=0, estimated_cost_usd=0.0)
        # meal_gen image prompt: spend without a run.
        _seed_event(test_db, step="image_prompt", uncached_input_tokens=200, output_tokens=60, estimated_cost_usd=0.001)
        # receipt_parse: one successful run with vision + cache reads and an Opus model.
        _seed_run(test_db, "run-receipt", "receipt_parse", "succeeded")
        _seed_event(
            test_db, workflow="receipt_parse", step="receipt_scan", model="claude-opus-5", run_id="run-receipt",
            uncached_input_tokens=1000, cache_read_tokens=1000, output_tokens=400, image_count=1,
            approx_visual_tokens=1500, estimated_cost_usd=0.02,
        )
        # Stale event outside the window must be ignored.
        _seed_event(test_db, created_at=datetime.now(UTC) - timedelta(days=10), estimated_cost_usd=99.0)

        summary = client.get("/api/metrics/llm/summary?days=7", headers=auth_headers).json()

        assert summary["window"]["days"] == 7
        totals = summary["totals"]
        assert totals["calls"] == 5
        assert totals["estimated_cost_usd"] == pytest.approx(0.043)
        assert totals["tokens"]["cache_read"] == 1000
        assert totals["tokens"]["total_input"] == 2000 + 2500 + 200 + 2000
        assert totals["cache_read_pct"] == pytest.approx(100 * 1000 / 6700, abs=0.01)
        assert totals["vision"]["approx_visual_tokens"] == 1500
        assert totals["vision"]["vision_share_pct"] == pytest.approx(100 * 1500 / 6700, abs=0.01)

        by_workflow = {row["workflow"]: row for row in summary["workflows"]}
        assert [row["workflow"] for row in summary["workflows"]][:3] == ["receipt_parse", "ingredient_normalize", "meal_gen"]

        meal = by_workflow["meal_gen"]
        assert meal["runs"] == 2
        assert meal["successful_runs"] == 1
        assert meal["failed_runs"] == 1
        assert meal["run_success_rate_pct"] == 50.0
        assert meal["cost_per_successful_run_usd"] == pytest.approx(0.022)
        assert meal["input_tokens_per_successful_run"] == 4500
        assert meal["output_tokens_per_successful_run"] == 1300
        assert meal["cost_outside_runs_usd"] == pytest.approx(0.001)
        assert meal["retry_rate_pct"] == 50.0
        assert meal["escalation_rate_pct"] == 0.0
        assert meal["error_calls"] == 1
        assert {step["step"] for step in meal["steps"]} == {"meal_generate", "image_prompt"}

        receipt = by_workflow["receipt_parse"]
        assert receipt["cost_per_successful_run_usd"] == pytest.approx(0.02)
        assert receipt["cache_read_pct"] == 50.0
        assert receipt["vision"]["vision_share_pct"] == 75.0
        assert receipt["models"][0]["model"] == "claude-opus-5"

        normalize = by_workflow["ingredient_normalize"]
        assert normalize["calls"] == 0
        assert normalize["cost_per_successful_run_usd"] is None

        models = {row["model"]: row for row in summary["models"]}
        assert set(models) == {"claude-sonnet-5", "claude-opus-5"}
        assert models["claude-opus-5"]["share_of_cost_pct"] == pytest.approx(100 * 0.02 / 0.043, abs=0.01)

        assert len(summary["daily"]) in (7, 8)
        today = datetime.now(UTC).date().isoformat()
        today_row = next(row for row in summary["daily"] if row["date"] == today)
        assert today_row["calls"] == 5
        assert today_row["by_workflow"]["receipt_parse"] == pytest.approx(0.02)

    def test_route_share_and_confidence_are_exposed(self, client, test_db, auth_headers):
        """FOOD-55 schema: ocr/cache steps sit next to Claude calls in the same run."""
        _seed_run(test_db, "r-ocr", "receipt_parse", "succeeded")
        _seed_event(test_db, workflow="receipt_parse", step="receipt_ocr", run_id="r-ocr", route="ocr",
                    provider="local", model="ocr", confidence=0.91, uncached_input_tokens=0, output_tokens=0,
                    estimated_cost_usd=0.0)
        _seed_event(test_db, workflow="receipt_parse", step="receipt_scan", run_id="r-ocr", route="haiku",
                    model="claude-haiku-4-5", estimated_cost_usd=0.002)
        _seed_event(test_db, workflow="receipt_parse", step="receipt_scan", run_id="r-ocr", route="opus",
                    model="claude-opus-5", estimated_cost_usd=0.02)

        summary = client.get("/api/metrics/llm/summary", headers=auth_headers).json()
        receipt = next(row for row in summary["workflows"] if row["workflow"] == "receipt_parse")
        routes = {row["route"]: row for row in receipt["routes"]}
        assert set(routes) == {"ocr", "haiku", "opus"}
        assert routes["ocr"]["share_of_calls_pct"] == pytest.approx(33.33, abs=0.01)
        assert routes["opus"]["estimated_cost_usd"] == pytest.approx(0.02)
        assert receipt["escalation_rate_pct"] == 100.0  # haiku -> opus within one run
        assert receipt["cost_per_successful_run_usd"] == pytest.approx(0.022)

        events = client.get("/api/metrics/llm/events?workflow=receipt_parse", headers=auth_headers).json()["events"]
        ocr_event = next(event for event in events if event["step"] == "receipt_ocr")
        assert ocr_event["route"] == "ocr"
        assert ocr_event["confidence"] == 0.91
        assert ocr_event["provider"] == "local"

    def test_escalation_rate_counts_runs_with_multiple_models(self, client, test_db, auth_headers):
        _seed_run(test_db, "r1", "meal_gen", "succeeded")
        _seed_event(test_db, run_id="r1", model="claude-haiku-4-5")
        _seed_event(test_db, run_id="r1", model="claude-sonnet-5")
        summary = client.get("/api/metrics/llm/summary", headers=auth_headers).json()
        meal = next(row for row in summary["workflows"] if row["workflow"] == "meal_gen")
        assert meal["escalation_rate_pct"] == 100.0
        assert meal["retry_rate_pct"] == 0.0
        assert meal["calls_per_run"] == 2.0

    def test_recent_events_endpoint_filters(self, client, test_db, auth_headers):
        _seed_event(test_db, workflow="meal_gen", status="ok")
        _seed_event(test_db, workflow="receipt_parse", step="receipt_scan", status="error", error_type="APIError")

        payload = client.get("/api/metrics/llm/events?limit=10", headers=auth_headers).json()
        assert len(payload["events"]) == 2
        assert payload["workflows"] == ["receipt_parse", "ingredient_normalize", "meal_gen"]
        required = {
            "workflow", "step", "model", "uncached_input_tokens", "cache_write_5m_tokens",
            "cache_write_1h_tokens", "cache_read_tokens", "output_tokens", "image_count",
            "approx_visual_tokens", "stop_reason", "latency_ms", "estimated_cost_usd",
        }
        assert required <= set(payload["events"][0])

        errors = client.get("/api/metrics/llm/events?status=error", headers=auth_headers).json()["events"]
        assert [event["workflow"] for event in errors] == ["receipt_parse"]

        meals = client.get("/api/metrics/llm/events?workflow=meal_gen", headers=auth_headers).json()["events"]
        assert [event["workflow"] for event in meals] == ["meal_gen"]

    def test_pricing_endpoint_lists_active_table(self, client, auth_headers):
        payload = client.get("/api/metrics/llm/pricing", headers=auth_headers).json()
        assert payload["claude-sonnet-5"] == {
            "input": 2.0, "output": 10.0, "cache_write_5m": 2.5, "cache_write_1h": 4.0, "cache_read": 0.2,
        }


class TestAccessControl:
    def test_unauthenticated_is_401(self, client):
        assert client.get("/api/metrics/llm/summary").status_code == 401

    def test_dev_mode_allows_any_signed_in_user(self, client, auth_headers):
        with patch.object(settings, "environment", "development"), patch.object(settings, "admin_emails", ""), patch.object(settings, "metrics_api_token", ""):
            assert client.get("/api/metrics/llm/summary", headers=auth_headers).status_code == 200

    def test_production_without_allowlist_is_403(self, client, auth_headers):
        with patch.object(settings, "environment", "production"), patch.object(settings, "admin_emails", ""), patch.object(settings, "metrics_api_token", ""):
            assert client.get("/api/metrics/llm/summary", headers=auth_headers).status_code == 403

    def test_admin_email_allowlist(self, client, test_db, auth_headers):
        with patch.object(settings, "environment", "production"), patch.object(settings, "admin_emails", "ops@example.com"):
            assert client.get("/api/metrics/llm/summary", headers=auth_headers).status_code == 403
        with patch.object(settings, "environment", "production"), patch.object(settings, "admin_emails", "Test@Example.com, ops@example.com"):
            assert client.get("/api/metrics/llm/summary", headers=auth_headers).status_code == 200

    def test_bearer_token_grants_access_without_session(self, client):
        with patch.object(settings, "environment", "production"), patch.object(settings, "metrics_api_token", "s3cret"):
            assert client.get("/api/metrics/llm/summary", headers={"Authorization": "Bearer s3cret"}).status_code == 200
            assert client.get("/api/metrics/llm/summary", headers={"Authorization": "Bearer wrong"}).status_code == 403

    def test_configured_token_or_allowlist_disables_dev_open_access(self, client, auth_headers):
        with patch.object(settings, "environment", "development"), patch.object(settings, "metrics_api_token", "s3cret"), patch.object(settings, "admin_emails", ""):
            assert client.get("/api/metrics/llm/summary", headers=auth_headers).status_code == 403


class TestAnthropicAdminReconciliation:
    def test_not_configured(self, client, auth_headers):
        clear_cache()
        with patch.object(settings, "anthropic_admin_api_key", ""):
            payload = client.get("/api/metrics/llm/anthropic", headers=auth_headers).json()
        assert payload["configured"] is False

    def test_summarizes_usage_buckets_by_model_tier_workspace(self):
        buckets = [
            {
                "starting_at": "2026-09-10T00:00:00Z",
                "results": [
                    {
                        "model": "claude-opus-5", "service_tier": "standard", "workspace_id": None,
                        "uncached_input_tokens": 1500, "cache_read_input_tokens": 500, "output_tokens": 200,
                        "cache_creation": {"ephemeral_5m_input_tokens": 100, "ephemeral_1h_input_tokens": 50},
                    },
                    {
                        "model": "claude-sonnet-5", "service_tier": "batch", "workspace_id": "wrkspc_1",
                        "uncached_input_tokens": 1000, "cache_read_input_tokens": 0, "output_tokens": 100,
                        "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 0},
                    },
                ],
            }
        ]
        summary = summarize_usage_buckets(buckets)
        assert summary["tokens"]["uncached_input_tokens"] == 2500
        assert summary["tokens"]["cache_write_5m_tokens"] == 100
        assert summary["tokens"]["cache_write_1h_tokens"] == 50
        assert summary["tokens"]["total_input"] == 3150
        assert summary["cache_read_pct"] == pytest.approx(100 * 500 / 3150, abs=0.01)
        assert {(row["model"], row["service_tier"], row["workspace_id"]) for row in summary["rows"]} == {
            ("claude-opus-5", "standard", "default"),
            ("claude-sonnet-5", "batch", "wrkspc_1"),
        }

    def test_summarizes_cost_buckets_in_usd(self):
        buckets = [
            {
                "results": [
                    {"amount": "123.45", "currency": "USD", "model": "claude-opus-5", "workspace_id": None, "token_type": "uncached_input_tokens", "cost_type": "tokens"},
                    {"amount": "10", "currency": "USD", "model": "claude-opus-5", "workspace_id": None, "token_type": "output_tokens", "cost_type": "tokens"},
                    {"amount": "5", "currency": "USD", "description": "Web Search Usage", "cost_type": "web_search"},
                ]
            }
        ]
        summary = summarize_cost_buckets(buckets)
        assert summary["total_usd"] == pytest.approx(1.3845)
        assert summary["by_model"]["claude-opus-5"] == pytest.approx(1.3345)
        assert summary["by_token_type"]["web_search"] == pytest.approx(0.05)

    def test_report_calls_admin_api_with_grouping_and_paginates(self):
        clear_cache()
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            assert request.headers["x-api-key"] == "sk-ant-admin-test"
            assert request.headers["anthropic-version"] == "2023-06-01"
            params = request.url.params
            if request.url.path.endswith("/usage_report/messages"):
                assert params.get_list("group_by[]") == ["model", "service_tier", "workspace_id"]
                if params.get("page") is None:
                    return httpx.Response(200, json={"data": [{"results": [{"model": "claude-opus-5", "uncached_input_tokens": 10, "output_tokens": 1, "cache_read_input_tokens": 0, "cache_creation": {}}]}], "has_more": True, "next_page": "p2"})
                return httpx.Response(200, json={"data": [{"results": [{"model": "claude-opus-5", "uncached_input_tokens": 5, "output_tokens": 1, "cache_read_input_tokens": 0, "cache_creation": {}}]}], "has_more": False, "next_page": None})
            assert params.get_list("group_by[]") == ["workspace_id", "description"]
            return httpx.Response(200, json={"data": [{"results": [{"amount": "250", "model": "claude-opus-5"}]}], "has_more": False})

        transport = httpx.MockTransport(handler)
        with patch.object(settings, "anthropic_admin_api_key", "sk-ant-admin-test"):
            with httpx.Client(transport=transport) as http:
                report = reconciliation_report(7, client=http)

        assert report["configured"] is True
        assert report["usage"]["tokens"]["uncached_input_tokens"] == 15
        assert report["cost"]["total_usd"] == pytest.approx(2.5)
        assert len(calls) == 3
        clear_cache()

    def test_api_errors_are_reported_not_raised(self):
        clear_cache()
        transport = httpx.MockTransport(lambda request: httpx.Response(401, json={"error": {"message": "bad key"}}))
        with patch.object(settings, "anthropic_admin_api_key", "sk-ant-admin-test"):
            with httpx.Client(transport=transport) as http:
                report = reconciliation_report(7, client=http)
        assert report["configured"] is True
        assert "401" in report["error"]
        clear_cache()
