"""Unit tests for the Tesseract wrapper (FOOD-55 slice 2).

Everything here runs without the Tesseract binary except the single test
marked ``requires_tesseract``, which is skipped when it is not installed.
"""
import io
from unittest.mock import patch

import pytest
from PIL import Image, ImageDraw, ImageFont

from app.config import settings
from app.services.receipt_ocr import (
    OcrUnavailableError,
    lines_from_tesseract_data,
    run_tesseract,
    tesseract_available,
)
from app.services.receipt_parser import clean_ocr_line, parse_receipt_text
from app.services.receipt_preprocess import prepare_for_ocr


def _tsv_dict(words: list[tuple[str, float, int]]) -> dict:
    """Build an ``image_to_data`` style dict from (text, conf, line_num) tuples."""
    return {
        "text": [w[0] for w in words],
        "conf": [w[1] for w in words],
        "block_num": [1] * len(words),
        "par_num": [1] * len(words),
        "line_num": [w[2] for w in words],
    }


class TestLinesFromTesseractData:
    def test_groups_words_into_lines_and_collects_confidences(self):
        data = _tsv_dict(
            [
                ("", -1, 1),
                ("WHOLE", 95.0, 1),
                ("FOODS", 91.0, 1),
                ("ORG", 80.0, 2),
                ("BNNAS", "70", 2),
            ]
        )
        lines, confidences = lines_from_tesseract_data(data)
        assert lines == ["WHOLE FOODS", "ORG BNNAS"]
        assert confidences == [95.0, 91.0, 80.0, 70.0]

    def test_blank_words_and_negative_conf_are_ignored(self):
        lines, confidences = lines_from_tesseract_data(_tsv_dict([("   ", 90.0, 1), ("X", -1, 1)]))
        assert lines == ["X"]
        assert confidences == []


class TestRunTesseract:
    def test_mean_confidence_and_text(self):
        data = _tsv_dict([("MILK", 90.0, 1), ("3.49", 70.0, 1), ("TOTAL", 80.0, 2), ("3.49", 80.0, 2)])
        with patch("pytesseract.image_to_data", return_value=data) as mocked:
            result = run_tesseract(Image.new("L", (10, 10)))
        assert result.text == "MILK 3.49\nTOTAL 3.49"
        assert result.confidence == pytest.approx(80.0)
        assert result.word_count == 4
        assert result.engine == "tesseract"
        assert mocked.call_args.kwargs["config"] == settings.receipt_ocr_tesseract_config

    def test_no_words_gives_none_confidence(self):
        with patch("pytesseract.image_to_data", return_value=_tsv_dict([])):
            result = run_tesseract(Image.new("L", (10, 10)))
        assert result.text == ""
        assert result.confidence is None

    def test_missing_binary_raises_unavailable(self):
        from pytesseract import TesseractNotFoundError

        with patch("pytesseract.image_to_data", side_effect=TesseractNotFoundError()):
            with pytest.raises(OcrUnavailableError):
                run_tesseract(Image.new("L", (10, 10)))


class TestTesseractAvailable:
    def test_false_when_binary_missing(self):
        with patch("app.services.receipt_ocr.shutil.which", return_value=None):
            assert tesseract_available() is False

    def test_true_when_binary_found(self):
        with patch("app.services.receipt_ocr.shutil.which", return_value="/usr/bin/tesseract"):
            assert tesseract_available() is True


class TestCleanOcrLine:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("GRK YOGURT 320Z 5.49 F", "GRK YOGURT 32 OZ 5.49 F"),
            ("ALMOND BUTTER16O0Z 9.99", "ALMOND BUTTER16 OZ 9.99"),
            ("MILK S3.49", "MILK $3.49"),
            ("SALSA 2.99", "SALSA 2.99"),
        ],
    )
    def test_confusions(self, raw, expected):
        assert clean_ocr_line(raw) == expected


@pytest.mark.skipif(not tesseract_available(), reason="Tesseract binary not installed")
class TestLiveTesseract:
    """Smoke test against the real binary on a rendered receipt (skipped in CI without Tesseract)."""

    def test_rendered_receipt_round_trips_through_parser(self):
        lines = [
            "WHOLE FOODS MARKET",
            "ORG BNNAS",
            "2.14 lb @ 0.69 /lb      1.48 F",
            "ALMOND BUTTER 16 OZ     9.99 F",
            "SUBTOTAL               11.47",
            "TOTAL                  11.47",
        ]
        font = ImageFont.load_default(size=36)
        image = Image.new("RGB", (900, 60 * len(lines) + 80), "white")
        draw = ImageDraw.Draw(image)
        for index, line in enumerate(lines):
            draw.text((40, 40 + 60 * index), line, fill="black", font=font)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")

        prepared = prepare_for_ocr(buffer.getvalue())
        assert prepared is not None
        result = run_tesseract(prepared)

        assert result.confidence is not None and result.confidence > 50
        assert "WHOLE FOODS" in result.text.upper()

        outcome = parse_receipt_text(result.text)
        assert outcome.receipt.store_name == "Whole Foods Market"
        names = {item.store_item_name.upper() for item in outcome.receipt.items}
        assert "ORG BNNAS" in names
        assert outcome.diagnostics.total == pytest.approx(11.47)
