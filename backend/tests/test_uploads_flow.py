"""API-level tests for upload storage in S3 mode and the local-disk fallback.

Covers the FOOD-47 acceptance criteria:

* S3 mode (``UPLOADS_BUCKET`` set): receipts, meal photos and cookbook photos
  are written to the bucket under ``receipts/``, ``meals/`` and ``cookbook/``,
  the database stores object keys, photos are served via presigned URLs, and
  nothing is written to local disk.
* Local fallback (``UPLOADS_BUCKET`` unset): the same flows work against the
  ``*_upload_dir`` directories.
"""

import re
from unittest.mock import Mock, patch
from urllib.parse import quote

import pytest
from sqlalchemy.orm import Session

from app.models import CookbookEntry, Meal, Receipt, User
from tests.conftest import FAKE_BUCKET, build_receipt_flow_side_effect

HEX32 = r"[0-9a-f]{32}"
PHOTO_BYTES = b"\x89PNG\r\n\x1a\nfake-photo"


def _create_meal(db: Session, user: User) -> Meal:
    meal = Meal(
        user_id=user.id,
        name="Chicken and Rice",
        description="Simple",
        ingredients_used="- Chicken Breast: 200 g",
        ingredients_used_data=[{"name": "Chicken Breast", "quantity": 200.0, "unit": "g"}],
        instructions="Cook it",
    )
    db.add(meal)
    db.commit()
    db.refresh(meal)
    return meal


def _complete_meal_with_photo(client, meal_id: str):
    return client.post(
        f"/api/meals/{meal_id}/complete",
        files={"file": ("dinner photo.png", PHOTO_BYTES, "image/png")},
    )


