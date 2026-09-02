from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.auth_utils import (
    SESSION_COOKIE_NAME,
    dummy_password_hash,
    hash_password,
    verify_password,
)
from app.database import get_db
from app.dependencies import get_current_user
from app.models import User
from app.password_utils import validate_password
from app.schemas import (
    AuthResponse,
    ForgotPasswordRequest,
    LoginRequest,
    MessageResponse,
    RegisterRequest,
    ResetPasswordRequest,
    UserResponse,
)
from app.sessions import (
    clear_session_cookie,
    consume_password_reset_token,
    create_password_reset_token,
    create_session,
    get_active_session,
    revoke_all_sessions,
    revoke_session,
    set_session_cookie,
)

router = APIRouter(prefix="/auth", tags=["auth"])

GENERIC_RESET_MESSAGE = (
    "If an account exists for that email, password reset instructions have been sent."
)
GENERIC_RESET_TOKEN_ERROR = "This reset link is invalid or has expired."


def _issue_session(response: Response, db: Session, user: User) -> AuthResponse:
    raw_token = create_session(db, user.id)
    set_session_cookie(response, raw_token)
    return AuthResponse(user=UserResponse.model_validate(user))


@router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
def register(
    payload: RegisterRequest,
    response: Response,
    db: Session = Depends(get_db),
) -> AuthResponse:
    email = payload.email.lower().strip()
    password_error = validate_password(payload.password)

    if password_error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=password_error)

    existing_user = db.query(User).filter(User.email == email).first()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists.",
        )

    name = payload.name.strip() or email.split("@")[0]
    user = User(
        name=name,
        email=email,
        password=hash_password(payload.password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    return _issue_session(response, db, user)


@router.post("/login", response_model=AuthResponse)
def login(
    payload: LoginRequest,
    response: Response,
    db: Session = Depends(get_db),
) -> AuthResponse:
    email = payload.email.lower().strip()
    user = db.query(User).filter(User.email == email).first()

    if user is None or not user.password:
        verify_password(payload.password, dummy_password_hash())
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
        )

    if not verify_password(payload.password, user.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
        )

    return _issue_session(response, db, user)


@router.post("/logout", response_model=MessageResponse)
def logout(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> MessageResponse:
    raw_token = request.cookies.get(SESSION_COOKIE_NAME)
    if raw_token:
        session = get_active_session(db, raw_token)
        if session is not None:
            revoke_session(db, session)

    clear_session_cookie(response)
    return MessageResponse(message="Signed out.")


@router.get("/me", response_model=UserResponse)
def me(current_user: User = Depends(get_current_user)) -> UserResponse:
    return UserResponse.model_validate(current_user)


@router.post("/forgot-password", response_model=MessageResponse)
def forgot_password(
    payload: ForgotPasswordRequest,
    db: Session = Depends(get_db),
) -> MessageResponse:
    email = payload.email.lower().strip()
    user = db.query(User).filter(User.email == email).first()
    if user is not None and user.password:
        create_password_reset_token(db, user)

    return MessageResponse(message=GENERIC_RESET_MESSAGE)


@router.post("/reset-password", response_model=MessageResponse)
def reset_password(
    payload: ResetPasswordRequest,
    db: Session = Depends(get_db),
) -> MessageResponse:
    password_error = validate_password(payload.password)
    if password_error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=password_error)

    reset = consume_password_reset_token(db, payload.token)
    if reset is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=GENERIC_RESET_TOKEN_ERROR,
        )

    user = db.get(User, reset.user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=GENERIC_RESET_TOKEN_ERROR,
        )

    user.password = hash_password(payload.password)
    db.commit()
    revoke_all_sessions(db, user.id)

    return MessageResponse(message="Your password has been reset. You can sign in now.")
