from datetime import UTC, datetime

from fastapi import Response
from sqlalchemy.orm import Session

from app.auth_utils import (
    SESSION_COOKIE_NAME,
    generate_token,
    hash_token,
    is_expired,
)
from app.config import settings
from app.models import AuthSession, PasswordResetToken, User, utcnow


def set_session_cookie(response: Response, raw_token: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=raw_token,
        max_age=int(settings.session_ttl.total_seconds()),
        httponly=True,
        samesite="lax",
        secure=settings.session_cookie_secure,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        path="/",
        httponly=True,
        samesite="lax",
        secure=settings.session_cookie_secure,
    )


def create_session(db: Session, user_id: str) -> str:
    raw_token = generate_token()
    session = AuthSession(
        user_id=user_id,
        token_hash=hash_token(raw_token),
        expires_at=datetime.now(UTC) + settings.session_ttl,
    )
    db.add(session)
    db.commit()
    return raw_token


def get_active_session(db: Session, raw_token: str) -> AuthSession | None:
    session = (
        db.query(AuthSession)
        .filter(
            AuthSession.token_hash == hash_token(raw_token),
            AuthSession.revoked_at.is_(None),
        )
        .first()
    )
    if session is None or is_expired(session.expires_at):
        return None
    return session


def revoke_session(db: Session, session: AuthSession) -> None:
    session.revoked_at = utcnow()
    db.commit()


def revoke_all_sessions(db: Session, user_id: str) -> None:
    now = utcnow()
    (
        db.query(AuthSession)
        .filter(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
        .update({"revoked_at": now})
    )
    db.commit()


def create_password_reset_token(db: Session, user: User) -> str:
    now = utcnow()
    (
        db.query(PasswordResetToken)
        .filter(
            PasswordResetToken.user_id == user.id,
            PasswordResetToken.used_at.is_(None),
        )
        .update({"used_at": now})
    )

    raw_token = generate_token()
    db.add(
        PasswordResetToken(
            user_id=user.id,
            token_hash=hash_token(raw_token),
            expires_at=now + settings.password_reset_ttl,
        )
    )
    db.commit()
    return raw_token


def get_valid_password_reset_token(db: Session, raw_token: str) -> PasswordResetToken | None:
    if not raw_token:
        return None

    reset = (
        db.query(PasswordResetToken)
        .filter(
            PasswordResetToken.token_hash == hash_token(raw_token),
            PasswordResetToken.used_at.is_(None),
        )
        .first()
    )
    if reset is None or is_expired(reset.expires_at):
        return None
    return reset


def consume_password_reset_token(db: Session, raw_token: str) -> PasswordResetToken | None:
    reset = get_valid_password_reset_token(db, raw_token)
    if reset is None:
        return None

    reset.used_at = utcnow()
    db.commit()
    db.refresh(reset)
    return reset