class TestS3Mode:
    def test_lifespan_does_not_create_local_upload_dirs(self, fake_s3, client):
        assert client.get("/api/health").status_code == 200
        assert not fake_s3.local_root.exists()

    @patch("app.services.receipt_analyzer.anthropic.Anthropic")
    @patch("app.services.receipt_analyzer.settings.anthropic_api_key", "test-api-key")
    @patch("app.config.settings.anthropic_api_key", "test-api-key")
    def test_receipt_upload_stores_object_key_in_bucket(
        self,
        mock_anthropic_class,
        fake_s3,
        client,
        test_db: Session,
        test_user: User,
        auth_headers,
        sample_receipt_response,
        sample_nutrition_estimates,
    ):
        mock_client = Mock()
        mock_anthropic_class.return_value = mock_client
        mock_client.messages.create.side_effect = build_receipt_flow_side_effect(
            sample_receipt_response,
            [
                sample_nutrition_estimates["Organic Bananas"],
                sample_nutrition_estimates["Almond Butter"],
                sample_nutrition_estimates["Greek Yogurt"],
            ],
        )

        response = client.post(
            "/api/receipts/upload",
            files={"file": ("../My Receipt.jpg", b"fake image data", "image/jpeg")},
            data={"manual_items": "[]"},
            headers=auth_headers,
        )

        assert response.status_code == 201, response.text
        payload = response.json()
        key = payload["filename"]
        assert re.fullmatch(rf"receipts/{test_user.id}/{HEX32}_My Receipt\.jpg", key)
        assert payload["original_name"] == "My Receipt.jpg"

        db_receipt = test_db.query(Receipt).filter(Receipt.id == payload["id"]).one()
        assert db_receipt.filename == key

        assert fake_s3.objects[(FAKE_BUCKET, key)] == {
            "Body": b"fake image data",
            "ContentType": "image/jpeg",
        }
        assert not fake_s3.local_root.exists()

        # Claude received the uploaded bytes without a round-trip to disk.
        first_call = mock_client.messages.create.call_args_list[0]
        image_block = first_call.kwargs["messages"][0]["content"][0]
        assert image_block["source"]["media_type"] == "image/jpeg"

        # Discarding the pending review removes the object from the bucket.
        assert client.post("/api/receipts/discard-pending", headers=auth_headers).status_code == 204
        assert fake_s3.keys(FAKE_BUCKET) == set()

    def test_receipt_upload_fails_cleanly_when_bucket_write_fails(
        self, fake_s3, client, test_db: Session, test_user: User, auth_headers
    ):
        from botocore.exceptions import ClientError

        def denied(**_):
            raise ClientError({"Error": {"Code": "AccessDenied", "Message": "no"}}, "PutObject")

        fake_s3.put_object = denied

        response = client.post(
            "/api/receipts/upload",
            files={"file": ("receipt.jpg", b"fake image data", "image/jpeg")},
            data={"manual_items": "[]"},
            headers=auth_headers,
        )

        assert response.status_code == 503
        assert test_db.query(Receipt).count() == 0

    def test_meal_photo_to_cookbook_flow(
        self, fake_s3, client, test_db: Session, test_user: User, auth_headers, monkeypatch
    ):
        meal = _create_meal(test_db, test_user)

        response = _complete_meal_with_photo(client, meal.id)
        assert response.status_code == 200, response.text
        assert response.json()["photo_url"] == f"/api/meals/{meal.id}/photo"

        # The meal row is consumed and its photo moved under cookbook/.
        assert test_db.query(Meal).filter(Meal.id == meal.id).first() is None
        entry = test_db.query(CookbookEntry).filter(CookbookEntry.user_id == test_user.id).one()
        assert re.fullmatch(rf"cookbook/{test_user.id}/{HEX32}_dinner photo\.png", entry.photo_filename)

        keys = fake_s3.keys(FAKE_BUCKET)
        assert keys == {entry.photo_filename}
        assert fake_s3.objects[(FAKE_BUCKET, entry.photo_filename)] == {
            "Body": PHOTO_BYTES,
            "ContentType": "image/png",
        }
        assert not fake_s3.local_root.exists()

        # Listing exposes the photo through the API route...
        listing = client.get("/api/cookbook", headers=auth_headers).json()
        assert listing[0]["photo_url"] == f"/api/cookbook/{entry.id}/photo"

        # ...which redirects to a short-lived presigned S3 URL.
        photo = client.get(listing[0]["photo_url"], headers=auth_headers, follow_redirects=False)
        assert photo.status_code == 307
        assert photo.headers["location"].startswith(
            f"https://{FAKE_BUCKET}.s3.amazonaws.com/{quote(entry.photo_filename)}?"
        )
        assert "X-Amz-Expires=300" in photo.headers["location"]
        assert photo.headers["cache-control"] == "private, no-store"

        # With redirects disabled the API streams the bytes itself.
        monkeypatch.setenv("UPLOADS_SIGNED_URL_TTL_SECONDS", "0")
        streamed = client.get(listing[0]["photo_url"], headers=auth_headers, follow_redirects=False)
        assert streamed.status_code == 200
        assert streamed.content == PHOTO_BYTES
        assert streamed.headers["content-type"].startswith("image/png")

        # Deleting the entry deletes the object.
        assert client.delete(f"/api/cookbook/{entry.id}", headers=auth_headers).status_code == 204
        assert fake_s3.keys(FAKE_BUCKET) == set()

    def test_photo_endpoints_404_when_object_missing(
        self, fake_s3, client, test_db: Session, test_user: User, auth_headers
    ):
        entry = CookbookEntry(
            user_id=test_user.id,
            title="Ghost",
            photo_filename=f"cookbook/{test_user.id}/deadbeef_ghost.png",
        )
        test_db.add(entry)
        test_db.commit()

        response = client.get(f"/api/cookbook/{entry.id}/photo", headers=auth_headers)
        assert response.status_code == 404
        assert response.json()["detail"] == "Photo not found."

    def test_legacy_basename_rows_resolve_under_prefix(
        self, fake_s3, client, test_db: Session, test_user: User, auth_headers
    ):
        """Rows written before object keys stored only ``<uuid>_<name>``."""
        legacy_key = f"cookbook/{test_user.id}/abc_old.png"
        fake_s3.put_object(Bucket=FAKE_BUCKET, Key=legacy_key, Body=b"old", ContentType="image/png")
        entry = CookbookEntry(user_id=test_user.id, title="Old", photo_filename="abc_old.png")
        test_db.add(entry)
        test_db.commit()

        response = client.get(
            f"/api/cookbook/{entry.id}/photo", headers=auth_headers, follow_redirects=False
        )
        assert response.status_code == 307
        assert legacy_key in response.headers["location"]

        assert client.delete(f"/api/cookbook/{entry.id}", headers=auth_headers).status_code == 204
        assert legacy_key not in fake_s3.keys(FAKE_BUCKET)


