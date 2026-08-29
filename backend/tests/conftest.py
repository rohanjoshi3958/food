"""
Test fixtures and utilities for the test suite.
"""
import os
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.auth_utils import hash_password
from app.database import Base
from app.models import User


# Create a test engine that will be used throughout
_test_engine = None
_TestingSessionLocal = None


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
         patch("app.db_migrate.run_migrations"):  # Skip migrations in tests
        
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
    """Get an auth token for the test user."""
    response = client.post(
        "/api/auth/login",
        json={"email": "test@example.com", "password": "testpassword123"}
    )
    assert response.status_code == 200
    return response.json()["access_token"]


@pytest.fixture
def auth_headers(auth_token):
    """Get auth headers for authenticated requests."""
    return {"Authorization": f"Bearer {auth_token}"}


@pytest.fixture
def temp_upload_dir(tmp_path):
    """Create a temporary directory for test uploads."""
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    return upload_dir


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
