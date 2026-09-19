"""Prompt layout invariants left over from FOOD-56 (user -> system move).

FOOD-56 split every prompt into a byte-stable cached ``system`` prefix and a
per-request user tail. Two things must hold for the eval baselines in this
directory to stay meaningful:

1. **The prompt text is frozen.** ``fixtures/prompt_snapshots.json`` pins a
   sha256 of every prefix and user template. Any edit — even whitespace —
   busts the Anthropic cache *and* changes model behaviour, so it has to be a
   deliberate, re-baselined change rather than drift.
2. **The split does not add, drop, or duplicate content.** Re-joining the
   cached prefix with the user tail must reproduce the single-turn rendering
   of the same prompt exactly, and every per-request value must appear once
   in the request. That is the precondition for "moving text from the user
   turn to the system block does not change outputs"; the live layout A/B in
   ``test_live_evals.py`` is how that claim is verified on a real model.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from app.models import Ingredient, Meal
from app.services import meal_generator, meal_image, receipt_analyzer
from app.services.meal_generator import (
    FOLLOW_UP_PROMPT,
    MEAL_CALORIE_MAX,
    MEAL_CALORIE_MIN,
    MEAL_GENERATION_PROMPT,
    MEAL_GENERATION_USER_PROMPT,
    PreviousMealTurn,
    generate_meal_from_ingredients,
)
from app.services.meal_image import PROMPT_SYSTEM, _build_image_prompt
from app.services.receipt_analyzer import (
    NUTRITION_ESTIMATE_PROMPT,
    NUTRITION_ESTIMATE_USER_PROMPT,
    PANTRY_MATCH_PROMPT,
    PANTRY_MATCH_USER_PROMPT,
    RECEIPT_ANALYSIS_PROMPT,
    UNIT_CHECK_PROMPT,
    UNIT_CHECK_USER_PROMPT,
    check_ingredient_unit,
    estimate_ingredient_nutrition,
    match_ingredient_to_pantry,
)
from tests.evals.conftest import FIXTURES_DIR, load_json

SNAPSHOTS = load_json(FIXTURES_DIR / "prompt_snapshots.json")
MEAL_PREFIX = MEAL_GENERATION_PROMPT.format(
    calorie_min=MEAL_CALORIE_MIN, calorie_max=MEAL_CALORIE_MAX
)

CURRENT_PROMPTS: dict[str, dict[str, str | None]] = {
    "receipt.analyze_image": {"system_prefix": RECEIPT_ANALYSIS_PROMPT, "user_template": None},
    "receipt.nutrition_estimate": {
        "system_prefix": NUTRITION_ESTIMATE_PROMPT,
        "user_template": NUTRITION_ESTIMATE_USER_PROMPT,
    },
    "receipt.unit_check": {
        "system_prefix": UNIT_CHECK_PROMPT,
        "user_template": UNIT_CHECK_USER_PROMPT,
    },
    "receipt.pantry_match": {
        "system_prefix": PANTRY_MATCH_PROMPT,
        "user_template": PANTRY_MATCH_USER_PROMPT,
    },
    "meal.generate": {
        "system_prefix": MEAL_PREFIX,
        "user_template": MEAL_GENERATION_USER_PROMPT,
        "follow_up_template": FOLLOW_UP_PROMPT,
    },
    "meal.image_prompt": {"system_prefix": PROMPT_SYSTEM, "user_template": None},
}


def sha256(text: str | None) -> str | None:
    return None if text is None else hashlib.sha256(text.encode("utf-8")).hexdigest()


def _request(fake) -> dict:
    assert fake.calls, "no request captured"
    return fake.calls[-1]


def _rejoined(call: dict) -> str:
    return call["system_text"] + "\n\n" + call["tail"]


class TestPromptSnapshots:
    @pytest.mark.parametrize("call_site", sorted(CURRENT_PROMPTS))
    def test_prompt_text_matches_snapshot(self, call_site):
        snapshot = SNAPSHOTS[call_site]
        for part, text in CURRENT_PROMPTS[call_site].items():
            assert snapshot.get(part) == sha256(text), (
                f"{call_site}.{part} changed. Prompt edits bust the cache and invalidate "
                "the eval baselines: update tests/evals/fixtures/prompt_snapshots.json "
                "and re-run the live evals in the same PR."
            )

    def test_snapshot_covers_every_call_site(self):
        assert set(SNAPSHOTS) - {"_comment"} == set(CURRENT_PROMPTS)


class TestLayoutEquivalence:
    def test_unit_check_rejoins_to_single_turn_prompt(self, fake_claude):
        fake_claude.script_unit_check(
            "Watermelon", {"unit_plausible": True, "unit_warning": None}
        )
        check_ingredient_unit("Watermelon", "gallon")
        call = _request(fake_claude)
        expected = UNIT_CHECK_PROMPT + "\n\n" + UNIT_CHECK_USER_PROMPT.format(
            ingredient_name="Watermelon", unit="gallon"
        )
        assert _rejoined(call) == expected
        assert call["tail"].count("Watermelon") == 1
        assert "Watermelon" not in call["system_text"]

    def test_nutrition_rejoins_to_single_turn_prompt(self, fake_claude):
        fake_claude.script_nutrition(
            "Almond Butter",
            {
                "recognized": True,
                "serving_size": "2 tbsp (32g)",
                "servings_per_container": 15,
                "calories": 190,
            },
        )
        estimate_ingredient_nutrition("Almond Butter", "1", "each")
        call = _request(fake_claude)
        expected = NUTRITION_ESTIMATE_PROMPT + "\n\n" + NUTRITION_ESTIMATE_USER_PROMPT.format(
            ingredient_name="Almond Butter", quantity="1", unit="each"
        )
        assert _rejoined(call) == expected
        assert call["tail"].count("Almond Butter") == 1

    def test_pantry_match_rejoins_to_single_turn_prompt(self, fake_claude):
        pantry = [{"id": "ing-1", "name": "Sweet Potato", "unit": "lb"}]
        fake_claude.script_pantry_match(
            "SWT PTATO",
            {"match_id": None, "ambiguous": False, "canonical_name": "Sweet Potato"},
        )
        match_ingredient_to_pantry("SWT PTATO", "lb", pantry)
        call = _request(fake_claude)
        expected = PANTRY_MATCH_PROMPT + "\n\n" + PANTRY_MATCH_USER_PROMPT.format(
            ingredient_name="SWT PTATO",
            unit="lb",
            pantry_json=json.dumps(pantry, ensure_ascii=True),
        )
        assert _rejoined(call) == expected
        assert call["tail"].count("ing-1") == 1
        assert "ing-1" not in call["system_text"]

    def test_meal_generation_rejoins_to_single_turn_prompt(self, fake_claude):
        pantry = [
            Ingredient(
                id="oats", user_id="u", name="Rolled Oats", quantity="1000", unit="g",
                serving_size="40 g", servings_per_container=0.025, calories=150,
            )
        ]
        fake_claude.script_meals(
            [
                {
                    "name": "Oat Bowl",
                    "description": "Oats.",
                    "ingredients_used": [{"name": "Rolled Oats", "amount": "160 g"}],
                    "instructions": ["Cook.", "Serve."],
                }
            ]
        )
        generate_meal_from_ingredients(pantry)
        call = _request(fake_claude)
        expected = MEAL_PREFIX + "\n\n" + MEAL_GENERATION_USER_PROMPT.format(
            ingredients=meal_generator._format_ingredients(pantry)
        )
        assert _rejoined(call) == expected
        assert call["tail"].count("Rolled Oats") == 1
        assert "Rolled Oats" not in call["system_text"]

    def test_follow_up_turn_is_the_frozen_template(self, fake_claude):
        pantry = [
            Ingredient(
                id="oats", user_id="u", name="Rolled Oats", quantity="1000", unit="g",
                serving_size="40 g", servings_per_container=0.025, calories=150,
            )
        ]
        fake_claude.script_meals(
            [
                {
                    "name": "Different Oats",
                    "description": "Oats.",
                    "ingredients_used": [{"name": "Rolled Oats", "amount": "160 g"}],
                    "instructions": ["Cook.", "Serve."],
                }
            ]
        )
        generate_meal_from_ingredients(
            pantry, previous_meal=PreviousMealTurn(name="Oat Bowl")
        )
        call = _request(fake_claude)
        roles = [message["role"] for message in call["messages"]]
        assert roles == ["user", "assistant", "user"]
        assert call["messages"][2]["content"] == FOLLOW_UP_PROMPT.format(
            calorie_min=MEAL_CALORIE_MIN, calorie_max=MEAL_CALORIE_MAX
        )

    def test_meal_image_prompt_keeps_meal_fields_in_tail(self, fake_claude, monkeypatch):
        monkeypatch.setattr(meal_image.anthropic, "Anthropic", lambda *a, **k: fake_claude)
        meal = Meal(name="Hearty Oat Bowl", description="Warm oats.", user_id="u")
        _build_image_prompt(meal)
        call = _request(fake_claude)
        assert call["system_text"] == PROMPT_SYSTEM
        assert call["tail"].count("Hearty Oat Bowl") == 1
        assert "Hearty Oat Bowl" not in call["system_text"]

    def test_no_per_request_placeholder_in_any_cached_prefix(self):
        placeholders = ("{ingredient_name}", "{quantity}", "{unit}", "{pantry_json}", "{ingredients}")
        for call_site, parts in CURRENT_PROMPTS.items():
            for placeholder in placeholders:
                assert placeholder not in parts["system_prefix"], (
                    f"{call_site}: {placeholder} leaked into the cached prefix"
                )
