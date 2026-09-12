"""Unit tests for meal generation: parsing, calorie retry loop, and call budget.

``generate_meal_from_ingredients`` can make up to ``MEAL_GENERATION_ATTEMPTS``
Claude calls per user click. These tests pin that budget and the accept /
retry / fallback decisions so cost changes (fewer attempts, different model,
cached prompts) are measured against a known baseline.
"""
from __future__ import annotations

import json
from unittest.mock import Mock, patch

import pytest

from app.config import MEAL_ANTHROPIC_MODEL
from app.models import Ingredient
from app.services import meal_generator
from app.services.meal_generator import (
    MEAL_CALORIE_MAX,
    MEAL_CALORIE_MIN,
    MEAL_GENERATION_ATTEMPTS,
    MealGenerationError,
    PreviousMealTurn,
    _parse_generated_meal,
    generate_meal_from_ingredients,
    normalize_instructions,
)

from tests.conftest import create_mock_anthropic_response


def _pantry() -> list[Ingredient]:
    """Two lb-based items with exact oz serving math (see smoke test)."""
    return [
        Ingredient(
            id="chicken",
            user_id="u",
            name="Chicken Breast",
            quantity="2",
            unit="lb",
            serving_size="4 oz (112g)",
            servings_per_container=4,
            calories=187,
            protein_g=35,
        ),
        Ingredient(
            id="rice",
            user_id="u",
            name="White Rice",
            quantity="2",
            unit="lb",
            serving_size="2 oz (56g)",
            servings_per_container=8,
            calories=200,
            carbs_g=44,
        ),
    ]


def _meal(name: str, chicken_oz: float, rice_oz: float) -> str:
    return json.dumps(
        {
            "name": name,
            "description": "Test meal.",
            "ingredients_used": [
                {"name": "Chicken Breast", "amount": f"{chicken_oz:g} oz"},
                {"name": "White Rice", "amount": f"{rice_oz:g} oz"},
            ],
            "instructions": ["Cook.", "Serve."],
        }
    )


# 8 oz chicken + 4 oz rice = 2×187 + 2×200 = 774 kcal (in range)
IN_RANGE = _meal("Chicken and Rice Bowl", 8, 4)
# 2 oz chicken + 1 oz rice = 0.5×187 + 0.5×200 ≈ 194 kcal (too light)
TOO_LIGHT = _meal("Tiny Chicken Bite", 2, 1)
# 16 oz chicken + 8 oz rice = 4×187 + 4×200 = 1548 kcal (too heavy)
TOO_HEAVY = _meal("Chicken Feast", 16, 8)


