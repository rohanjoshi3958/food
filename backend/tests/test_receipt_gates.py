"""Unit tests for OCR-first confidence gates (FOOD-55 slice 2)."""
import pytest

from app.services.receipt_analyzer import ParsedReceipt, ParsedReceiptItem
from app.services.receipt_gates import (
    REASON_LOW_OCR_CONFIDENCE,
    REASON_MISSING_QTY_UNIT,
    REASON_SCHEMA_INVALID,
    REASON_TOTALS_MISMATCH,
    REASON_ZERO_FOOD_LINES,
    evaluate_ocr_gates,
    schema_errors,
    totals_match,
)
from app.services.receipt_parser import ParseDiagnostics, parse_receipt_text

GATE_KWARGS = {
    "min_ocr_confidence": 60.0,
    "max_missing_qty_unit_ratio": 0.5,
    "totals_tolerance": 0.02,
}


def _receipt(*names: str, is_food: bool = True) -> ParsedReceipt:
    return ParsedReceipt(
        store_name="Test",
        items=[
            ParsedReceiptItem(store_item_name=n, ingredient_name=n.title(), is_food=is_food, quantity="1", unit="each")
            for n in names
        ],
    )


def _diag(**overrides) -> ParseDiagnostics:
    base = {"item_count": 2, "food_item_count": 2, "subtotal": 10.0, "item_price_sum": 10.0}
    base.update(overrides)
    return ParseDiagnostics(**base)


class TestGatePass:
    def test_clean_receipt_passes_with_high_confidence(self, ocr_receipt_text):
        outcome = parse_receipt_text(ocr_receipt_text)
        decision = evaluate_ocr_gates(outcome.receipt, outcome.diagnostics, 91.0, **GATE_KWARGS)
        assert decision.passed is True
        assert decision.reasons == []
        assert decision.confidence == pytest.approx(0.91)

    def test_unverified_totals_pass_but_lower_confidence(self):
        decision = evaluate_ocr_gates(_receipt("A", "B"), _diag(subtotal=None, total=None), 90.0, **GATE_KWARGS)
        assert decision.passed is True
        assert "totals_unverified" in decision.notes
        assert decision.confidence == pytest.approx(0.81)


class TestGateFailures:
    def test_low_ocr_confidence(self):
        decision = evaluate_ocr_gates(_receipt("A", "B"), _diag(), 42.0, **GATE_KWARGS)
        assert decision.passed is False
        assert REASON_LOW_OCR_CONFIDENCE in decision.reasons
        assert decision.confidence < 0.5

    def test_missing_ocr_confidence_counts_as_low(self):
        decision = evaluate_ocr_gates(_receipt("A"), _diag(food_item_count=1), None, **GATE_KWARGS)
        assert REASON_LOW_OCR_CONFIDENCE in decision.reasons

    def test_zero_food_lines(self):
        decision = evaluate_ocr_gates(_receipt("BAG", is_food=False), _diag(food_item_count=0), 95.0, **GATE_KWARGS)
        assert decision.passed is False
        assert REASON_ZERO_FOOD_LINES in decision.reasons

    def test_totals_mismatch(self):
        decision = evaluate_ocr_gates(_receipt("A", "B"), _diag(subtotal=10.0, item_price_sum=7.5), 95.0, **GATE_KWARGS)
        assert decision.passed is False
        assert decision.reasons == [REASON_TOTALS_MISMATCH]

    def test_mostly_missing_qty_unit(self):
        diag = _diag(food_item_count=4, missing_qty_unit_count=3)
        decision = evaluate_ocr_gates(_receipt("A", "B", "C", "D"), diag, 95.0, **GATE_KWARGS)
        assert decision.passed is False
        assert decision.reasons == [REASON_MISSING_QTY_UNIT]

    def test_schema_invalid_when_names_are_blank(self):
        receipt = ParsedReceipt(items=[ParsedReceiptItem(store_item_name="  ", ingredient_name="", quantity="1", unit="each")])
        decision = evaluate_ocr_gates(receipt, _diag(food_item_count=1), 95.0, **GATE_KWARGS)
        assert decision.passed is False
        assert REASON_SCHEMA_INVALID in decision.reasons
        assert any("ingredient_name" in note for note in decision.notes)

    def test_multiple_reasons_accumulate(self):
        decision = evaluate_ocr_gates(_receipt("BAG", is_food=False), _diag(food_item_count=0, item_price_sum=1.0), 10.0, **GATE_KWARGS)
        assert set(decision.reasons) >= {REASON_LOW_OCR_CONFIDENCE, REASON_ZERO_FOOD_LINES, REASON_TOTALS_MISMATCH}


class TestTotalsMatch:
    def test_uses_subtotal_when_present(self):
        assert totals_match(_diag(subtotal=10.0, item_price_sum=10.1), 0.02) is True
        assert totals_match(_diag(subtotal=10.0, item_price_sum=10.5), 0.02) is False

    def test_falls_back_to_total_minus_tax(self):
        assert totals_match(_diag(subtotal=None, total=10.8, tax=0.8, item_price_sum=10.0), 0.02) is True

    def test_none_when_no_reference(self):
        assert totals_match(_diag(subtotal=None, total=None), 0.02) is None

    def test_absolute_floor_for_tiny_receipts(self):
        # 2% of $1.00 is 2c; the 5c floor should still accept a 4c rounding drift.
        assert totals_match(_diag(subtotal=1.00, item_price_sum=1.04), 0.02) is True


class TestSchemaErrors:
    def test_valid_receipt_has_no_errors(self):
        assert schema_errors(_receipt("A")) == []
