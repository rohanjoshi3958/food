"""List-price table and cost estimation for Claude usage.

Prices are USD per million tokens (MTok) from the public Anthropic pricing
page. They are *estimates*: the Anthropic Usage & Cost Admin API is the
source of truth for billing and can be surfaced next to these numbers on
the dashboard for reconciliation.

Override or extend the table without a deploy by setting ``LLM_PRICING_JSON``
to a JSON object keyed by model family, for example::

    {"claude-sonnet-5": {"input": 2, "output": 10, "cache_write_5m": 2.5,
                         "cache_write_1h": 4, "cache_read": 0.2}}
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass

from app.config import settings

PRICING_VERSION = "2026-09-anthropic-list"


@dataclass(frozen=True)
class ModelPricing:
    """USD per MTok for each billable token class."""

    input: float
    output: float
    cache_write_5m: float
    cache_write_1h: float
    cache_read: float


@dataclass(frozen=True)
class TokenUsage:
    uncached_input_tokens: int = 0
    cache_write_5m_tokens: int = 0
    cache_write_1h_tokens: int = 0
    cache_read_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_input_tokens(self) -> int:
        return (
            self.uncached_input_tokens
            + self.cache_write_5m_tokens
            + self.cache_write_1h_tokens
            + self.cache_read_tokens
        )

    @property
    def total_tokens(self) -> int:
        return self.total_input_tokens + self.output_tokens


@dataclass(frozen=True)
class CostBreakdown:
    input_usd: float
    cache_write_usd: float
    cache_read_usd: float
    output_usd: float
    pricing_known: bool

    @property
    def total_usd(self) -> float:
        return self.input_usd + self.cache_write_usd + self.cache_read_usd + self.output_usd

    def as_dict(self) -> dict:
        data = asdict(self)
        data["total_usd"] = self.total_usd
        return data


# Keyed by model *family* (the id with any dated / revision suffix removed).
DEFAULT_PRICING: dict[str, ModelPricing] = {
    "claude-opus-5": ModelPricing(5.0, 25.0, 6.25, 10.0, 0.50),
    "claude-opus-4-8": ModelPricing(5.0, 25.0, 6.25, 10.0, 0.50),
    "claude-opus-4-7": ModelPricing(5.0, 25.0, 6.25, 10.0, 0.50),
    "claude-opus-4-6": ModelPricing(5.0, 25.0, 6.25, 10.0, 0.50),
    "claude-opus-4-5": ModelPricing(5.0, 25.0, 6.25, 10.0, 0.50),
    "claude-opus-4-1": ModelPricing(15.0, 75.0, 18.75, 30.0, 1.50),
    "claude-opus-4": ModelPricing(15.0, 75.0, 18.75, 30.0, 1.50),
    "claude-sonnet-5": ModelPricing(2.0, 10.0, 2.50, 4.0, 0.20),
    "claude-sonnet-4-6": ModelPricing(3.0, 15.0, 3.75, 6.0, 0.30),
    "claude-sonnet-4-5": ModelPricing(3.0, 15.0, 3.75, 6.0, 0.30),
    "claude-sonnet-4": ModelPricing(3.0, 15.0, 3.75, 6.0, 0.30),
    "claude-haiku-4-5": ModelPricing(1.0, 5.0, 1.25, 2.0, 0.10),
    "claude-haiku-3-5": ModelPricing(0.80, 4.0, 1.0, 1.60, 0.08),
}

_DATE_SUFFIX = re.compile(r"-(\d{8}|latest)$")
_VERSION_IN_NAME = re.compile(r"claude-(?:opus|sonnet|haiku)-(\d+)(?:[-.](\d+))?")

_REQUIRED_PRICE_KEYS = ("input", "output", "cache_write_5m", "cache_write_1h", "cache_read")


def model_family(model: str) -> str:
    """Normalize ``claude-sonnet-5-20260101`` / ``claude-sonnet-5.0`` → ``claude-sonnet-5``."""
    name = model.strip().lower()
    name = _DATE_SUFFIX.sub("", name)
    name = name.replace(".", "-")
    # Drop trailing "-0" so "claude-sonnet-5-0" matches "claude-sonnet-5".
    name = re.sub(r"-0$", "", name)
    return name


def model_version(model: str) -> tuple[int, int] | None:
    match = _VERSION_IN_NAME.search(model.lower())
    if not match:
        return None
    major = int(match.group(1))
    minor = int(match.group(2) or 0)
    return (major, minor)


def _override_pricing() -> dict[str, ModelPricing]:
    raw = (settings.llm_pricing_json or "").strip()
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, dict):
        return {}

    overrides: dict[str, ModelPricing] = {}
    for model, prices in payload.items():
        if not isinstance(prices, dict):
            continue
        if any(key not in prices for key in _REQUIRED_PRICE_KEYS):
            continue
        try:
            overrides[model_family(str(model))] = ModelPricing(
                **{key: float(prices[key]) for key in _REQUIRED_PRICE_KEYS}
            )
        except (TypeError, ValueError):
            continue
    return overrides


def pricing_table() -> dict[str, ModelPricing]:
    return {**DEFAULT_PRICING, **_override_pricing()}


def pricing_for_model(model: str) -> ModelPricing | None:
    table = pricing_table()
    family = model_family(model)
    if family in table:
        return table[family]

    # Fall back to the longest known prefix (e.g. an unlisted point release).
    candidates = [known for known in table if family.startswith(known + "-")]
    if candidates:
        return table[max(candidates, key=len)]
    return None


def estimate_cost(model: str, usage: TokenUsage) -> CostBreakdown:
    pricing = pricing_for_model(model)
    if pricing is None:
        return CostBreakdown(0.0, 0.0, 0.0, 0.0, pricing_known=False)

    per_token = 1 / 1_000_000
    return CostBreakdown(
        input_usd=usage.uncached_input_tokens * pricing.input * per_token,
        cache_write_usd=(
            usage.cache_write_5m_tokens * pricing.cache_write_5m
            + usage.cache_write_1h_tokens * pricing.cache_write_1h
        )
        * per_token,
        cache_read_usd=usage.cache_read_tokens * pricing.cache_read * per_token,
        output_usd=usage.output_tokens * pricing.output * per_token,
        pricing_known=True,
    )
