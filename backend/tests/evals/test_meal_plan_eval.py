"""Meal-plan acceptability eval (Food QA bar: meal-plan acceptability).

A meal is *acceptable* when, after the real generation pipeline (parse ->
same-meal avoidance -> clamp to pantry -> scale to one plate -> calorie
estimate -> retry loop) has run against the scripted model responses:

* the schema is valid (name, at least one ingredient with an amount);
* instructions are present with at least two steps;
* the pipeline's own calorie estimate lands in ``MEAL_CALORIE_MIN..MAX``
  (500–800 kcal today) — calories are computed from the pantry's label
  nutrition, never mocked;
* every ingredient used exists in the pantry.

We also track how many Claude calls each meal costs, since the retry loop is
the meal path's main cost lever.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.models import Ingredient
from app.services.ingredient_deduction import _find_matching_ingredient
from app.services.meal_generator import (
    MEAL_CALORIE_MAX,
    MEAL_CALORIE_MIN,
    MEAL_GENERATION_ATTEMPTS,
    GeneratedMeal,
    MealGenerationError,
    PreviousMealTurn,
    _estimate_meal_calories,
    generate_meal_from_ingredients,
)
from tests.evals.conftest import FakeClaude, load_meal_plan_fixtures
from tests.evals.scoring import Tally

FIXTURES = load_meal_plan_fixtures()
CASES = FIXTURES["cases"]
PANTRIES = FIXTURES["pantries"]


@dataclass
class MealOutcome:
    meal: GeneratedMeal | None
    error: str | None
    calls: int
    calories: float | None
    schema_valid: bool
    instructions_present: bool
    calorie_in_band: bool
    pantry_only: bool
    missing_ingredients: list[str]

    @property
    def acceptable(self) -> bool:
        return (
            self.schema_valid
            and self.instructions_present
            and self.calorie_in_band
            and self.pantry_only
        )


def build_pantry(rows: list[dict]) -> list[Ingredient]:
    return [Ingredient(user_id="eval-user", **row) for row in rows]


def _instruction_steps(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def run_meal_case(fake: FakeClaude, case: dict) -> MealOutcome:
    pantry = build_pantry(PANTRIES[case["pantry"]])
    fake.calls.clear()
    fake.script_meals(case["model_responses"])
    previous = (
        PreviousMealTurn(**case["previous_meal"]) if case.get("previous_meal") else None
    )

    try:
        meal = generate_meal_from_ingredients(pantry, previous_meal=previous)
    except MealGenerationError as exc:
        return MealOutcome(None, str(exc), fake.meal_calls, None, False, False, False, False, [])

    schema_valid = bool(meal.name.strip()) and bool(meal.ingredients_used) and all(
        item.name.strip() and item.amount.strip() for item in meal.ingredients_used
    )
    instructions_present = len(_instruction_steps(meal.instructions)) >= 2
    calories = _estimate_meal_calories(pantry, meal.ingredients_used)
    in_band = calories is not None and MEAL_CALORIE_MIN <= calories <= MEAL_CALORIE_MAX
    missing = [
        item.name
        for item in meal.ingredients_used
        if _find_matching_ingredient(pantry, item.name) is None
    ]
    return MealOutcome(
        meal=meal,
        error=None,
        calls=fake.meal_calls,
        calories=calories,
        schema_valid=schema_valid,
        instructions_present=instructions_present,
        calorie_in_band=in_band,
        pantry_only=not missing,
        missing_ingredients=missing,
    )


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_meal_case_outcome_matches_label(case, fake_claude):
    outcome = run_meal_case(fake_claude, case)
    expected = case["expected"]

    assert outcome.acceptable is expected["acceptable"], (
        f"{case['id']}: acceptable={outcome.acceptable} (label {expected['acceptable']}); "
        f"calories={outcome.calories} missing={outcome.missing_ingredients} error={outcome.error}"
    )
    assert outcome.calls == expected["calls"], (
        f"{case['id']}: {outcome.calls} meal calls, label says {expected['calls']}"
    )
    if expected.get("calories") is not None:
        assert outcome.calories == expected["calories"], (
            f"{case['id']}: pipeline estimated {outcome.calories} kcal, label {expected['calories']}"
        )
    assert outcome.calls <= MEAL_GENERATION_ATTEMPTS


def test_meal_plan_meets_baselines(fake_claude, gate):
    schema = Tally()
    instructions = Tally()
    calories = Tally()
    pantry_only = Tally()
    acceptable = Tally()
    label_match = Tally()
    total_calls = 0

    for case in CASES:
        outcome = run_meal_case(fake_claude, case)
        expected = case["expected"]
        label = case["id"]
        total_calls += outcome.calls

        schema.add(outcome.schema_valid, f"{label}: {outcome.error or 'invalid schema'}")
        instructions.add(outcome.instructions_present, f"{label}: <2 instruction steps")
        calories.add(
            outcome.calorie_in_band,
            f"{label}: {outcome.calories} kcal outside {MEAL_CALORIE_MIN}-{MEAL_CALORIE_MAX}",
        )
        pantry_only.add(outcome.pantry_only, f"{label}: not in pantry {outcome.missing_ingredients}")
        acceptable.add(outcome.acceptable, f"{label}")
        label_match.add(
            outcome.acceptable is expected["acceptable"]
            and outcome.calls == expected["calls"]
            and (expected.get("calories") is None or outcome.calories == expected["calories"]),
            f"{label}: acceptable={outcome.acceptable} calls={outcome.calls} kcal={outcome.calories}",
        )

    suite = gate("meal_plan")
    suite.check("schema_valid_rate", schema.rate, schema.describe())
    suite.check("instructions_present_rate", instructions.rate, instructions.describe())
    suite.check("calorie_in_band_rate", calories.rate, calories.describe())
    suite.check("pantry_only_rate", pantry_only.rate, pantry_only.describe())
    suite.check("acceptability_rate", acceptable.rate, acceptable.describe())
    suite.check(
        "mean_calls_per_meal", total_calls / len(CASES), f"{total_calls} calls over {len(CASES)} meals"
    )
    suite.check("outcome_matches_label_rate", label_match.rate, label_match.describe())
    suite.assert_all()
