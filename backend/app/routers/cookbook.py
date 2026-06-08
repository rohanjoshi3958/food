from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models import CookbookEntry, User
from app.schemas import CookbookEntryResponse

router = APIRouter(prefix="/cookbook", tags=["cookbook"])


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
    return [CookbookEntryResponse.model_validate(entry) for entry in entries]
