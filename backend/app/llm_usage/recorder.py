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
import queue
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from typing import Any, Protocol

from app.config import settings
from app.llm_usage.context import (
    WORKFLOW_UNATTRIBUTED,
    WorkflowRun,
    current_run,
)
from app.llm_usage.pricing import PRICING_VERSION, TokenUsage, estimate_cost, model_family
from app.llm_usage.vision import summarize_visual_input

logger = logging.getLogger("food.llm_usage")
_internal_logger = logging.getLogger(__name__)

PROVIDER_ANTHROPIC = "anthropic"
PROVIDER_LOCAL = "local"

# Pipeline routes. Claude calls infer theirs from the model family; the
# OCR-first receipt pipeline (FOOD-55) passes ``route`` explicitly.
ROUTE_CACHE = "cache"
ROUTE_OCR = "ocr"
ROUTE_HAIKU = "haiku"
ROUTE_SONNET = "sonnet"
ROUTE_OPUS = "opus"
KNOWN_ROUTES = (ROUTE_CACHE, ROUTE_OCR, ROUTE_HAIKU, ROUTE_SONNET, ROUTE_OPUS)


def route_for_model(model: str | None) -> str | None:
    if not model:
        return None
    family = model_family(model)
    for route in (ROUTE_OPUS, ROUTE_SONNET, ROUTE_HAIKU):
        if f"-{route}-" in f"-{family}-":
            return route
    return None


@dataclass
class UsageEvent:
    workflow: str
    step: str
    model: str
    status: str
    latency_ms: int
    call_site: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    run_id: str | None = None
    attempt: int = 1
    provider: str = PROVIDER_ANTHROPIC
    route: str | None = None
    confidence: float | None = None
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


_DB_QUEUE_MAXSIZE = 256


def _snapshot_run(run: WorkflowRun) -> WorkflowRun:
    return WorkflowRun(
        workflow=run.workflow,
        recorded=run.recorded,
        run_id=run.run_id,
        user_id=run.user_id,
        receipt_id=run.receipt_id,
        meal_id=run.meal_id,
        started_monotonic=run.started_monotonic,
        call_count=run.call_count,
    )


class AsyncDatabaseSink:
    """Enqueue durable writes so the product path never waits on Postgres.

    Logging stays on :class:`LogSink` and is dispatched independently. When
    the bounded queue is full, the write is dropped and logged.
    """

    def __init__(
        self,
        inner: DatabaseSink | None = None,
        *,
        maxsize: int = _DB_QUEUE_MAXSIZE,
    ) -> None:
        self._inner = inner or DatabaseSink()
        self._queue: queue.Queue[tuple[str, tuple[Any, ...], dict[str, Any]]] = queue.Queue(
            maxsize=maxsize
        )
        self._started = False
        self._start_lock = threading.Lock()
        self._thread = threading.Thread(
            target=self._worker,
            name="llm-usage-db-sink",
            daemon=True,
        )

    def _ensure_worker(self) -> None:
        with self._start_lock:
            if not self._started:
                self._thread.start()
                self._started = True

    def _enqueue(self, method: str, *args: Any, **kwargs: Any) -> None:
        self._ensure_worker()
        try:
            self._queue.put_nowait((method, args, kwargs))
        except queue.Full:
            _internal_logger.warning(
                "LLM usage DB queue full; dropping %s",
                method,
            )

    def record_event(self, event: UsageEvent) -> None:
        self._enqueue("record_event", replace(event))

    def run_started(self, run: WorkflowRun) -> None:
        self._enqueue("run_started", _snapshot_run(run))

    def run_finished(
        self,
        run: WorkflowRun,
        *,
        status: str,
        error_type: str | None,
        duration_ms: int,
    ) -> None:
        self._enqueue(
            "run_finished",
            _snapshot_run(run),
            status=status,
            error_type=error_type,
            duration_ms=duration_ms,
        )

    def _worker(self) -> None:
        while True:
            method, args, kwargs = self._queue.get()
            try:
                getattr(self._inner, method)(*args, **kwargs)
            except Exception:  # noqa: BLE001 - instrumentation must never break product flows
                _internal_logger.warning(
                    "LLM usage sink DatabaseSink.%s failed",
                    method,
                    exc_info=True,
                )
            finally:
                self._queue.task_done()

    def flush(self, timeout: float | None = 5.0) -> None:
        """Wait until queued writes finish (tests)."""
        if not self._started:
            return
        if timeout is None:
            self._queue.join()
            return
        done = threading.Event()

        def _join() -> None:
            self._queue.join()
            done.set()

        threading.Thread(target=_join, name="llm-usage-db-flush", daemon=True).start()
        if not done.wait(timeout):
            raise TimeoutError("LLM usage DB queue did not drain")


_sinks: list[UsageSink] | None = None


def default_sinks() -> list[UsageSink]:
    return [LogSink(), AsyncDatabaseSink()]


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
    route: str | None = None,
    call_site: str | None = None,
    workflow: str | None = None,
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
        workflow=workflow or (run.workflow if run else WORKFLOW_UNATTRIBUTED),
        step=step,
        model=model,
        status="ok" if error is None else "error",
        latency_ms=latency_ms,
        call_site=call_site,
        run_id=run.run_id if run else None,
        attempt=attempt,
        route=route or route_for_model(model),
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