@pytest.fixture
def claude(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")
    client = Mock()
    with patch.object(meal_generator.anthropic, "Anthropic", return_value=client):
        yield client


def _script(client: Mock, *responses: str) -> None:
    client.messages.create.side_effect = [
        create_mock_anthropic_response(text) for text in responses
    ]


class TestParsing:
    def test_normalize_instructions_numbers_list_steps(self):
        assert normalize_instructions(["  Boil water. ", "", "Add rice."]) == (
            "1. Boil water.\n2. Add rice."
        )

    def test_normalize_instructions_splits_inline_numbered_string(self):
        assert normalize_instructions("1. Boil water. 2. Add rice.") == (
            "1. Boil water.\n2. Add rice."
        )

    def test_parse_accepts_fenced_json(self):
        fenced = "```json\n" + IN_RANGE + "\n```"
        parsed = _parse_generated_meal(fenced)
        assert parsed.name == "Chicken and Rice Bowl"
        assert parsed.instructions == "1. Cook.\n2. Serve."
        assert [i.amount for i in parsed.ingredients_used] == ["8 oz", "4 oz"]

    def test_parse_rejects_meal_without_name_or_instructions(self):
        with pytest.raises(MealGenerationError):
            _parse_generated_meal(json.dumps({"name": " ", "description": "", "instructions": []}))


class TestCallBudget:
    def test_in_range_meal_uses_exactly_one_call(self, claude):
        _script(claude, IN_RANGE)

        meal = generate_meal_from_ingredients(_pantry())

        assert meal.name == "Chicken and Rice Bowl"
        assert claude.messages.create.call_count == 1
        kwargs = claude.messages.create.call_args.kwargs
        assert kwargs["model"] == MEAL_ANTHROPIC_MODEL
        assert kwargs["max_tokens"] == 4096
        assert "system" not in kwargs, "no system prompt today (relevant to prompt caching)"

    def test_out_of_range_meal_triggers_one_retry_with_feedback(self, claude):
        _script(claude, TOO_LIGHT, IN_RANGE)

        meal = generate_meal_from_ingredients(_pantry())

        assert meal.name == "Chicken and Rice Bowl"
        assert claude.messages.create.call_count == 2
        # Retry carries the prior assistant turn plus a corrective user message,
        # so each retry re-sends the whole (growing) conversation.
        retry_messages = claude.messages.create.call_args_list[1].kwargs["messages"]
        assert [m["role"] for m in retry_messages] == ["user", "assistant", "user"]
        assert "too light" in retry_messages[-1]["content"]
        assert f"{MEAL_CALORIE_MIN} and {MEAL_CALORIE_MAX}" in retry_messages[-1]["content"]

    def test_too_heavy_meal_asks_to_reduce_portions(self, claude):
        _script(claude, TOO_HEAVY, IN_RANGE)

        generate_meal_from_ingredients(_pantry())

        retry_messages = claude.messages.create.call_args_list[1].kwargs["messages"]
        assert "above the" in retry_messages[-1]["content"]

    def test_exhausts_attempts_then_returns_closest_meal(self, claude):
        # Never in range: 194, 1548, 194, 1548 kcal → closest is TOO_LIGHT
        # (306 below min vs 748 above max).
        _script(claude, TOO_LIGHT, TOO_HEAVY, TOO_LIGHT, TOO_HEAVY)

        meal = generate_meal_from_ingredients(_pantry())

        assert claude.messages.create.call_count == MEAL_GENERATION_ATTEMPTS
        assert meal.name == "Tiny Chicken Bite"

    def test_unparseable_responses_count_against_budget(self, claude):
        _script(claude, "not json", "```json\n{}\n```", IN_RANGE)

        meal = generate_meal_from_ingredients(_pantry())

        assert meal.name == "Chicken and Rice Bowl"
        assert claude.messages.create.call_count == 3

    def test_all_attempts_unparseable_raises(self, claude):
        _script(claude, *(["garbage"] * MEAL_GENERATION_ATTEMPTS))

        with pytest.raises(MealGenerationError, match="Could not parse"):
            generate_meal_from_ingredients(_pantry())

        assert claude.messages.create.call_count == MEAL_GENERATION_ATTEMPTS

    def test_same_meal_as_previous_is_retried(self, claude):
        repeat = _meal("Chicken and Rice Bowl", 8, 4)
        different = _meal("Rice Porridge with Chicken", 8, 4)
        _script(claude, repeat, different)

        meal = generate_meal_from_ingredients(
            _pantry(),
            previous_meal=PreviousMealTurn(name="CHICKEN AND RICE BOWL!!"),
        )

        assert meal.name == "Rice Porridge with Chicken"
        assert claude.messages.create.call_count == 2
        first_messages = claude.messages.create.call_args_list[0].kwargs["messages"]
        assert [m["role"] for m in first_messages] == ["user", "assistant", "user"]
        assert "different single-person meal" in first_messages[-1]["content"]


class TestGuards:
    def test_missing_api_key_makes_no_call(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "")
        with patch.object(meal_generator.anthropic, "Anthropic") as anthropic_cls:
            with pytest.raises(MealGenerationError, match="not configured"):
                generate_meal_from_ingredients(_pantry())
        anthropic_cls.assert_not_called()

    def test_empty_pantry_makes_no_call(self, claude):
        with pytest.raises(MealGenerationError, match="Add ingredients"):
            generate_meal_from_ingredients([])
        claude.messages.create.assert_not_called()

    def test_amounts_are_clamped_to_pantry_maximums(self, claude):
        # Asks for 5 lb of chicken when only 2 lb exist → clamped to pantry.
        greedy = json.dumps(
            {
                "name": "Greedy Chicken",
                "description": "Too much.",
                "ingredients_used": [{"name": "Chicken Breast", "amount": "5 lb"}],
                "instructions": ["Cook."],
            }
        )
        _script(claude, greedy)

        meal = generate_meal_from_ingredients(_pantry())

        # 5 lb → clamped to the 2 lb on hand → scaled to 4 servings (16 oz),
        # which is 748 kcal and therefore accepted on the first call.
        assert meal.ingredients_used[0].amount == "16 oz"
        assert claude.messages.create.call_count == 1
