"""Claude call instrumentation hooks: attribution, recording, safety."""

import contextvars
import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from app.llm_usage import (
    WORKFLOW_INGREDIENT_NORMALIZE,
    WORKFLOW_MEAL_GEN,
    WORKFLOW_RECEIPT_PARSE,
    WORKFLOW_UNATTRIBUTED,
    create_message,
    current_run,
    set_sinks,
    workflow_scope,
)
from app.llm_usage.recorder import token_usage_from_response
from app.services import meal_image, receipt_analyzer


class MemorySink:
    def __init__(self) -> None:
        self.events = []
        self.runs_started = []
        self.runs_finished = []

    def record_event(self, event) -> None:
        self.events.append(event)

    def run_started(self, run) -> None:
        self.runs_started.append(run)

    def run_finished(self, run, *, status, error_type, duration_ms) -> None:
        self.runs_finished.append((run, status, error_type, duration_ms))


class ExplodingSink(MemorySink):
    def record_event(self, event) -> None:
        raise RuntimeError("sink is down")


@pytest.fixture
def sink():
    memory = MemorySink()
    set_sinks([memory])
    try:
        yield memory
    finally:
        set_sinks(None)


def fake_response(
    *,
    text: str = "{}",
    input_tokens: int = 1200,
    output_tokens: int = 80,
    cache_creation: int = 0,
    cache_read: int = 0,
    stop_reason: str = "end_turn",
    model: str = "claude-opus-5",
    detailed_cache: dict | None = None,
):
    usage = SimpleNamespace(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_creation_input_tokens=cache_creation,
        cache_read_input_tokens=cache_read,
    )
    if detailed_cache is not None:
        usage.cache_creation = SimpleNamespace(**detailed_cache)
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        usage=usage,
        stop_reason=stop_reason,
        model=model,
        _request_id="req_123",
    )


def fake_client(*responses):
    client = Mock()
    client.messages.create.side_effect = list(responses)
    return client


class TestCreateMessage:
    def test_records_every_required_field(self, sink):
        client = fake_client(fake_response(cache_creation=300, cache_read=500))

        with workflow_scope(WORKFLOW_RECEIPT_PARSE, user_id="u1", receipt_id="r1") as run:
            message = create_message(
                client,
                step="receipt_scan",
                model="claude-opus-5",
                max_tokens=4096,
                messages=[{"role": "user", "content": "hi"}],
            )

        assert message.stop_reason == "end_turn"
        client.messages.create.assert_called_once_with(
            model="claude-opus-5",
            max_tokens=4096,
            messages=[{"role": "user", "content": "hi"}],
        )

        [event] = sink.events
        assert event.workflow == WORKFLOW_RECEIPT_PARSE
        assert event.step == "receipt_scan"
        assert event.run_id == run.run_id
        assert event.model == "claude-opus-5"
        assert event.uncached_input_tokens == 1200
        assert event.cache_write_5m_tokens == 300
        assert event.cache_write_1h_tokens == 0
        assert event.cache_read_tokens == 500
        assert event.output_tokens == 80
        assert event.stop_reason == "end_turn"
        assert event.latency_ms >= 0
        assert event.status == "ok"
        assert event.attempt == 1
        assert event.max_tokens == 4096
        assert event.user_id == "u1"
        assert event.receipt_id == "r1"
        assert event.anthropic_request_id == "req_123"
        assert event.pricing_known is True
        expected = (1200 * 5 + 300 * 6.25 + 500 * 0.50 + 80 * 25) / 1e6
        assert event.estimated_cost_usd == pytest.approx(expected)

        assert [status for _, status, _, _ in sink.runs_finished] == ["succeeded"]

    def test_detailed_cache_breakdown_is_preferred(self, sink):
        response = fake_response(
            cache_creation=999,
            detailed_cache={"ephemeral_5m_input_tokens": 100, "ephemeral_1h_input_tokens": 250},
        )
        usage = token_usage_from_response(response)
        assert usage.cache_write_5m_tokens == 100
        assert usage.cache_write_1h_tokens == 250

    def test_total_cache_write_attributed_to_1h_when_requested(self):
        response = fake_response(cache_creation=400)
        kwargs = {
            "system": [{"type": "text", "text": "sys", "cache_control": {"type": "ephemeral", "ttl": "1h"}}],
            "messages": [],
        }
        usage = token_usage_from_response(response, kwargs)
        assert usage.cache_write_1h_tokens == 400
        assert usage.cache_write_5m_tokens == 0

    def test_counts_images_and_visual_tokens(self, sink):
        import base64
        import struct

        png = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", 1000, 1000) + b"\x00" * 30
        client = fake_client(fake_response())
        with workflow_scope(WORKFLOW_RECEIPT_PARSE):
            create_message(
                client,
                step="receipt_scan",
                model="claude-opus-5",
                max_tokens=10,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": base64.b64encode(png).decode()}},
                            {"type": "text", "text": "Analyze"},
                        ],
                    }
                ],
            )
        [event] = sink.events
        assert event.image_count == 1
        assert event.approx_visual_tokens == 1296

    def test_tolerates_mock_responses_without_usage(self, sink):
        """Existing tests use bare Mock() responses; instrumentation must cope."""
        client = fake_client(Mock())
        with workflow_scope(WORKFLOW_MEAL_GEN):
            create_message(client, step="meal_generate", model="claude-sonnet-5", max_tokens=1, messages=[])
        [event] = sink.events
        assert event.uncached_input_tokens == 0
        assert event.output_tokens == 0
        assert event.stop_reason is None
        assert event.estimated_cost_usd == 0.0
        assert event.status == "ok"

    def test_error_is_recorded_and_reraised(self, sink):
        client = Mock()
        client.messages.create.side_effect = ValueError("boom")
        with pytest.raises(ValueError, match="boom"):
            with workflow_scope(WORKFLOW_MEAL_GEN):
                create_message(client, step="meal_generate", model="claude-sonnet-5", max_tokens=1, messages=[])
        [event] = sink.events
        assert event.status == "error"
        assert event.error_type == "ValueError"
        assert event.estimated_cost_usd == 0.0
        assert sink.runs_finished[0][1] == "failed"
        assert sink.runs_finished[0][2] == "ValueError"

    def test_sink_failures_never_reach_the_caller(self):
        set_sinks([ExplodingSink()])
        try:
            client = fake_client(fake_response(text="ok"))
            with workflow_scope(WORKFLOW_MEAL_GEN):
                message = create_message(client, step="meal_generate", model="claude-sonnet-5", max_tokens=1, messages=[])
            assert message.content[0].text == "ok"
        finally:
            set_sinks(None)

    def test_call_outside_scope_is_unattributed_not_dropped(self, sink):
        client = fake_client(fake_response())
        create_message(client, step="adhoc", model="claude-sonnet-5", max_tokens=1, messages=[])
        [event] = sink.events
        assert event.workflow == WORKFLOW_UNATTRIBUTED
        assert event.run_id is None

    def test_explicit_attempt_is_recorded(self, sink):
        client = fake_client(fake_response(), fake_response())
        with workflow_scope(WORKFLOW_MEAL_GEN):
            create_message(client, step="meal_generate", attempt=1, model="claude-sonnet-5", max_tokens=1, messages=[])
            create_message(client, step="meal_generate", attempt=2, model="claude-sonnet-5", max_tokens=1, messages=[])
        assert [event.attempt for event in sink.events] == [1, 2]


