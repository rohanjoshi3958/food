"""Upload storage for receipts, meal photos and cookbook photos.

Objects are addressed by stable keys of the form ``<prefix>/<user_id>/<file>``
where ``<prefix>`` is one of :data:`UPLOAD_PREFIXES`. The same key is stored in
the database regardless of backend:

* **S3** (``UPLOADS_BUCKET`` set): objects live in the private uploads bucket
  under those prefixes, matching the IAM policy attached to the App Runner
  instance role. Credentials stay on the default AWS SDK chain (instance role
  in prod). Region is taken from ``AWS_REGION`` or ``AWS_DEFAULT_REGION`` (the
  latter is what boto3 reads on its own) and passed as ``region_name``.
* **Local disk** (``UPLOADS_BUCKET`` unset): each prefix maps to one of the
  ``*_upload_dir`` settings so ``docker-compose`` / ``npm run dev`` keep working
  without any AWS configuration.
"""

from __future__ import annotations

import logging
import mimetypes
import os
import shutil
import threading
import uuid
from abc import ABC, abstractmethod
from pathlib import Path

from fastapi import HTTPException, status
from fastapi.responses import FileResponse, RedirectResponse, Response

from app.config import settings

logger = logging.getLogger(__name__)

RECEIPTS_PREFIX = "receipts"
MEALS_PREFIX = "meals"
COOKBOOK_PREFIX = "cookbook"
UPLOAD_PREFIXES: tuple[str, ...] = (RECEIPTS_PREFIX, MEALS_PREFIX, COOKBOOK_PREFIX)

_MAX_FILENAME_LENGTH = 120


class StorageError(Exception):
    """Base class for storage backend failures."""


class InvalidObjectKey(StorageError, ValueError):
    """The key is not of the form ``<allowed prefix>/<user_id>/<file>``."""


class StorageObjectNotFound(StorageError):
    """The requested object does not exist."""


# ---------------------------------------------------------------------------
# Keys
# ---------------------------------------------------------------------------


def safe_filename(name: str | None) -> str:
    """Reduce a client-supplied filename to a single, key-safe path segment."""
    candidate = (name or "").replace("\\", "/").rsplit("/", 1)[-1]
    candidate = "".join(ch for ch in candidate if ch.isprintable() and ch not in "\x00")
    candidate = candidate.strip().strip(".")
    if not candidate:
        return "upload"
    if len(candidate) > _MAX_FILENAME_LENGTH:
        stem, dot, suffix = candidate.rpartition(".")
        if dot and len(suffix) <= 10:
            keep = _MAX_FILENAME_LENGTH - len(suffix) - 1
            candidate = f"{stem[:keep]}.{suffix}"
        else:
            candidate = candidate[:_MAX_FILENAME_LENGTH]
    return candidate


def validate_object_key(key: str) -> str:
    if not key or key.startswith("/") or key.endswith("/") or "\\" in key:
        raise InvalidObjectKey(f"Invalid object key: {key!r}")

    parts = key.split("/")
    if len(parts) < 3 or any(part in ("", ".", "..") for part in parts):
        raise InvalidObjectKey(f"Invalid object key: {key!r}")

    if parts[0] not in UPLOAD_PREFIXES:
        raise InvalidObjectKey(
            f"Object key {key!r} must start with one of {', '.join(UPLOAD_PREFIXES)}"
        )
    return key


def build_object_key(prefix: str, user_id: str, filename: str | None) -> str:
    """Create a new, unique key: ``<prefix>/<user_id>/<uuid>_<safe filename>``."""
    if prefix not in UPLOAD_PREFIXES:
        raise InvalidObjectKey(f"Unknown upload prefix: {prefix!r}")
    if not user_id or "/" in user_id or "\\" in user_id:
        raise InvalidObjectKey(f"Invalid user id for object key: {user_id!r}")
    return validate_object_key(
        f"{prefix}/{user_id}/{uuid.uuid4().hex}_{safe_filename(filename)}"
    )


