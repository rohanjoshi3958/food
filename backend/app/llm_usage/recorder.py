"""Instrumented Claude Messages calls and usage-event sinks.

Call sites replace ``client.messages.create(**kwargs)`` with
``create_message(client, step="...", **kwargs)``. The wrapper measures
latency, reads token usage from the response, estimates USD cost, and hands
a :class:`UsageEvent` to every registered sink. Sinks never raise into the
product path: persistence or logging failures are logged and swallowed.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from app.config import settings
from app.llm_usage.context import (
    WORKFLOW_UNATTRIBUTED,
    WorkflowRun,
    current_run,
)
from app.llm_usage.pricing import PRICING_VERSION, TokenUsage, estimate_cost
from app.llm_usage.vision import summarize_visual_input

logger = logging.getLogger("food.llm_usage")
_internal_logger = logging.getLogger(__name__)

PROVIDER_ANTHROPIC = "anthropic"


@dataclass
class UsageEvent:
    workflow: str
    step: str
    model: str
    status: str
    latency_ms: int
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    run_id: str | None = None
    attempt: int = 1
    provider: str = PROVIDER_ANTHROPIC
    service_tier: str | None = None
    error_type: str | None = None
    stop_reason: str | None = None
    max_tokens: int | None = None

    uncached_input_tokens: int = 0
    cache_write_5m_tokens: int = 0
    cache_write_1h_tokens: int = 0
    cache_read_tokens: int = 0
    output_tokens: int = 0

    image_count: int = 0
    document_count: int = 0
    approx_visual_tokens: int | None = None
    input_text_chars: int = 0

    estimated_cost_usd: float = 0.0
    input_cost_usd: float = 0.0
    cache_write_cost_usd: float = 0.0
    cache_read_cost_usd: float = 0.0
    output_cost_usd: float = 0.0
    pricing_known: bool = True
    pricing_version: str | None = PRICING_VERSION

    user_id: str | None = None
    receipt_id: str | None = None
    meal_id: str | None = None
    anthropic_request_id: str | None = None

    @property
    def total_input_tokens(self) -> int:
        return (
            self.uncached_input_tokens
            + self.cache_write_5m_tokens
            + self.cache_write_1h_tokens
            + self.cache_read_tokens
        )

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["created_at"] = self.created_at.isoformat()
        data["total_input_tokens"] = self.total_input_tokens
        data["total_tokens"] = self.total_input_tokens + self.output_tokens
        return data


class UsageSink(Protocol):
    def record_event(self, event: UsageEvent) -> None: ...

    def run_started(self, run: WorkflowRun) -> None: ...

    def run_finished(
        self,
        run: WorkflowRun,
        *,
        status: str,
        error_type: str | None,
        duration_ms: int,
    ) -> None: ...


class LogSink:
    """Structured JSON lines on stdout; the zero-infrastructure metrics sink."""

    def __init__(self) -> None:
        if not logger.handlers:
            handler = logging.StreamHandler(sys.stdout)
            handler.setFormatter(logging.Formatter("%(message)s"))
            logger.addHandler(handler)
            logger.propagate = False
        logger.setLevel(logging.INFO)

    def record_event(self, event: UsageEvent) -> None:
        if not settings.llm_usage_log_enabled:
            return
        logger.info(json.dumps({"kind": "llm_usage_event", **event.as_dict()}, default=str))

    def run_started(self, run: WorkflowRun) -> None:
        return None

    def run_finished(
        self,
        run: WorkflowRun,
        *,
        status: str,
        error_type: str | None,
        duration_ms: int,
    ) -> None:
        if not settings.llm_usage_log_enabled:
            return
        logger.info(
            json.dumps(
                {
                    "kind": "llm_workflow_run",
                    "workflow": run.workflow,
                    "run_id": run.run_id,
                    "status": status,
                    "error_type": error_type,
                    "duration_ms": duration_ms,
                    "call_count": run.call_count,
                    "user_id": run.user_id,
                    "receipt_id": run.receipt_id,
                    "meal_id": run.meal_id,
                }
            )
        )


class DatabaseSink:
    """Durable store in the app's Postgres via the existing SQLAlchemy models.

    Uses a fresh short-lived session per write so it never interferes with the
    request's transaction and is safe from worker threads.
    """

    def _session(self):
        from app import database  # resolved at call time so tests can patch it

        return database.SessionLocal()

    def record_event(self, event: UsageEvent) -> None:
        from app.models import LlmUsageEvent

        payload = event.as_dict()
        payload.pop("total_input_tokens", None)
        payload.pop("total_tokens", None)
        payload["created_at"] = event.created_at
        session = self._session()
        try:
            session.add(LlmUsageEvent(**payload))
            session.commit()
        finally:
            session.close()

    def run_started(self, run: WorkflowRun) -> None:
        from app.models import LlmWorkflowRun

        if not run.recorded or run.run_id is None:
            return
        session = self._session()
        try:
            session.add(
                LlmWorkflowRun(
                    id=run.run_id,
                    workflow=run.workflow,
                    status="running",
                    user_id=run.user_id,
                    receipt_id=run.receipt_id,
                    meal_id=run.meal_id,
                )
            )
            session.commit()
        finally:
            session.close()

    def run_finished(
        self,
        run: WorkflowRun,
        *,
        status: str,
        error_type: str | None,
        duration_ms: int,
    ) -> None:
        from app.models import LlmWorkflowRun

        if not run.recorded or run.run_id is None:
            return
        session = self._session()
        try:
            row = session.get(LlmWorkflowRun, run.run_id)
            if row is None:
                row = LlmWorkflowRun(id=run.run_id, workflow=run.workflow)
                session.add(row)
            row.status = status
            row.error_type = error_type
            row.user_id = run.user_id
            row.receipt_id = run.receipt_id
            row.meal_id = run.meal_id
            row.finished_at = datetime.now(UTC)
            row.duration_ms = duration_ms
            session.commit()
        finally:
            session.close()


_sinks: list[UsageSink] | None = None


def default_sinks() -> list[UsageSink]:
    return [LogSink(), DatabaseSink()]


def get_sinks() -> list[UsageSink]:
    global _sinks
    if _sinks is None:
        _sinks = default_sinks()
    return _sinks


def set_sinks(sinks: list[UsageSink] | None) -> None:
    """Replace the active sinks (tests) or reset to defaults with ``None``."""
    global _sinks
    _sinks = list(sinks) if sinks is not None else None


def _dispatch(method: str, *args, **kwargs) -> None:
    for sink in get_sinks():
        try:
            getattr(sink, method)(*args, **kwargs)
        except Exception:  # noqa: BLE001 - instrumentation must never break product flows
            _internal_logger.warning(
                "LLM usage sink %s.%s failed", type(sink).__name__, method, exc_info=True
            )


def on_run_started(run: WorkflowRun) -> None:
    _dispatch("run_started", run)


def on_run_finished(run: WorkflowRun, *, status: str, error: BaseException | None) -> None:
    duration_ms = int((time.monotonic() - run.started_monotonic) * 1000)
    _dispatch(
        "run_finished",
        run,
        status=status,
        error_type=type(error).__name__ if error is not None else None,
        duration_ms=duration_ms,
    )


def record_event(event: UsageEvent) -> None:
    _dispatch("record_event", event)


# --- Response parsing -------------------------------------------------------


def _as_int(value: Any) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    return 0


def _as_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _field(obj: Any, name: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _request_uses_1h_cache(kwargs: dict[str, Any]) -> bool:
    def walk(node: Any) -> bool:
        if isinstance(node, dict):
            cache_control = node.get("cache_control")
            if isinstance(cache_control, dict) and cache_control.get("ttl") == "1h":
                return True
            return any(walk(value) for value in node.values())
        if isinstance(node, list):
            return any(walk(item) for item in node)
        return False

    return walk(kwargs.get("system")) or walk(kwargs.get("messages"))


def token_usage_from_response(message: Any, request_kwargs: dict[str, Any] | None = None) -> TokenUsage:
    """Read token counts from a Messages response, tolerating SDK differences.

    Older SDKs expose only ``cache_creation_input_tokens``; newer ones add a
    ``cache_creation`` object split by TTL. When only the total is available
    it is attributed to the 5-minute tier unless the request asked for 1h.
    """
    usage = _field(message, "usage")
    uncached = _as_int(_field(usage, "input_tokens"))
    cache_read = _as_int(_field(usage, "cache_read_input_tokens"))
    output = _as_int(_field(usage, "output_tokens"))

    cache_creation = _field(usage, "cache_creation")
    write_5m = _as_int(_field(cache_creation, "ephemeral_5m_input_tokens"))
    write_1h = _as_int(_field(cache_creation, "ephemeral_1h_input_tokens"))
    if not write_5m and not write_1h:
        total_write = _as_int(_field(usage, "cache_creation_input_tokens"))
        if _request_uses_1h_cache(request_kwargs or {}):
            write_1h = total_write
        else:
            write_5m = total_write

    return TokenUsage(
        uncached_input_tokens=uncached,
        cache_write_5m_tokens=write_5m,
        cache_write_1h_tokens=write_1h,
        cache_read_tokens=cache_read,
        output_tokens=output,
    )


def build_event(
    *,
    step: str,
    request_kwargs: dict[str, Any],
    message: Any,
    latency_ms: int,
    error: BaseException | None = None,
    run: WorkflowRun | None = None,
    attempt: int = 1,
) -> UsageEvent:
    model = _as_str(request_kwargs.get("model")) or _as_str(_field(message, "model")) or "unknown"
    usage = (
        token_usage_from_response(message, request_kwargs)
        if error is None
        else TokenUsage()
    )
    cost = estimate_cost(model, usage)
    visual = summarize_visual_input(
        request_kwargs.get("messages") or [],
        request_kwargs.get("system"),
        model,
    )
    usage_obj = _field(message, "usage") if error is None else None

    return UsageEvent(
        workflow=run.workflow if run else WORKFLOW_UNATTRIBUTED,
        step=step,
        model=model,
        status="ok" if error is None else "error",
        latency_ms=latency_ms,
        run_id=run.run_id if run else None,
        attempt=attempt,
        service_tier=_as_str(_field(usage_obj, "service_tier")),
        error_type=type(error).__name__ if error is not None else None,
        stop_reason=_as_str(_field(message, "stop_reason")) if error is None else None,
        max_tokens=request_kwargs.get("max_tokens")
        if isinstance(request_kwargs.get("max_tokens"), int)
        else None,
        uncached_input_tokens=usage.uncached_input_tokens,
        cache_write_5m_tokens=usage.cache_write_5m_tokens,
        cache_write_1h_tokens=usage.cache_write_1h_tokens,
        cache_read_tokens=usage.cache_read_tokens,
        output_tokens=usage.output_tokens,
        image_count=visual.image_count,
        document_count=visual.document_count,
        approx_visual_tokens=visual.approx_visual_tokens,
        input_text_chars=visual.input_text_chars,
        estimated_cost_usd=cost.total_usd,
        input_cost_usd=cost.input_usd,
        cache_write_cost_usd=cost.cache_write_usd,
        cache_read_cost_usd=cost.cache_read_usd,
        output_cost_usd=cost.output_usd,
        pricing_known=cost.pricing_known,
        user_id=run.user_id if run else None,
        receipt_id=run.receipt_id if run else None,
        meal_id=run.meal_id if run else None,
        anthropic_request_id=_as_str(_field(message, "_request_id")) if error is None else None,
    )


def create_message(client: Any, *, step: str, attempt: int = 1, **kwargs: Any) -> Any:
    """``client.messages.create(**kwargs)`` plus usage/cost recording.

    Behaves exactly like the underlying call from the caller's perspective:
    same return value, same exceptions. Recording happens after the call and
    cannot alter the outcome.

    ``attempt`` is 1 for a first try and 2+ when the caller is retrying the
    same step (e.g. the meal-generation loop). It drives the retry rate on
    the dashboard, so independent per-item calls should leave it at 1.
    """
    run = current_run()
    if run is not None:
        run.call_count += 1
    started = time.perf_counter()
    try:
        message = client.messages.create(**kwargs)
    except BaseException as exc:
        latency_ms = int((time.perf_counter() - started) * 1000)
        _safe_record(
            step=step,
            request_kwargs=kwargs,
            message=None,
            latency_ms=latency_ms,
            error=exc,
            run=run,
            attempt=attempt,
        )
        raise

    latency_ms = int((time.perf_counter() - started) * 1000)
    _safe_record(
        step=step,
        request_kwargs=kwargs,
        message=message,
        latency_ms=latency_ms,
        error=None,
        run=run,
        attempt=attempt,
    )
    return message


def _safe_record(**kwargs: Any) -> None:
    try:
        record_event(build_event(**kwargs))
    except Exception:  # noqa: BLE001 - never let instrumentation break the product path
        _internal_logger.warning("Failed to record LLM usage event", exc_info=True)
