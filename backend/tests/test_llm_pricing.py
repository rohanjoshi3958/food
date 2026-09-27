"""Cost estimation and vision-token math for Claude usage instrumentation."""

import base64
import json
import struct
from unittest.mock import patch

import pytest

from app.llm_usage.pricing import (
    DEFAULT_PRICING,
    TokenUsage,
    estimate_cost,
    model_family,
    pricing_for_model,
)
from app.llm_usage.vision import (
    image_dimensions,
    summarize_visual_input,
    visual_tokens_for_dimensions,
)


class TestModelFamily:
    @pytest.mark.parametrize(
        ("model", "family"),
        [
            ("claude-opus-5", "claude-opus-5"),
            ("claude-sonnet-5-20260415", "claude-sonnet-5"),
            ("claude-sonnet-4.5", "claude-sonnet-4-5"),
            ("claude-haiku-4-5-latest", "claude-haiku-4-5"),
            ("Claude-Opus-5-0", "claude-opus-5"),
        ],
    )
    def test_normalizes_suffixes(self, model, family):
        assert model_family(model) == family

    def test_prefix_fallback_for_unlisted_point_release(self):
        assert pricing_for_model("claude-sonnet-5-1-20270101") == DEFAULT_PRICING["claude-sonnet-5"]

    def test_unknown_model_has_no_pricing(self):
        assert pricing_for_model("gpt-image-1") is None


class TestEstimateCost:
    def test_opus_input_and_output(self):
        cost = estimate_cost("claude-opus-5", TokenUsage(uncached_input_tokens=1_000_000, output_tokens=100_000))
        assert cost.pricing_known is True
        assert cost.input_usd == pytest.approx(5.0)
        assert cost.output_usd == pytest.approx(2.5)
        assert cost.total_usd == pytest.approx(7.5)

    def test_sonnet_cache_tiers(self):
        cost = estimate_cost(
            "claude-sonnet-5",
            TokenUsage(
                uncached_input_tokens=100_000,
                cache_write_5m_tokens=100_000,
                cache_write_1h_tokens=100_000,
                cache_read_tokens=1_000_000,
                output_tokens=10_000,
            ),
        )
        # $2 in, $2.50 5m write, $4 1h write, $0.20 read, $10 out per MTok.
        assert cost.input_usd == pytest.approx(0.20)
        assert cost.cache_write_usd == pytest.approx(0.25 + 0.40)
        assert cost.cache_read_usd == pytest.approx(0.20)
        assert cost.output_usd == pytest.approx(0.10)
        assert cost.total_usd == pytest.approx(1.15)

    def test_small_call_matches_hand_calculation(self):
        # A typical unit-check call on Opus: 350 in, 40 out.
        cost = estimate_cost("claude-opus-5", TokenUsage(uncached_input_tokens=350, output_tokens=40))
        assert cost.total_usd == pytest.approx(350 * 5 / 1e6 + 40 * 25 / 1e6)

    def test_unknown_model_is_zero_and_flagged(self):
        cost = estimate_cost("mystery-model", TokenUsage(uncached_input_tokens=1000, output_tokens=1000))
        assert cost.pricing_known is False
        assert cost.total_usd == 0.0

    def test_env_override_replaces_and_adds_models(self):
        override = json.dumps(
            {
                "claude-sonnet-5": {
                    "input": 3,
                    "output": 15,
                    "cache_write_5m": 3.75,
                    "cache_write_1h": 6,
                    "cache_read": 0.3,
                },
                "claude-new-1": {
                    "input": 1,
                    "output": 2,
                    "cache_write_5m": 1,
                    "cache_write_1h": 1,
                    "cache_read": 1,
                },
            }
        )
        with patch("app.llm_usage.pricing.settings.llm_pricing_json", override):
            sonnet = estimate_cost("claude-sonnet-5", TokenUsage(uncached_input_tokens=1_000_000))
            new_model = estimate_cost("claude-new-1", TokenUsage(output_tokens=1_000_000))
        assert sonnet.input_usd == pytest.approx(3.0)
        assert new_model.pricing_known is True
        assert new_model.output_usd == pytest.approx(2.0)

    def test_malformed_override_is_ignored(self):
        with patch("app.llm_usage.pricing.settings.llm_pricing_json", "{not json"):
            assert pricing_for_model("claude-opus-5") == DEFAULT_PRICING["claude-opus-5"]


def _png(width: int, height: int) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", width, height) + b"\x08\x02" + b"\x00" * 20


def _gif(width: int, height: int) -> bytes:
    return b"GIF89a" + struct.pack("<HH", width, height) + b"\x00" * 20


def _jpeg(width: int, height: int) -> bytes:
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00" + b"\x00" * 9
    sof0 = b"\xff\xc0" + struct.pack(">H", 17) + b"\x08" + struct.pack(">HH", height, width) + b"\x03" + b"\x00" * 9
    return b"\xff\xd8" + app0 + sof0 + b"\x00" * 16


def _webp_vp8x(width: int, height: int) -> bytes:
    header = b"RIFF" + struct.pack("<I", 30) + b"WEBP" + b"VP8X" + struct.pack("<I", 10) + b"\x00" * 4
    return header + (width - 1).to_bytes(3, "little") + (height - 1).to_bytes(3, "little") + b"\x00" * 8


class TestImageDimensions:
    @pytest.mark.parametrize(
        ("data", "expected"),
        [
            (_png(640, 480), (640, 480)),
            (_gif(320, 200), (320, 200)),
            (_jpeg(1024, 768), (1024, 768)),
            (_webp_vp8x(1200, 900), (1200, 900)),
            (b"not an image at all, definitely not", None),
        ],
    )
    def test_reads_headers(self, data, expected):
        assert image_dimensions(data) == expected


class TestVisualTokens:
    @pytest.mark.parametrize(
        ("width", "height", "model", "expected"),
        [
            # Anthropic's published examples (standard vs high-resolution tiers).
            (200, 200, "claude-sonnet-4-5", 64),
            (1000, 1000, "claude-opus-5", 1296),
            (1920, 1080, "claude-sonnet-4-5", 1560),
            (1920, 1080, "claude-opus-5", 2691),
            (3840, 2160, "claude-sonnet-4-5", 1560),
            (3840, 2160, "claude-opus-5", 4784),
        ],
    )
    def test_matches_published_table(self, width, height, model, expected):
        assert visual_tokens_for_dimensions(width, height, model) == expected

    def test_summarize_counts_images_documents_and_text(self):
        image_block = {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": base64.b64encode(_png(1000, 1000)).decode(),
            },
        }
        messages = [
            {"role": "user", "content": [image_block, {"type": "text", "text": "x" * 400}]},
            {"role": "assistant", "content": "y" * 100},
        ]
        summary = summarize_visual_input(messages, "system prompt", "claude-opus-5")
        assert summary.image_count == 1
        assert summary.document_count == 0
        assert summary.approx_visual_tokens == 1296
        assert summary.input_text_chars == 400 + 100 + len("system prompt")

    def test_pdf_document_has_unknown_visual_tokens(self):
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": "AAAA"}},
                    {"type": "text", "text": "read this"},
                ],
            }
        ]
        summary = summarize_visual_input(messages, None, "claude-opus-5")
        assert summary.document_count == 1
        assert summary.approx_visual_tokens is None

    def test_text_only_has_no_visual_input(self):
        summary = summarize_visual_input([{"role": "user", "content": "hello"}], None, "claude-sonnet-5")
        assert summary.image_count == 0
        assert summary.approx_visual_tokens is None
        assert summary.has_visual_input is False
