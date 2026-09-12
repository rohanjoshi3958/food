import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Response,
    UploadFile,
    status,
)
from sqlalchemy.orm import Session, joinedload

from app.config import settings
from app.database import get_db
from app.dependencies import get_current_user
from app.models import Receipt, User
from app.schemas import (
    ConfirmReceiptRequest,
    DraftIngredientItem,
    IngredientResponse,
    ReceiptResponse,
)
from app.services.ingredients import (
    AmbiguousPantryMatchError,
    canonicalize_draft_items,
    create_ingredient,
)
from app.services.ingredient_merge import merge_draft_items
from app.services.receipt_analyzer import STAGE_QUEUED, ReceiptAnalysisError
from app.services.receipt_jobs import (
    ANALYSIS_TIMEOUT,
    ANALYSIS_TIMEOUT_MESSAGE,
    MAX_RECEIPTS_PER_USER,
    delete_receipt_file,
    prune_old_receipts,
    run_receipt_analysis,
)
from app.validation import validate_ingredient_input

router = APIRouter(prefix="/receipts", tags=["receipts"])


def _discard_receipts_with_statuses(
    db: Session,
    user: User,
    statuses: tuple[str, ...],
) -> None:
    receipts = (
        db.query(Receipt)
        .filter(
            Receipt.user_id == user.id,
            Receipt.analysis_status.in_(statuses),
        )
        .all()
    )

    for receipt in receipts:
        delete_receipt_file(receipt)
        db.delete(receipt)

    if receipts:
        db.commit()


def _validate_draft_item(item: DraftIngredientItem) -> None:
    if not item.ingredient_name.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Enter an ingredient name.",
        )

    is_valid, error_message = validate_ingredient_input(item.quantity, item.unit)
    if not is_valid:
        label = item.ingredient_name.strip()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f'"{label}": {error_message}',
        )


def _manual_draft_items(raw_items: list[dict]) -> list[dict]:
    drafts: list[dict] = []
    for raw in raw_items:
        item = DraftIngredientItem.model_validate(
            {
                **raw,
                "store_item_name": raw.get("store_item_name") or raw.get("ingredient_name", ""),
                "is_manual": True,
            }
        )
        _validate_draft_item(item)
        drafts.append(item.model_dump())
    return drafts


def _parse_manual_items(manual_items: str) -> list[dict]:
    if not manual_items.strip():
        return []

    try:
        payload = json.loads(manual_items)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid manual ingredient data.",
        ) from exc

    if not isinstance(payload, list):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Manual ingredients must be a list.",
        )

    return _manual_draft_items(payload)


def _receipt_response(receipt: Receipt) -> ReceiptResponse:
    raw_drafts = [
        DraftIngredientItem.model_validate(item).model_dump()
        for item in (receipt.draft_items or [])
        if item.get("is_food", True)
    ]

    draft_items = [
        DraftIngredientItem.model_validate(item)
        for item in merge_draft_items(raw_drafts)
    ]

    return ReceiptResponse(
        id=receipt.id,
        original_name=receipt.original_name,
        filename=receipt.filename,
        store_name=receipt.store_name,
        analysis_status=receipt.analysis_status,
        analysis_stage=receipt.analysis_stage,
        analysis_error=receipt.analysis_error,
        uploaded_at=receipt.uploaded_at,
        ingredients=[
            IngredientResponse.model_validate(ingredient)
            for ingredient in receipt.ingredients
        ],
        draft_items=draft_items,
    )


@router.get("", response_model=list[ReceiptResponse])
def list_receipts(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ReceiptResponse]:
    # Failed analyses are never kept in Uploaded receipts.
    _discard_receipts_with_statuses(db, current_user, ("failed",))
    prune_old_receipts(db, current_user.id)

    receipts = (
        db.query(Receipt)
        .options(joinedload(Receipt.ingredients))
        .filter(
            Receipt.user_id == current_user.id,
            Receipt.analysis_status.notin_(
                ("pending_review", "processing", "failed")
            ),
        )
        .order_by(Receipt.uploaded_at.desc())
        .limit(MAX_RECEIPTS_PER_USER)
        .all()
    )
    return [_receipt_response(receipt) for receipt in receipts]


