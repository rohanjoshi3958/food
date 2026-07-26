from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.dependencies import get_current_user
from app.models import CookbookEntry, User
from app.schemas import CookbookEntryResponse, cookbook_entry_response
from app.services.cookbook import remove_cookbook_entry

router = APIRouter(prefix="/cookbook", tags=["cookbook"])


def _get_entry_for_user(entry_id: str, current_user: User, db: Session) -> CookbookEntry:
    entry = (
        db.query(CookbookEntry)
        .filter(CookbookEntry.id == entry_id, CookbookEntry.user_id == current_user.id)
        .first()
    )

    if entry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entry not found.")

    return entry


def _cookbook_photo_path(user_id: str, filename: str) -> Path:
    return Path(settings.cookbook_upload_dir) / user_id / filename


@router.get("", response_model=list[CookbookEntryResponse])
def list_cookbook_entries(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[CookbookEntryResponse]:
    entries = (
        db.query(CookbookEntry)
        .filter(CookbookEntry.user_id == current_user.id)
        .order_by(CookbookEntry.created_at.desc())
        .all()
    )
    return [cookbook_entry_response(entry) for entry in entries]


@router.delete("/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_cookbook_entry(
    entry_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    entry = _get_entry_for_user(entry_id, current_user, db)
    remove_cookbook_entry(db, entry, current_user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{entry_id}/photo")
def get_cookbook_photo(
    entry_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FileResponse:
    entry = _get_entry_for_user(entry_id, current_user, db)

    if not entry.photo_filename:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Photo not found.")

    photo_path = _cookbook_photo_path(current_user.id, entry.photo_filename)

    if not photo_path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Photo not found.")

    return FileResponse(photo_path)
