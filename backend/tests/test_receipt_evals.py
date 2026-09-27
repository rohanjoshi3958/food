"""Keeps the FOOD-55 eval scaffold loadable and its scoring rules honest.

No images, Tesseract, or Anthropic involved.
"""
import json
from pathlib import Path

import pytest

from evals.receipts import run_eval
from evals.receipts.scoring import (
    ESCALATED,
    Label,
    LabelItem,
    Prediction,
    PredictionItem,
    compare,
    match_items,
    normalize_name,
    score_case,
    score_run,
)

EVAL_DIR = Path(__file__).resolve().parents[1] / "evals" / "receipts"


class TestScaffoldIntegrity:
    def test_validate_command_passes(self, capsys):
        assert run_eval.cmd_validate(None) == 0
        assert "ERROR" not in capsys.readouterr().out

    def test_every_required_category_has_a_case_with_labels(self):
        manifest = json.loads((EVAL_DIR / "manifest.json").read_text())
        required = {
            "clean_grocery", "sku_abbrevs", "mixed_non_food", "blurry_thermal",
            "multi_column", "missing_qty_unit", "reupload_hash", "pdf",
        }
        assert set(manifest["categories"]) == required
        assert {case["category"] for case in manifest["cases"]} == required
        for case in manifest["cases"]:
            assert (EVAL_DIR / case["labels"]).exists(), case["id"]
            assert (EVAL_DIR / Path(case["labels"]).parent / "README.md").exists(), case["id"]

    def test_labels_match_json_schema_required_fields(self):
        schema = json.loads((EVAL_DIR / "schema" / "label.schema.json").read_text())
        item_required = set(schema["properties"]["items"]["items"]["required"])
        assert item_required == {"store_item_name", "ingredient_name", "quantity", "unit", "is_food"}
        assert set(LabelItem.model_fields) == item_required
        assert set(schema["required"]) <= set(Label.model_fields)

    def test_text_mode_run_and_score_round_trip(self, tmp_path):
        out = tmp_path / "cand.json"
        assert run_eval.main(["run", "--mode", "ocr-text", "--out", str(out)]) == 0
        predictions = json.loads(out.read_text())
        assert predictions["clean_grocery_01"]["path"] == "ocr"
        assert predictions["reupload_hash_01"]["path"] == "cache"
        assert predictions["blurry_thermal_01"]["path"] == ESCALATED
        assert "low_ocr_confidence" in predictions["blurry_thermal_01"]["gate_reasons"]
        assert predictions["missing_qty_unit_01"]["gate_reasons"] == ["missing_qty_unit"]
        # Advisory scoring passes; the strict merge gate fails until images + baseline exist.
        assert run_eval.main(["score", "--predictions", str(out)]) == 0
        assert run_eval.main(["score", "--predictions", str(out), "--strict"]) == 1


def _label(*names: str, **overrides) -> Label:
    items = [LabelItem(store_item_name=n.upper(), ingredient_name=n, quantity="1", unit="each", is_food=True) for n in names]
    return Label(case_id="x", store_name="Store", items=items, **overrides)


def _pred(*names: str, path="ocr", **overrides) -> Prediction:
    items = [PredictionItem(store_item_name=n.upper(), ingredient_name=n, quantity="1", unit="each") for n in names]
    return Prediction(path=path, store_name="Store", items=items, **overrides)