class TestLocalFallback:
    def test_lifespan_creates_local_upload_dirs(self, local_uploads, client):
        assert client.get("/api/health").status_code == 200
        for root in local_uploads.values():
            assert root.is_dir()

    def test_meal_photo_to_cookbook_flow_on_disk(
        self, local_uploads, client, test_db: Session, test_user: User, auth_headers
    ):
        meal = _create_meal(test_db, test_user)

        response = _complete_meal_with_photo(client, meal.id)
        assert response.status_code == 200, response.text

        entry = test_db.query(CookbookEntry).filter(CookbookEntry.user_id == test_user.id).one()
        assert re.fullmatch(rf"cookbook/{test_user.id}/{HEX32}_dinner photo\.png", entry.photo_filename)

        cookbook_files = list((local_uploads["cookbook"] / test_user.id).iterdir())
        assert [path.name for path in cookbook_files] == [entry.photo_filename.rsplit("/", 1)[-1]]
        assert cookbook_files[0].read_bytes() == PHOTO_BYTES

        # The temporary meal photo is cleaned up after the cookbook copy.
        meals_dir = local_uploads["meals"] / test_user.id
        assert not meals_dir.exists() or list(meals_dir.iterdir()) == []

        photo = client.get(f"/api/cookbook/{entry.id}/photo", headers=auth_headers)
        assert photo.status_code == 200
        assert photo.content == PHOTO_BYTES
        assert photo.headers["content-type"].startswith("image/png")

        assert client.delete(f"/api/cookbook/{entry.id}", headers=auth_headers).status_code == 204
        assert not cookbook_files[0].exists()

    @patch("app.services.receipt_analyzer.anthropic.Anthropic")
    @patch("app.services.receipt_analyzer.settings.anthropic_api_key", "test-api-key")
    @patch("app.config.settings.anthropic_api_key", "test-api-key")
    def test_receipt_upload_writes_under_receipts_dir(
        self,
        mock_anthropic_class,
        local_uploads,
        client,
        test_db: Session,
        test_user: User,
        auth_headers,
        sample_receipt_response,
        sample_nutrition_estimates,
    ):
        mock_client = Mock()
        mock_anthropic_class.return_value = mock_client
        mock_client.messages.create.side_effect = build_receipt_flow_side_effect(
            sample_receipt_response,
            [
                sample_nutrition_estimates["Organic Bananas"],
                sample_nutrition_estimates["Almond Butter"],
                sample_nutrition_estimates["Greek Yogurt"],
            ],
        )

        response = client.post(
            "/api/receipts/upload",
            files={"file": ("receipt.jpg", b"fake image data", "image/jpeg")},
            data={"manual_items": "[]"},
            headers=auth_headers,
        )

        assert response.status_code == 201, response.text
        key = response.json()["filename"]
        assert key.startswith(f"receipts/{test_user.id}/")
        stored = local_uploads["receipts"] / test_user.id / key.rsplit("/", 1)[-1]
        assert stored.read_bytes() == b"fake image data"

        assert client.post("/api/receipts/discard-pending", headers=auth_headers).status_code == 204
        assert not stored.exists()

    def test_legacy_local_path_rows_are_deleted_on_prune(
        self, local_uploads, client, test_db: Session, test_user: User, auth_headers, tmp_path
    ):
        legacy_file = tmp_path / "legacy" / "old-receipt.jpg"
        legacy_file.parent.mkdir()
        legacy_file.write_bytes(b"old")
        receipt = Receipt(
            user_id=test_user.id,
            filename=str(legacy_file),
            original_name="old-receipt.jpg",
            analysis_status="failed",
        )
        test_db.add(receipt)
        test_db.commit()

        assert client.get("/api/receipts", headers=auth_headers).status_code == 200
        assert not legacy_file.exists()
        assert test_db.query(Receipt).count() == 0


