"""Opt-in live evals: the same fixtures and thresholds against a real model.

Skipped by default and in CI (no paid calls). Run locally before changing
model routing:

    FOOD_EVAL_LIVE=1 FOOD_EVAL_MODEL=claude-haiku-4-5 pytest tests/evals -m live -s

* ``FOOD_EVAL_LIVE=1`` enables these tests.
* ``FOOD_EVAL_MODEL`` forces every call site onto one model id so a cheaper
  tier can be scored end to end before it is routed in production. Leave it
  unset to score the tiers currently configured in ``app/config.py``.
* ``FOOD_EVAL_ANTHROPIC_API_KEY`` (or ``ANTHROPIC_API_KEY``) supplies the key.

Receipt fixtures are only exercised when an image exists at
``fixtures/receipts/images/<fixture id><file_suffix>``; the labelled corpus
ships without images, so ``test_live_receipt_parse`` reports how many were
available and skips when none are.

The layout A/B (``test_live_layout_ab_pantry_match``) is the FOOD-56 residual
check: it sends each pantry-match case both as the cached
``system`` + user-tail request and as a single legacy user turn, and reports
how often the parsed decisions agree.
"""

from __future__ import annotations

import json

import pytest

from app.services.ingredient_deduction import _find_matching_ingredient
from app.services.meal_generator import (
    MEAL_CALORIE_MAX,
    MEAL_CALORIE_MIN,
    MealGenerationError,
    PreviousMealTurn,
    _estimate_meal_calories,
    generate_meal_from_ingredients,
)
from app.services.receipt_analyzer import (
    PANTRY_MATCH_PROMPT,
    PANTRY_MATCH_USER_PROMPT,
    PantryMatchResult,
    _extract_json,
    _get_client,
    analyze_receipt_image,
    match_ingredient_to_pantry,
)
from tests.evals.conftest import (
    FIXTURES_DIR,
    load_meal_plan_fixtures,
    load_pantry_match_fixtures,
    load_receipt_fixtures,
)
from tests.evals.scoring import Tally, names_equivalent
from tests.evals.test_meal_plan_eval import build_pantry
from tests.evals.test_receipt_parse_eval import ReceiptScorecard, score_receipt

pytestmark = pytest.mark.live


def _decision_matches(result: PantryMatchResult, expected: dict) -> bool:
    if expected["decision"] == "ambiguous":
        return result.ambiguous is True
    if expected["decision"] == "merge":
        return result.match_id == expected["into"] and not result.ambiguous
    return result.match_id is None and not result.ambiguous


def test_live_ingredient_match(live_anthropic, gate):
    cases = [c for c in load_pantry_match_fixtures()["cases"] if c["expected"]["llm_calls"]]
    decision = Tally()
    canonical = Tally()
    for case in cases:
        incoming = case["incoming"]
        pantry = [
            {"id": row["id"], "name": row["name"], "unit": row.get("unit") or ""}
            for row in case["pantry"]
        ]
        result = match_ingredient_to_pantry(incoming["ingredient_name"], incoming["unit"], pantry)
        decision.add(
            _decision_matches(result, case["expected"]),
            f"{case['id']}: got match_id={result.match_id} ambiguous={result.ambiguous}",
        )
        if case["expected"].get("display_name"):
            canonical.add(
                names_equivalent(result.canonical_name, case["expected"]["display_name"]),
                f"{case['id']}: canonical {result.canonical_name!r}",
            )

    suite = gate("ingredient_match", mode=f"live:{live_anthropic or 'configured'}")
    suite.check("match_decision_accuracy", decision.rate, decision.describe())
    suite.check("canonical_name_accuracy", canonical.rate, canonical.describe())
    suite.assert_all()


