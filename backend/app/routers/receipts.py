import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.orm import Session, joinedload

from app.config import settings
from app.database import get_db
from app.dependencies import get_current_user
from app.models import Ingredient, Receipt, User
from app.schemas import (
    ConfirmReceiptRequest,
    DraftIngredientItem,
    IngredientResponse,
    ReceiptResponse,
)
from app.services.receipt_analyzer import (
    ReceiptAnalysisError,
    analyze_receipt_image,
    estimate_ingredient_nutrition,
)

router = APIRouter(prefix="/receipts", tags=["receipts"])


def _draft_from_parsed_items(items: list) -> list[dict]:
    return [
        DraftIngredientItem(
            store_item_name=item.store_item_name,
            ingredient_name=item.ingredient_name,
            quantity=item.quantity,
            unit=item.unit,
            serving_size=item.serving_size,
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
    ]


def _receipt_response(receipt: Receipt) -> ReceiptResponse:
    draft_items = [
        DraftIngredientItem.model_validate(item)
        for item in (receipt.draft_items or [])
    ]

    return ReceiptResponse(
        id=receipt.id,
        original_name=receipt.original_name,
        filename=receipt.filename,
        store_name=receipt.store_name,
        analysis_status=receipt.analysis_status,
        analysis_error=receipt.analysis_error,
        uploaded_at=receipt.uploaded_at,
        ingredients=[
            IngredientResponse.model_validate(ingredient)
            for ingredient in receipt.ingredients
        ],
        draft_items=draft_items,
    )


def _resolve_item_nutrition(item: DraftIngredientItem) -> DraftIngredientItem:
    if not item.is_manual:
        return item

    try:
        estimated = estimate_ingredient_nutrition(
            item.ingredient_name,
            item.quantity,
            item.unit,
        )
    except ReceiptAnalysisError:
        return item

    return DraftIngredientItem(
        store_item_name=item.store_item_name or item.ingredient_name,
        ingredient_name=item.ingredient_name,
        quantity=item.quantity,
        unit=item.unit,
        serving_size=estimated.serving_size,
        calories=estimated.calories,
        protein_g=estimated.protein_g,
        carbs_g=estimated.carbs_g,
        fat_g=estimated.fat_g,
        fiber_g=estimated.fiber_g,
        sodium_mg=estimated.sodium_mg,
        nutrition_notes=estimated.nutrition_notes,
        is_manual=True,
    )


@router.get("", response_model=list[ReceiptResponse])
def list_receipts(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ReceiptResponse]:
    receipts = (
        db.query(Receipt)
        .options(joinedload(Receipt.ingredients))
        .filter(Receipt.user_id == current_user.id)
        .order_by(Receipt.uploaded_at.desc())
        .all()
    )
    return [_receipt_response(receipt) for receipt in receipts]


@router.get("/{receipt_id}", response_model=ReceiptResponse)
def get_receipt(
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

    return _receipt_response(receipt)


@router.post("/upload", response_model=ReceiptResponse, status_code=status.HTTP_201_CREATED)
async def upload_receipt(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReceiptResponse:
    if not file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A file is required.",
        )

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
    )
    db.add(receipt)
    db.commit()
    db.refresh(receipt)

    try:
        parsed = analyze_receipt_image(destination)

        receipt.store_name = parsed.store_name
        receipt.analysis_status = "pending_review"
        receipt.analysis_error = None
        receipt.draft_items = _draft_from_parsed_items(parsed.items)

        db.commit()
        db.refresh(receipt)
        return _receipt_response(receipt)
    except ReceiptAnalysisError as exc:
        receipt.analysis_status = "failed"
        receipt.analysis_error = str(exc)
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        receipt.analysis_status = "failed"
        receipt.analysis_error = "Receipt analysis failed. Please try again."
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Receipt analysis failed. Please try again.",
        ) from exc


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
            detail="Add at least one ingredient before saving.",
        )

    for existing in list(receipt.ingredients):
        db.delete(existing)

    resolved_items = [_resolve_item_nutrition(item) for item in payload.items]

    for item in resolved_items:
        ingredient = Ingredient(
            user_id=current_user.id,
            receipt_id=receipt.id,
            name=item.ingredient_name.strip(),
            store_item_name=item.store_item_name or item.ingredient_name,
            quantity=item.quantity,
            unit=item.unit,
            serving_size=item.serving_size,
            calories=item.calories,
            protein_g=item.protein_g,
            carbs_g=item.carbs_g,
            fat_g=item.fat_g,
            fiber_g=item.fiber_g,
            sodium_mg=item.sodium_mg,
            nutrition_notes=item.nutrition_notes,
        )
        db.add(ingredient)

    receipt.analysis_status = "completed"
    receipt.draft_items = None
    receipt.analysis_error = None

    db.commit()
    db.refresh(receipt)
    receipt = (
        db.query(Receipt)
        .options(joinedload(Receipt.ingredients))
        .filter(Receipt.id == receipt.id)
        .one()
    )
    return _receipt_response(receipt)