class TestWorkflowScope:
    def test_nested_same_workflow_reuses_run(self, sink):
        with workflow_scope(WORKFLOW_INGREDIENT_NORMALIZE, user_id="u1") as outer:
            with workflow_scope(WORKFLOW_INGREDIENT_NORMALIZE, receipt_id="r9") as inner:
                assert inner is outer
                assert current_run().receipt_id == "r9"
        assert len(sink.runs_started) == 1
        assert len(sink.runs_finished) == 1

    def test_run_less_scope_tags_without_recording_run(self, sink):
        client = fake_client(fake_response())
        with workflow_scope(WORKFLOW_MEAL_GEN, meal_id="m1", record_run=False):
            create_message(client, step="image_prompt", model="claude-sonnet-5", max_tokens=1, messages=[])
        assert sink.runs_started == []
        [event] = sink.events
        assert event.workflow == WORKFLOW_MEAL_GEN
        assert event.run_id is None
        assert event.meal_id == "m1"

    def test_recorded_scope_inside_run_less_scope_creates_run(self, sink):
        with workflow_scope(WORKFLOW_INGREDIENT_NORMALIZE, record_run=False):
            with workflow_scope(WORKFLOW_INGREDIENT_NORMALIZE) as inner:
                assert inner.recorded is True
        assert len(sink.runs_started) == 1

    def test_scope_is_restored_after_exit(self, sink):
        with workflow_scope(WORKFLOW_RECEIPT_PARSE):
            pass
        assert current_run() is None

    def test_context_propagates_to_copied_thread_context(self, sink):
        client = fake_client(fake_response(), fake_response())

        def call():
            create_message(client, step="nutrition_estimate", model="claude-opus-5", max_tokens=1, messages=[])

        with workflow_scope(WORKFLOW_RECEIPT_PARSE, receipt_id="r1") as run:
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(contextvars.copy_context().run, call) for _ in range(2)]
                for future in futures:
                    future.result()

        assert {event.run_id for event in sink.events} == {run.run_id}
        assert all(event.receipt_id == "r1" for event in sink.events)