def create_message(
    client: Any,
    *,
    step: str,
    attempt: int = 1,
    route: str | None = None,
    call_site: str | None = None,
    workflow: str | None = None,
    **kwargs: Any,
) -> Any:
    """``client.messages.create(**kwargs)`` plus usage/cost recording.

    Behaves exactly like the underlying call from the caller's perspective:
    same return value, same exceptions. Recording happens after the call and
    cannot alter the outcome.

    ``attempt`` is 1 for a first try and 2+ when the caller is retrying the
    same step (e.g. the meal-generation loop). It drives the retry rate on
    the dashboard, so independent per-item calls should leave it at 1.

    ``route`` defaults to the model family (``opus`` / ``sonnet`` / ``haiku``);
    pass it explicitly when a call is part of a tiered pipeline.

    ``call_site`` is the LLM platform's fine-grained id (stored verbatim) and
    ``workflow`` the aggregate FOOD-54 label; when ``workflow`` is given it is
    authoritative, otherwise the active ``workflow_scope`` (or
    ``unattributed``) is used. Production code reaches this through
    ``app.services.anthropic_cache.create_cached_message`` so caching and
    metrics share one path.
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
            route=route,
            call_site=call_site,
            workflow=workflow,
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
        route=route,
        call_site=call_site,
        workflow=workflow,
    )
    return message


def record_pipeline_step(
    *,
    step: str,
    route: str | None,
    latency_ms: int,
    status: str = "ok",
    error: BaseException | None = None,
    confidence: float | None = None,
    model: str | None = None,
    provider: str = PROVIDER_LOCAL,
    usage: TokenUsage | None = None,
    attempt: int = 1,
    call_site: str | None = None,
    workflow: str | None = None,
) -> UsageEvent | None:
    """Record a non-Claude pipeline step (OCR pass, cache hit, rules) as an event.

    Stable entry point for the OCR-first receipt pipeline (FOOD-55) so it can
    report ``route`` / ``confidence`` / latency into the same table and
    dashboard instead of keeping parallel counters. Attribution (workflow,
    run, user/receipt ids) comes from the enclosing :func:`workflow_scope`.
    Tokens and cost are zero unless ``model`` and ``usage`` are supplied.
    Never raises.
    """
    run = current_run()
    try:
        token_usage = usage or TokenUsage()
        cost = estimate_cost(model, token_usage) if model else None
        event = UsageEvent(
            workflow=workflow or (run.workflow if run else WORKFLOW_UNATTRIBUTED),
            step=step,
            call_site=call_site,
            model=model or (route or "none"),
            status="error" if error is not None else status,
            latency_ms=max(int(latency_ms), 0),
            run_id=run.run_id if run else None,
            attempt=attempt,
            provider=provider,
            route=route,
            confidence=confidence,
            error_type=type(error).__name__ if error is not None else None,
            uncached_input_tokens=token_usage.uncached_input_tokens,
            cache_write_5m_tokens=token_usage.cache_write_5m_tokens,
            cache_write_1h_tokens=token_usage.cache_write_1h_tokens,
            cache_read_tokens=token_usage.cache_read_tokens,
            output_tokens=token_usage.output_tokens,
            estimated_cost_usd=cost.total_usd if cost else 0.0,
            input_cost_usd=cost.input_usd if cost else 0.0,
            cache_write_cost_usd=cost.cache_write_usd if cost else 0.0,
            cache_read_cost_usd=cost.cache_read_usd if cost else 0.0,
            output_cost_usd=cost.output_usd if cost else 0.0,
            pricing_known=cost.pricing_known if cost else True,
            pricing_version=PRICING_VERSION if cost else None,
            user_id=run.user_id if run else None,
            receipt_id=run.receipt_id if run else None,
            meal_id=run.meal_id if run else None,
        )
        if run is not None:
            run.call_count += 1
        record_event(event)
        return event
    except Exception:  # noqa: BLE001 - never let instrumentation break the product path
        _internal_logger.warning("Failed to record pipeline step %s", step, exc_info=True)
        return None


@dataclass
class PipelineStep:
    """Mutable handle yielded by :func:`pipeline_step` so callers can report results."""

    step: str
    route: str | None = None
    confidence: float | None = None
    model: str | None = None
    provider: str = PROVIDER_LOCAL
    usage: TokenUsage | None = None
    attempt: int = 1
    # Set this when the step caught and handled an execution failure (e.g. the
    # OCR binary is missing) so the event is recorded as status="error" even
    # though nothing propagated. A normal negative outcome (a gate rejecting
    # low-confidence output) is *not* an error.
    error: BaseException | None = None


@contextmanager
def pipeline_step(step: str, *, route: str | None = None, attempt: int = 1) -> Iterator[PipelineStep]:
    """Time a non-Claude step and record it on exit (including on error).

    Example (FOOD-55)::

        with pipeline_step("receipt_ocr", route=ROUTE_OCR) as ocr:
            text, score = run_ocr(image)
            ocr.confidence = score

    Exceptions escaping the block are recorded and re-raised; failures the
    block handled itself can be reported via ``handle.error``.
    """
    handle = PipelineStep(step=step, route=route, attempt=attempt)
    started = time.perf_counter()
    try:
        yield handle
    except BaseException as exc:
        record_pipeline_step(
            step=handle.step,
            route=handle.route,
            latency_ms=int((time.perf_counter() - started) * 1000),
            error=exc,
            confidence=handle.confidence,
            model=handle.model,
            provider=handle.provider,
            usage=handle.usage,
            attempt=handle.attempt,
        )
        raise
    record_pipeline_step(
        step=handle.step,
        route=handle.route,
        latency_ms=int((time.perf_counter() - started) * 1000),
        error=handle.error,
        confidence=handle.confidence,
        model=handle.model,
        provider=handle.provider,
        usage=handle.usage,
        attempt=handle.attempt,
    )


def _safe_record(**kwargs: Any) -> None:
    try:
        record_event(build_event(**kwargs))
    except Exception:  # noqa: BLE001 - never let instrumentation break the product path
        _internal_logger.warning("Failed to record LLM usage event", exc_info=True)
