"""Receipt extraction facade (FOOD-55 slices 1-2).

Order of rungs when ``RECEIPT_OCR_FIRST`` is on:

    cache -> ocr (Tesseract + rules + gates) -> haiku (text, optional)
          -> sonnet (vision on downsampled image, optional) -> opus_baseline

With the flag off this is exactly the pre-FOOD-55 behaviour
(``analyze_receipt_image``), optionally short-circuited by the hash cache.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import RECEIPT_ANTHROPIC_MODEL, settings
from app.models import Receipt
from app.services.receipt_analyzer import (
    ParsedReceipt,
    ReceiptAnalysisError,
    _media_type_for_path,
    analyze_receipt_image,
    enrich_receipt_nutrition,
    extract_receipt_text,
    extract_receipt_vision,
    require_food_items,
)
from app.services.receipt_gates import GateDecision, evaluate_ocr_gates, schema_errors
from app.services.receipt_ocr import OcrResult, OcrUnavailableError, run_tesseract
from app.services.receipt_parser import parse_receipt_text
from app.services.receipt_preprocess import content_hash, normalize_for_vision, prepare_for_ocr

logger = logging.getLogger(__name__)

PATH_CACHE = "cache"
PATH_OCR = "ocr"
PATH_HAIKU = "haiku"
PATH_SONNET = "sonnet"
PATH_OPUS_BASELINE = "opus_baseline"

# Statuses whose stored analysis_result is safe to reuse.
_CACHEABLE_STATUSES = ("pending_review", "completed", "cancelled")


@dataclass
class ReceiptAnalysisOutcome:
    """Result of one extraction plus the structured fields FOOD-54 will export.

    Nothing here is emitted anywhere yet; the metrics helper from Metrics eng
    is the single wire-up point (see the TODO in routers/receipts.py).
    """

    parsed: ParsedReceipt
    path: str  # cache|ocr|haiku|sonnet|opus_baseline
    content_hash: str
    latency_ms: float
    confidence: float | None = None  # 0-1 gate score; 1.0 for cache, None for LLM rungs
    ocr_confidence: float | None = None  # raw Tesseract mean word confidence, 0-100
    # LLM tokens consumed by the winning rung. Left None until FOOD-54 defines
    # how usage is attributed across escalations.
    tokens: int | None = None
    gate_reasons: list[str] = field(default_factory=list)
    # Rungs tried (and failed) before ``path`` succeeded, in order.
    escalations: list[str] = field(default_factory=list)


def find_cached_analysis(
    db: Session,
    user_id: str,
    digest: str,
    *,
    exclude_receipt_id: str | None = None,
) -> ParsedReceipt | None:
    """Most recent successful analysis of the same bytes for this user, if any."""
    query = (
        db.query(Receipt)
        .filter(
            Receipt.user_id == user_id,
            Receipt.content_hash == digest,
            Receipt.analysis_result.isnot(None),
            Receipt.analysis_status.in_(_CACHEABLE_STATUSES),
        )
        .order_by(Receipt.uploaded_at.desc())
    )
    if exclude_receipt_id is not None:
        query = query.filter(Receipt.id != exclude_receipt_id)

    for candidate in query.limit(3):
        try:
            return ParsedReceipt.model_validate(candidate.analysis_result)
        except Exception:
            logger.warning("Ignoring unparseable cached analysis on receipt %s", candidate.id)
    return None


def path_for_model(model: str) -> str:
    lowered = model.lower()
    if "haiku" in lowered:
        return PATH_HAIKU
    if "sonnet" in lowered:
        return PATH_SONNET
    return PATH_OPUS_BASELINE


def _run_ocr(contents: bytes) -> OcrResult:
    image = prepare_for_ocr(contents)
    if image is None:
        raise OcrUnavailableError("Upload is not a decodable image (PDF or corrupt file).")
    return run_tesseract(image)


def _ocr_rung(contents: bytes) -> tuple[ParsedReceipt | None, GateDecision, OcrResult | None]:
    """Tesseract -> rules -> gates. Never raises; a failure is a failed gate."""
    try:
        ocr = _run_ocr(contents)
    except OcrUnavailableError as exc:
        logger.info("OCR unavailable, escalating: %s", exc)
        return None, GateDecision(passed=False, confidence=0.0, reasons=["ocr_unavailable"]), None

    try:
        outcome = parse_receipt_text(ocr.text)
    except Exception as exc:  # parser bugs must escalate, not 500
        logger.exception("Receipt parser failed")
        return None, GateDecision(passed=False, confidence=0.0, reasons=["schema_invalid"], notes=[str(exc)]), ocr

    decision = evaluate_ocr_gates(
        outcome.receipt,
        outcome.diagnostics,
        ocr.confidence,
        min_ocr_confidence=settings.receipt_ocr_min_confidence,
        max_missing_qty_unit_ratio=settings.receipt_ocr_max_missing_qty_unit_ratio,
        totals_tolerance=settings.receipt_ocr_totals_tolerance,
    )
    return outcome.receipt, decision, ocr


def _llm_rung_ok(parsed: ParsedReceipt) -> list[str]:
    reasons: list[str] = []
    if schema_errors(parsed):
        reasons.append("schema_invalid")
    if not any(item.is_food for item in parsed.items):
        reasons.append("zero_food_lines")
    return reasons


def _analyze_ocr_first(file_path: Path, contents: bytes, digest: str, started: float) -> ReceiptAnalysisOutcome:
    escalations: list[str] = []
    gate_reasons: list[str] = []

    parsed, decision, ocr = _ocr_rung(contents)
    ocr_confidence = ocr.confidence if ocr else None
    if parsed is not None and decision.passed:
        return ReceiptAnalysisOutcome(
            parsed=enrich_receipt_nutrition(parsed),
            path=PATH_OCR,
            content_hash=digest,
            latency_ms=(time.perf_counter() - started) * 1000,
            confidence=decision.confidence,
            ocr_confidence=ocr_confidence,
        )
    escalations.append(PATH_OCR)
    gate_reasons.extend(decision.reasons)

    # Soft fallback: cheap text model over the OCR output.
    text_model = settings.receipt_ocr_text_fallback_model
    if text_model and ocr is not None and ocr.text.strip():
        rung = path_for_model(text_model)
        try:
            candidate = extract_receipt_text(ocr.text, model=text_model)
            reasons = _llm_rung_ok(candidate)
        except ReceiptAnalysisError as exc:
            candidate, reasons = None, [f"{rung}_error"]
            logger.info("Text fallback failed, escalating: %s", exc)
        if candidate is not None and not reasons:
            return ReceiptAnalysisOutcome(
                parsed=enrich_receipt_nutrition(candidate),
                path=rung,
                content_hash=digest,
                latency_ms=(time.perf_counter() - started) * 1000,
                ocr_confidence=ocr_confidence,
                gate_reasons=gate_reasons,
                escalations=escalations,
            )
        escalations.append(rung)
        gate_reasons.extend(reasons)

    # Hard fallback: vision on a downsampled image. Defaults to the Opus
    # baseline model when no Sonnet override is configured.
    vision_model = settings.receipt_ocr_vision_fallback_model or RECEIPT_ANTHROPIC_MODEL
    media_type, _content_type = _media_type_for_path(file_path)
    normalized = normalize_for_vision(contents, settings.receipt_vision_long_edge)
    if normalized is not None:
        data, media_type = normalized.data, normalized.media_type
    else:
        data = contents
    parsed = require_food_items(extract_receipt_vision(data, media_type, model=vision_model))
    return ReceiptAnalysisOutcome(
        parsed=enrich_receipt_nutrition(parsed),
        path=path_for_model(vision_model),
        content_hash=digest,
        latency_ms=(time.perf_counter() - started) * 1000,
        ocr_confidence=ocr_confidence,
        gate_reasons=gate_reasons,
        escalations=escalations,
    )


def analyze_receipt(
    file_path: Path,
    contents: bytes,
    *,
    db: Session,
    user_id: str,
    receipt_id: str | None = None,
) -> ReceiptAnalysisOutcome:
    """Entry point used by the upload route. Raises ``ReceiptAnalysisError`` like before."""
    started = time.perf_counter()
    digest = content_hash(contents)

    if settings.receipt_analysis_cache:
        cached = find_cached_analysis(db, user_id, digest, exclude_receipt_id=receipt_id)
        if cached is not None:
            return ReceiptAnalysisOutcome(
                parsed=cached,
                path=PATH_CACHE,
                content_hash=digest,
                latency_ms=(time.perf_counter() - started) * 1000,
                confidence=1.0,
            )

    if not settings.anthropic_api_key:
        raise ReceiptAnalysisError(
            "Anthropic API key is not configured. Add ANTHROPIC_API_KEY to your .env file."
        )

    if settings.receipt_ocr_first:
        return _analyze_ocr_first(file_path, contents, digest, started)

    parsed = analyze_receipt_image(file_path)
    return ReceiptAnalysisOutcome(
        parsed=parsed,
        path=PATH_OPUS_BASELINE,
        content_hash=digest,
        latency_ms=(time.perf_counter() - started) * 1000,
    )