class TestStorageCompensation:
    """Rollback / commit-order compensation for S3 objects (CodeRabbit FOOD-47)."""

    def test_receipt_upload_deletes_object_when_initial_commit_fails(
        self, fake_s3, client, test_db: Session, test_user: User, auth_headers
    ):
        from sqlalchemy.exc import IntegrityError

        with patch.object(
            test_db,
            "commit",
            side_effect=IntegrityError("INSERT", {}, Exception("constraint")),
        ):
            response = client.post(
                "/api/receipts/upload",
                files={"file": ("receipt.jpg", b"fake image data", "image/jpeg")},
                data={"manual_items": "[]"},
                headers=auth_headers,
            )

        assert response.status_code == 503
        test_db.rollback()
        assert test_db.query(Receipt).count() == 0
        assert fake_s3.keys(FAKE_BUCKET) == set()

    def test_receipt_upload_keeps_object_when_commit_error_is_ambiguous_and_row_exists(
        self, fake_s3, client, test_db: Session, test_user: User, auth_headers
    ):
        original_commit = test_db.commit

        def commit_then_lose_connection():
            original_commit()
            raise Exception("connection lost after commit")

        with patch.object(test_db, "commit", side_effect=commit_then_lose_connection):
            response = client.post(
                "/api/receipts/upload",
                files={"file": ("receipt.jpg", b"fake image data", "image/jpeg")},
                data={"manual_items": "[]"},
                headers=auth_headers,
            )

        assert response.status_code == 503
        receipts = test_db.query(Receipt).all()
        assert len(receipts) == 1
        assert receipts[0].filename in fake_s3.keys(FAKE_BUCKET)
        assert receipts[0].filename.startswith(f"receipts/{test_user.id}/")

    def test_meal_photo_is_deleted_when_persist_commit_fails(
        self, fake_s3, client, test_db: Session, test_user: User, auth_headers
    ):
        from sqlalchemy.exc import IntegrityError

        meal = _create_meal(test_db, test_user)
        with patch.object(
            test_db,
            "commit",
            side_effect=IntegrityError("UPDATE", {}, Exception("constraint")),
        ):
            response = _complete_meal_with_photo(client, meal.id)

        assert response.status_code == 500
        test_db.rollback()
        remaining = test_db.query(Meal).filter(Meal.id == meal.id).one()
        assert remaining.photo_filename is None
        assert fake_s3.keys(FAKE_BUCKET) == set()

    def test_meal_source_photo_survives_failed_delete_commit(
        self, fake_s3, client, test_db: Session, test_user: User, auth_headers
    ):
        meal = _create_meal(test_db, test_user)
        original_commit = test_db.commit
        commits = {"n": 0}

        def counted_commit():
            commits["n"] += 1
            if commits["n"] == 3:
                raise Exception("meal delete commit failed")
            original_commit()

        with patch.object(test_db, "commit", side_effect=counted_commit):
            response = _complete_meal_with_photo(client, meal.id)

        assert response.status_code == 500
        test_db.rollback()
        remaining = test_db.query(Meal).filter(Meal.id == meal.id).one()
        assert remaining.photo_filename
        assert remaining.photo_filename in fake_s3.keys(FAKE_BUCKET)
        entry = test_db.query(CookbookEntry).filter(CookbookEntry.user_id == test_user.id).one()
        assert entry.photo_filename in fake_s3.keys(FAKE_BUCKET)
        assert entry.photo_filename != remaining.photo_filename

    def test_cookbook_update_keeps_old_photo_when_commit_fails(
        self, fake_s3, test_db: Session, test_user: User
    ):
        from app.services.cookbook import add_meal_to_cookbook
        from app.storage import get_storage

        meal = _create_meal(test_db, test_user)
        first_key = f"meals/{test_user.id}/aaa_first.png"
        get_storage().put(first_key, b"first", content_type="image/png")
        meal.photo_filename = first_key
        test_db.commit()

        entry = add_meal_to_cookbook(test_db, meal, test_user)
        old_photo = entry.photo_filename
        assert old_photo.startswith(f"cookbook/{test_user.id}/")
        assert fake_s3.objects[(FAKE_BUCKET, old_photo)]["Body"] == b"first"

        second_key = f"meals/{test_user.id}/bbb_second.png"
        get_storage().put(second_key, b"second", content_type="image/png")
        meal.photo_filename = second_key
        test_db.commit()

        with patch.object(test_db, "commit", side_effect=Exception("update failed")):
            with pytest.raises(Exception, match="update failed"):
                add_meal_to_cookbook(test_db, meal, test_user)

        test_db.refresh(entry)
        assert entry.photo_filename == old_photo
        cookbook_keys = {key for key in fake_s3.keys(FAKE_BUCKET) if key.startswith("cookbook/")}
        assert cookbook_keys == {old_photo}
        assert fake_s3.objects[(FAKE_BUCKET, old_photo)]["Body"] == b"first"
