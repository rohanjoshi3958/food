"""Anthropic Message Batches helper for offline Claude work (FOOD-57).

Interactive user-facing paths stay on the synchronous Messages API
(``create_cached_message``). This module is for historical receipt reprocess,
nightly meal regeneration, and any other job that can wait minutes to hours
for a 50% token discount.

Each batched request reuses the FOOD-56 cache breakpoint via
``build_cached_message_params``, with a **1-hour** cache TTL (batches almost
always run longer than the interactive 5-minute window).

See ``backend/BATCH_API.md`` for the interactive vs batch split and the
failure / retry policy.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from app.services.anthropic_cache import (
    CACHE_TTL_1H,
    build_cached_message_params,
    log_cache_usage,
)

logger = logging.getLogger(__name__)

CUSTOM_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

# API hard cap. Jobs with large vision payloads should pass a smaller chunk.
MAX_REQUESTS_PER_BATCH = 100_000
DEFAULT_POLL_INTERVAL_SECONDS = 30.0
DEFAULT_POLL_TIMEOUT_SECONDS = 24 * 60 * 60
DEFAULT_MAX_RETRIES = 1

# Result ``type`` values that are safe to resubmit as a new batch.
RETRYABLE_RESULT_TYPES = frozenset({"expired", "canceled"})
# ``errored`` is retryable unless the error is a bad request / auth problem.
NON_RETRYABLE_ERROR_TYPES = frozenset(
    {
        "invalid_request_error",
        "authentication_error",
        "billing_error",
        "permission_error",
        "not_found_error",
    }
)


class BatchError(Exception):
    """Base error for Message Batch submit / poll / result handling."""


class BatchSubmitError(BatchError):
    """The batch could not be created (empty, duplicate ids, API reject)."""


class BatchTimeoutError(BatchError):
    """Polling hit ``poll_timeout`` before ``processing_status == ended``."""

    def __init__(self, batch_id: str, last_status: str | None):
        self.batch_id = batch_id
        self.last_status = last_status
        super().__init__(
            f"Timed out waiting for batch {batch_id} "
            f"(last status={last_status!r}). Resume with this id."
        )


class BatchRunInterrupted(BatchError):
    """A later chunk failed after earlier chunks already produced results.

    ``outcome`` holds successes, failures, and ``submitted_batch_ids`` collected
    so far so callers can apply or report that work instead of resubmitting it.
    """

    def __init__(self, outcome: BatchRunResult, cause: BaseException):
        self.outcome = outcome
        super().__init__(
            f"Message Batch interrupted after {len(outcome.submitted_batch_ids)} "
            f"submit(s); {len(outcome.succeeded)} succeeded so far. {cause}"
        )


def _attr(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


@dataclass(frozen=True)
class BatchMessageRequest:
    """One Messages-API-shaped request inside a batch."""

    custom_id: str
    call_site: str
    model: str
    max_tokens: int
    system_prefix: str
    messages: list[dict[str, Any]]
    extra_params: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not CUSTOM_ID_RE.fullmatch(self.custom_id):
            raise BatchSubmitError(
                f"custom_id {self.custom_id!r} must match {CUSTOM_ID_RE.pattern}"
            )
        if self.max_tokens < 1:
            raise BatchSubmitError(
                f"{self.custom_id}: batch requests require max_tokens >= 1"
            )

    def to_api_request(self, *, cache_ttl: str = CACHE_TTL_1H) -> dict[str, Any]:
        """Wire payload: ``{custom_id, params}`` with a 1h cached system block."""
        self.validate()
        return {
            "custom_id": self.custom_id,
            "params": build_cached_message_params(
                model=self.model,
                max_tokens=self.max_tokens,
                system_prefix=self.system_prefix,
                messages=self.messages,
                cache_ttl=cache_ttl,
                **self.extra_params,
            ),
        }


@dataclass
class BatchItemResult:
    """One row from the batch results JSONL, keyed by ``custom_id``."""

    custom_id: str
    type: str
    message: Any | None = None
    error_type: str | None = None
    error_message: str | None = None
    raw: Any = None

    @classmethod
    def from_sdk(cls, row: Any) -> BatchItemResult:
        custom_id = _attr(row, "custom_id")
        if not custom_id:
            raise BatchError("batch result row is missing custom_id")
        result = _attr(row, "result")
        rtype = _attr(result, "type") or _attr(row, "type") or "errored"
        message = _attr(result, "message")
        error = _attr(result, "error")
        error_type = _attr(error, "type")
        error_message = _attr(error, "message")
        nested = _attr(error, "error")
        # Anthropic's envelope is often {type: "error", error: {type, message}}.
        # The inner type is the one that decides retry vs give up.
        if nested is not None and error_type in {None, "error"}:
            error_type = _attr(nested, "type")
            error_message = _attr(nested, "message")
        return cls(
            custom_id=str(custom_id),
            type=str(rtype),
            message=message,
            error_type=str(error_type) if error_type else None,
            error_message=str(error_message) if error_message else None,
            raw=row,
        )

    @property
    def succeeded(self) -> bool:
        return self.type == "succeeded"

    @property
    def retryable(self) -> bool:
        if self.type in RETRYABLE_RESULT_TYPES:
            return True
        if self.type == "errored":
            return self.error_type not in NON_RETRYABLE_ERROR_TYPES
        return False


@dataclass
class BatchRunResult:
    """Outcome of :func:`run_message_batch` after submit / poll / optional retry."""

    succeeded: dict[str, BatchItemResult] = field(default_factory=dict)
    failed: dict[str, BatchItemResult] = field(default_factory=dict)
    submitted_batch_ids: list[str] = field(default_factory=list)
    retry_rounds: int = 0

    def message_for(self, custom_id: str) -> Any:
        item = self.succeeded.get(custom_id)
        if item is None or item.message is None:
            raise KeyError(custom_id)
        return item.message


def _chunks(
    items: list[BatchMessageRequest], size: int
) -> Iterable[list[BatchMessageRequest]]:
    if size < 1:
        raise BatchSubmitError("chunk_size must be >= 1")
    for index in range(0, len(items), size):
        yield items[index : index + size]


def _validate_request_list(requests: list[BatchMessageRequest]) -> None:
    seen: set[str] = set()
    for request in requests:
        request.validate()
        if request.custom_id in seen:
            raise BatchSubmitError(
                f"duplicate custom_id {request.custom_id!r} in one batch"
            )
        seen.add(request.custom_id)


def submit_message_batch(
    client: Any,
    requests: list[BatchMessageRequest],
    *,
    cache_ttl: str = CACHE_TTL_1H,
) -> Any:
    """POST ``/v1/messages/batches``. ``requests`` must be non-empty and unique."""
    if not requests:
        raise BatchSubmitError("cannot submit an empty Message Batch")
    _validate_request_list(requests)
    payload = [request.to_api_request(cache_ttl=cache_ttl) for request in requests]
    logger.info(
        "anthropic batch submit count=%s cache_ttl=%s custom_ids=%s",
        len(payload),
        cache_ttl,
        ",".join(request.custom_id for request in requests),
    )
    return client.messages.batches.create(requests=payload)


def poll_message_batch(
    client: Any,
    batch_id: str,
    *,
    interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
    timeout: float = DEFAULT_POLL_TIMEOUT_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> Any:
    """Retrieve until ``processing_status`` is ``ended``, or raise timeout.

    ``canceling`` is not terminal — keep polling until Anthropic flips the
    batch to ``ended`` (partial results may still be available).
    """
    if timeout <= 0:
        raise BatchTimeoutError(batch_id, None)
    deadline = clock() + timeout
    last_status: str | None = None
    while True:
        batch = client.messages.batches.retrieve(batch_id)
        last_status = _attr(batch, "processing_status")
        if last_status == "ended":
            return batch
        if clock() >= deadline:
            raise BatchTimeoutError(batch_id, last_status)
        logger.info(
            "anthropic batch poll id=%s status=%s", batch_id, last_status
        )
        sleep(interval)


def _iter_result_rows(raw: Any) -> Iterable[Any]:
    if raw is None:
        return
    if isinstance(raw, dict) and "custom_id" in raw:
        yield raw
        return
    if hasattr(raw, "custom_id") and not isinstance(raw, (list, tuple)):
        yield raw
        return
    try:
        yield from raw
    except TypeError as exc:
        raise BatchError("batch results are not iterable") from exc


def collect_batch_results(client: Any, batch_id: str) -> dict[str, BatchItemResult]:
    """Map ``custom_id`` → :class:`BatchItemResult`. Order is not meaningful."""
    mapped: dict[str, BatchItemResult] = {}
    for row in _iter_result_rows(client.messages.batches.results(batch_id)):
        item = BatchItemResult.from_sdk(row)
        mapped[item.custom_id] = item
    return mapped


def _missing_result(custom_id: str) -> BatchItemResult:
    return BatchItemResult(
        custom_id=custom_id,
        type="errored",
        error_type="missing_result",
        error_message="custom_id was not present in the batch results file",
    )


def _log_success(request: BatchMessageRequest, item: BatchItemResult) -> None:
    if item.message is None:
        return
    log_cache_usage(item.message, call_site=request.call_site, model=request.model)


def run_message_batch(
    client: Any,
    requests: list[BatchMessageRequest],
    *,
    cache_ttl: str = CACHE_TTL_1H,
    max_retries: int = DEFAULT_MAX_RETRIES,
    chunk_size: int = MAX_REQUESTS_PER_BATCH,
    poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
    poll_timeout: float = DEFAULT_POLL_TIMEOUT_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> BatchRunResult:
    """Submit, poll, collect, and retry failed items.

    Retry policy (one extra batch by default):

    * ``expired`` / ``canceled`` — always retried.
    * ``errored`` — retried unless ``error_type`` is a non-retryable client
      error (invalid request, auth, billing, permission, not found).
    * Missing ``custom_id`` in the results file — retried.
    * ``succeeded`` — never retried.
    * ``invalid_request_error`` — never retried; the params must be fixed.

    Interactive callers must not use this function. Timeouts leave the
    Anthropic batch id on :class:`BatchTimeoutError` so an operator can
    resume with :func:`poll_message_batch` + :func:`collect_batch_results`.
    """
    outcome = BatchRunResult()
    if not requests:
        return outcome
    _validate_request_list(requests)

    pending = list(requests)
    rounds = 0

    while pending:
        next_pending: list[BatchMessageRequest] = []
        for chunk in _chunks(pending, chunk_size):
            try:
                submitted = submit_message_batch(client, chunk, cache_ttl=cache_ttl)
                batch_id = str(_attr(submitted, "id") or "")
                if not batch_id:
                    raise BatchSubmitError("batch create response is missing id")
                outcome.submitted_batch_ids.append(batch_id)
                poll_message_batch(
                    client,
                    batch_id,
                    interval=poll_interval,
                    timeout=poll_timeout,
                    sleep=sleep,
                    clock=clock,
                )
                results = collect_batch_results(client, batch_id)
            except BatchRunInterrupted:
                raise
            except Exception as exc:
                raise BatchRunInterrupted(outcome, exc) from exc
            for request in chunk:
                item = results.get(request.custom_id) or _missing_result(
                    request.custom_id
                )
                if item.succeeded:
                    outcome.succeeded[request.custom_id] = item
                    _log_success(request, item)
                    continue
                if item.retryable and rounds < max_retries:
                    logger.warning(
                        "anthropic batch retry custom_id=%s type=%s error_type=%s",
                        request.custom_id,
                        item.type,
                        item.error_type,
                    )
                    next_pending.append(request)
                    continue
                logger.error(
                    "anthropic batch failed custom_id=%s type=%s error_type=%s "
                    "error=%s",
                    request.custom_id,
                    item.type,
                    item.error_type,
                    item.error_message,
                )
                outcome.failed[request.custom_id] = item
        if not next_pending:
            break
        rounds += 1
        outcome.retry_rounds = rounds
        pending = next_pending

    return outcome
