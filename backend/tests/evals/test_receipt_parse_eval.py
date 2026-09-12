"""Receipt parse accuracy eval (Food QA bar: receipt accuracy).

Each fixture under ``fixtures/receipts/`` is a labelled receipt: the raw
model response the extraction call site returns for that receipt, per-item
nutrition answers, and the ground-truth line items a human labelled. The
fixture flows through the real ``analyze_receipt_image`` path (JSON
extraction, schema validation, threaded nutrition enrichment) with the
Anthropic client mocked, so a regression anywhere in that pipeline — or a
re-recorded response from a cheaper model tier — shows up as a metric drop
against ``baselines.json``.

Lines are aligned on ``store_item_name`` (the text printed on the receipt),
then ingredient name, food classification, quantity, and unit are scored.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.receipt_analyzer import ParsedReceipt, analyze_receipt_image
from tests.evals.conftest import FakeClaude, load_receipt_fixtures
from tests.evals.scoring import (
    Tally,
    names_equivalent,
    quantities_equivalent,
    receipt_line_key,
    units_equivalent,
)

RECEIPT_FIXTURES = load_receipt_fixtures()
FIXTURE_IDS = [fixture["id"] for fixture in RECEIPT_FIXTURES]


class ReceiptScorecard:
    def __init__(self) -> None:
        self.line_recall = Tally()
        self.line_precision = Tally()
        self.ingredient_name = Tally()
        self.is_food = Tally()
        self.quantity = Tally()
        self.unit = Tally()
        self.store_name = Tally()
        self.nutrition_coverage = Tally()


def run_receipt_fixture(fake: FakeClaude, fixture: dict, tmp_path: Path) -> ParsedReceipt:
    fake.script_receipt(fixture)
    receipt_path = tmp_path / f"{fixture['id']}{fixture.get('file_suffix', '.jpg')}"
    receipt_path.write_bytes(b"\xff\xd8 eval receipt bytes " + fixture["id"].encode())
    return analyze_receipt_image(receipt_path)


def score_receipt(card: ReceiptScorecard, fixture: dict, parsed: ParsedReceipt) -> None:
    rid = fixture["id"]
    expected = fixture["expected"]
    expected_by_key = {
        receipt_line_key(item["store_item_name"]): item for item in expected["items"]
    }
    parsed_by_key = {receipt_line_key(item.store_item_name): item for item in parsed.items}

    card.store_name.add(
        (parsed.store_name or "").strip().casefold()
        == (expected["store_name"] or "").strip().casefold(),
        f"{rid}: store_name {parsed.store_name!r} != {expected['store_name']!r}",
    )

    for key, parsed_item in parsed_by_key.items():
        card.line_precision.add(
            key in expected_by_key,
            f"{rid}: hallucinated line {parsed_item.store_item_name!r}",
        )

    for key, want in expected_by_key.items():
        got = parsed_by_key.get(key)
        card.line_recall.add(got is not None, f"{rid}: missing line {want['store_item_name']!r}")
        if got is None:
            continue

        label = f"{rid}/{want['store_item_name']}"
        card.ingredient_name.add(
            names_equivalent(got.ingredient_name, want["ingredient_name"]),
            f"{label}: name {got.ingredient_name!r} != {want['ingredient_name']!r}",
        )
        card.is_food.add(
            got.is_food is want["is_food"],
            f"{label}: is_food {got.is_food} != {want['is_food']}",
        )

        if not want["is_food"]:
            continue

        if want.get("quantity") is not None:
            card.quantity.add(
                quantities_equivalent(got.quantity, want["quantity"]),
                f"{label}: quantity {got.quantity!r} != {want['quantity']!r}",
            )
        if want.get("unit") is not None:
            card.unit.add(
                units_equivalent(got.unit, want["unit"]),
                f"{label}: unit {got.unit!r} != {want['unit']!r}",
            )
        if want.get("expect_recognized", True):
            card.nutrition_coverage.add(
                got.calories is not None,
                f"{label}: no nutrition after enrichment",
            )
        # Food items must always leave the pipeline with a usable quantity/unit.
        assert got.quantity, f"{label}: food item left with empty quantity"
        assert got.unit, f"{label}: food item left with empty unit"


@pytest.mark.parametrize("fixture", RECEIPT_FIXTURES, ids=FIXTURE_IDS)
def test_receipt_fixture_parses_through_pipeline(fixture, fake_claude, tmp_path):
    """Per-receipt sanity: the pipeline returns items and spends the expected calls."""
    parsed = run_receipt_fixture(fake_claude, fixture, tmp_path)

    assert parsed.items, f"{fixture['id']}: pipeline returned no items"
    food_lines = sum(1 for item in parsed.items if item.is_food)
    assert len(fake_claude.calls) == 1 + food_lines, (
        f"{fixture['id']}: expected 1 extraction + {food_lines} nutrition calls, "
        f"saw {len(fake_claude.calls)}"
    )
    card = ReceiptScorecard()
    score_receipt(card, fixture, parsed)
    assert card.line_recall.hits > 0, f"{fixture['id']}: no labelled line was found"


def test_receipt_parse_meets_baselines(fake_claude, tmp_path, gate):
    """Corpus-level gate against ``baselines.json``."""
    card = ReceiptScorecard()
    for fixture in RECEIPT_FIXTURES:
        fake_claude.calls.clear()
        parsed = run_receipt_fixture(fake_claude, fixture, tmp_path)
        score_receipt(card, fixture, parsed)

    suite = gate("receipt_parse")
    suite.check("line_item_recall", card.line_recall.rate, card.line_recall.describe())
    suite.check("line_item_precision", card.line_precision.rate, card.line_precision.describe())
    suite.check(
        "ingredient_name_accuracy", card.ingredient_name.rate, card.ingredient_name.describe()
    )
    suite.check("is_food_accuracy", card.is_food.rate, card.is_food.describe())
    suite.check("quantity_accuracy", card.quantity.rate, card.quantity.describe())
    suite.check("unit_accuracy", card.unit.rate, card.unit.describe())
    suite.check("store_name_accuracy", card.store_name.rate, card.store_name.describe())
    suite.check(
        "nutrition_coverage", card.nutrition_coverage.rate, card.nutrition_coverage.describe()
    )
    suite.assert_all()
