"""Structured path telemetry for receipt analysis (FOOD-55).

Until the FOOD-54 metrics hooks land this emits one structured log record per
analysis and fans out to any registered listeners. Metrics eng can attach a
listener without touching the pipeline.
"""

from __future__ import annotations

import json
import logging
from typing import Callable

logger = logging.getLogger("app.receipts.telemetry")

RECEIPT_PATHS = ("cache", "ocr", "haiku", "sonnet", "opus_baseline")

_listeners: list[Callable[[dict], None]] = []


def register_receipt_path_listener(listener: Callable[[dict], None]) -> None:
    _listeners.append(listener)


def clear_receipt_path_listeners() -> None:
    _listeners.clear()


def emit_receipt_path(event: dict) -> None:
    """Log ``event`` as JSON and notify listeners; never raises into the request path."""
    payload = {"event": "receipt_analysis", **event}
    try:
        logger.info(json.dumps(payload, default=str, sort_keys=True))
    except Exception:  # pragma: no cover - logging must never break uploads
        logger.exception("failed to serialize receipt telemetry")

    for listener in list(_listeners):
        try:
            listener(payload)
        except Exception:  # pragma: no cover
            logger.exception("receipt telemetry listener failed")
