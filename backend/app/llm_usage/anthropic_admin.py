"""Anthropic Usage & Cost Admin API client for billing reconciliation.

Our per-call events estimate cost from list prices; the Admin API reports
what Anthropic actually metered for the whole organization. The dashboard
shows both so ops can spot drift (pricing changes, calls outside this app,
untracked workspaces).

Requires an Admin API key (``sk-ant-admin...``) in ``ANTHROPIC_ADMIN_API_KEY``;
workspace-scoped keys do not work. Results are cached in-process for a few
minutes, matching Anthropic's polling guidance.
"""

from __future__ import annotations

import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.config import settings

API_BASE = "https://api.anthropic.com"
ANTHROPIC_VERSION = "2023-06-01"
USER_AGENT = "food-llm-metrics/1.0"
CACHE_TTL_SECONDS = 300
MAX_PAGES = 10

_cache: dict[tuple[str, int], tuple[float, dict[str, Any]]] = {}


class AnthropicAdminError(Exception):
    pass


def is_configured() -> bool:
    return bool((settings.anthropic_admin_api_key or "").strip())


def _headers() -> dict[str, str]:
    return {
        "x-api-key": settings.anthropic_admin_api_key,
        "anthropic-version": ANTHROPIC_VERSION,
        "user-agent": USER_AGENT,
    }


def _window(days: int) -> tuple[str, str]:
    days = max(1, min(int(days), 31))
    until = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    since = until - timedelta(days=days)
    return since.strftime("%Y-%m-%dT%H:%M:%SZ"), until.strftime("%Y-%m-%dT%H:%M:%SZ")


def _get_all_pages(client: httpx.Client, path: str, params: list[tuple[str, str]]) -> list[dict]:
    buckets: list[dict] = []
    page: str | None = None
    for _ in range(MAX_PAGES):
        query = list(params)
        if page:
            query.append(("page", page))
        response = client.get(f"{API_BASE}{path}", params=query, headers=_headers())
        if response.status_code >= 400:
            detail = response.text[:300]
            raise AnthropicAdminError(
                f"Anthropic Admin API returned {response.status_code} for {path}: {detail}"
            )
        payload = response.json()
        buckets.extend(payload.get("data") or [])
        if not payload.get("has_more"):
            break
        page = payload.get("next_page")
        if not page:
            break
    return buckets


def fetch_usage_report(client: httpx.Client, days: int) -> list[dict]:
    since, until = _window(days)
    params = [
        ("starting_at", since),
        ("ending_at", until),
        ("bucket_width", "1d"),
        ("limit", "31"),
        ("group_by[]", "model"),
        ("group_by[]", "service_tier"),
        ("group_by[]", "workspace_id"),
    ]
    return _get_all_pages(client, "/v1/organizations/usage_report/messages", params)


def fetch_cost_report(client: httpx.Client, days: int) -> list[dict]:
    since, until = _window(days)
    params = [
        ("starting_at", since),
        ("ending_at", until),
        ("bucket_width", "1d"),
        ("limit", "31"),
        ("group_by[]", "workspace_id"),
        ("group_by[]", "description"),
    ]
    return _get_all_pages(client, "/v1/organizations/cost_report", params)


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def summarize_usage_buckets(buckets: list[dict]) -> dict[str, Any]:
    totals = defaultdict(int)
    rows: dict[tuple[str, str, str], dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for bucket in buckets:
        for item in bucket.get("results") or []:
            cache_creation = item.get("cache_creation") or {}
            values = {
                "uncached_input_tokens": _int(item.get("uncached_input_tokens")),
                "cache_write_5m_tokens": _int(cache_creation.get("ephemeral_5m_input_tokens")),
                "cache_write_1h_tokens": _int(cache_creation.get("ephemeral_1h_input_tokens")),
                "cache_read_tokens": _int(item.get("cache_read_input_tokens")),
                "output_tokens": _int(item.get("output_tokens")),
            }
            key = (
                item.get("model") or "unknown",
                item.get("service_tier") or "unknown",
                item.get("workspace_id") or "default",
            )
            for name, amount in values.items():
                totals[name] += amount
                rows[key][name] += amount

    total_input = (
        totals["uncached_input_tokens"]
        + totals["cache_write_5m_tokens"]
        + totals["cache_write_1h_tokens"]
        + totals["cache_read_tokens"]
    )
    return {
        "tokens": {
            **totals,
            "total_input": total_input,
            "total": total_input + totals["output_tokens"],
        },
        "cache_read_pct": round(100.0 * totals["cache_read_tokens"] / total_input, 2)
        if total_input
        else None,
        "rows": [
            {"model": model, "service_tier": tier, "workspace_id": workspace, **values}
            for (model, tier, workspace), values in sorted(rows.items())
        ],
    }


def summarize_cost_buckets(buckets: list[dict]) -> dict[str, Any]:
    total_usd = 0.0
    by_model: dict[str, float] = defaultdict(float)
    by_workspace: dict[str, float] = defaultdict(float)
    by_token_type: dict[str, float] = defaultdict(float)
    for bucket in buckets:
        for item in bucket.get("results") or []:
            try:
                usd = float(item.get("amount") or 0) / 100.0
            except (TypeError, ValueError):
                continue
            total_usd += usd
            by_model[item.get("model") or item.get("description") or "other"] += usd
            by_workspace[item.get("workspace_id") or "default"] += usd
            by_token_type[item.get("token_type") or item.get("cost_type") or "other"] += usd
    return {
        "total_usd": round(total_usd, 4),
        "by_model": {k: round(v, 4) for k, v in sorted(by_model.items())},
        "by_workspace": {k: round(v, 4) for k, v in sorted(by_workspace.items())},
        "by_token_type": {k: round(v, 4) for k, v in sorted(by_token_type.items())},
    }


def reconciliation_report(days: int = 7, *, client: httpx.Client | None = None) -> dict[str, Any]:
    """Organization-level usage and cost for the window, or a disabled marker."""
    if not is_configured():
        return {
            "configured": False,
            "message": "Set ANTHROPIC_ADMIN_API_KEY (an Admin API key) to reconcile against Anthropic billing.",
        }

    cache_key = ("report", int(days))
    cached = _cache.get(cache_key)
    now = time.monotonic()
    if cached and now - cached[0] < CACHE_TTL_SECONDS:
        return cached[1]

    owns_client = client is None
    client = client or httpx.Client(timeout=20.0)
    try:
        usage = summarize_usage_buckets(fetch_usage_report(client, days))
        cost = summarize_cost_buckets(fetch_cost_report(client, days))
    except (httpx.HTTPError, AnthropicAdminError, ValueError) as exc:
        return {"configured": True, "error": str(exc)}
    finally:
        if owns_client:
            client.close()

    since, until = _window(days)
    report = {
        "configured": True,
        "window": {"since": since, "until": until, "days": int(days)},
        "usage": usage,
        "cost": cost,
        "fetched_at": datetime.now(UTC).isoformat(),
    }
    _cache[cache_key] = (now, report)
    return report


def clear_cache() -> None:
    _cache.clear()
