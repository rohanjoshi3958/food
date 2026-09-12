"""Prompt-cache breakpoint placement on every Claude call site (FOOD-56).

These tests mock the Anthropic client and inspect the exact kwargs sent to
``messages.create``. They pin two invariants:

1. The byte-stable prefix (rules + JSON schema) is the cached ``system`` block.
2. Per-request content (item names, pantry JSON, ingredient lists, images,
   conversation turns) is *not* in the cached block and lives in ``messages``.
"""

import json
import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.config import MEAL_ANTHROPIC_MODEL, RECEIPT_ANTHROPIC_MODEL
from app.models import Ingredient, Meal
from app.services import meal_generator, meal_image, receipt_analyzer
from app.services.anthropic_cache import (
    EPHEMERAL_CACHE_CONTROL,
    cached_system_prompt,
    cached_text_block,
    create_cached_message,
    extract_cache_usage,
    log_cache_usage,
)
from app.services.meal_generator import (
    FOLLOW_UP_PROMPT,
    MEAL_CALORIE_MAX,
    MEAL_CALORIE_MIN,
    MEAL_GENERATION_PROMPT,
    PreviousMealTurn,
    generate_meal_from_ingredients,
)
from app.services.meal_image import PROMPT_SYSTEM, _build_image_prompt
from app.services.receipt_analyzer import (
    NUTRITION_ESTIMATE_PROMPT,
    PANTRY_MATCH_PROMPT,
    RECEIPT_ANALYSIS_PROMPT,
    UNIT_CHECK_PROMPT,
    analyze_receipt_image,
    check_ingredient_unit,
    estimate_ingredient_nutrition,
    match_ingredient_to_pantry,
)
from tests.conftest import create_mock_anthropic_response

# Every stable prefix must expose the JSON response shape so a format change
# is an intentional (and visible) cache bust rather than a silent drift.
STABLE_PREFIXES = {
    "receipt_analysis": RECEIPT_ANALYSIS_PROMPT,
    "nutrition_estimate": NUTRITION_ESTIMATE_PROMPT,
    "unit_check": UNIT_CHECK_PROMPT,
    "pantry_match": PANTRY_MATCH_PROMPT,
    "meal_generation": MEAL_GENERATION_PROMPT.format(
        calorie_min=MEAL_CALORIE_MIN, calorie_max=MEAL_CALORIE_MAX
    ),
}


def _text_response(payload: dict | str):
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return create_mock_anthropic_response(text)


def _assert_single_cached_system(kwargs: dict, expected_text: str) -> None:
    system = kwargs["system"]
    assert isinstance(system, list), "system must be a list of content blocks"
    assert len(system) == 1, "exactly one breakpoint block expected"
    block = system[0]
    assert block["type"] == "text"
    assert block["cache_control"] == {"type": "ephemeral"}
    assert block["text"] == expected_text


def _assert_no_cache_control_in_messages(kwargs: dict) -> None:
    for message in kwargs["messages"]:
        content = message["content"]
        if isinstance(content, str):
            continue
        for block in content:
            assert "cache_control" not in block, (
                "variable content must not carry a cache breakpoint"
            )


def _messages_text(kwargs: dict) -> str:
    parts: list[str] = []
    for message in kwargs["messages"]:
        content = message["content"]
        if isinstance(content, str):
            parts.append(content)
            continue
        for block in content:
            if block.get("type") == "text":
                parts.append(block["text"])
            else:
                parts.append(json.dumps(block, sort_keys=True))
    return "\n".join(parts)


