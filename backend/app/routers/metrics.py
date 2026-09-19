"""Internal ops endpoints for the Claude usage / cost dashboard (FOOD-54).

Not part of the consumer product. Access is granted to:

- requests carrying ``Authorization: Bearer <METRICS_API_TOKEN>``, or
- signed-in users whose email is listed in ``ADMIN_EMAILS``.

In known local environments (``ENVIRONMENT`` of development / dev / local /
test), when neither setting is configured, any signed-in user may view the
dashboard so QA and developers can verify numbers locally. Any other or unset
value fails closed.
"""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.auth_utils import SESSION_COOKIE_NAME
from app.config import settings
from app.database import get_db
from app.llm_usage import KNOWN_WORKFLOWS
from app.llm_usage.aggregates import MAX_WINDOW_DAYS, recent_events, summarize
from app.llm_usage.anthropic_admin import reconciliation_report
from app.llm_usage.pricing import pricing_table
from app.models import User
from app.sessions import get_active_session

router = APIRouter(prefix="/metrics/llm", tags=["metrics"])

KNOWN_DEV_ENVIRONMENTS = frozenset({"development", "dev", "local", "test"})


def _bearer_token(request: Request) -> str | None:
    header = request.headers.get("authorization") or ""
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


def _session_user(request: Request, db: Session) -> User | None:
    raw_token = request.cookies.get(SESSION_COOKIE_NAME)
    if not raw_token:
        return None
    session = get_active_session(db, raw_token)
    if session is None:
        return None
    return db.get(User, session.user_id)


def _admin_emails() -> set[str]:
    return {
        email.strip().lower()
        for email in (settings.admin_emails or "").split(",")
        if email.strip()
    }


def require_metrics_access(
    request: Request,
    db: Session = Depends(get_db),
) -> str:
    """Return a short label for who was granted access; raise otherwise."""
    configured_token = (settings.metrics_api_token or "").strip()
    presented = _bearer_token(request)
    if configured_token and presented and secrets.compare_digest(presented, configured_token):
        return "token"

    user = _session_user(request, db)
    admin_emails = _admin_emails()
    if user is not None and user.email.lower() in admin_emails:
        return f"admin:{user.id}"

    # Fail closed: only well-known local environments are open by default, so an
    # unset or misspelled ENVIRONMENT never exposes metrics to every user.
    known_dev = (settings.environment or "").lower() in KNOWN_DEV_ENVIRONMENTS
    dev_open = known_dev and not admin_emails and not configured_token
    if user is not None and dev_open:
        return f"dev:{user.id}"

    if user is None and presented is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated.",
        )
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Metrics access is restricted to admins.",
    )


_DAYS = Query(7, ge=1, le=MAX_WINDOW_DAYS, description="Trailing window in days")


@router.get("/summary")
def llm_usage_summary(
    days: int = _DAYS,
    _access: str = Depends(require_metrics_access),
    db: Session = Depends(get_db),
) -> dict:
    """Per-workflow $ / tokens per successful run, cache %, model mix, retries."""
    return summarize(db, days=days)


@router.get("/events")
def llm_usage_events(
    limit: int = Query(50, ge=1, le=500),
    workflow: str | None = Query(None),
    status_filter: str | None = Query(None, alias="status", pattern="^(ok|error)$"),
    _access: str = Depends(require_metrics_access),
    db: Session = Depends(get_db),
) -> dict:
    """Most recent per-call usage events, newest first."""
    return {
        "events": recent_events(db, limit=limit, workflow=workflow, status=status_filter),
        "workflows": list(KNOWN_WORKFLOWS),
    }


@router.get("/anthropic")
def llm_anthropic_reconciliation(
    days: int = Query(7, ge=1, le=31),
    _access: str = Depends(require_metrics_access),
) -> dict:
    """Organization usage/cost from the Anthropic Admin API, if configured."""
    return reconciliation_report(days)


@router.get("/pricing")
def llm_pricing(_access: str = Depends(require_metrics_access)) -> dict:
    """Active USD/MTok table used for estimates (defaults + LLM_PRICING_JSON)."""
    return {
        model: {
            "input": prices.input,
            "output": prices.output,
            "cache_write_5m": prices.cache_write_5m,
            "cache_write_1h": prices.cache_write_1h,
            "cache_read": prices.cache_read,
        }
        for model, prices in sorted(pricing_table().items())
    }
