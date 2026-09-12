"""In-process Tesseract OCR for receipts (FOOD-55 slice 2).

``pytesseract`` is imported lazily so the rest of the app (and CI without the
Tesseract binary) keeps working; callers get ``OcrUnavailableError`` instead.
"""

from __future__ import annotations

import shutil
from collections import OrderedDict
from dataclasses import dataclass, field

from PIL import Image

from app.config import settings


class OcrUnavailableError(Exception):
    """Tesseract (or pytesseract) is missing, or the input is not an image."""


@dataclass
class OcrResult:
    text: str
    # Mean Tesseract word confidence on a 0-100 scale; None when no words.
    confidence: float | None
    engine: str = "tesseract"
    word_count: int = 0
    lines: list[str] = field(default_factory=list)


def tesseract_available() -> bool:
    try:
        import pytesseract  # noqa: F401
    except ImportError:
        return False
    override = settings.receipt_ocr_tesseract_cmd
    if override:
        return shutil.which(override) is not None or _is_file(override)
    return shutil.which("tesseract") is not None


def _is_file(path: str) -> bool:
    from pathlib import Path

    candidate = Path(path)
    return candidate.is_file()


def lines_from_tesseract_data(data: dict) -> tuple[list[str], list[float]]:
    """Rebuild text lines from ``image_to_data`` output, keeping word confidences.

    A single Tesseract pass gives us both the text and per-word confidence,
    so the gate sees exactly the text the parser sees.
    """
    grouped: "OrderedDict[tuple[int, int, int], list[str]]" = OrderedDict()
    confidences: list[float] = []

    texts = data.get("text", [])
    for index, raw in enumerate(texts):
        word = (raw or "").strip()
        if not word:
            continue
        try:
            conf = float(data["conf"][index])
        except (KeyError, IndexError, TypeError, ValueError):
            conf = -1.0
        # Tesseract reports -1 for non-word boxes.
        if conf >= 0:
            confidences.append(conf)
        key = (
            int(data["block_num"][index]),
            int(data["par_num"][index]),
            int(data["line_num"][index]),
        )
        grouped.setdefault(key, []).append(word)

    lines = [" ".join(words) for words in grouped.values()]
    return lines, confidences


def run_tesseract(image: Image.Image) -> OcrResult:
    """OCR a preprocessed PIL image. Raises ``OcrUnavailableError`` if Tesseract is missing."""
    try:
        import pytesseract
        from pytesseract import Output, TesseractNotFoundError
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise OcrUnavailableError("pytesseract is not installed.") from exc

    if settings.receipt_ocr_tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = settings.receipt_ocr_tesseract_cmd

    try:
        data = pytesseract.image_to_data(
            image,
            config=settings.receipt_ocr_tesseract_config,
            output_type=Output.DICT,
        )
    except TesseractNotFoundError as exc:
        raise OcrUnavailableError("Tesseract binary is not installed.") from exc
    except pytesseract.TesseractError as exc:
        raise OcrUnavailableError(f"Tesseract failed: {exc}") from exc

    lines, confidences = lines_from_tesseract_data(data)
    confidence = sum(confidences) / len(confidences) if confidences else None
    return OcrResult(
        text="\n".join(lines),
        confidence=confidence,
        word_count=len(confidences),
        lines=lines,
    )
