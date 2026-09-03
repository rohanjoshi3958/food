from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.auth_utils import SESSION_COOKIE_NAME, hash_password
from app.models import (
    AuthSession,
    CookbookEntry,
    Ingredient,
    Meal,
    PasswordResetToken,
    Receipt,
    User,
)
from app.schemas import LoginRequest
from app.sessions import create_password_reset_token


STRONG_PASSWORD = "ValidPass1!"
NEW_PASSWORD = "NewPass2!"


def _cookie_header(response) -> str:
    return response.headers.get("set-cookie") or ""


def _login(client, email: str, password: str):
    client.cookies.clear()
    return client.post("/api/auth/login", json={"email": email, "password": password})


def _create_user(db: Session, email: str, password: str, name: str = "Other User") -> User:
    user = User(email=email, name=name, password=hash_password(password))
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


class TestPasswordStorage:
    def test_register_stores_hashed_password_not_plaintext(self, client, test_db: Session):
        response = client.post(
            "/api/auth/register",
            json={
                "name": "Ada",
                "email": "ada@example.com",
                "password": STRONG_PASSWORD,
            },
        )

        assert response.status_code == 201
        assert "access_token" not in response.json()
        assert STRONG_PASSWORD not in response.text

        user = test_db.query(User).filter(User.email == "ada@example.com").one()
        assert user.password != STRONG_PASSWORD
        assert user.password.startswith("$2b$")

    def test_login_request_repr_hides_password(self):
        payload = LoginRequest(email="ada@example.com", password=STRONG_PASSWORD)
        assert STRONG_PASSWORD not in repr(payload)


class TestLoginLogout:
    def test_login_sets_httponly_session_cookie(self, client, test_user):
        response = _login(client, test_user.email, "testpassword123")

        assert response.status_code == 200
        assert response.json()["user"]["email"] == test_user.email
        assert "access_token" not in response.json()
        assert SESSION_COOKIE_NAME in response.cookies

        cookie = _cookie_header(response).lower()
        assert "httponly" in cookie
        assert "samesite=lax" in cookie

        me = client.get("/api/auth/me")
        assert me.status_code == 200
        assert me.json()["id"] == test_user.id

    def test_login_rejects_wrong_password(self, client, test_user, test_db: Session):
        response = _login(client, test_user.email, "wrong-password")

        assert response.status_code == 401
        assert response.json()["detail"] == "Invalid email or password."
        assert SESSION_COOKIE_NAME not in response.cookies
        assert test_db.query(AuthSession).count() == 0

    def test_login_unknown_email_uses_same_error(self, client):
        response = _login(client, "missing@example.com", "testpassword123")

        assert response.status_code == 401
        assert response.json()["detail"] == "Invalid email or password."

    def test_logout_revokes_session(self, client, test_user, test_db: Session):
        login_response = _login(client, test_user.email, "testpassword123")
        assert login_response.status_code == 200

        session = test_db.query(AuthSession).one()
        assert session.revoked_at is None

        logout_response = client.post("/api/auth/logout")
        assert logout_response.status_code == 200

        test_db.refresh(session)
        assert session.revoked_at is not None

        me = client.get("/api/auth/me")
        assert me.status_code == 401

    def test_me_rejects_missing_session(self, client):
        response = client.get("/api/auth/me")
        assert response.status_code == 401

    def test_bearer_token_is_not_accepted(self, client, test_user):
        _login(client, test_user.email, "testpassword123")
        client.cookies.clear()

        response = client.get(
            "/api/auth/me",
            headers={"Authorization": "Bearer fake-legacy-token"},
        )
        assert response.status_code == 401


class TestExpiredCredentials:
    def test_expired_session_is_rejected(self, client, test_user, test_db: Session):
        assert _login(client, test_user.email, "testpassword123").status_code == 200

        session = test_db.query(AuthSession).one()
        session.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        test_db.commit()

        response = client.get("/api/auth/me")
        assert response.status_code == 401
        assert response.json()["detail"] == "Invalid or expired session."

    def test_revoked_session_is_rejected(self, client, test_user, test_db: Session):
        assert _login(client, test_user.email, "testpassword123").status_code == 200

        session = test_db.query(AuthSession).one()
        session.revoked_at = datetime.now(UTC)
        test_db.commit()

        response = client.get("/api/ingredients")
        assert response.status_code == 401


