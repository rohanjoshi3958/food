"""Unit tests for app.storage: object keys, local + S3 backends, serving."""

import re
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.responses import FileResponse, RedirectResponse

from app import storage
from app.storage import (
    COOKBOOK_PREFIX,
    MEALS_PREFIX,
    RECEIPTS_PREFIX,
    InvalidObjectKey,
    LocalUploadStorage,
    S3UploadStorage,
    StorageError,
    StorageObjectNotFound,
    build_object_key,
    get_storage,
    resolve_photo_key,
    safe_filename,
    serve_object,
    validate_object_key,
)
from tests.conftest import FAKE_BUCKET, FakeS3Client

USER = "user-123"
HEX32 = r"[0-9a-f]{32}"


class TestKeys:
    def test_safe_filename_strips_directories_and_junk(self):
        assert safe_filename("receipt.jpg") == "receipt.jpg"
        assert safe_filename("../../etc/passwd") == "passwd"
        assert safe_filename("C:\\Users\\me\\photo.png") == "photo.png"
        assert safe_filename("  spaced name.pdf ") == "spaced name.pdf"
        assert safe_filename("bad\x00name.jpg") == "badname.jpg"
        assert safe_filename("") == "upload"
        assert safe_filename(None) == "upload"
        assert safe_filename("...") == "upload"

    def test_safe_filename_truncates_but_keeps_extension(self):
        long_name = "a" * 500 + ".jpeg"
        result = safe_filename(long_name)
        assert len(result) <= 120
        assert result.endswith(".jpeg")

    @pytest.mark.parametrize("prefix", [RECEIPTS_PREFIX, MEALS_PREFIX, COOKBOOK_PREFIX])
    def test_build_object_key_layout(self, prefix):
        key = build_object_key(prefix, USER, "photo.png")
        assert re.fullmatch(rf"{prefix}/{USER}/{HEX32}_photo\.png", key)
        assert validate_object_key(key) == key

    def test_build_object_key_is_unique(self):
        first = build_object_key(MEALS_PREFIX, USER, "a.png")
        second = build_object_key(MEALS_PREFIX, USER, "a.png")
        assert first != second

    def test_build_object_key_rejects_unknown_prefix_or_bad_user(self):
        with pytest.raises(InvalidObjectKey):
            build_object_key("avatars", USER, "a.png")
        with pytest.raises(InvalidObjectKey):
            build_object_key(MEALS_PREFIX, "", "a.png")
        with pytest.raises(InvalidObjectKey):
            build_object_key(MEALS_PREFIX, "a/b", "a.png")
        with pytest.raises(InvalidObjectKey):
            build_object_key(MEALS_PREFIX, r"a\b", "a.png")

    @pytest.mark.parametrize(
        "bad_key",
        [
            "",
            "/receipts/u/f.jpg",
            "receipts/u/",
            "receipts/u",
            "receipts//f.jpg",
            "receipts/u/../other/f.jpg",
            "receipts/./f.jpg",
            "uploads/receipts/u/f.jpg",
            "avatars/u/f.jpg",
            r"receipts\u\f.jpg",
            r"receipts/u/..\..\secret",
            r"receipts/u/foo\bar.jpg",
        ],
    )
    def test_validate_object_key_rejects(self, bad_key):
        with pytest.raises(InvalidObjectKey):
            validate_object_key(bad_key)

    def test_resolve_photo_key_handles_legacy_basename_and_full_key(self):
        assert (
            resolve_photo_key(MEALS_PREFIX, USER, "abc_photo.png")
            == f"meals/{USER}/abc_photo.png"
        )
        full = f"cookbook/{USER}/abc_photo.png"
        assert resolve_photo_key(COOKBOOK_PREFIX, USER, full) == full
        with pytest.raises(InvalidObjectKey):
            resolve_photo_key(MEALS_PREFIX, USER, "avatars/x/y.png")


@pytest.fixture
def local_backend(tmp_path):
    return LocalUploadStorage(
        {
            RECEIPTS_PREFIX: tmp_path / "r",
            MEALS_PREFIX: tmp_path / "m",
            COOKBOOK_PREFIX: tmp_path / "c",
        }
    )