class TestAnthropicCacheHelpers:
    def test_cached_text_block_shape(self):
        block = cached_text_block("rules")
        assert block == {
            "type": "text",
            "text": "rules",
            "cache_control": {"type": "ephemeral"},
        }
        # Callers get their own copy; mutating it must not leak globally.
        block["cache_control"]["type"] = "mutated"
        assert EPHEMERAL_CACHE_CONTROL == {"type": "ephemeral"}

    def test_cached_system_prompt_is_single_block_list(self):
        system = cached_system_prompt("prefix")
        assert system == [cached_text_block("prefix")]

    def test_create_cached_message_passes_system_and_messages(self):
        client = MagicMock()
        client.messages.create.return_value = _text_response("ok")
        messages = [{"role": "user", "content": "tail"}]

        result = create_cached_message(
            client,
            call_site="test",
            model="model-x",
            max_tokens=12,
            system_prefix="prefix",
            messages=messages,
            temperature=0.2,
        )

        assert result is client.messages.create.return_value
        kwargs = client.messages.create.call_args.kwargs
        assert kwargs["model"] == "model-x"
        assert kwargs["max_tokens"] == 12
        assert kwargs["temperature"] == 0.2
        assert kwargs["messages"] is messages
        _assert_single_cached_system(kwargs, "prefix")

    def test_create_cached_message_propagates_errors(self):
        client = MagicMock()
        client.messages.create.side_effect = RuntimeError("boom")
        with pytest.raises(RuntimeError):
            create_cached_message(
                client,
                call_site="test",
                model="m",
                max_tokens=1,
                system_prefix="p",
                messages=[],
            )

    def test_extract_cache_usage_reads_sdk_fields(self):
        message = SimpleNamespace(
            usage=SimpleNamespace(
                input_tokens=10,
                output_tokens=4,
                cache_creation_input_tokens=900,
                cache_read_input_tokens=0,
            )
        )
        assert extract_cache_usage(message) == {
            "input_tokens": 10,
            "output_tokens": 4,
            "cache_creation_input_tokens": 900,
            "cache_read_input_tokens": 0,
        }

    def test_extract_cache_usage_tolerates_missing_or_mocked_usage(self):
        assert extract_cache_usage(SimpleNamespace()) == {
            "input_tokens": None,
            "output_tokens": None,
            "cache_creation_input_tokens": None,
            "cache_read_input_tokens": None,
        }
        # Mock() attributes are Mock objects, not ints; they must not crash
        # or be reported as numbers.
        assert all(v is None for v in extract_cache_usage(MagicMock()).values())

    def test_log_cache_usage_emits_counters(self, caplog):
        message = SimpleNamespace(
            usage=SimpleNamespace(
                input_tokens=1,
                output_tokens=2,
                cache_creation_input_tokens=0,
                cache_read_input_tokens=750,
            )
        )
        with caplog.at_level(logging.INFO, logger="app.services.anthropic_cache"):
            log_cache_usage(message, call_site="unit.test")

        record = caplog.records[-1].getMessage()
        assert "call_site=unit.test" in record
        assert "cache_read_input_tokens=750" in record
        assert "cache_creation_input_tokens=0" in record


class TestStablePrefixContents:
    @pytest.mark.parametrize("name", sorted(STABLE_PREFIXES))
    def test_prefix_contains_json_schema_and_no_format_placeholders(self, name):
        prefix = STABLE_PREFIXES[name]
        assert "Respond with ONLY" in prefix
        assert "{" in prefix and "}" in prefix
        # A leftover {placeholder} means per-request data would be formatted
        # into the cached block.
        for placeholder in (
            "{ingredient_name}",
            "{quantity}",
            "{unit}",
            "{pantry_json}",
            "{ingredients}",
            "{calorie_min}",
            "{calorie_max}",
        ):
            assert placeholder not in prefix

    def test_meal_prefix_pins_calorie_band(self):
        prefix = STABLE_PREFIXES["meal_generation"]
        assert f"{MEAL_CALORIE_MIN} and {MEAL_CALORIE_MAX} kcal" in prefix
        assert "Available ingredients:" not in prefix