def resolve_photo_key(prefix: str, user_id: str, stored_value: str) -> str:
    """Return a full object key for a ``photo_filename`` column value.

    Rows written before object keys were introduced hold only the stored
    basename; rebuild the key from the owning user and prefix in that case.
    """
    if "/" in stored_value:
        return validate_object_key(stored_value)
    return validate_object_key(f"{prefix}/{user_id}/{stored_value}")


def guess_content_type(key_or_name: str, fallback: str = "application/octet-stream") -> str:
    guessed, _ = mimetypes.guess_type(key_or_name)
    return guessed or fallback


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------


class UploadStorage(ABC):
    backend: str

    @abstractmethod
    def put(self, key: str, data: bytes, content_type: str | None = None) -> None: ...

    @abstractmethod
    def get(self, key: str) -> bytes: ...

    @abstractmethod
    def exists(self, key: str) -> bool: ...

    @abstractmethod
    def delete(self, key: str) -> None:
        """Remove the object; deleting a missing object is not an error."""

    @abstractmethod
    def copy(self, source_key: str, destination_key: str) -> None: ...

    def signed_url(self, key: str, expires_in: int) -> str | None:
        """Time-limited URL the browser can fetch directly, if supported."""
        del key, expires_in
        return None

    def local_path(self, key: str) -> Path | None:
        """Filesystem path for the object, if the backend is local."""
        del key
        return None


class LocalUploadStorage(UploadStorage):
    backend = "local"

    def __init__(self, roots: dict[str, Path]):
        missing = set(UPLOAD_PREFIXES) - set(roots)
        if missing:
            raise ValueError(f"Missing local roots for prefixes: {sorted(missing)}")
        self._roots = {prefix: Path(root) for prefix, root in roots.items()}

    @classmethod
    def from_settings(cls) -> "LocalUploadStorage":
        return cls(
            {
                RECEIPTS_PREFIX: Path(settings.upload_dir),
                MEALS_PREFIX: Path(settings.meal_upload_dir),
                COOKBOOK_PREFIX: Path(settings.cookbook_upload_dir),
            }
        )

    def ensure_directories(self) -> None:
        for root in self._roots.values():
            root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        validate_object_key(key)
        prefix, remainder = key.split("/", 1)
        root = self._roots[prefix].resolve()
        candidate = (root / remainder).resolve()
        if not candidate.is_relative_to(root):
            raise InvalidObjectKey(
                f"Object key {key!r} resolves outside the {prefix} storage root"
            )
        return candidate

    def put(self, key: str, data: bytes, content_type: str | None = None) -> None:
        del content_type
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def get(self, key: str) -> bytes:
        path = self._path(key)
        try:
            return path.read_bytes()
        except FileNotFoundError as exc:
            raise StorageObjectNotFound(key) from exc

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def copy(self, source_key: str, destination_key: str) -> None:
        source = self._path(source_key)
        if not source.is_file():
            raise StorageObjectNotFound(source_key)
        destination = self._path(destination_key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)

    def local_path(self, key: str) -> Path | None:
        return self._path(key)


