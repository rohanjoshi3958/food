"""Shared helpers for Anthropic prompt caching.

Every Claude call in the backend goes through :func:`create_cached_message`
so that the byte-stable prompt prefix (rules, ontology, JSON response schema)
is sent as a cached ``system`` block and the per-request tail (item names,
pantry snapshots, ingredient lists, images, conversation turns) stays in
``messages`` after the cache breakpoint.

See ``backend/PROMPT_CACHING.md`` for what must never sit before the
breakpoint. This module logs the raw usage counters Anthropic returns so
cache hits can be verified (FOOD-56); the per-workflow token/cost events
behind the ops dashboard (FOOD-54) are recorded by :mod:`app.llm_usage`,
which wraps this one interactive call path so there is no parallel metrics
pipeline. Batch construction (FOOD-57) uses :func:`build_cached_message_params`
with ``cache_ttl="1h"`` instead of :func:`create_cached_message`.
"""

from __future__ import annotations

import logging
from typing import Any

from app.llm_usage import (
    WORKFLOW_INGREDIENT_NORMALIZE,
    WORKFLOW_MEAL_GEN,
    WORKFLOW_RECEIPT_PARSE,
    create_message,
)

logger = logging.getLogger(__name__)

# call_site (fine-grained, owned by the LLM platform) → (workflow_id, step).
#
# ``workflow_id`` is the FOOD-54 aggregate label and is authoritative for a
# known call_site regardless of which router scope is active, so "cost of a
# receipt parse" always means the same set of call sites. The active
# ``app.llm_usage.workflow_scope`` still supplies run attribution (run_id,
# user / receipt / meal ids) so per-run costs include every call in the run.
CALL_SITE_WORKFLOWS: dict[str, tuple[str, str]] = {
    "receipt.analyze_image": (WORKFLOW_RECEIPT_PARSE, "receipt_scan"),
    "receipt.nutrition_estimate": (WORKFLOW_RECEIPT_PARSE, "nutrition_estimate"),
    "receipt.unit_check": (WORKFLOW_RECEIPT_PARSE, "unit_check"),
    # FOOD-55 OCR-first: Haiku cleanup of OCR text (vision fallback reuses receipt.analyze_image).
    "receipt.ocr_text_cleanup": (WORKFLOW_RECEIPT_PARSE, "ocr_text_cleanup"),
    "receipt.pantry_match": (WORKFLOW_INGREDIENT_NORMALIZE, "pantry_match"),
    "meal.generate": (WORKFLOW_MEAL_GEN, "meal_generate"),
    "meal.image_prompt": (WORKFLOW_MEAL_GEN, "image_prompt"),
}


def workflow_for_call_site(call_site: str) -> tuple[str | None, str]:
    """Return ``(workflow_id, step)``; unknown call sites keep their name as step."""
    mapped = CALL_SITE_WORKFLOWS.get(call_site)
    if mapped is None:
        return None, call_site
    return mapped

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
    attempt: int = 1,
    route: str | None = None,
    **kwargs: Any,
) -> Any:
    """Call ``client.messages.create`` with ``system_prefix`` as the cached block.

    ``messages`` is sent verbatim after the breakpoint; callers own its
    ordering (images, per-item fields, multi-turn history all belong here).
    Interactive callers leave ``cache_ttl`` unset (5-minute default). Batch
    construction goes through :func:`build_cached_message_params` with
    ``cache_ttl="1h"`` instead of this function.

    ``attempt`` (2+ when re-trying the same step, e.g. the meal calorie loop)
    and ``route`` (``haiku`` / ``sonnet`` / ``opus``; inferred from ``model``
    when omitted) are recorded on the FOOD-54 usage event and not forwarded
    to the API.
    """
    workflow, step = workflow_for_call_site(call_site)
    message = create_message(
        client,
        step=step,
        call_site=call_site,
        workflow=workflow,
        attempt=attempt,
        route=route,
        **build_cached_message_params(
            model=model,
            max_tokens=max_tokens,
            system_prefix=system_prefix,
            messages=messages,
            cache_ttl=cache_ttl,
            **kwargs,
        ),
    )
    log_cache_usage(message, call_site=call_site, model=model)
    return message