class TestPasswordReset:
    def test_forgot_password_is_generic_and_creates_hashed_token(
        self, client, test_user, test_db: Session
    ):
        known = client.post(
            "/api/auth/forgot-password",
            json={"email": test_user.email},
        )
        unknown = client.post(
            "/api/auth/forgot-password",
            json={"email": "nobody@example.com"},
        )

        assert known.status_code == 200
        assert unknown.status_code == 200
        assert known.json()["message"] == unknown.json()["message"]
        assert "token" not in known.json()

        reset = test_db.query(PasswordResetToken).one()
        assert reset.user_id == test_user.id
        assert reset.used_at is None
        assert reset.token_hash != test_user.email
        assert len(reset.token_hash) == 64

    def test_reset_password_is_single_use(self, client, test_user, test_db: Session):
        token = create_password_reset_token(test_db, test_user)

        first = client.post(
            "/api/auth/reset-password",
            json={"token": token, "password": NEW_PASSWORD},
        )
        second = client.post(
            "/api/auth/reset-password",
            json={"token": token, "password": "AnotherPass3!"},
        )

        assert first.status_code == 200
        assert second.status_code == 400
        assert second.json()["detail"] == "This reset link is invalid or has expired."

        assert _login(client, test_user.email, NEW_PASSWORD).status_code == 200
        assert _login(client, test_user.email, "testpassword123").status_code == 401

    def test_expired_reset_token_is_rejected(self, client, test_user, test_db: Session):
        token = create_password_reset_token(test_db, test_user)
        reset = test_db.query(PasswordResetToken).one()
        reset.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        test_db.commit()

        preview = client.get("/api/auth/reset-password", params={"token": token})
        response = client.post(
            "/api/auth/reset-password",
            json={"token": token, "password": NEW_PASSWORD},
        )

        assert preview.status_code == 400
        assert preview.json()["detail"] == "This reset link is invalid or has expired."
        assert response.status_code == 400
        assert response.json()["detail"] == "This reset link is invalid or has expired."
        assert _login(client, test_user.email, "testpassword123").status_code == 200

    def test_valid_reset_token_can_be_checked_without_consuming(
        self, client, test_user, test_db: Session
    ):
        token = create_password_reset_token(test_db, test_user)

        preview = client.get("/api/auth/reset-password", params={"token": token})
        assert preview.status_code == 200

        reset = test_db.query(PasswordResetToken).one()
        assert reset.used_at is None

        response = client.post(
            "/api/auth/reset-password",
            json={"token": token, "password": NEW_PASSWORD},
        )
        assert response.status_code == 200

    def test_missing_reset_token_is_rejected(self, client):
        response = client.get("/api/auth/reset-password")
        assert response.status_code == 400
        assert response.json()["detail"] == "This reset link is invalid or has expired."

    def test_used_reset_token_is_rejected_on_preview(self, client, test_user, test_db: Session):
        token = create_password_reset_token(test_db, test_user)
        used = client.post(
            "/api/auth/reset-password",
            json={"token": token, "password": NEW_PASSWORD},
        )
        assert used.status_code == 200

        preview = client.get("/api/auth/reset-password", params={"token": token})
        assert preview.status_code == 400
        assert preview.json()["detail"] == "This reset link is invalid or has expired."

    def test_unknown_reset_token_is_rejected_on_preview(self, client):
        preview = client.get(
            "/api/auth/reset-password",
            params={"token": "not-a-real-reset-token"},
        )
        assert preview.status_code == 400
        assert preview.json()["detail"] == "This reset link is invalid or has expired."

    def test_reset_password_revokes_active_sessions(
        self, client, test_user, test_db: Session
    ):
        assert _login(client, test_user.email, "testpassword123").status_code == 200
        token = create_password_reset_token(test_db, test_user)

        response = client.post(
            "/api/auth/reset-password",
            json={"token": token, "password": NEW_PASSWORD},
        )
        assert response.status_code == 200

        session = test_db.query(AuthSession).one()
        assert session.revoked_at is not None
        assert client.get("/api/auth/me").status_code == 401

    def test_reset_rejects_weak_password(self, client, test_user, test_db: Session):
        token = create_password_reset_token(test_db, test_user)

        response = client.post(
            "/api/auth/reset-password",
            json={"token": token, "password": "password"},
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "Password must include at least one uppercase letter."
        reset = test_db.query(PasswordResetToken).one()
        assert reset.used_at is None

    def test_register_rejects_password_without_symbol(self, client):
        response = client.post(
            "/api/auth/register",
            json={"email": "new@example.com", "password": "Validpass1", "name": "New"},
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "Password must include at least one symbol."


class TestCrossUserAccess:
    def test_user_cannot_access_another_users_resources(
        self, client, test_user, test_db: Session
    ):
        owner = test_user
        outsider = _create_user(test_db, "outsider@example.com", "testpassword123")

        ingredient = Ingredient(
            user_id=owner.id,
            name="Milk",
            quantity="1",
            unit="l",
        )
        meal = Meal(
            user_id=owner.id,
            name="Oatmeal",
            description="Breakfast",
        )
        entry = CookbookEntry(
            user_id=owner.id,
            title="Oatmeal",
        )
        receipt = Receipt(
            user_id=owner.id,
            filename="receipt.jpg",
            original_name="receipt.jpg",
            analysis_status="pending_review",
            draft_items=[],
        )
        test_db.add_all([ingredient, meal, entry, receipt])
        test_db.commit()
        test_db.refresh(ingredient)
        test_db.refresh(meal)
        test_db.refresh(entry)
        test_db.refresh(receipt)

        assert _login(client, outsider.email, "testpassword123").status_code == 200

        assert client.get("/api/ingredients").json() == []
        assert client.get("/api/meals").json() == []
        assert client.get("/api/cookbook").json() == []
        assert client.delete(f"/api/ingredients/{ingredient.id}").status_code == 404
        assert client.get(f"/api/meals/{meal.id}").status_code == 404
        assert client.delete(f"/api/cookbook/{entry.id}").status_code == 404
        assert (
            client.patch(
                f"/api/receipts/{receipt.id}/draft",
                json={"items": []},
            ).status_code
            == 404
        )
        assert client.post(f"/api/receipts/{receipt.id}/cancel").status_code == 404
        assert (
            client.post(
                f"/api/receipts/{receipt.id}/confirm",
                json={"items": []},
            ).status_code
            == 404
        )

        test_db.refresh(ingredient)
        test_db.refresh(meal)
        test_db.refresh(entry)
        test_db.refresh(receipt)
        assert test_db.get(Ingredient, ingredient.id) is not None
        assert test_db.get(Meal, meal.id) is not None
        assert test_db.get(CookbookEntry, entry.id) is not None
        assert test_db.get(Receipt, receipt.id) is not None