@router.post("/discard-pending", status_code=status.HTTP_204_NO_CONTENT)
def discard_pending_receipts(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """Remove unfinished reviews left behind by logout or closed tabs."""
    _discard_receipts_with_statuses(
        db,
        current_user,
        ("pending_review", "processing", "failed"),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/upload",
    response_model=ReceiptResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_receipt(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    manual_items: str = Form(default="[]"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReceiptResponse:
    """Store the upload and queue analysis.

    Returns right away with ``analysis_status="processing"``; the receipt id is
    the job id. Poll ``GET /receipts/{receipt_id}`` for progress and the result.
    """
    if not file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A file is required.",
        )

    pre_manual_items = _parse_manual_items(manual_items)

    upload_root = Path(settings.upload_dir) / current_user.id
    upload_root.mkdir(parents=True, exist_ok=True)

    safe_name = Path(file.filename).name
    stored_name = f"{uuid.uuid4().hex}_{safe_name}"
    destination = upload_root / stored_name

    contents = await file.read()
    destination.write_bytes(contents)

    receipt = Receipt(
        user_id=current_user.id,
        filename=str(destination),
        original_name=safe_name,
        analysis_status="processing",
        analysis_stage=STAGE_QUEUED,
    )
    db.add(receipt)
    db.commit()
    db.refresh(receipt)

    background_tasks.add_task(
        run_receipt_analysis,
        receipt.id,
        str(destination),
        pre_manual_items,
    )
    return _receipt_response(receipt)


def _analysis_timed_out(receipt: Receipt) -> bool:
    uploaded_at = receipt.uploaded_at
    if uploaded_at.tzinfo is None:
        uploaded_at = uploaded_at.replace(tzinfo=UTC)
    return datetime.now(UTC) - uploaded_at > ANALYSIS_TIMEOUT


@router.get("/{receipt_id}", response_model=ReceiptResponse)
def get_receipt(
    receipt_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReceiptResponse:
    """Poll endpoint for receipt analysis.

    While ``analysis_status`` is ``processing``, ``analysis_stage`` describes
    progress. Once it becomes ``pending_review`` the draft items are the result;
    ``failed`` carries the reason in ``analysis_error``.
    """
    # The background worker writes from its own session, so bypass anything
    # cached on this one and read the current row.
    receipt = (
        db.query(Receipt)
        .options(joinedload(Receipt.ingredients))
        .populate_existing()
        .filter(Receipt.id == receipt_id, Receipt.user_id == current_user.id)
        .first()
    )

    if receipt is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Receipt not found.")

    if receipt.analysis_status == "processing" and _analysis_timed_out(receipt):
        delete_receipt_file(receipt)
        receipt.analysis_status = "failed"
        receipt.analysis_stage = None
        receipt.analysis_error = ANALYSIS_TIMEOUT_MESSAGE
        receipt.draft_items = None
        db.commit()
        db.refresh(receipt)

    return _receipt_response(receipt)


@router.patch("/{receipt_id}/draft", response_model=ReceiptResponse)
def update_receipt_draft(
    receipt_id: str,
    payload: ConfirmReceiptRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReceiptResponse:
    receipt = (
        db.query(Receipt)
        .options(joinedload(Receipt.ingredients))
        .filter(Receipt.id == receipt_id, Receipt.user_id == current_user.id)
        .first()
    )

    if receipt is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Receipt not found.")

    if receipt.analysis_status != "pending_review":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only receipts awaiting review can update their draft items.",
        )

    receipt.draft_items = merge_draft_items(
        [
            DraftIngredientItem.model_validate(item.model_dump()).model_dump()
            for item in payload.items
        ]
    )
    for raw_item in receipt.draft_items or []:
        _validate_draft_item(DraftIngredientItem.model_validate(raw_item))
    db.commit()
    db.refresh(receipt)
    return _receipt_response(receipt)


@router.post("/{receipt_id}/cancel", response_model=ReceiptResponse)
def cancel_receipt_review(
    receipt_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReceiptResponse:
    receipt = (
        db.query(Receipt)
        .options(joinedload(Receipt.ingredients))
        .filter(Receipt.id == receipt_id, Receipt.user_id == current_user.id)
        .first()
    )

    if receipt is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Receipt not found.")

    if receipt.analysis_status != "pending_review":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only receipts awaiting review can be cancelled.",
        )

    receipt.analysis_status = "cancelled"
    receipt.draft_items = None
    receipt.analysis_error = None
    db.commit()
    db.refresh(receipt)
    return _receipt_response(receipt)


@router.post("/{receipt_id}/confirm", response_model=ReceiptResponse)
def confirm_receipt(
    receipt_id: str,
    payload: ConfirmReceiptRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReceiptResponse:
    receipt = (
        db.query(Receipt)
        .options(joinedload(Receipt.ingredients))
        .filter(Receipt.id == receipt_id, Receipt.user_id == current_user.id)
        .first()
    )

    if receipt is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Receipt not found.")

    if receipt.analysis_status not in {"pending_review", "processing"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This receipt has already been confirmed.",
        )

    if not payload.items:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Add at least one line item before saving.",
        )

    for existing in list(receipt.ingredients):
        db.delete(existing)

    food_payloads = [
        item.model_dump() for item in payload.items if item.is_food
    ]
    # Canonicalize names before merge so abbreviation/plural variants collapse.
    canonicalized_payloads = canonicalize_draft_items(db, current_user, food_payloads)
    merged_items = [
        DraftIngredientItem.model_validate(item)
        for item in merge_draft_items(canonicalized_payloads)
    ]

    for item in merged_items:
        _validate_draft_item(item)

    ambiguous_drafts: list[dict] = []
    try:
        for item in merged_items:
            try:
                create_ingredient(
                    db,
                    current_user,
                    item,
                    receipt_id=receipt.id,
                    allow_llm_merge=True,
                )
            except AmbiguousPantryMatchError as amb:
                # Keep ambiguous rows pending instead of inserting duplicates.
                draft = amb.draft_item.model_dump()
                draft["ingredient_name"] = amb.canonical_name or amb.ingredient_name
                ambiguous_drafts.append(draft)
    except ReceiptAnalysisError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    if ambiguous_drafts:
        receipt.analysis_status = "pending_review"
        receipt.draft_items = ambiguous_drafts
        receipt.analysis_error = (
            "Some ingredients matched more than one pantry item and need review."
        )
    else:
        receipt.analysis_status = "completed"
        receipt.draft_items = None
        receipt.analysis_error = None

    db.commit()
    db.refresh(receipt)
    prune_old_receipts(db, current_user.id)
    receipt = (
        db.query(Receipt)
        .options(joinedload(Receipt.ingredients))
        .filter(Receipt.id == receipt.id)
        .one_or_none()
    )
    if receipt is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Receipt was discarded because only the 3 most recent receipts are kept.",
        )
    return _receipt_response(receipt)