class TestReceiptAnalyzerBreakpoints:
    @patch("app.services.receipt_analyzer._get_client")
    def test_unit_check(self, mock_get_client):
        create = mock_get_client.return_value.messages.create
        create.return_value = _text_response(
            {"unit_plausible": False, "unit_warning": "Use each or lb."}
        )

        assert check_ingredient_unit("Watermelon", "hogshead") == "Use each or lb."

        kwargs = create.call_args.kwargs
        assert kwargs["model"] == RECEIPT_ANTHROPIC_MODEL
        _assert_single_cached_system(kwargs, UNIT_CHECK_PROMPT)
        _assert_no_cache_control_in_messages(kwargs)
        assert "Watermelon" not in kwargs["system"][0]["text"]
        assert "hogshead" not in kwargs["system"][0]["text"]
        tail = _messages_text(kwargs)
        assert "- Item: Watermelon" in tail
        assert "- Unit: hogshead" in tail

    @patch("app.services.receipt_analyzer._get_client")
    def test_pantry_match(self, mock_get_client):
        create = mock_get_client.return_value.messages.create
        create.return_value = _text_response(
            {"match_id": "ing-1", "ambiguous": False, "canonical_name": "Sweet Potato"}
        )
        pantry = [{"id": "ing-1", "name": "Sweet Potato", "unit": "lb"}]

        result = match_ingredient_to_pantry("SWT PTATO", "lb", pantry)

        assert result.match_id == "ing-1"
        kwargs = create.call_args.kwargs
        _assert_single_cached_system(kwargs, PANTRY_MATCH_PROMPT)
        _assert_no_cache_control_in_messages(kwargs)
        system_text = kwargs["system"][0]["text"]
        assert "SWT PTATO" not in system_text
        assert "Sweet Potato" not in system_text
        assert "ing-1" not in system_text
        tail = _messages_text(kwargs)
        assert "- name: SWT PTATO" in tail
        assert json.dumps(pantry, ensure_ascii=True) in tail

    @patch("app.services.receipt_analyzer._get_client")
    def test_pantry_match_prefix_is_byte_stable_across_pantries(self, mock_get_client):
        create = mock_get_client.return_value.messages.create
        create.return_value = _text_response(
            {"match_id": None, "ambiguous": False, "canonical_name": "Tomato"}
        )

        match_ingredient_to_pantry("Tomatoes", "each", [])
        first = create.call_args.kwargs["system"]
        match_ingredient_to_pantry(
            "Bananas", "lb", [{"id": "x", "name": "Banana", "unit": "lb"}]
        )
        second = create.call_args.kwargs["system"]

        assert first == second

    @patch("app.services.receipt_analyzer._get_client")
    def test_nutrition_estimate(self, mock_get_client):
        create = mock_get_client.return_value.messages.create
        create.return_value = _text_response(
            {
                "recognized": True,
                "quantity": "1",
                "unit": "each",
                "serving_size": "2 tbsp",
                "servings_per_container": 15,
                "calories": 190,
            }
        )

        item = estimate_ingredient_nutrition("Almond Butter", "1", "each")

        assert item.calories == 190
        kwargs = create.call_args.kwargs
        _assert_single_cached_system(kwargs, NUTRITION_ESTIMATE_PROMPT)
        _assert_no_cache_control_in_messages(kwargs)
        assert "Almond Butter" not in kwargs["system"][0]["text"]
        tail = _messages_text(kwargs)
        assert "- Item: Almond Butter" in tail
        assert "- Quantity purchased: 1" in tail
        assert "- Unit: each" in tail

    @patch("app.services.receipt_analyzer._get_client")
    def test_nutrition_estimate_unknown_fields_stay_in_tail(self, mock_get_client):
        create = mock_get_client.return_value.messages.create
        create.return_value = _text_response({"recognized": True, "calories": 1})

        estimate_ingredient_nutrition("Bananas")

        kwargs = create.call_args.kwargs
        _assert_single_cached_system(kwargs, NUTRITION_ESTIMATE_PROMPT)
        tail = _messages_text(kwargs)
        assert "- Quantity purchased: unknown" in tail
        assert "- Unit: unknown" in tail

    @patch("app.services.receipt_analyzer.settings.anthropic_api_key", "test-key")
    @patch("app.services.receipt_analyzer._get_client")
    def test_analyze_receipt_image_keeps_image_after_breakpoint(
        self, mock_get_client, tmp_path
    ):
        create = mock_get_client.return_value.messages.create
        receipt_payload = {
            "store_name": "Market",
            "items": [
                {
                    "store_item_name": "BANANAS",
                    "ingredient_name": "Banana",
                    "is_food": True,
                    "quantity": "1",
                    "unit": "lb",
                }
            ],
        }
        create.return_value = _text_response(receipt_payload)
        receipt = tmp_path / "receipt.png"
        receipt.write_bytes(b"\x89PNG fake receipt bytes")

        with patch(
            "app.services.receipt_analyzer._enrich_receipt_nutrition",
            side_effect=lambda parsed: parsed,
        ):
            parsed = analyze_receipt_image(receipt)

        assert parsed.store_name == "Market"
        kwargs = create.call_args.kwargs
        assert kwargs["model"] == RECEIPT_ANTHROPIC_MODEL
        _assert_single_cached_system(kwargs, RECEIPT_ANALYSIS_PROMPT)
        _assert_no_cache_control_in_messages(kwargs)

        assert len(kwargs["messages"]) == 1
        content = kwargs["messages"][0]["content"]
        assert [block["type"] for block in content] == ["image"]
        assert content[0]["source"]["media_type"] == "image/png"
        assert content[0]["source"]["type"] == "base64"
        # The image must never be part of the cached prefix.
        assert content[0]["source"]["data"] not in kwargs["system"][0]["text"]

    @patch("app.services.receipt_analyzer.settings.anthropic_api_key", "test-key")
    @patch("app.services.receipt_analyzer._get_client")
    def test_analyze_receipt_pdf_uses_document_block(self, mock_get_client, tmp_path):
        create = mock_get_client.return_value.messages.create
        create.return_value = _text_response(
            {
                "store_name": None,
                "items": [
                    {"store_item_name": "MILK", "ingredient_name": "Milk", "is_food": True}
                ],
            }
        )
        receipt = tmp_path / "receipt.pdf"
        receipt.write_bytes(b"%PDF-1.4 fake")

        with patch(
            "app.services.receipt_analyzer._enrich_receipt_nutrition",
            side_effect=lambda parsed: parsed,
        ):
            analyze_receipt_image(receipt)

        kwargs = create.call_args.kwargs
        _assert_single_cached_system(kwargs, RECEIPT_ANALYSIS_PROMPT)
        content = kwargs["messages"][0]["content"]
        assert [block["type"] for block in content] == ["document"]
        assert content[0]["source"]["media_type"] == "application/pdf"


