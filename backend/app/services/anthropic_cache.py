"""Shared helpers for Anthropic prompt caching.

Every Claude call in the backend goes through :func:`create_cached_message`
so that the byte-stable prompt prefix (rules, ontology, JSON response schema)
is sent as a cached ``system`` block and the per-request tail (item names,
pantry snapshots, ingredient lists, images, conversation turns) stays in
``messages`` after the cache breakpoint.

See ``backend/PROMPT_CACHING.md`` for what must never sit before the
breakpoint. This module deliberately does not build a metrics/cost pipeline;
it only logs the usage counters Anthropic returns so cache hits can be
verified (FOOD-54 owns anything beyond that and can wrap this one call path).
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Only "ephemeral" exists today; it defaults to a 5-minute TTL, which suits
# the interactive receipt / meal flows where the same user issues bursts of
# calls (one per receipt line, one per meal retry).
EPHEMERAL_CACHE_CONTROL: dict[str, str] = {"type": "ephemeral"}

USAGE_FIELDS: tuple[str, ...] = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)


def cached_text_block(text: str) -> dict[str, Any]:
    """Return a text content block that ends a cacheable prefix."""
    return {
        "type": "text",
        "text": text,
        "cache_control": dict(EPHEMERAL_CACHE_CONTROL),
    }


def cached_system_prompt(prefix: str) -> list[dict[str, Any]]:
    """Build the ``system`` parameter for a byte-stable prompt prefix.

    ``prefix`` must be fully determined by deployed code (constants,
    calorie bands, JSON schemas). Anything derived from the request breaks
    the cache for every caller.
    """
    return [cached_text_block(prefix)]


def _usage_int(usage: Any, field: str) -> int | None:
    value = getattr(usage, field, None)
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def extract_cache_usage(message: Any) -> dict[str, int | None]:
    """Pull the token counters off a Messages API response.

    Missing fields (older SDKs, mocked responses) come back as ``None``.
    """
    usage = getattr(message, "usage", None)
    return {field: _usage_int(usage, field) for field in USAGE_FIELDS}


def log_cache_usage(
    message: Any, *, call_site: str, model: str | None = None
) -> dict[str, int | None]:
    """Log prompt-cache counters for one call and return them.

    Both cache counters at 0 means the prefix was not cached, usually because
    it is below the model's minimum cacheable length or because something
    volatile sits before the breakpoint. ``model`` is included so the model
    mix per call site can be read off the same line (FOOD-58 routing).
    """
    usage = extract_cache_usage(message)
    logger.info(
        "anthropic call_site=%s model=%s input_tokens=%s output_tokens=%s "
        "cache_creation_input_tokens=%s cache_read_input_tokens=%s",
        call_site,
        model or "",
        usage["input_tokens"],
        usage["output_tokens"],
        usage["cache_creation_input_tokens"],
        usage["cache_read_input_tokens"],
    )
    return usage


def create_cached_message(
    client: Any,
    *,
    call_site: str,
    model: str,
    max_tokens: int,
    system_prefix: str,
    messages: list[dict[str, Any]],
    **kwargs: Any,
) -> Any:
    """Call ``client.messages.create`` with ``system_prefix`` as the cached block.

    ``messages`` is sent verbatim after the breakpoint; callers own its
    ordering (images, per-item fields, multi-turn history all belong here).
    """
    message = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=cached_system_prompt(system_prefix),
        messages=messages,
        **kwargs,
    )
    log_cache_usage(message, call_site=call_site, model=model)
    return message