class TestScoring:
    def test_normalize_name_handles_case_punctuation_and_plurals(self):
        assert normalize_name("Organic Bananas!") == "organic banana"
        assert normalize_name("Cherries") == "cherry"
        assert normalize_name("Tortilla Chips 13oz") == "tortilla chip 13oz"

    def test_match_items_prefers_exact_then_falls_back_to_overlap(self):
        predicted = [PredictionItem(ingredient_name="Greek Yogurt"), PredictionItem(ingredient_name="Chicken Breast Boneless")]
        labeled = [
            LabelItem(store_item_name="CHKN BRST BNLS", ingredient_name="Boneless Chicken Breast", quantity=None, unit=None, is_food=True),
            LabelItem(store_item_name="GRK YOGURT", ingredient_name="Greek Yogurt", quantity=None, unit=None, is_food=True),
        ]
        assert match_items(predicted, labeled) == [(0, 1), (1, 0)]

    def test_case_f1_and_exact(self):
        label = _label("Milk", "Eggs", "Bread")
        pred = _pred("Milk", "Eggs", "Cereal")
        pred.items[1].unit = "dozen"
        score = score_case("c", pred, label)
        assert (score.tp, score.fp, score.fn) == (2, 1, 1)
        assert score.f1 == pytest.approx(2 / 3)
        assert score.exact == 1  # eggs unit differs
        assert score.success is True

    def test_missing_quantity_default_is_not_exact_but_still_matches(self):
        label = Label(case_id="x", store_name=None, items=[LabelItem(store_item_name="TOMATOES", ingredient_name="Tomatoes", quantity=None, unit=None, is_food=True)])
        pred = Prediction(path="ocr", items=[PredictionItem(store_item_name="TOMATOES", ingredient_name="Tomatoes", quantity="1", unit="each")])
        score = score_case("c", pred, label)
        assert score.tp == 1 and score.exact == 0

    def test_escalated_cases_use_baseline_fallback_and_its_cost(self):
        manifest = [{"id": "a", "expect_path": "opus_baseline", "smoke": True}]
        labels = {"a": _label("Milk")}
        candidate = {"a": _pred(path=ESCALATED, gate_reasons=["low_ocr_confidence"])}
        baseline = {"a": _pred("Milk", path="opus_baseline")}
        costs = {"ocr": 0.0, "opus_baseline": 0.05}

        run = score_run(candidate, labels, manifest, costs, fallback=baseline)
        assert run.cases[0].path == "opus_baseline"
        assert run.micro_f1 == 1.0
        assert run.pct_no_vision == 0.0
        assert run.cost_per_success == pytest.approx(0.05)
        assert run.smoke_failures == []

        without = score_run(candidate, labels, manifest, costs)
        assert without.cases == [] and without.skipped[0][0] == "a"

    def test_smoke_case_on_wrong_path_is_a_regression(self):
        manifest = [{"id": "a", "expect_path": "cache", "smoke": True}]
        run = score_run({"a": _pred("Milk", path="ocr")}, {"a": _label("Milk")}, manifest)
        assert run.micro_f1 == 1.0
        assert run.smoke_failures == ["a"]
        assert compare(run, None).passed is False

    def test_cheaper_but_worse_fails(self):
        manifest = [{"id": "a"}, {"id": "b"}]
        labels = {"a": _label("Milk", "Eggs"), "b": _label("Bread", "Butter")}
        baseline = score_run({"a": _pred("Milk", "Eggs", path="opus_baseline"), "b": _pred("Bread", "Butter", path="opus_baseline")}, labels, manifest, {"opus_baseline": 0.05, "ocr": 0.0})
        candidate = score_run({"a": _pred("Milk", "Eggs"), "b": _pred("Bread")}, labels, manifest, {"opus_baseline": 0.05, "ocr": 0.0})

        assert candidate.cost_per_success < baseline.cost_per_success
        assert candidate.pct_no_vision == 1.0
        verdict = compare(candidate, baseline)
        assert verdict.passed is False
        assert "cheaper-but-worse" in verdict.reasons[0]

    def test_within_margin_passes(self):
        manifest = [{"id": str(i)} for i in range(50)]
        labels = {str(i): _label("Milk", "Eggs") for i in range(50)}
        baseline_preds = {str(i): _pred("Milk", "Eggs", path="opus_baseline") for i in range(50)}
        candidate_preds = dict(baseline_preds)
        candidate_preds["0"] = _pred("Milk")  # one missed item out of 100 → F1 ≈ 0.995
        baseline = score_run(baseline_preds, labels, manifest)
        candidate = score_run(candidate_preds, labels, manifest)
        assert compare(candidate, baseline).passed is True

    def test_strict_requires_baseline_and_no_skips(self):
        run = score_run({}, {"a": _label("Milk")}, [{"id": "a"}])
        verdict = compare(run, None, strict=True)
        assert verdict.passed is False
        assert any("baseline" in r for r in verdict.reasons)
        assert any("unscored" in r for r in verdict.reasons)

    def test_strict_rejects_incomplete_baseline(self):
        manifest = [{"id": "a"}, {"id": "b"}]
        labels = {"a": _label("Milk"), "b": _label("Eggs")}
        candidate = score_run(
            {"a": _pred("Milk"), "b": _pred("Eggs")},
            labels,
            manifest,
        )
        baseline = score_run(
            {"a": _pred("Milk", path="opus_baseline")},
            labels,
            manifest,
        )
        assert baseline.skipped and baseline.skipped[0][0] == "b"
        advisory = compare(candidate, baseline)
        assert advisory.passed is True
        verdict = compare(candidate, baseline, strict=True)
        assert verdict.passed is False
        assert any("baseline has unscored cases" in reason for reason in verdict.reasons)
        assert "b (" in verdict.reasons[-1]