def test_live_meal_plan(live_anthropic, gate):
    fixtures = load_meal_plan_fixtures()
    acceptable = Tally()
    calories_ok = Tally()
    pantry_only = Tally()
    for case in fixtures["cases"]:
        pantry = build_pantry(fixtures["pantries"][case["pantry"]])
        previous = PreviousMealTurn(**case["previous_meal"]) if case.get("previous_meal") else None
        try:
            meal = generate_meal_from_ingredients(pantry, previous_meal=previous)
        except MealGenerationError as exc:
            acceptable.add(False, f"{case['id']}: {exc}")
            continue
        calories = _estimate_meal_calories(pantry, meal.ingredients_used)
        in_band = calories is not None and MEAL_CALORIE_MIN <= calories <= MEAL_CALORIE_MAX
        missing = [
            item.name
            for item in meal.ingredients_used
            if _find_matching_ingredient(pantry, item.name) is None
        ]
        steps = [line for line in meal.instructions.splitlines() if line.strip()]
        calories_ok.add(in_band, f"{case['id']}: {calories} kcal")
        pantry_only.add(not missing, f"{case['id']}: {missing}")
        acceptable.add(in_band and not missing and len(steps) >= 2, f"{case['id']}")

    suite = gate("meal_plan", mode=f"live:{live_anthropic or 'configured'}")
    suite.check("acceptability_rate", acceptable.rate, acceptable.describe())
    suite.check("calorie_in_band_rate", calories_ok.rate, calories_ok.describe())
    suite.check("pantry_only_rate", pantry_only.rate, pantry_only.describe())
    suite.assert_all()


def test_live_receipt_parse(live_anthropic, gate):
    images_dir = FIXTURES_DIR / "receipts" / "images"
    available = []
    for fixture in load_receipt_fixtures():
        image = images_dir / f"{fixture['id']}{fixture.get('file_suffix', '.jpg')}"
        if image.exists():
            available.append((fixture, image))
    if not available:
        pytest.skip(f"no receipt images under {images_dir}; add <fixture id><suffix> files to score live")

    card = ReceiptScorecard()
    for fixture, image in available:
        score_receipt(card, fixture, analyze_receipt_image(image))

    suite = gate("receipt_parse", mode=f"live:{live_anthropic or 'configured'}")
    suite.check("line_item_recall", card.line_recall.rate, card.line_recall.describe())
    suite.check("ingredient_name_accuracy", card.ingredient_name.rate, card.ingredient_name.describe())
    suite.check("is_food_accuracy", card.is_food.rate, card.is_food.describe())
    suite.check("quantity_accuracy", card.quantity.rate, card.quantity.describe())
    suite.check("unit_accuracy", card.unit.rate, card.unit.describe())
    suite.assert_all()


def test_live_layout_ab_pantry_match(live_anthropic, gate):
    """FOOD-56 residual: cached system+tail vs. legacy single user turn."""
    cases = [c for c in load_pantry_match_fixtures()["cases"] if c["expected"]["llm_calls"]]
    client = _get_client()
    agreement = Tally()
    for case in cases:
        incoming = case["incoming"]
        pantry = [
            {"id": row["id"], "name": row["name"], "unit": row.get("unit") or ""}
            for row in case["pantry"]
        ]
        cached = match_ingredient_to_pantry(incoming["ingredient_name"], incoming["unit"], pantry)

        legacy_prompt = PANTRY_MATCH_PROMPT + "\n\n" + PANTRY_MATCH_USER_PROMPT.format(
            ingredient_name=incoming["ingredient_name"],
            unit=incoming["unit"] or "each",
            pantry_json=json.dumps(pantry, ensure_ascii=True),
        )
        response = client.messages.create(
            model=live_anthropic or cached_model_for_pantry_match(),
            max_tokens=256,
            messages=[{"role": "user", "content": legacy_prompt}],
        )
        text = "".join(block.text for block in response.content if block.type == "text")
        try:
            payload = _extract_json(text)
        except (json.JSONDecodeError, ValueError):
            payload = {}
        legacy = PantryMatchResult(
            match_id=payload.get("match_id") if payload.get("match_id") in {p["id"] for p in pantry} else None,
            ambiguous=payload.get("ambiguous") is True,
            canonical_name=payload.get("canonical_name"),
        )
        agreement.add(
            (cached.match_id, cached.ambiguous) == (legacy.match_id, legacy.ambiguous),
            f"{case['id']}: cached={cached.match_id}/{cached.ambiguous} legacy={legacy.match_id}/{legacy.ambiguous}",
        )

    suite = gate("prompt_layout", mode=f"live:{live_anthropic or 'configured'}")
    suite.check("layout_ab_agreement", agreement.rate, agreement.describe())
    suite.assert_all()


def cached_model_for_pantry_match() -> str:
    from app.config import RECEIPT_ANTHROPIC_MODEL

    return RECEIPT_ANTHROPIC_MODEL
