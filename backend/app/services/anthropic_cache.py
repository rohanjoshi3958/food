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

# Only "ephemeral" exists today. Interactive receipt / meal flows omit ``ttl``
# so Anthropic's default 5-minute window applies (bursts of per-line / retry
# calls). Batch jobs (FOOD-57) pass ``ttl="1h"`` because most batches take
# longer than five minutes; see backend/BATCH_API.md.
CACHE_TTL_5M = "5m"
CACHE_TTL_1H = "1h"
VALID_CACHE_TTLS = frozenset({CACHE_TTL_5M, CACHE_TTL_1H})
EPHEMERAL_CACHE_CONTROL: dict[str, str] = {"type": "ephemeral"}
EPHEMERAL_1H_CACHE_CONTROL: dict[str, str] = {"type": "ephemeral", "ttl": CACHE_TTL_1H}

USAGE_FIELDS: tuple[str, ...] = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)


def cached_text_block(text: str, *, ttl: str | None = None) -> dict[str, Any]:
    """Return a text content block that ends a cacheable prefix.

    ``ttl`` is omitted on the interactive path (Anthropic default: 5 minutes).
    Batch jobs pass :data:`CACHE_TTL_1H`.
    """
    control = dict(EPHEMERAL_CACHE_CONTROL)
    if ttl is not None:
        if ttl not in VALID_CACHE_TTLS:
            raise ValueError(
                f"cache ttl must be one of {sorted(VALID_CACHE_TTLS)}, got {ttl!r}"
            )
        control["ttl"] = ttl
    return {
        "type": "text",
        "text": text,
        "cache_control": control,
    }


def cached_system_prompt(
    prefix: str, *, ttl: str | None = None
) -> list[dict[str, Any]]:
    """Build the ``system`` parameter for a byte-stable prompt prefix.

    ``prefix`` must be fully determined by deployed code (constants,
    calorie bands, JSON schemas). Anything derived from the request breaks
    the cache for every caller.
    """
    return [cached_text_block(prefix, ttl=ttl)]


def build_cached_message_params(
    *,
    model: str,
    max_tokens: int,
    system_prefix: str,
    messages: list[dict[str, Any]],
    cache_ttl: str | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Kwargs shared by the sync Messages API and a Batch ``params`` object."""
    return {
        "model": model,
        "max_tokens": max_tokens,
        "system": cached_system_prompt(system_prefix, ttl=cache_ttl),
        "messages": messages,
        **kwargs,
    }


def message_text_blocks(message: Any) -> list[str]:
    """Collect text blocks from a Messages API or Batch result message.

    Accepts SDK objects and plain dicts so batch result rows can be parsed
    without a second conversion step.
    """
    content = getattr(message, "content", None)
    if content is None and isinstance(message, dict):
        content = message.get("content")
    texts: list[str] = []
    for block in content or []:
        if isinstance(block, dict):
            if block.get("type") != "text":
                continue
            text = block.get("text")
        else:
            if getattr(block, "type", None) != "text":
                continue
            text = getattr(block, "text", None)
        if text:
            texts.append(text)
    return texts


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
    cache_ttl: str | None = None,
    **kwargs: Any,
) -> Any:
    """Call ``client.messages.create`` with ``system_prefix`` as the cached block.

    ``messages`` is sent verbatim after the breakpoint; callers own its
    ordering (images, per-item fields, multi-turn history all belong here).
    Interactive callers leave ``cache_ttl`` unset (5-minute default). Batch
    construction goes through :func:`build_cached_message_params` with
    ``cache_ttl="1h"`` instead of this function.
    """
    message = client.messages.create(
        **build_cached_message_params(
            model=model,
            max_tokens=max_tokens,
            system_prefix=system_prefix,
            messages=messages,
            cache_ttl=cache_ttl,
            **kwargs,
        )
    )
    log_cache_usage(message, call_site=call_site, model=model)
    return message