class TestLocalUploadStorage:
    def test_requires_every_prefix(self, tmp_path):
        with pytest.raises(ValueError):
            LocalUploadStorage({RECEIPTS_PREFIX: tmp_path})

    def test_roundtrip_maps_prefix_to_root(self, local_backend, tmp_path):
        key = f"meals/{USER}/x_photo.png"
        assert not local_backend.exists(key)

        local_backend.put(key, b"png-bytes", content_type="image/png")

        assert local_backend.exists(key)
        assert local_backend.get(key) == b"png-bytes"
        assert local_backend.local_path(key) == (tmp_path / "m" / USER / "x_photo.png").resolve()
        assert (tmp_path / "m" / USER / "x_photo.png").read_bytes() == b"png-bytes"
        assert local_backend.signed_url(key, 60) is None

    def test_copy_and_delete(self, local_backend, tmp_path):
        source = f"meals/{USER}/x_photo.png"
        destination = f"cookbook/{USER}/y_photo.png"
        local_backend.put(source, b"data")

        local_backend.copy(source, destination)
        assert local_backend.get(destination) == b"data"
        assert (tmp_path / "c" / USER / "y_photo.png").exists()

        local_backend.delete(source)
        assert not local_backend.exists(source)
        local_backend.delete(source)  # idempotent

    def test_missing_objects(self, local_backend):
        with pytest.raises(StorageObjectNotFound):
            local_backend.get(f"receipts/{USER}/nope.jpg")
        with pytest.raises(StorageObjectNotFound):
            local_backend.copy(f"meals/{USER}/nope.jpg", f"cookbook/{USER}/x.jpg")

    def test_rejects_traversal_keys(self, local_backend):
        with pytest.raises(InvalidObjectKey):
            local_backend.put(f"receipts/{USER}/../../escape.txt", b"x")
        with pytest.raises(InvalidObjectKey):
            local_backend.get("uploads/receipts/u/f.jpg")

    def test_path_stays_under_configured_root(self, local_backend, tmp_path):
        key = f"receipts/{USER}/ok.jpg"
        path = local_backend._path(key)
        assert path.is_relative_to((tmp_path / "r").resolve())

    def test_path_rejects_resolved_escape_even_if_key_slipped_through(
        self, local_backend, monkeypatch
    ):
        monkeypatch.setattr("app.storage.validate_object_key", lambda key: key)
        with pytest.raises(InvalidObjectKey, match="outside the receipts storage root"):
            local_backend._path(f"receipts/{USER}/../../outside.txt")

    def test_ensure_directories(self, local_backend, tmp_path):
        local_backend.ensure_directories()
        assert (tmp_path / "r").is_dir()
        assert (tmp_path / "m").is_dir()
        assert (tmp_path / "c").is_dir()


class TestS3UploadStorage:
    @pytest.fixture
    def fake(self):
        return FakeS3Client()

    @pytest.fixture
    def backend(self, fake):
        return S3UploadStorage(FAKE_BUCKET, client=fake)

    def test_requires_bucket(self):
        with pytest.raises(ValueError):
            S3UploadStorage("", client=FakeS3Client())

    def test_put_get_exists_delete(self, backend, fake):
        key = f"receipts/{USER}/x_receipt.jpg"
        assert not backend.exists(key)

        backend.put(key, b"jpeg", content_type="image/jpeg")

        assert fake.objects[(FAKE_BUCKET, key)] == {"Body": b"jpeg", "ContentType": "image/jpeg"}
        assert backend.exists(key)
        assert backend.get(key) == b"jpeg"
        assert backend.local_path(key) is None

        backend.delete(key)
        assert not backend.exists(key)
        backend.delete(key)  # missing object is not an error

    def test_put_guesses_content_type_from_key(self, backend, fake):
        key = f"meals/{USER}/x_dinner.png"
        backend.put(key, b"png")
        assert fake.objects[(FAKE_BUCKET, key)]["ContentType"] == "image/png"

    def test_copy_is_server_side(self, backend, fake):
        source = f"meals/{USER}/x_dinner.png"
        destination = f"cookbook/{USER}/y_dinner.png"
        backend.put(source, b"png", content_type="image/png")

        backend.copy(source, destination)

        assert backend.get(destination) == b"png"
        copy_calls = [call for call in fake.calls if call[0] == "copy_object"]
        assert copy_calls == [
            (
                "copy_object",
                {
                    "Bucket": FAKE_BUCKET,
                    "Key": destination,
                    "CopySource": {"Bucket": FAKE_BUCKET, "Key": source},
                },
            )
        ]

    def test_missing_objects_map_to_not_found(self, backend):
        with pytest.raises(StorageObjectNotFound):
            backend.get(f"receipts/{USER}/nope.jpg")
        with pytest.raises(StorageObjectNotFound):
            backend.copy(f"meals/{USER}/nope.jpg", f"cookbook/{USER}/x.jpg")

    def test_other_client_errors_become_storage_error(self, fake, backend):
        from botocore.exceptions import ClientError

        def boom(**_):
            raise ClientError(
                {"Error": {"Code": "AccessDenied", "Message": "denied"}},
                "GetObject",
            )

        fake.get_object = boom
        with pytest.raises(StorageError) as excinfo:
            backend.get(f"receipts/{USER}/x.jpg")
        assert not isinstance(excinfo.value, StorageObjectNotFound)

    def test_signed_url(self, backend):
        key = f"cookbook/{USER}/x_dinner.png"
        url = backend.signed_url(key, 300)
        assert url.startswith(f"https://{FAKE_BUCKET}.s3.amazonaws.com/{key}")
        assert "X-Amz-Expires=300" in url

    def test_never_touches_keys_outside_allowed_prefixes(self, backend, fake):
        with pytest.raises(InvalidObjectKey):
            backend.put("avatars/u/x.png", b"x")
        with pytest.raises(InvalidObjectKey):
            backend.signed_url("../secrets", 60)
        assert fake.calls == []