class S3UploadStorage(UploadStorage):
    backend = "s3"

    def __init__(self, bucket: str, client=None):
        if not bucket:
            raise ValueError("S3UploadStorage requires a bucket name")
        self.bucket = bucket
        if client is None:
            import boto3
            from botocore.config import Config

            # Credentials stay on the default chain (instance role in prod).
            # Region: boto3 reads AWS_DEFAULT_REGION, not AWS_REGION. App Runner
            # documents AWS_REGION, so pass whichever of the two is set.
            client_kwargs: dict[str, object] = {
                "config": Config(signature_version="s3v4", retries={"mode": "standard"}),
            }
            region = (os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "").strip()
            if region:
                client_kwargs["region_name"] = region
            client = boto3.client("s3", **client_kwargs)
        self._client = client

    @staticmethod
    def _is_not_found(exc: Exception) -> bool:
        response = getattr(exc, "response", None) or {}
        code = str(response.get("Error", {}).get("Code", ""))
        status_code = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        return code in {"404", "NoSuchKey", "NotFound"} or status_code == 404

    def put(self, key: str, data: bytes, content_type: str | None = None) -> None:
        validate_object_key(key)
        try:
            self._client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=data,
                ContentType=content_type or guess_content_type(key),
            )
        except Exception as exc:
            raise StorageError(f"Unable to write {key!r} to S3") from exc

    def get(self, key: str) -> bytes:
        validate_object_key(key)
        try:
            response = self._client.get_object(Bucket=self.bucket, Key=key)
        except Exception as exc:
            if self._is_not_found(exc):
                raise StorageObjectNotFound(key) from exc
            raise StorageError(f"Unable to read {key!r} from S3") from exc
        return response["Body"].read()

    def exists(self, key: str) -> bool:
        validate_object_key(key)
        try:
            self._client.head_object(Bucket=self.bucket, Key=key)
        except Exception as exc:
            if self._is_not_found(exc):
                return False
            raise StorageError(f"Unable to stat {key!r} in S3") from exc
        return True

    def delete(self, key: str) -> None:
        validate_object_key(key)
        try:
            self._client.delete_object(Bucket=self.bucket, Key=key)
        except Exception as exc:
            if self._is_not_found(exc):
                return
            raise StorageError(f"Unable to delete {key!r} from S3") from exc

    def copy(self, source_key: str, destination_key: str) -> None:
        validate_object_key(source_key)
        validate_object_key(destination_key)
        try:
            self._client.copy_object(
                Bucket=self.bucket,
                Key=destination_key,
                CopySource={"Bucket": self.bucket, "Key": source_key},
                ContentType=guess_content_type(destination_key),
                MetadataDirective="REPLACE",
            )
        except Exception as exc:
            if self._is_not_found(exc):
                raise StorageObjectNotFound(source_key) from exc
            raise StorageError(
                f"Unable to copy {source_key!r} to {destination_key!r} in S3"
            ) from exc

    def signed_url(self, key: str, expires_in: int) -> str | None:
        validate_object_key(key)
        try:
            return self._client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": key},
                ExpiresIn=expires_in,
            )
        except Exception as exc:
            raise StorageError(f"Unable to sign URL for {key!r}") from exc


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

_s3_lock = threading.Lock()
_s3_instances: dict[str, S3UploadStorage] = {}


def get_storage() -> UploadStorage:
    """Storage for the current settings: S3 when UPLOADS_BUCKET is set, else disk."""
    bucket = settings.uploads_bucket.strip()
    if not bucket:
        return LocalUploadStorage.from_settings()

    with _s3_lock:
        storage = _s3_instances.get(bucket)
        if storage is None:
            storage = S3UploadStorage(bucket)
            _s3_instances[bucket] = storage
        return storage


def reset_storage_cache() -> None:
    with _s3_lock:
        _s3_instances.clear()


# ---------------------------------------------------------------------------
# Helpers shared by routers / services
# ---------------------------------------------------------------------------


def delete_quietly(key: str | None) -> None:
    """Best-effort delete used for cleanup paths; never raises."""
    if not key:
        return
    try:
        get_storage().delete(key)
    except InvalidObjectKey:
        logger.warning("Skipping delete of malformed object key %r", key)
    except StorageError:
        logger.warning("Failed to delete upload %r", key, exc_info=True)


def serve_object(key: str, *, not_found_detail: str = "File not found.") -> Response:
    """Serve an object to the browser.

    S3: redirect to a short-lived presigned GET URL so bytes never pass through
    the API (unless ``UPLOADS_SIGNED_URL_TTL_SECONDS`` is 0, in which case the
    object is streamed). Local: plain ``FileResponse``.
    """
    storage = get_storage()
    try:
        validate_object_key(key)
        if not storage.exists(key):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=not_found_detail)
    except InvalidObjectKey as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=not_found_detail
        ) from exc

    ttl = settings.uploads_signed_url_ttl_seconds
    if ttl > 0:
        url = storage.signed_url(key, ttl)
        if url:
            return RedirectResponse(
                url,
                status_code=status.HTTP_307_TEMPORARY_REDIRECT,
                headers={"Cache-Control": "private, no-store"},
            )

    path = storage.local_path(key)
    if path is not None:
        return FileResponse(path)

    return Response(
        content=storage.get(key),
        media_type=guess_content_type(key),
        headers={"Cache-Control": "private, no-store"},
    )
