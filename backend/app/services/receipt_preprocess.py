"""Image normalization and content hashing for receipt uploads (FOOD-55 slice 1).

Pillow is a hard dependency; Tesseract is not touched here.
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass

from PIL import Image, ImageOps, UnidentifiedImageError

# Vision models don't need more than this to read a receipt; smaller uploads
# mean fewer image tokens on the LLM rungs.
DEFAULT_VISION_LONG_EDGE = 1600
# Tesseract wants roughly 300 DPI. Receipt photos are usually tall and narrow,
# so we clamp the long edge rather than the width.
OCR_MIN_LONG_EDGE = 1800
OCR_MAX_LONG_EDGE = 3200


def content_hash(data: bytes) -> str:
    """SHA-256 hex digest of the raw upload bytes."""
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class NormalizedImage:
    data: bytes
    media_type: str
    width: int
    height: int


def _open_image(data: bytes) -> Image.Image | None:
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except (UnidentifiedImageError, OSError, ValueError):
        return None
    # Respect camera orientation so text lines stay horizontal.
    return ImageOps.exif_transpose(image)


def _resize_long_edge(image: Image.Image, long_edge: int) -> Image.Image:
    current = max(image.size)
    if current == long_edge:
        return image
    scale = long_edge / current
    size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
    return image.resize(size, Image.Resampling.LANCZOS)


def normalize_for_vision(
    data: bytes,
    long_edge: int = DEFAULT_VISION_LONG_EDGE,
) -> NormalizedImage | None:
    """Downsample an image upload to ``long_edge`` and re-encode as JPEG.

    Returns ``None`` for non-image payloads (e.g. PDFs) so callers can send
    the original bytes instead.
    """
    image = _open_image(data)
    if image is None:
        return None

    if max(image.size) > long_edge:
        image = _resize_long_edge(image, long_edge)

    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")

    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=85, optimize=True)
    return NormalizedImage(
        data=buffer.getvalue(),
        media_type="image/jpeg",
        width=image.width,
        height=image.height,
    )


def prepare_for_ocr(data: bytes) -> Image.Image | None:
    """Return a grayscale, contrast-stretched image sized for Tesseract.

    Returns ``None`` when the bytes are not a decodable image.
    """
    image = _open_image(data)
    if image is None:
        return None

    image = image.convert("L")
    long_edge = max(image.size)
    if long_edge < OCR_MIN_LONG_EDGE:
        image = _resize_long_edge(image, OCR_MIN_LONG_EDGE)
    elif long_edge > OCR_MAX_LONG_EDGE:
        image = _resize_long_edge(image, OCR_MAX_LONG_EDGE)

    return ImageOps.autocontrast(image, cutoff=1)
