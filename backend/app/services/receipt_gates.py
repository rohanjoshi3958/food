"""Confidence gates for the OCR-first receipt path (FOOD-55 slice 2).

A gate failure means "escalate to the next rung", never "reject the upload".
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import ValidationError

from app.services.receipt_analyzer import ParsedReceipt
from app.services.receipt_parser import ParseDiagnostics

REASON_LOW_OCR_CONFIDENCE = "low_ocr_confidence"
REASON_ZERO_FOOD_LINES = "zero_food_lines"
REASON_TOTALS_MISMATCH = "totals_mismatch"
REASON_MISSING_QTY_UNIT = "missing_qty_unit"
REASON_SCHEMA_INVALID = "schema_invalid"

TOTALS_ABSOLUTE_FLOOR = 0.05


@dataclass
class GateDecision:
    passed: bool
    # 0-1 score reported in telemetry; derived from OCR confidence and checks.
    confidence: float
    reasons: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def schema_errors(receipt: ParsedReceipt) -> list[str]:
    """Re-validate the public shape plus the invariants the UI relies on."""
    errors: list[str] = []
    try:
        ParsedReceipt.model_validate(receipt.model_dump())
    except ValidationError as exc:
        errors.append(str(exc))
        return errors

    for index, item in enumerate(receipt.items):
        if not item.store_item_name.strip():
            errors.append(f"items[{index}].store_item_name is empty")
        if not item.ingredient_name.strip():
            errors.append(f"items[{index}].ingredient_name is empty")
    return errors


def totals_match(
    diagnostics: ParseDiagnostics,
    tolerance: float,
) -> bool | None:
    """True/False when the receipt printed a reference total, None when it did not."""
    reference = diagnostics.subtotal
    if reference is None and diagnostics.total is not None:
        reference = round(diagnostics.total - (diagnostics.tax or 0.0), 2)
    if reference is None or reference <= 0:
        return None

    allowed = max(TOTALS_ABSOLUTE_FLOOR, abs(reference) * tolerance)
    return abs(diagnostics.item_price_sum - reference) <= allowed


def evaluate_ocr_gates(
    receipt: ParsedReceipt,
    diagnostics: ParseDiagnostics,
    ocr_confidence: float | None,
    *,
    min_ocr_confidence: float,
    max_missing_qty_unit_ratio: float,
    totals_tolerance: float,
) -> GateDecision:
    reasons: list[str] = []
    notes: list[str] = []

    if ocr_confidence is None or ocr_confidence < min_ocr_confidence:
        reasons.append(REASON_LOW_OCR_CONFIDENCE)

    invalid = schema_errors(receipt)
    if invalid:
        reasons.append(REASON_SCHEMA_INVALID)
        notes.extend(invalid[:3])

    if diagnostics.food_item_count == 0:
        reasons.append(REASON_ZERO_FOOD_LINES)

    matched = totals_match(diagnostics, totals_tolerance)
    if matched is False:
        reasons.append(REASON_TOTALS_MISMATCH)
    elif matched is None:
        notes.append("totals_unverified")

    if (
        diagnostics.food_item_count > 0
        and diagnostics.missing_qty_unit_ratio > max_missing_qty_unit_ratio
    ):
        reasons.append(REASON_MISSING_QTY_UNIT)

    confidence = (ocr_confidence or 0.0) / 100.0
    if matched is None:
        confidence *= 0.9
    confidence = max(0.0, min(1.0, confidence)) if not reasons else min(confidence, 0.49)

    return GateDecision(passed=not reasons, confidence=round(confidence, 3), reasons=reasons, notes=notes)
