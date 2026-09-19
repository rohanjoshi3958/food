"""Unit tests for receipt image preprocessing and content hashing (FOOD-55 slice 1)."""
import hashlib
import io

from PIL import Image

from app.services.receipt_preprocess import (
    OCR_MAX_LONG_EDGE,
    OCR_MIN_LONG_EDGE,
    content_hash,
    normalize_for_vision,
    prepare_for_ocr,
)


def _png_bytes(size: tuple[int, int], mode: str = "RGB") -> bytes:
    buffer = io.BytesIO()
    Image.new(mode, size, "white").save(buffer, format="PNG")
    return buffer.getvalue()


class TestContentHash:
    def test_sha256_of_raw_bytes(self):
        data = b"fake image data"
        assert content_hash(data) == hashlib.sha256(data).hexdigest()

    def test_identical_bytes_share_hash_and_differ_otherwise(self):
        assert content_hash(b"abc") == content_hash(b"abc")
        assert content_hash(b"abc") != content_hash(b"abd")


class TestNormalizeForVision:
    def test_downsamples_long_edge_and_reencodes_jpeg(self):
        normalized = normalize_for_vision(_png_bytes((4000, 2000)), long_edge=1600)
        assert normalized is not None
        assert normalized.media_type == "image/jpeg"
        assert (normalized.width, normalized.height) == (1600, 800)
        assert Image.open(io.BytesIO(normalized.data)).format == "JPEG"

    def test_small_images_are_not_upscaled(self):
        normalized = normalize_for_vision(_png_bytes((300, 900)), long_edge=1600)
        assert normalized is not None
        assert (normalized.width, normalized.height) == (300, 900)

    def test_rgba_is_flattened_to_rgb(self):
        normalized = normalize_for_vision(_png_bytes((100, 100), mode="RGBA"))
        assert normalized is not None
        assert Image.open(io.BytesIO(normalized.data)).mode == "RGB"

    def test_non_image_bytes_return_none(self):
        assert normalize_for_vision(b"%PDF-1.4 not really an image") is None
        assert normalize_for_vision(b"") is None


class TestPrepareForOcr:
    def test_grayscale_and_upscaled_to_min_long_edge(self):
        image = prepare_for_ocr(_png_bytes((300, 900)))
        assert image is not None
        assert image.mode == "L"
        assert max(image.size) == OCR_MIN_LONG_EDGE

    def test_capped_at_max_long_edge(self):
        image = prepare_for_ocr(_png_bytes((1000, 5000)))
        assert image is not None
        assert max(image.size) == OCR_MAX_LONG_EDGE

    def test_non_image_returns_none(self):
        assert prepare_for_ocr(b"fake image data") is None
