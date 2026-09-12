"""Workflow attribution for Claude calls.

Routers/services open a :func:`workflow_scope` around a product workflow;
every Claude call made inside it (including from worker threads that copy
the context) is tagged with the workflow id, a per-run id, and the app
identifiers already in play (user / receipt / meal).
"""

from __future__ import annotations

import contextvars
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

# Canonical workflow ids from FOOD-54. Every Claude call is attributed to one.
WORKFLOW_RECEIPT_PARSE = "receipt_parse"
WORKFLOW_INGREDIENT_NORMALIZE = "ingredient_normalize"
WORKFLOW_MEAL_GEN = "meal_gen"
# Safety net: a Claude call outside any declared scope is still counted.
WORKFLOW_UNATTRIBUTED = "unattributed"

KNOWN_WORKFLOWS = (
    WORKFLOW_RECEIPT_PARSE,
    WORKFLOW_INGREDIENT_NORMALIZE,
    WORKFLOW_MEAL_GEN,
)


@dataclass
class WorkflowRun:
    workflow: str
    recorded: bool = True
    run_id: str | None = field(default_factory=lambda: uuid.uuid4().hex)
    user_id: str | None = None
    receipt_id: str | None = None
    meal_id: str | None = None
    started_monotonic: float = field(default_factory=time.monotonic)
    call_count: int = 0


_current_run: contextvars.ContextVar[WorkflowRun | None] = contextvars.ContextVar(
    "llm_usage_current_run", default=None
)


def current_run() -> WorkflowRun | None:
    return _current_run.get()


@contextmanager
def workflow_scope(
    workflow: str,
    *,
    user_id: str | None = None,
    receipt_id: str | None = None,
    meal_id: str | None = None,
    record_run: bool = True,
) -> Iterator[WorkflowRun]:
    """Attribute all Claude calls within the block to ``workflow``.

    With ``record_run=True`` (default) the block is one *run*: its outcome is
    persisted so the dashboard can report $ per successful run. Nested scopes
    of the same workflow reuse the outer recorded run, so a service that
    declares its own scope can also be called from a router that already did.

    With ``record_run=False`` calls are tagged with the workflow but no run is
    created. Use it for auxiliary calls (live unit checks, image prompts) that
    should count toward a workflow's spend without inflating its run count.
    """
    from app.llm_usage import recorder  # local import avoids a cycle

    outer = _current_run.get()
    if outer is not None and outer.recorded and outer.workflow == workflow:
        _fill_missing_ids(outer, user_id=user_id, receipt_id=receipt_id, meal_id=meal_id)
        yield outer
        return

    run = WorkflowRun(
        workflow=workflow,
        recorded=record_run,
        run_id=uuid.uuid4().hex if record_run else None,
        user_id=user_id,
        receipt_id=receipt_id,
        meal_id=meal_id,
    )
    token = _current_run.set(run)
    if record_run:
        recorder.on_run_started(run)
    try:
        yield run
    except BaseException as exc:
        if record_run:
            recorder.on_run_finished(run, status="failed", error=exc)
        raise
    else:
        if record_run:
            recorder.on_run_finished(run, status="succeeded", error=None)
    finally:
        _current_run.reset(token)


def bind_run_ids(*, receipt_id: str | None = None, meal_id: str | None = None) -> None:
    """Attach ids that only become known part-way through a run."""
    run = _current_run.get()
    if run is None:
        return
    if receipt_id:
        run.receipt_id = receipt_id
    if meal_id:
        run.meal_id = meal_id


def _fill_missing_ids(
    run: WorkflowRun,
    *,
    user_id: str | None,
    receipt_id: str | None,
    meal_id: str | None,
) -> None:
    if user_id and not run.user_id:
        run.user_id = user_id
    if receipt_id and not run.receipt_id:
        run.receipt_id = receipt_id
    if meal_id and not run.meal_id:
        run.meal_id = meal_id