class TestGetStorage:
    def test_local_when_bucket_unset(self, local_uploads):
        backend = get_storage()
        assert isinstance(backend, LocalUploadStorage)
        assert backend.local_path(f"receipts/{USER}/a.jpg") == (
            local_uploads["receipts"] / USER / "a.jpg"
        ).resolve()

    def test_local_when_bucket_is_blank(self, local_uploads, monkeypatch):
        monkeypatch.setenv("UPLOADS_BUCKET", "   ")
        assert isinstance(get_storage(), LocalUploadStorage)

    def test_s3_when_uploads_bucket_set(self, fake_s3, monkeypatch):
        monkeypatch.delenv("AWS_REGION", raising=False)
        monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
        storage.reset_storage_cache()
        backend = get_storage()
        assert isinstance(backend, S3UploadStorage)
        assert backend.bucket == FAKE_BUCKET
        # Credentials stay on the default chain; region is only passed when
        # AWS_REGION / AWS_DEFAULT_REGION is set (see tests below).
        _, kwargs = fake_s3.client_factory.call_args
        assert "region_name" not in kwargs
        assert "aws_access_key_id" not in kwargs
        assert "endpoint_url" not in kwargs

    def test_s3_client_passes_aws_region(self, fake_s3, monkeypatch):
        monkeypatch.setenv("AWS_REGION", "us-west-2")
        monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
        storage.reset_storage_cache()
        get_storage()
        _, kwargs = fake_s3.client_factory.call_args
        assert kwargs["region_name"] == "us-west-2"
        assert "aws_access_key_id" not in kwargs

    def test_s3_client_falls_back_to_aws_default_region(self, fake_s3, monkeypatch):
        monkeypatch.delenv("AWS_REGION", raising=False)
        monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-west-1")
        storage.reset_storage_cache()
        get_storage()
        _, kwargs = fake_s3.client_factory.call_args
        assert kwargs["region_name"] == "eu-west-1"

    def test_s3_client_prefers_aws_region_over_default(self, fake_s3, monkeypatch):
        monkeypatch.setenv("AWS_REGION", "us-east-1")
        monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-west-1")
        storage.reset_storage_cache()
        get_storage()
        _, kwargs = fake_s3.client_factory.call_args
        assert kwargs["region_name"] == "us-east-1"

    def test_s3_client_is_cached_per_bucket(self, fake_s3, monkeypatch):
        first = get_storage()
        assert get_storage() is first
        assert fake_s3.client_factory.call_count == 1

        monkeypatch.setenv("UPLOADS_BUCKET", "another-bucket")
        other = get_storage()
        assert other is not first
        assert other.bucket == "another-bucket"
        storage.reset_storage_cache()


class TestServeObject:
    def test_local_serves_file(self, local_uploads):
        key = f"cookbook/{USER}/x_dinner.png"
        get_storage().put(key, b"png")

        response = serve_object(key)

        assert isinstance(response, FileResponse)
        assert Path(response.path) == local_uploads["cookbook"] / USER / "x_dinner.png"

    def test_s3_redirects_to_presigned_url(self, fake_s3, monkeypatch):
        monkeypatch.setenv("UPLOADS_SIGNED_URL_TTL_SECONDS", "120")
        key = f"cookbook/{USER}/x_dinner.png"
        get_storage().put(key, b"png")

        response = serve_object(key)

        assert isinstance(response, RedirectResponse)
        assert response.status_code == 307
        location = response.headers["location"]
        assert location.startswith(f"https://{FAKE_BUCKET}.s3.amazonaws.com/{key}")
        assert "X-Amz-Expires=120" in location
        assert response.headers["cache-control"] == "private, no-store"

    def test_s3_streams_bytes_when_signed_urls_disabled(self, fake_s3, monkeypatch):
        monkeypatch.setenv("UPLOADS_SIGNED_URL_TTL_SECONDS", "0")
        key = f"meals/{USER}/x_dinner.png"
        get_storage().put(key, b"png-bytes")

        response = serve_object(key)

        assert response.status_code == 200
        assert response.body == b"png-bytes"
        assert response.media_type == "image/png"
        assert not any(call[0] == "generate_presigned_url" for call in fake_s3.calls)

    def test_missing_object_is_404(self, fake_s3):
        with pytest.raises(HTTPException) as excinfo:
            serve_object(f"meals/{USER}/missing.png", not_found_detail="Photo not found.")
        assert excinfo.value.status_code == 404
        assert excinfo.value.detail == "Photo not found."

    def test_malformed_key_is_404_not_500(self, local_uploads):
        with pytest.raises(HTTPException) as excinfo:
            serve_object("uploads/meals/u/legacy.png")
        assert excinfo.value.status_code == 404