class TestCallSitesAreInstrumented:
    """Every production Claude path goes through create_message."""

    @patch("app.services.receipt_analyzer._get_client")
    def test_unit_check_and_pantry_match_and_nutrition(self, mock_get_client, sink):
        mock_get_client.return_value = fake_client(
            fake_response(text=json.dumps({"unit_plausible": True, "unit_warning": None})),
            fake_response(text=json.dumps({"match_id": None, "ambiguous": False, "canonical_name": "Banana"})),
            fake_response(text=json.dumps({"recognized": True, "calories": 100, "quantity": "1", "unit": "each"})),
        )
        with workflow_scope(WORKFLOW_INGREDIENT_NORMALIZE):
            receipt_analyzer.check_ingredient_unit("banana", "each")
            receipt_analyzer.match_ingredient_to_pantry("banana", "each", [])
            receipt_analyzer.estimate_ingredient_nutrition("banana", "1", "each")

        assert [event.step for event in sink.events] == ["unit_check", "pantry_match", "nutrition_estimate"]
        assert all(event.workflow == WORKFLOW_INGREDIENT_NORMALIZE for event in sink.events)
        assert all(event.model == "claude-opus-5" for event in sink.events)

    @patch("app.services.receipt_analyzer.anthropic.Anthropic")
    @patch("app.services.receipt_analyzer.settings.anthropic_api_key", "test-key")
    def test_receipt_scan_records_image_and_enrichment_calls(self, mock_anthropic, sink, tmp_path):
        import struct

        png = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", 800, 600) + b"\x00" * 30
        path = tmp_path / "receipt.png"
        path.write_bytes(png)
        scan = {"store_name": "Shop", "items": [{"store_item_name": "BANANA", "ingredient_name": "Banana", "is_food": True, "quantity": "1", "unit": "each"}]}
        nutrition = {"recognized": True, "quantity": "1", "unit": "each", "calories": 100}
        mock_anthropic.return_value = fake_client(
            fake_response(text=json.dumps(scan), input_tokens=2000, output_tokens=300),
            fake_response(text=json.dumps(nutrition), input_tokens=400, output_tokens=120),
        )

        with workflow_scope(WORKFLOW_RECEIPT_PARSE, receipt_id="r1") as run:
            receipt_analyzer.analyze_receipt_image(path)

        steps = sorted(event.step for event in sink.events)
        assert steps == ["nutrition_estimate", "receipt_scan"]
        scan_event = next(event for event in sink.events if event.step == "receipt_scan")
        assert scan_event.image_count == 1
        assert scan_event.approx_visual_tokens == 29 * 22
        assert all(event.run_id == run.run_id for event in sink.events)

    @patch("app.services.meal_generator.anthropic.Anthropic")
    @patch("app.services.meal_generator.settings.anthropic_api_key", "test-key")
    def test_meal_generation_retries_carry_attempt_numbers(self, mock_anthropic, sink):
        from app.models import Ingredient

        pantry = [
            Ingredient(
                id="i1", user_id="u1", name="Rice", quantity="1000", unit="g",
                serving_size="100 g", servings_per_container=10, calories=130,
            )
        ]
        too_light = {"name": "Tiny", "description": "d", "ingredients_used": [{"name": "Rice", "amount": "10 g"}], "instructions": ["Cook."]}
        good = {"name": "Big Rice Bowl", "description": "d", "ingredients_used": [{"name": "Rice", "amount": "500 g"}], "instructions": ["Cook."]}
        mock_anthropic.return_value = fake_client(
            fake_response(text=json.dumps(too_light), model="claude-sonnet-5"),
            fake_response(text=json.dumps(good), model="claude-sonnet-5"),
        )

        from app.services.meal_generator import generate_meal_from_ingredients

        with workflow_scope(WORKFLOW_MEAL_GEN, user_id="u1"):
            meal = generate_meal_from_ingredients(pantry)

        assert meal.name == "Big Rice Bowl"
        assert [event.attempt for event in sink.events] == [1, 2]
        assert all(event.step == "meal_generate" for event in sink.events)
        assert all(event.model == "claude-sonnet-5" for event in sink.events)

    @patch("app.services.meal_image.anthropic.Anthropic")
    @patch("app.services.meal_image.settings.anthropic_api_key", "test-key")
    def test_image_prompt_call_is_recorded(self, mock_anthropic, sink):
        from app.models import Meal

        mock_anthropic.return_value = fake_client(fake_response(text="A bowl of rice", model="claude-sonnet-5"))
        meal = Meal(id="m1", user_id="u1", name="Rice Bowl", description="Tasty", ingredients_used="- Rice: 500 g")

        with workflow_scope(WORKFLOW_MEAL_GEN, meal_id="m1", record_run=False):
            prompt = meal_image._build_image_prompt(meal)

        assert prompt == "A bowl of rice"
        [event] = sink.events
        assert event.step == "image_prompt"
        assert event.workflow == WORKFLOW_MEAL_GEN
        assert event.meal_id == "m1"
