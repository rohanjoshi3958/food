"""Historical receipt reprocess via the Anthropic Message Batches API (FOOD-57).

Interactive ``POST /api/receipts/upload`` stays on the sync Messages API.
This CLI rebuilds vision extract (and optional per-item nutrition) for
receipts that already have a stored file, at batch prices (~50% off) with
the 1-hour prompt-cache TTL.

By default nothing is written. Pass ``--apply`` to replace
``receipts.analysis_result`` only — pantry rows, draft items, and
``analysis_status`` are left alone.

Usage (from ``backend/``)::

    python -m app.jobs.reprocess_receipts
    python -m app.jobs.reprocess_receipts --receipt-id <uuid> --apply
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

import anthropic
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.models import Receipt
from app.services.anthropic_batch import (
    BatchMessageRequest,
    BatchRunInterrupted,
    BatchRunResult,
    run_message_batch,
)
from app.services.model_router import route_model
from app.services.receipt_analyzer import (
    NUTRITION_ESTIMATE_PROMPT,
    NUTRITION_ESTIMATE_USER_PROMPT,
    RECEIPT_ANALYSIS_PROMPT,
    ParsedReceipt,
    ParsedReceiptItem,
    ReceiptAnalysisError,
    _media_type_for_path,
    parse_nutrition_message,
    parse_receipt_message,
)

logger = logging.getLogger(__name__)

VISION_CHUNK_SIZE = 50
NUTRITION_CHUNK_SIZE = 200
VISION_MAX_TOKENS = 4096
NUTRITION_MAX_TOKENS = 1024


@dataclass
class NutritionWork:
    custom_id: str
    receipt_id: str
    item_index: int
    item: ParsedReceiptItem


@dataclass
class ReprocessReport:
    vision_ok: dict[str, ParsedReceipt] = field(default_factory=dict)
    vision_failed: dict[str, str] = field(default_factory=dict)
    skipped_missing_file: list[str] = field(default_factory=list)
    nutrition_failed: dict[str, str] = field(default_factory=dict)
    applied: list[str] = field(default_factory=list)
    apply_failed: dict[str, str] = field(default_factory=dict)
    batch_ids: list[str] = field(default_factory=list)
    interrupted: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "vision_ok": list(self.vision_ok),
            "vision_failed": self.vision_failed,
            "skipped_missing_file": self.skipped_missing_file,
            "nutrition_failed": self.nutrition_failed,
            "applied": self.applied,
            "apply_failed": self.apply_failed,
            "batch_ids": self.batch_ids,
            "interrupted": self.interrupted,
        }

    def exit_status(self) -> int:
        if (
            self.vision_failed
            or self.nutrition_failed
            or self.apply_failed
            or self.interrupted
        ):
            return 1
        return 0


def _require_api_key() -> None:
    if not settings.anthropic_api_key:
        raise SystemExit(
            "ANTHROPIC_API_KEY is not configured. Add it to the environment / .env."
        )


def get_client() -> anthropic.Anthropic:
    _require_api_key()
    return anthropic.Anthropic(api_key=settings.anthropic_api_key)


def load_receipts(
    db: Session,
    *,
    receipt_ids: Sequence[str] | None = None,
    user_id: str | None = None,
    limit: int | None = None,
) -> list[Receipt]:
    query = db.query(Receipt).order_by(Receipt.uploaded_at.desc())
    if receipt_ids:
        query = query.filter(Receipt.id.in_(list(receipt_ids)))
    if user_id:
        query = query.filter(Receipt.user_id == user_id)
    if limit is not None:
        query = query.limit(limit)
    return list(query.all())


def build_vision_request(receipt: Receipt) -> BatchMessageRequest | None:
    """One ``receipt.analyze_image`` batch item, or ``None`` if the file is gone."""
    path = Path(receipt.filename) if receipt.filename else None
    if path is None or not path.is_file():
        return None

    try:
        media_type, content_type = _media_type_for_path(path)
        encoded = base64.standard_b64encode(path.read_bytes()).decode("utf-8")
    except (OSError, ReceiptAnalysisError):
        return None
    content_block = {
        "type": content_type,
        "source": {"type": "base64", "media_type": media_type, "data": encoded},
    }
    return BatchMessageRequest(
        custom_id=receipt.id,
        call_site="receipt.analyze_image",
        model=route_model("receipt.analyze_image").model,
        max_tokens=VISION_MAX_TOKENS,
        system_prefix=RECEIPT_ANALYSIS_PROMPT,
        messages=[{"role": "user", "content": [content_block]}],
    )


def nutrition_custom_id(receipt_id: str, item_index: int) -> str:
    return f"n{item_index:03d}-{receipt_id}"


def build_nutrition_work(
    receipt_id: str, parsed: ParsedReceipt
) -> list[NutritionWork]:
    work: list[NutritionWork] = []
    for index, item in enumerate(parsed.items):
        if not item.is_food:
            continue
        work.append(
            NutritionWork(
                custom_id=nutrition_custom_id(receipt_id, index),
                receipt_id=receipt_id,
                item_index=index,
                item=item,
            )
        )
    return work


def build_nutrition_request(work: NutritionWork) -> BatchMessageRequest:
    qty = (work.item.quantity or "").strip() or "unknown"
    unit_label = (work.item.unit or "").strip() or "unknown"
    return BatchMessageRequest(
        custom_id=work.custom_id,
        call_site="receipt.nutrition_estimate",
        model=route_model("receipt.nutrition_estimate").model,
        max_tokens=NUTRITION_MAX_TOKENS,
        system_prefix=NUTRITION_ESTIMATE_PROMPT,
        messages=[
            {
                "role": "user",
                "content": NUTRITION_ESTIMATE_USER_PROMPT.format(
                    ingredient_name=work.item.ingredient_name,
                    quantity=qty,
                    unit=unit_label,
                ),
            }
        ],
    )


def _merge_nutrition(
    item: ParsedReceiptItem, estimated: ParsedReceiptItem
) -> ParsedReceiptItem:
    return item.model_copy(
        update={
            "quantity": item.quantity or estimated.quantity or "1",
            "unit": item.unit or estimated.unit or "each",
            "serving_size": estimated.serving_size,
            "servings_per_container": estimated.servings_per_container,
            "calories": estimated.calories,
            "protein_g": estimated.protein_g,
            "carbs_g": estimated.carbs_g,
            "fat_g": estimated.fat_g,
            "fiber_g": estimated.fiber_g,
            "sodium_mg": estimated.sodium_mg,
            "nutrition_notes": estimated.nutrition_notes,
        }
    )


def apply_parsed_receipt(db: Session, receipt_id: str, parsed: ParsedReceipt) -> None:
    receipt = db.query(Receipt).filter(Receipt.id == receipt_id).one()
    receipt.analysis_result = parsed.model_dump()
    db.commit()


def _run_kwargs(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "poll_interval": args.poll_interval,
        "poll_timeout": args.poll_timeout,
        "max_retries": args.max_retries,
    }


def reprocess_receipts(
    db: Session,
    client: Any,
    receipts: list[Receipt],
    *,
    apply: bool = False,
    skip_nutrition: bool = False,
    poll_interval: float = 30.0,
    poll_timeout: float = 24 * 60 * 60,
    max_retries: int = 1,
    sleep=None,
    clock=None,
) -> ReprocessReport:
    """Submit vision (+ nutrition) batches and optionally persist analysis_result."""
    report = ReprocessReport()
    run_kw: dict[str, Any] = {
        "poll_interval": poll_interval,
        "poll_timeout": poll_timeout,
        "max_retries": max_retries,
    }
    if sleep is not None:
        run_kw["sleep"] = sleep
    if clock is not None:
        run_kw["clock"] = clock

    vision_requests: list[BatchMessageRequest] = []
    for receipt in receipts:
        request = build_vision_request(receipt)
        if request is None:
            report.skipped_missing_file.append(receipt.id)
            logger.warning("Skipping receipt %s: stored file missing", receipt.id)
            continue
        vision_requests.append(request)

    vision_interrupted = False
    if vision_requests:
        try:
            vision = run_message_batch(
                client,
                vision_requests,
                chunk_size=VISION_CHUNK_SIZE,
                **run_kw,
            )
        except BatchRunInterrupted as exc:
            vision = exc.outcome
            vision_interrupted = True
            report.interrupted = str(exc)
        report.batch_ids.extend(vision.submitted_batch_ids)
        _record_vision(vision, report)

    if vision_interrupted or skip_nutrition or not report.vision_ok:
        _maybe_apply(db, report, apply)
        return report

    nutrition_work: list[NutritionWork] = []
    for receipt_id, parsed in report.vision_ok.items():
        nutrition_work.extend(build_nutrition_work(receipt_id, parsed))

    if not nutrition_work:
        _maybe_apply(db, report, apply)
        return report

    try:
        nutrition = run_message_batch(
            client,
            [build_nutrition_request(item) for item in nutrition_work],
            chunk_size=NUTRITION_CHUNK_SIZE,
            **run_kw,
        )
    except BatchRunInterrupted as exc:
        nutrition = exc.outcome
        report.interrupted = str(exc)
    report.batch_ids.extend(nutrition.submitted_batch_ids)
    _apply_nutrition(nutrition, nutrition_work, report)
    _maybe_apply(db, report, apply)
    return report


def _record_vision(vision: BatchRunResult, report: ReprocessReport) -> None:
    for custom_id, item in vision.succeeded.items():
        try:
            report.vision_ok[custom_id] = parse_receipt_message(item.message)
        except ReceiptAnalysisError as exc:
            report.vision_failed[custom_id] = str(exc)
    for custom_id, item in vision.failed.items():
        report.vision_failed[custom_id] = (
            item.error_message or item.error_type or item.type
        )


def _apply_nutrition(
    nutrition: BatchRunResult,
    work_items: list[NutritionWork],
    report: ReprocessReport,
) -> None:
    by_id = {item.custom_id: item for item in work_items}
    for custom_id, item in nutrition.succeeded.items():
        work = by_id[custom_id]
        parsed = report.vision_ok.get(work.receipt_id)
        if parsed is None:
            continue
        try:
            estimated = parse_nutrition_message(
                item.message,
                ingredient_name=work.item.ingredient_name,
                quantity=work.item.quantity,
                unit=work.item.unit,
            )
        except ReceiptAnalysisError as exc:
            report.nutrition_failed[custom_id] = str(exc)
            continue
        items = list(parsed.items)
        items[work.item_index] = _merge_nutrition(items[work.item_index], estimated)
        report.vision_ok[work.receipt_id] = ParsedReceipt(
            store_name=parsed.store_name, items=items
        )
    for custom_id, item in nutrition.failed.items():
        report.nutrition_failed[custom_id] = (
            item.error_message or item.error_type or item.type
        )


def _maybe_apply(db: Session, report: ReprocessReport, apply: bool) -> None:
    if not apply:
        return
    for receipt_id, parsed in report.vision_ok.items():
        try:
            apply_parsed_receipt(db, receipt_id, parsed)
            report.applied.append(receipt_id)
        except Exception as exc:
            db.rollback()
            logger.exception("Failed to persist analysis_result for %s", receipt_id)
            report.apply_failed[receipt_id] = str(exc)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Reprocess stored receipt images through Anthropic Message Batches. "
            "Interactive upload stays on the sync Messages API."
        )
    )
    parser.add_argument(
        "--receipt-id",
        action="append",
        dest="receipt_ids",
        default=[],
        help="Limit to one receipt id (repeatable).",
    )
    parser.add_argument("--user-id", help="Limit to one user's receipts.")
    parser.add_argument("--limit", type=int, help="Max receipts, most recent first.")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write analysis_result for successful parses. Does not touch pantry.",
    )
    parser.add_argument(
        "--skip-nutrition",
        action="store_true",
        help="Vision extract only (skip the per-item nutrition batch).",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=30.0,
        help="Seconds between batch status polls (default 30).",
    )
    parser.add_argument(
        "--poll-timeout",
        type=float,
        default=24 * 60 * 60,
        help="Give up polling after this many seconds (default 24h).",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=1,
        help="Extra batch rounds for expired/canceled/server errors (default 1).",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args = build_parser().parse_args(argv)
    client = get_client()
    db = SessionLocal()
    try:
        receipts = load_receipts(
            db,
            receipt_ids=args.receipt_ids or None,
            user_id=args.user_id,
            limit=args.limit,
        )
        if not receipts:
            print(json.dumps({"vision_ok": [], "message": "no receipts matched"}))
            return 0
        report = reprocess_receipts(
            db,
            client,
            receipts,
            apply=args.apply,
            skip_nutrition=args.skip_nutrition,
            **_run_kwargs(args),
        )
        print(json.dumps(report.to_json(), indent=2))
        return report.exit_status()
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
