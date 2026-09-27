"""Approximate visual-token accounting for image inputs.

Claude bills images as ``ceil(w/28) * ceil(h/28)`` visual tokens after
downscaling to the model's resolution tier (standard: 1568 px long edge /
1568 tokens; high-resolution on Claude 4.7+: 2576 px / 4784 tokens).

We only have the request payload, not Anthropic's exact resize routine, so
these numbers are labelled *approximate* everywhere they surface. They are
good enough to answer "what share of receipt-parse input is vision vs text".
"""

from __future__ import annotations

import base64
import math
import struct
from dataclasses import dataclass

from app.llm_usage.pricing import model_version

PATCH_PX = 28


@dataclass(frozen=True)
class ResolutionTier:
    name: str
    max_long_edge: int
    max_visual_tokens: int


STANDARD_TIER = ResolutionTier("standard", 1568, 1568)
HIGH_RES_TIER = ResolutionTier("high_resolution", 2576, 4784)


def resolution_tier(model: str) -> ResolutionTier:
    version = model_version(model)
    if version is None:
        return STANDARD_TIER
    return HIGH_RES_TIER if version >= (4, 7) else STANDARD_TIER


def visual_tokens_for_dimensions(width: int, height: int, model: str) -> int:
    if width <= 0 or height <= 0:
        return 0

    tier = resolution_tier(model)
    scale = min(1.0, tier.max_long_edge / max(width, height))
    raw_tokens = (width * height) / (PATCH_PX * PATCH_PX)
    if raw_tokens * scale * scale > tier.max_visual_tokens:
        scale = min(scale, math.sqrt(tier.max_visual_tokens / raw_tokens))

    tokens = _patch_tokens(width * scale, height * scale)
    # The formula above ignores patch rounding; nudge down until we respect the cap.
    while tokens > tier.max_visual_tokens and scale > 0.01:
        scale *= 0.99
        tokens = _patch_tokens(width * scale, height * scale)
    return tokens


def _patch_tokens(width: float, height: float) -> int:
    return math.ceil(width / PATCH_PX) * math.ceil(height / PATCH_PX)


def image_dimensions(data: bytes) -> tuple[int, int] | None:
    """Read width/height from PNG, JPEG, GIF, or WebP headers. None if unknown."""
    if len(data) < 24:
        return None

    if data[:8] == b"\x89PNG\r\n\x1a\n":
        width, height = struct.unpack(">II", data[16:24])
        return int(width), int(height)

    if data[:6] in (b"GIF87a", b"GIF89a"):
        width, height = struct.unpack("<HH", data[6:10])
        return int(width), int(height)

    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return _webp_dimensions(data)

    if data[:2] == b"\xff\xd8":
        return _jpeg_dimensions(data)

    return None


def _webp_dimensions(data: bytes) -> tuple[int, int] | None:
    chunk = data[12:16]
    if chunk == b"VP8 " and len(data) >= 30:
        width, height = struct.unpack("<HH", data[26:30])
        return width & 0x3FFF, height & 0x3FFF
    if chunk == b"VP8L" and len(data) >= 25:
        bits = struct.unpack("<I", data[21:25])[0]
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    if chunk == b"VP8X" and len(data) >= 30:
        width = int.from_bytes(data[24:27], "little") + 1
        height = int.from_bytes(data[27:30], "little") + 1
        return width, height
    return None


def _jpeg_dimensions(data: bytes) -> tuple[int, int] | None:
    offset = 2
    length = len(data)
    while offset + 9 < length:
        if data[offset] != 0xFF:
            offset += 1
            continue
        marker = data[offset + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            offset += 2
            continue
        segment_length = struct.unpack(">H", data[offset + 2 : offset + 4])[0]
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            height, width = struct.unpack(">HH", data[offset + 5 : offset + 9])
            return int(width), int(height)
        offset += 2 + segment_length
    return None


@dataclass(frozen=True)
class VisualInputSummary:
    image_count: int = 0
    document_count: int = 0
    approx_visual_tokens: int | None = None
    input_text_chars: int = 0

    @property
    def has_visual_input(self) -> bool:
        return self.image_count > 0 or self.document_count > 0


def summarize_visual_input(messages: list, system, model: str) -> VisualInputSummary:
    """Count image/document blocks and estimate their visual tokens.

    ``approx_visual_tokens`` is None when a visual block's size cannot be
    determined (e.g. PDFs, URL sources) so callers can distinguish
    "no images" from "images of unknown cost".
    """
    image_count = 0
    document_count = 0
    visual_tokens = 0
    unknown_visual = False
    text_chars = 0

    if isinstance(system, str):
        text_chars += len(system)
    elif isinstance(system, list):
        for block in system:
            text_chars += len(_block_text(block))

    for message in messages or []:
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, str):
            text_chars += len(content)
            continue
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type == "text":
                text_chars += len(block.get("text") or "")
            elif block_type == "image":
                image_count += 1
                dims = _dimensions_from_source(block.get("source"))
                if dims is None:
                    unknown_visual = True
                else:
                    visual_tokens += visual_tokens_for_dimensions(*dims, model)
            elif block_type == "document":
                document_count += 1
                unknown_visual = True

    approx = None
    if (image_count or document_count) and not unknown_visual:
        approx = visual_tokens
    elif image_count and unknown_visual and visual_tokens:
        # Partial estimate is still more useful than nothing for mixed payloads.
        approx = visual_tokens

    return VisualInputSummary(
        image_count=image_count,
        document_count=document_count,
        approx_visual_tokens=approx,
        input_text_chars=text_chars,
    )


def _block_text(block) -> str:
    if isinstance(block, dict) and block.get("type") == "text":
        return block.get("text") or ""
    return ""


def _dimensions_from_source(source) -> tuple[int, int] | None:
    if not isinstance(source, dict) or source.get("type") != "base64":
        return None
    encoded = source.get("data")
    if not isinstance(encoded, str):
        return None
    # Headers live in the first few hundred bytes; JPEG SOF may sit behind a
    # large EXIF/ICC segment, so decode a generous prefix only.
    try:
        head = base64.b64decode(encoded[: 256 * 1024], validate=False)
    except (ValueError, TypeError):
        return None
    return image_dimensions(head)
