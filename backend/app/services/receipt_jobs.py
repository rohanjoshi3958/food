"""Background receipt analysis.

Receipt uploads return immediately with ``analysis_status="processing"``; the
Claude call runs afterwards in a background task so no HTTP request waits on
it (App Runner caps sync requests at 120s and the Amplify SSR proxy at ~30s).
The receipt row doubles as the job record: clients poll ``GET /receipts/{id}``
until the status leaves ``processing``.
"""
import logging
from datetime import timedelta
from pathlib import Path

from sqlalchemy.orm import Session

from app import database
from app.models import Receipt
from app.schemas import DraftIngredientItem
from app.services.ingredient_merge import merge_draft_items
from app.services.receipt_analyzer import (
    STAGE_QUEUED,
    ParsedReceiptItem,
    ReceiptAnalysisError,
    analyze_receipt_image,
)
from app.storage import (
    InvalidObjectKey,
    StorageError,
    StorageObjectNotFound,
    delete_quietly,
    get_storage,
    validate_object_key,
)

logger = logging.getLogger(__name__)

MAX_RECEIPTS_PER_USER = 3

# A receipt still "processing" after this long has lost its worker (for example
# the server restarted mid-analysis) and is reported as failed on poll.
ANALYSIS_TIMEOUT = timedelta(minutes=10)
ANALYSIS_TIMEOUT_MESSAGE = (
    "Receipt analysis timed out. Please upload the receipt again."
)
GENERIC_FAILURE_MESSAGE = "Receipt analysis failed. Please try again."
NO_INGREDIENTS_MESSAGE = (
    "No ingredients found. Add items manually or try a clearer receipt photo."
)


def delete_stored_upload(stored: str | None) -> None:
    """Remove an upload by object key or legacy local path. Idempotent."""
    if not stored:
        return
    try:
        validate_object_key(stored)
    except InvalidObjectKey:
        # Rows written before object keys existed hold a local filesystem path.
        legacy = Path(stored)
        if legacy.is_file():
            legacy.unlink()
        return
    delete_quietly(stored)


def delete_receipt_file(receipt: Receipt) -> None:
    delete_stored_upload(receipt.filename)


def _read_receipt_bytes(stored: str) -> tuple[bytes, str]:
    """Load receipt bytes from UploadStorage, or a legacy local path."""
    try:
        validate_object_key(stored)
    except InvalidObjectKey:
        path = Path(stored)
        try:
            return path.read_bytes(), path.name
        except OSError as exc:
            raise ReceiptAnalysisError(
                "Unable to read the uploaded receipt."
            ) from exc
    try:
        return get_storage().get(stored), stored.rsplit("/", 1)[-1]
    except (StorageObjectNotFound, StorageError) as exc:
        raise ReceiptAnalysisError("Unable to read the uploaded receipt.") from exc


def prune_old_receipts(db: Session, user_id: str) -> None:
    receipts = (
        db.query(Receipt)
        .filter(Receipt.user_id == user_id)
        .order_by(Receipt.uploaded_at.desc())
        .all()
    )
    stale = receipts[MAX_RECEIPTS_PER_USER:]
    stored = [receipt.filename for receipt in stale]
    for receipt in stale:
        db.delete(receipt)
    if stale:
        db.commit()
    for filename in stored:
        delete_stored_upload(filename)


def draft_items_from_parsed(items: list[ParsedReceiptItem]) -> list[dict]:
    drafts = [
        DraftIngredientItem(
            store_item_name=item.store_item_name,
            ingredient_name=item.ingredient_name,
            is_food=item.is_food,
            quantity=item.quantity,
            unit=item.unit,
            serving_size=item.serving_size,
            servings_per_container=item.servings_per_container,
            calories=item.calories,
            protein_g=item.protein_g,
            carbs_g=item.carbs_g,
            fat_g=item.fat_g,
            fiber_g=item.fiber_g,
            sodium_mg=item.sodium_mg,
            nutrition_notes=item.nutrition_notes,
            is_manual=False,
        ).model_dump()
        for item in items
        if item.is_food
    ]
    return merge_draft_items(drafts)


def _load_processing_receipt(db: Session, receipt_id: str) -> Receipt | None:
    """Fetch the receipt fresh from the database, only if analysis is still expected.

    The user may have discarded the receipt (logout, closed tab) or it may have
    been pruned while Claude was running; in that case the result is dropped.
    """
    receipt = (
        db.query(Receipt)
        .populate_existing()
        .filter(Receipt.id == receipt_id)
        .first()
    )
    if receipt is None or receipt.analysis_status != "processing":
        return None
    return receipt


def _set_stage(db: Session, receipt_id: str, stage: str) -> None:
    receipt = _load_processing_receipt(db, receipt_id)
    if receipt is None:
        return
    receipt.analysis_stage = stage
    db.commit()


def _mark_failed(db: Session, receipt_id: str, message: str) -> None:
    receipt = _load_processing_receipt(db, receipt_id)
    if receipt is None:
        return
    # The record is kept so the poll can report the error; the upload itself
    # is no longer needed. Failed receipts are purged on the next list/discard.
    stored = receipt.filename
    receipt.analysis_status = "failed"
    receipt.analysis_stage = None
    receipt.analysis_error = message
    receipt.draft_items = None
    db.commit()
    delete_stored_upload(stored)


def run_receipt_analysis(
    receipt_id: str,
    object_key: str,
    pre_manual_items: list[dict] | None = None,
) -> None:
    """Analyze an uploaded receipt and store drafts on the receipt row.

    ``object_key`` is the UploadStorage key (or a legacy local path).
    Runs outside any request, so it opens its own database session.
    """
    db = database.SessionLocal()
    try:
        _set_stage(db, receipt_id, STAGE_QUEUED)

        try:
            contents, filename = _read_receipt_bytes(object_key)
            parsed = analyze_receipt_image(
                contents,
                filename,
                on_progress=lambda stage: _set_stage(db, receipt_id, stage),
            )
            draft_items = merge_draft_items(
                list(pre_manual_items or []) + draft_items_from_parsed(parsed.items)
            )
            if not draft_items:
                raise ReceiptAnalysisError(NO_INGREDIENTS_MESSAGE)
        except ReceiptAnalysisError as exc:
            db.rollback()
            _mark_failed(db, receipt_id, str(exc))
            return
        except Exception:
            logger.exception("Receipt analysis failed for receipt %s", receipt_id)
            db.rollback()
            _mark_failed(db, receipt_id, GENERIC_FAILURE_MESSAGE)
            return

        receipt = _load_processing_receipt(db, receipt_id)
        if receipt is None:
            return

        receipt.store_name = parsed.store_name
        receipt.analysis_status = "pending_review"
        receipt.analysis_stage = None
        receipt.analysis_error = None
        receipt.draft_items = draft_items
        db.commit()

        prune_old_receipts(db, receipt.user_id)
    except Exception:
        logger.exception("Unexpected error finishing receipt %s", receipt_id)
        db.rollback()
    finally:
        db.close()