def _ingredient(name: str, **fields) -> Ingredient:
    defaults = {
        "id": f"id-{name.lower().replace(' ', '-')}",
        "user_id": "user-1",
        "quantity": "500",
        "unit": "g",
        "serving_size": "50 g",
        "servings_per_container": 10,
        "calories": 150,
    }
    defaults.update(fields)
    return Ingredient(name=name, **defaults)


def _meal_json(name: str = "Oat Bowl") -> str:
    return json.dumps(
        {
            "name": name,
            "description": "A bowl.",
            "ingredients_used": [{"name": "Rolled Oats", "amount": "100 g"}],
            "instructions": ["Cook oats.", "Serve."],
        }
    )


class TestMealGeneratorBreakpoints:
    def setup_method(self):
        self.pantry = [
            _ingredient("Rolled Oats"),
            _ingredient("Peanut Butter", unit="each", quantity="1", serving_size="2 tbsp",
                        servings_per_container=15, calories=190),
        ]

    @patch("app.services.meal_generator.settings.anthropic_api_key", "test-key")
    @patch("app.services.meal_generator._estimate_meal_calories", return_value=650)
    @patch("app.services.meal_generator.anthropic.Anthropic")
    def test_single_turn_places_ingredients_after_breakpoint(
        self, mock_anthropic, _mock_calories
    ):
        create = mock_anthropic.return_value.messages.create
        create.return_value = _text_response(_meal_json())

        meal = generate_meal_from_ingredients(self.pantry)

        assert meal.name == "Oat Bowl"
        assert create.call_count == 1
        kwargs = create.call_args.kwargs
        assert kwargs["model"] == MEAL_ANTHROPIC_MODEL
        _assert_single_cached_system(kwargs, STABLE_PREFIXES["meal_generation"])
        _assert_no_cache_control_in_messages(kwargs)

        system_text = kwargs["system"][0]["text"]
        assert "Rolled Oats" not in system_text
        assert "Peanut Butter" not in system_text
        assert "Available ingredients:" not in system_text

        assert len(kwargs["messages"]) == 1
        first_user = kwargs["messages"][0]
        assert first_user["role"] == "user"
        assert first_user["content"].startswith("Available ingredients:\n")
        assert "- Rolled Oats, maximum available: 500 g (do not exceed)" in first_user["content"]
        assert "- Peanut Butter" in first_user["content"]

    @patch("app.services.meal_generator.settings.anthropic_api_key", "test-key")
    @patch("app.services.meal_generator._estimate_meal_calories", return_value=650)
    @patch("app.services.meal_generator.anthropic.Anthropic")
    def test_follow_up_turns_stay_in_messages(self, mock_anthropic, _mock_calories):
        create = mock_anthropic.return_value.messages.create
        create.return_value = _text_response(_meal_json("Peanut Oat Bowl"))
        previous = PreviousMealTurn(
            name="Oat Bowl",
            description="A bowl.",
            ingredients_used="- Rolled Oats: 100 g",
            instructions="1. Cook oats.",
        )

        generate_meal_from_ingredients(self.pantry, previous_meal=previous)

        kwargs = create.call_args.kwargs
        _assert_single_cached_system(kwargs, STABLE_PREFIXES["meal_generation"])
        assert "Oat Bowl" not in kwargs["system"][0]["text"]

        roles = [message["role"] for message in kwargs["messages"]]
        assert roles == ["user", "assistant", "user"]
        assert kwargs["messages"][1]["content"] == json.dumps(
            {
                "name": "Oat Bowl",
                "description": "A bowl.",
                "ingredients_used": "- Rolled Oats: 100 g",
                "instructions": "1. Cook oats.",
            },
            ensure_ascii=True,
        )
        assert kwargs["messages"][2]["content"] == FOLLOW_UP_PROMPT.format(
            calorie_min=MEAL_CALORIE_MIN, calorie_max=MEAL_CALORIE_MAX
        )

    @patch("app.services.meal_generator.settings.anthropic_api_key", "test-key")
    @patch("app.services.meal_generator._estimate_meal_calories", side_effect=[300, 650])
    @patch("app.services.meal_generator.anthropic.Anthropic")
    def test_calorie_retry_keeps_prefix_identical_and_appends_turns(
        self, mock_anthropic, _mock_calories
    ):
        create = mock_anthropic.return_value.messages.create
        create.side_effect = [
            _text_response(_meal_json("Light Oats")),
            _text_response(_meal_json("Hearty Oats")),
        ]

        meal = generate_meal_from_ingredients(self.pantry)

        assert meal.name == "Hearty Oats"
        assert create.call_count == 2
        first, second = (call.kwargs for call in create.call_args_list)

        assert first["system"] == second["system"]
        _assert_single_cached_system(second, STABLE_PREFIXES["meal_generation"])

        # Retry conversation grows strictly after the breakpoint.
        assert [m["role"] for m in first["messages"]] == ["user"]
        assert [m["role"] for m in second["messages"]] == ["user", "assistant", "user"]
        assert second["messages"][0] == first["messages"][0]
        assert second["messages"][1]["content"] == _meal_json("Light Oats")
        assert "too light" in second["messages"][2]["content"]
        assert "300 kcal" in second["messages"][2]["content"]
        _assert_no_cache_control_in_messages(second)

    @patch("app.services.meal_generator.settings.anthropic_api_key", "test-key")
    @patch("app.services.meal_generator._estimate_meal_calories", return_value=650)
    @patch("app.services.meal_generator.anthropic.Anthropic")
    def test_prefix_is_byte_stable_across_pantries(self, mock_anthropic, _mock_calories):
        create = mock_anthropic.return_value.messages.create
        create.return_value = _text_response(_meal_json())

        generate_meal_from_ingredients([_ingredient("Rolled Oats")])
        first = create.call_args.kwargs["system"]
        generate_meal_from_ingredients([_ingredient("Black Beans"), _ingredient("Rice")])
        second = create.call_args.kwargs["system"]

        assert first == second


