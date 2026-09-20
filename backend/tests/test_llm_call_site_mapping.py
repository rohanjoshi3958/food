"""call_site (LLM platform, FOOD-56) → workflow_id (FOOD-54) mapping."""

import inspect
import re

import pytest

from app.llm_usage import set_sinks
from app.services import meal_generator, meal_image, receipt_analyzer, receipt_pipeline
from app.services.anthropic_cache import (
    CALL_SITE_WORKFLOWS,
    create_cached_message,
    workflow_for_call_site,
)
from tests.test_llm_usage import MemorySink, fake_client, fake_response


@pytest.fixture
def sink():
    memory = MemorySink()
    set_sinks([memory])
    try:
        yield memory
    finally:
        set_sinks(None)


EXPECTED = {
    "receipt.analyze_image": "receipt_parse",
    "receipt.nutrition_estimate": "receipt_parse",
    "receipt.unit_check": "receipt_parse",
    "receipt.ocr_text_cleanup": "receipt_parse",
    "receipt.pantry_match": "ingredient_normalize",
    "meal.generate": "meal_gen",
    "meal.image_prompt": "meal_gen",
}


class TestMapping:
    def test_every_call_site_maps_to_agreed_workflow(self):
        assert {site: wf for site, (wf, _step) in CALL_SITE_WORKFLOWS.items()} == EXPECTED

    def test_every_call_site_string_in_source_is_mapped(self):
        """The strings the LLM platform uses must exist in the mapping verbatim."""
        found: set[str] = set()
        for module in (receipt_analyzer, meal_generator, meal_image, receipt_pipeline):
            found.update(re.findall(r'call_site="([^"]+)"', inspect.getsource(module)))
        assert found == set(EXPECTED)

    def test_unknown_call_site_falls_back_to_scope(self):
        assert workflow_for_call_site("future.thing") == (None, "future.thing")


class TestCreateCachedMessageRecordsUsage:
    def test_event_carries_call_site_workflow_and_step(self, sink):
        client = fake_client(fake_response(model="claude-opus-5", cache_read=400))
        create_cached_message(
            client,
            call_site="receipt.unit_check",
            model="claude-opus-5",
            max_tokens=256,
            system_prefix="rules",
            messages=[{"role": "user", "content": "- Item: banana\n- Unit: each"}],
        )
        [event] = sink.events
        assert event.call_site == "receipt.unit_check"
        assert event.workflow == "receipt_parse"
        assert event.step == "unit_check"
        assert event.route == "opus"
        assert event.cache_read_tokens == 400
        assert event.uncached_input_tokens == 1200

        kwargs = client.messages.create.call_args.kwargs
        assert kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}
        assert set(kwargs) == {"model", "max_tokens", "system", "messages"}

    def test_attempt_and_route_are_not_forwarded_to_the_api(self, sink):
        client = fake_client(fake_response(model="claude-sonnet-5"))
        create_cached_message(
            client,
            call_site="meal.generate",
            attempt=3,
            route="sonnet",
            model="claude-sonnet-5",
            max_tokens=10,
            system_prefix="chef",
            messages=[{"role": "user", "content": "pantry"}],
        )
        assert "attempt" not in client.messages.create.call_args.kwargs
        assert "route" not in client.messages.create.call_args.kwargs
        assert sink.events[0].attempt == 3
        assert sink.events[0].workflow == "meal_gen"

    def test_errors_are_recorded_and_propagate(self, sink):
        client = fake_client(RuntimeError("down"))
        with pytest.raises(RuntimeError):
            create_cached_message(
                client,
                call_site="meal.image_prompt",
                model="claude-sonnet-5",
                max_tokens=10,
                system_prefix="photo",
                messages=[{"role": "user", "content": "meal"}],
            )
        [event] = sink.events
        assert event.status == "error"
        assert event.call_site == "meal.image_prompt"
        assert event.workflow == "meal_gen"
