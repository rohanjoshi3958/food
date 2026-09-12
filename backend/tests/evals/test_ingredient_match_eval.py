"""Ingredient match / pantry normalize eval (Food QA bar: ingredient match rate).

Every case in ``fixtures/pantry_matches.json`` seeds a pantry in the test
database and pushes one incoming item through ``create_ingredient`` — the
same path the receipt confirm step and manual add use — with the pantry
match model response scripted. We score whether the *pipeline outcome*
(merge / new row / ambiguous hold, final display name) matches what a
reviewer labelled, and pin the guards that keep a cheaper or sloppier model
from corrupting the pantry: hallucinated ids are dropped, ``ambiguous``
never merges, exact local keys skip the LLM entirely.

``draft_collapse`` cases run ``canonicalize_draft_items`` + ``merge_draft_items``
(the receipt confirm pre-pass) and check that name variants collapse into
one draft row while unit differences do not.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from sqlalchemy.orm import Session

from app.config import OPUS_ANTHROPIC_MODEL
from app.models import Ingredient, User
from app.schemas import DraftIngredientItem
from app.services.ingredient_merge import _merge_key, merge_draft_items
from app.services.ingredients import (
    AmbiguousPantryMatchError,
    canonicalize_draft_items,
    create_ingredient,
)
from app.services.receipt_analyzer import PANTRY_MATCH_PROMPT
from tests.evals.conftest import FakeClaude, load_pantry_match_fixtures
from tests.evals.scoring import Tally, names_equivalent

FIXTURES = load_pantry_match_fixtures()
MATCH_CASES = FIXTURES["cases"]
COLLAPSE_CASES = FIXTURES["draft_collapse"]


@dataclass
class MatchOutcome:
    decision: str  # merge | new | ambiguous
    row_id: str | None
    display_name: str | None
    pantry_match_calls: int
    duplicate_rows: int


def seed_pantry(db: Session, user: User, rows: list[dict]) -> None:
    for row in rows:
        db.add(
            Ingredient(
                id=row["id"],
                user_id=user.id,
                name=row["name"],
                store_item_name=row["name"],
                quantity=row.get("quantity"),
                original_quantity=row.get("quantity"),
                unit=row.get("unit"),
            )
        )
    db.commit()


def run_match_case(fake: FakeClaude, db: Session, user: User, case: dict) -> MatchOutcome:
    for row in db.query(Ingredient).filter(Ingredient.user_id == user.id).all():
        db.delete(row)
    db.commit()
    seed_pantry(db, user, case["pantry"])
    seeded_ids = {row["id"] for row in case["pantry"]}

    fake.calls.clear()
    fake.pantry_matches.clear()
    fake.script_pantry_match(case["incoming"]["ingredient_name"], case["model_response"])

    item = DraftIngredientItem(**case["incoming"], is_food=True)
    try:
        created = create_ingredient(db, user, item, allow_llm_merge=True)
    except AmbiguousPantryMatchError:
        return MatchOutcome("ambiguous", None, None, fake.pantry_match_calls, 0)

    decision = "merge" if created.id in seeded_ids else "new"
    key = _merge_key(created.name, created.unit)
    same_key_rows = [
        row
        for row in db.query(Ingredient).filter(Ingredient.user_id == user.id).all()
        if _merge_key(row.name, row.unit) == key
    ]
    return MatchOutcome(
        decision=decision,
        row_id=created.id,
        display_name=created.name,
        pantry_match_calls=fake.pantry_match_calls,
        duplicate_rows=max(0, len(same_key_rows) - 1),
    )


def run_collapse_case(fake: FakeClaude, db: Session, user: User, case: dict) -> list[dict]:
    for row in db.query(Ingredient).filter(Ingredient.user_id == user.id).all():
        db.delete(row)
    db.commit()
    seed_pantry(db, user, case["pantry"])
    fake.pantry_matches.clear()
    for name, payload in case["model_responses"].items():
        fake.script_pantry_match(name, payload)
    return merge_draft_items(canonicalize_draft_items(db, user, case["items"]))


@pytest.mark.parametrize("case", MATCH_CASES, ids=[c["id"] for c in MATCH_CASES])
def test_match_case_outcome_matches_label(case, fake_claude, test_db, test_user):
    """Per-case readability; the corpus gate below is what fails on regressions."""
    outcome = run_match_case(fake_claude, test_db, test_user, case)
    expected = case["expected"]

    assert outcome.decision == expected["decision"], (
        f"{case['id']}: pipeline decided {outcome.decision!r}, label says {expected['decision']!r}"
    )
    assert outcome.pantry_match_calls == expected["llm_calls"], (
        f"{case['id']}: {outcome.pantry_match_calls} pantry-match calls, expected {expected['llm_calls']}"
    )
    if expected["decision"] == "merge":
        assert outcome.row_id == expected["into"]
    assert outcome.duplicate_rows == 0


def test_ingredient_match_meets_baselines(fake_claude, test_db, test_user, gate):
    decision = Tally()
    canonical = Tally()
    invalid_id_rejected = Tally()
    ambiguous_never_merged = Tally()
    collapse = Tally()
    duplicate_rows = 0
    pantry_match_calls = 0

    for case in MATCH_CASES:
        outcome = run_match_case(fake_claude, test_db, test_user, case)
        expected = case["expected"]
        label = case["id"]

        matched = outcome.decision == expected["decision"] and (
            expected["decision"] != "merge" or outcome.row_id == expected["into"]
        )
        decision.add(matched, f"{label}: got {outcome.decision}/{outcome.row_id}")

        if expected["decision"] == "new" and expected.get("display_name"):
            canonical.add(
                names_equivalent(outcome.display_name, expected["display_name"]),
                f"{label}: display {outcome.display_name!r} != {expected['display_name']!r}",
            )
        if case.get("hallucinated_id"):
            invalid_id_rejected.add(outcome.decision != "merge", f"{label}: merged on a bad id")
        if case.get("ambiguous_flagged"):
            ambiguous_never_merged.add(
                outcome.decision == "ambiguous", f"{label}: ambiguous response was {outcome.decision}"
            )
        duplicate_rows += outcome.duplicate_rows
        pantry_match_calls += outcome.pantry_match_calls

    for case in COLLAPSE_CASES:
        rows = run_collapse_case(fake_claude, test_db, test_user, case)
        want = case["expected"]
        names = [row["ingredient_name"] for row in rows]
        quantities = [row["quantity"] for row in rows]
        collapse.add(
            len(rows) == want["rows"]
            and all(names_equivalent(a, b) for a, b in zip(names, want["names"]))
            and quantities == want["quantities"],
            f"{case['id']}: got {list(zip(names, quantities))}",
        )

    suite = gate("ingredient_match")
    suite.check("match_decision_accuracy", decision.rate, decision.describe())
    suite.check("canonical_name_accuracy", canonical.rate, canonical.describe())
    suite.check("invalid_id_rejection", invalid_id_rejected.rate, invalid_id_rejected.describe())
    suite.check(
        "ambiguous_never_merged", ambiguous_never_merged.rate, ambiguous_never_merged.describe()
    )
    suite.check("duplicate_rows_created", float(duplicate_rows))
    suite.check("draft_collapse_rate", collapse.rate, collapse.describe())
    suite.check(
        "mean_pantry_match_calls",
        pantry_match_calls / len(MATCH_CASES),
        f"{pantry_match_calls} calls over {len(MATCH_CASES)} cases",
    )
    suite.assert_all()


def test_ingredient_match_with_routing_enabled(fake_claude, test_db, test_user, gate, monkeypatch):
    """Same corpus with MODEL_ROUTING_ENABLED=true: decisions must not change,
    and the model mix must shift off Opus (Sonnet first, Opus only on
    low-confidence escalation). Documents the cost/quality trade the flag buys."""
    monkeypatch.setenv("MODEL_ROUTING_ENABLED", "true")

    decision = Tally()
    total_calls = 0
    opus_calls = 0
    for case in MATCH_CASES:
        outcome = run_match_case(fake_claude, test_db, test_user, case)
        expected = case["expected"]
        matched = outcome.decision == expected["decision"] and (
            expected["decision"] != "merge" or outcome.row_id == expected["into"]
        )
        decision.add(matched, f"{case['id']}: got {outcome.decision}/{outcome.row_id}")
        pantry_calls = fake_claude.calls_for(PANTRY_MATCH_PROMPT)
        total_calls += len(pantry_calls)
        opus_calls += sum(1 for call in pantry_calls if call["model"] == OPUS_ANTHROPIC_MODEL)

    suite = gate("ingredient_match_routing_on")
    suite.check("match_decision_accuracy", decision.rate, decision.describe())
    suite.check(
        "mean_pantry_match_calls",
        total_calls / len(MATCH_CASES),
        f"{total_calls} calls over {len(MATCH_CASES)} cases (escalations add a call)",
    )
    suite.check(
        "opus_share_of_pantry_match_calls",
        opus_calls / total_calls if total_calls else 0.0,
        f"{opus_calls}/{total_calls} pantry-match calls reached Opus",
    )
    suite.assert_all()