class TestMealImageBreakpoints:
    @patch("app.services.meal_image.settings.anthropic_api_key", "test-key")
    @patch("app.services.meal_image.anthropic.Anthropic")
    def test_system_prompt_is_cached_and_meal_fields_are_not(self, mock_anthropic):
        create = mock_anthropic.return_value.messages.create
        create.return_value = _text_response("Overhead shot of a hearty oat bowl.")
        meal = Meal(
            name="Hearty Oat Bowl",
            description="Warm oats with peanut butter.",
            ingredients_used="- Rolled Oats: 100 g",
            user_id="user-1",
        )

        prompt = _build_image_prompt(meal)

        assert prompt == "Overhead shot of a hearty oat bowl."
        kwargs = create.call_args.kwargs
        assert kwargs["model"] == MEAL_ANTHROPIC_MODEL
        _assert_single_cached_system(kwargs, PROMPT_SYSTEM)
        _assert_no_cache_control_in_messages(kwargs)
        assert "Hearty Oat Bowl" not in kwargs["system"][0]["text"]
        tail = _messages_text(kwargs)
        assert "Meal name: Hearty Oat Bowl" in tail
        assert "Warm oats with peanut butter." in tail

    @patch("app.services.meal_image.settings.anthropic_api_key", "test-key")
    @patch("app.services.meal_image.anthropic.Anthropic")
    def test_falls_back_when_claude_call_fails(self, mock_anthropic):
        mock_anthropic.return_value.messages.create.side_effect = RuntimeError("down")
        meal = Meal(name="Soup", user_id="user-1")

        prompt = _build_image_prompt(meal)

        assert prompt.startswith("Photorealistic overhead food photography of Soup")


class TestAllCallSitesUseSharedHelper:
    """Guard against a future call site bypassing the cached create path."""

    @pytest.mark.parametrize("module", [receipt_analyzer, meal_generator, meal_image])
    def test_no_direct_messages_create(self, module):
        import inspect

        source = inspect.getsource(module)
        assert "messages.create(" not in source, (
            f"{module.__name__} must call create_cached_message so the cache "
            "breakpoint and usage logging apply"
        )
