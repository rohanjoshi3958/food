"""
Test fixtures and utilities for the test suite.
"""
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

backend_dir = Path(__file__).parent.parent
sys.path.insert(0, str(backend_dir))

from app.auth_utils import hash_password
from app.database import Base
from app.models import User


# Create a test engine that will be used throughout
_test_engine = None
_TestingSessionLocal = None


@pytest.fixture(autouse=True)
def clear_ai_provider_keys(monkeypatch):
    """Block accidental real Anthropic/OpenAI calls during tests (including CI)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("OPENAI_API_KEY", "")



def get_test_engine():
    """Get or create the test database engine."""
    global _test_engine, _TestingSessionLocal

    if _test_engine is None:
        db_fd, db_path = tempfile.mkstemp()
        db_url = f"sqlite:///{db_path}"

        _test_engine = create_engine(db_url, connect_args={"check_same_thread": False})

        # Enable foreign key constraints for SQLite
        @event.listens_for(_test_engine, "connect")
        def set_sqlite_pragma(dbapi_conn, connection_record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        _TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_test_engine)

    return _test_engine, _TestingSessionLocal


@pytest.fixture(scope="function")
def test_db():
    """Create a test database for each test function."""
    engine, SessionLocal = get_test_engine()

    # Create all tables
    Base.metadata.create_all(bind=engine)

    # Create a new session for this test
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
        # Clean up all tables after test
        Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client(test_db):
    """Create a test client for the FastAPI app."""
    # Patch the database engine and dependencies before importing the app
    with patch("app.database.engine", get_test_engine()[0]), \
         patch("app.database.SessionLocal", get_test_engine()[1]), \
         patch("app.db_migrate.run_migrations"), \
         patch("app.routers.auth.send_password_reset_email"):

        # Import app after patching
        from app.database import get_db
        from app.main import app

        # Override the database dependency to use test database
        def override_get_db():
            try:
                yield test_db
            finally:
                pass  # Don't close here, test_db fixture handles it

        app.dependency_overrides[get_db] = override_get_db

        # Use raise_server_exceptions=False to prevent startup events from causing errors
        with TestClient(app, raise_server_exceptions=False) as test_client:
            yield test_client

        # Clear overrides after test
        app.dependency_overrides.clear()


@pytest.fixture
def test_user(test_db: Session):
    """Create a test user."""
    user = User(
        email="test@example.com",
        name="Test User",
        password=hash_password("testpassword123")
    )
    test_db.add(user)
    test_db.commit()
    test_db.refresh(user)
    return user


@pytest.fixture
def auth_token(client, test_user):
    """Log in the test user and return the HttpOnly session cookie value."""
    from app.auth_utils import SESSION_COOKIE_NAME

    response = client.post(
        "/api/auth/login",
        json={"email": "test@example.com", "password": "testpassword123"}
    )
    assert response.status_code == 200
    assert SESSION_COOKIE_NAME in response.cookies
    return response.cookies[SESSION_COOKIE_NAME]


@pytest.fixture
def auth_headers(auth_token):
    """Kept for existing tests; the session lives in the client cookie jar."""
    del auth_token
    return {}


@pytest.fixture
def temp_upload_dir(tmp_path):
    """Create a temporary directory for test uploads."""
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    return upload_dir


@pytest.fixture
def local_uploads(tmp_path, monkeypatch):
    """Point the local-disk storage fallback at per-test directories.

    Returns the three roots keyed by object-key prefix.
    """
    from app import storage

    monkeypatch.delenv("UPLOADS_BUCKET", raising=False)
    roots = {
        "receipts": tmp_path / "uploads" / "receipts",
        "meals": tmp_path / "uploads" / "meals",
        "cookbook": tmp_path / "uploads" / "cookbook",
    }
    monkeypatch.setenv("UPLOAD_DIR", str(roots["receipts"]))
    monkeypatch.setenv("MEAL_UPLOAD_DIR", str(roots["meals"]))
    monkeypatch.setenv("COOKBOOK_UPLOAD_DIR", str(roots["cookbook"]))
    storage.reset_storage_cache()
    yield roots
    storage.reset_storage_cache()


class FakeS3Client:
    """Minimal in-memory stand-in for ``boto3.client("s3")``.

    Implements only the calls ``app.storage.S3UploadStorage`` makes and mimics
    botocore's ``ClientError`` shapes for missing objects so the backend's
    error mapping is exercised for real.
    """

    def __init__(self):
        self.objects: dict[tuple[str, str], dict] = {}
        self.calls: list[tuple[str, dict]] = []

    @staticmethod
    def _not_found(operation: str, code: str):
        from botocore.exceptions import ClientError

        return ClientError(
            {
                "Error": {"Code": code, "Message": code},
                "ResponseMetadata": {"HTTPStatusCode": 404},
            },
            operation,
        )

    def keys(self, bucket: str) -> set[str]:
        return {key for (b, key) in self.objects if b == bucket}

    def put_object(self, *, Bucket, Key, Body, ContentType=None, **_):
        self.calls.append(("put_object", {"Bucket": Bucket, "Key": Key, "ContentType": ContentType}))
        data = Body if isinstance(Body, bytes) else Body.read()
        self.objects[(Bucket, Key)] = {"Body": data, "ContentType": ContentType}
        return {"ETag": '"fake"'}

    def get_object(self, *, Bucket, Key, **_):
        import io

        self.calls.append(("get_object", {"Bucket": Bucket, "Key": Key}))
        obj = self.objects.get((Bucket, Key))
        if obj is None:
            raise self._not_found("GetObject", "NoSuchKey")
        return {"Body": io.BytesIO(obj["Body"]), "ContentType": obj["ContentType"]}

    def head_object(self, *, Bucket, Key, **_):
        self.calls.append(("head_object", {"Bucket": Bucket, "Key": Key}))
        if (Bucket, Key) not in self.objects:
            raise self._not_found("HeadObject", "404")
        return {"ContentLength": len(self.objects[(Bucket, Key)]["Body"])}

    def delete_object(self, *, Bucket, Key, **_):
        self.calls.append(("delete_object", {"Bucket": Bucket, "Key": Key}))
        self.objects.pop((Bucket, Key), None)
        return {}

    def copy_object(self, *, Bucket, Key, CopySource, ContentType=None, **_):
        self.calls.append(("copy_object", {"Bucket": Bucket, "Key": Key, "CopySource": CopySource}))
        source = self.objects.get((CopySource["Bucket"], CopySource["Key"]))
        if source is None:
            raise self._not_found("CopyObject", "NoSuchKey")
        self.objects[(Bucket, Key)] = {
            "Body": source["Body"],
            "ContentType": ContentType or source["ContentType"],
        }
        return {}

    def generate_presigned_url(self, ClientMethod, Params, ExpiresIn):
        self.calls.append(("generate_presigned_url", {"ClientMethod": ClientMethod, **Params}))
        return (
            f"https://{Params['Bucket']}.s3.amazonaws.com/{Params['Key']}"
            f"?X-Amz-Expires={ExpiresIn}&X-Amz-Signature=fake"
        )


FAKE_BUCKET = "food-test-uploads"


@pytest.fixture
def fake_s3(tmp_path, monkeypatch):
    """Run the app in S3 mode against an in-memory fake bucket.

    Sets ``UPLOADS_BUCKET`` and intercepts ``boto3.client`` so no AWS
    credentials, region, or network are needed. Local upload dirs are pointed
    at paths that must stay absent, so tests can assert nothing hit disk.
    """
    from app import storage

    fake = FakeS3Client()
    monkeypatch.setenv("UPLOADS_BUCKET", FAKE_BUCKET)
    never = tmp_path / "never-created"
    monkeypatch.setenv("UPLOAD_DIR", str(never / "receipts"))
    monkeypatch.setenv("MEAL_UPLOAD_DIR", str(never / "meals"))
    monkeypatch.setenv("COOKBOOK_UPLOAD_DIR", str(never / "cookbook"))
    storage.reset_storage_cache()

    with patch("boto3.client", return_value=fake) as client_factory:
        fake.client_factory = client_factory
        fake.local_root = never
        yield fake

    storage.reset_storage_cache()


@pytest.fixture
def mock_receipt_image(tmp_path):
    """Create a mock receipt image file."""
    receipt_path = tmp_path / "receipt.jpg"
    receipt_path.write_bytes(b"fake image data")
    return receipt_path


@pytest.fixture
def sample_receipt_response():
    """Sample Claude API response for receipt analysis."""
    return {
        "store_name": "Whole Foods Market",
        "items": [
            {
                "store_item_name": "ORGANIC BANANAS",
                "ingredient_name": "Organic Bananas",
                "is_food": True,
                "quantity": "2.5",
                "unit": "lb"
            },
            {
                "store_item_name": "ALMOND BUTTER",
                "ingredient_name": "Almond Butter",
                "is_food": True,
                "quantity": "1",
                "unit": "each"
            },
            {
                "store_item_name": "GREEK YOGURT",
                "ingredient_name": "Greek Yogurt",
                "is_food": True,
                "quantity": "32",
                "unit": "oz"
            }
        ]
    }


@pytest.fixture
def sample_nutrition_estimates():
    """Sample Claude API responses for nutrition estimation."""
    return {
        "Organic Bananas": {
            "recognized": True,
            "quantity": "2.5",
            "unit": "lb",
            "serving_size": "1 medium banana (118g)",
            "servings_per_container": 7,
            "calories": 105,
            "protein_g": 1.3,
            "carbs_g": 27,
            "fat_g": 0.4,
            "fiber_g": 3.1,
            "sodium_mg": 1,
            "nutrition_notes": "USDA standard nutrition data"
        },
        "Almond Butter": {
            "recognized": True,
            "quantity": "1",
            "unit": "each",
            "serving_size": "2 tbsp (32g)",
            "servings_per_container": 15,
            "calories": 190,
            "protein_g": 7,
            "carbs_g": 6,
            "fat_g": 18,
            "fiber_g": 3,
            "sodium_mg": 0,
            "nutrition_notes": "Typical almond butter jar"
        },
        "Greek Yogurt": {
            "recognized": True,
            "quantity": "32",
            "unit": "oz",
            "serving_size": "5.3 oz (150g)",
            "servings_per_container": 6,
            "calories": 100,
            "protein_g": 17,
            "carbs_g": 6,
            "fat_g": 0,
            "fiber_g": 0,
            "sodium_mg": 65,
            "nutrition_notes": "Non-fat Greek yogurt"
        }
    }


def create_mock_anthropic_response(content: str):
    """Create a mock Anthropic API response."""
    mock_response = Mock()
    mock_content_block = Mock()
    mock_content_block.type = "text"
    mock_content_block.text = content
    mock_response.content = [mock_content_block]
    return mock_response


def mock_unit_check_response() -> Mock:
    """Mock a plausible unit-check response."""
    return create_mock_anthropic_response(
        json.dumps({"unit_plausible": True, "unit_warning": None})
    )


def mock_pantry_match_response(
    *,
    match_id: str | None = None,
    ambiguous: bool = False,
    canonical_name: str | None = None,
) -> Mock:
    """Mock an LLM pantry-match response."""
    return create_mock_anthropic_response(
        json.dumps(
            {
                "match_id": match_id,
                "ambiguous": ambiguous,
                "canonical_name": canonical_name,
            }
        )
    )


def mock_nutrition_response(estimate: dict) -> Mock:
    """Mock a nutrition estimate response, ensuring recognized is set."""
    payload = {"recognized": True, **estimate}
    return create_mock_anthropic_response(json.dumps(payload))


def build_receipt_flow_side_effect(
    receipt_response: dict,
    upload_nutrition: list[dict],
    confirm_nutrition: list[dict] | None = None,
    *,
    existing_pantry_items: int = 0,
    confirm_create_match_flags: list[bool] | None = None,
) -> list[Mock]:
    """Anthropic calls: receipt scan, upload enrichment, then confirm checks.

    Confirm sequence:
    1. canonicalize_draft_items → one pantry-match call per food item
    2. per food item create: unit-check, nutrition, and pantry-match when
       ``confirm_create_match_flags[i]`` is True (default: always, because an
       empty or unit-filtered pantry still requests a canonical name).
    """
    confirm_nutrition = (
        confirm_nutrition if confirm_nutrition is not None else upload_nutrition
    )
    if confirm_create_match_flags is None:
        confirm_create_match_flags = [True] * len(confirm_nutrition)
    if len(confirm_create_match_flags) != len(confirm_nutrition):
        raise ValueError(
            "confirm_create_match_flags must match confirm_nutrition length"
        )

    side_effect = [
        create_mock_anthropic_response(json.dumps(receipt_response)),
        *[mock_nutrition_response(estimate) for estimate in upload_nutrition],
    ]
    # canonicalize_draft_items always requests a pantry match / canonical name.
    for _estimate in confirm_nutrition:
        side_effect.append(mock_pantry_match_response())
    for estimate, needs_create_match in zip(
        confirm_nutrition, confirm_create_match_flags, strict=True
    ):
        side_effect.append(mock_unit_check_response())
        side_effect.append(mock_nutrition_response(estimate))
        if needs_create_match:
            side_effect.append(mock_pantry_match_response())
    return side_effect
