"""Dashboard aggregates over ``llm_usage_events`` / ``llm_workflow_runs``.

Answers FOOD-54's questions for a trailing window (default 7 days):

- $ and tokens per *successful* receipt parse / normalize / meal
- cache read (hit) %, vision vs text share, model mix
- retry rate (a step re-tried inside one run) and escalation rate
  (more than one model used inside one run)

Events are pulled for the window with only the columns needed and folded
in Python so the same code runs on Postgres and the SQLite test database.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.llm_usage.context import KNOWN_WORKFLOWS
from app.llm_usage.pricing import PRICING_VERSION
from app.models import LlmUsageEvent, LlmWorkflowRun

MAX_WINDOW_DAYS = 90
# Rough chars-per-token used only to split input between text and vision
# when an event has images but their size was unknown.
CHARS_PER_TOKEN = 4.0


@dataclass
class TokenTotals:
    uncached_input: int = 0
    cache_write_5m: int = 0
    cache_write_1h: int = 0
    cache_read: int = 0
    output: int = 0

    @property
    def total_input(self) -> int:
        return self.uncached_input + self.cache_write_5m + self.cache_write_1h + self.cache_read

    @property
    def total(self) -> int:
        return self.total_input + self.output

    def add(self, event: LlmUsageEvent) -> None:
        self.uncached_input += event.uncached_input_tokens
        self.cache_write_5m += event.cache_write_5m_tokens
        self.cache_write_1h += event.cache_write_1h_tokens
        self.cache_read += event.cache_read_tokens
        self.output += event.output_tokens

    def as_dict(self) -> dict:
        return {
            "uncached_input": self.uncached_input,
            "cache_write_5m": self.cache_write_5m,
            "cache_write_1h": self.cache_write_1h,
            "cache_read": self.cache_read,
            "output": self.output,
            "total_input": self.total_input,
            "total": self.total,
        }


@dataclass
class Bucket:
    calls: int = 0
    ok_calls: int = 0
    error_calls: int = 0
    cost_usd: float = 0.0
    tokens: TokenTotals = field(default_factory=TokenTotals)
    latency_ms_sum: int = 0
    image_count: int = 0
    document_count: int = 0
    approx_visual_tokens: int = 0
    calls_with_visual: int = 0
    unpriced_calls: int = 0

    def add(self, event: LlmUsageEvent) -> None:
        self.calls += 1
        if event.status == "ok":
            self.ok_calls += 1
        else:
            self.error_calls += 1
        self.cost_usd += event.estimated_cost_usd
        self.tokens.add(event)
        self.latency_ms_sum += event.latency_ms
        self.image_count += event.image_count
        self.document_count += event.document_count
        if event.image_count or event.document_count:
            self.calls_with_visual += 1
        self.approx_visual_tokens += _visual_tokens(event)
        if not event.pricing_known:
            self.unpriced_calls += 1

    @property
    def avg_latency_ms(self) -> float | None:
        return round(self.latency_ms_sum / self.calls) if self.calls else None

    @property
    def cache_read_pct(self) -> float | None:
        return _pct(self.tokens.cache_read, self.tokens.total_input)

    def vision_dict(self) -> dict:
        visual = min(self.approx_visual_tokens, self.tokens.total_input)
        text = max(self.tokens.total_input - visual, 0)
        return {
            "image_count": self.image_count,
            "document_count": self.document_count,
            "calls_with_visual": self.calls_with_visual,
            "approx_visual_tokens": visual,
            "approx_text_tokens": text,
            "vision_share_pct": _pct(visual, self.tokens.total_input),
        }

    def as_dict(self) -> dict:
        return {
            "calls": self.calls,
            "ok_calls": self.ok_calls,
            "error_calls": self.error_calls,
            "error_rate_pct": _pct(self.error_calls, self.calls),
            "estimated_cost_usd": round(self.cost_usd, 6),
            "tokens": self.tokens.as_dict(),
            "cache_read_pct": self.cache_read_pct,
            "avg_latency_ms": self.avg_latency_ms,
            "unpriced_calls": self.unpriced_calls,
            "vision": self.vision_dict(),
        }


def _visual_tokens(event: LlmUsageEvent) -> int:
    if event.approx_visual_tokens is not None:
        return event.approx_visual_tokens
    if not (event.image_count or event.document_count):
        return 0
    # Unknown image size (e.g. PDF): everything that is not plausibly text.
    text_estimate = int(event.input_text_chars / CHARS_PER_TOKEN)
    total_input = (
        event.uncached_input_tokens
        + event.cache_write_5m_tokens
        + event.cache_write_1h_tokens
        + event.cache_read_tokens
    )
    return max(total_input - text_estimate, 0)


def _pct(part: float, whole: float) -> float | None:
    if not whole:
        return None
    return round(100.0 * part / whole, 2)


def _per(value: float, count: int, digits: int = 6) -> float | None:
    if not count:
        return None
    return round(value / count, digits)


def window_bounds(days: int) -> tuple[datetime, datetime]:
    days = max(1, min(int(days), MAX_WINDOW_DAYS))
    until = datetime.now(UTC)
    return until - timedelta(days=days), until


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def load_window(
    db: Session, since: datetime, until: datetime
) -> tuple[list[LlmUsageEvent], dict[str, LlmWorkflowRun]]:
    events = list(
        db.scalars(
            select(LlmUsageEvent)
            .where(LlmUsageEvent.created_at >= since, LlmUsageEvent.created_at < until)
            .order_by(LlmUsageEvent.created_at.asc())
        )
    )
    runs = {
        run.id: run
        for run in db.scalars(
            select(LlmWorkflowRun).where(
                LlmWorkflowRun.started_at >= since, LlmWorkflowRun.started_at < until
            )
        )
    }
    return events, runs


def summarize(db: Session, *, days: int = 7) -> dict:
    since, until = window_bounds(days)
    events, runs = load_window(db, since, until)

    totals = Bucket()
    by_workflow: dict[str, Bucket] = defaultdict(Bucket)
    by_workflow_step: dict[str, dict[str, Bucket]] = defaultdict(lambda: defaultdict(Bucket))
    by_workflow_model: dict[str, dict[str, Bucket]] = defaultdict(lambda: defaultdict(Bucket))
    by_workflow_route: dict[str, dict[str, Bucket]] = defaultdict(lambda: defaultdict(Bucket))
    by_model: dict[str, Bucket] = defaultdict(Bucket)
    by_day: dict[str, Bucket] = defaultdict(Bucket)
    by_day_workflow: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))

    # Per-run facts for $/successful run, retry and escalation rates.
    run_cost: dict[str, float] = defaultdict(float)
    run_input_tokens: dict[str, int] = defaultdict(int)
    run_output_tokens: dict[str, int] = defaultdict(int)
    run_models: dict[str, set[str]] = defaultdict(set)
    run_retried: set[str] = set()
    run_calls: dict[str, int] = defaultdict(int)

    for event in events:
        totals.add(event)
        by_workflow[event.workflow].add(event)
        by_workflow_step[event.workflow][event.step].add(event)
        by_workflow_model[event.workflow][event.model].add(event)
        by_workflow_route[event.workflow][event.route or "unknown"].add(event)
        by_model[event.model].add(event)
        day = _as_utc(event.created_at).date().isoformat()
        by_day[day].add(event)
        by_day_workflow[day][event.workflow] += event.estimated_cost_usd

        if event.run_id and event.run_id in runs:
            run_cost[event.run_id] += event.estimated_cost_usd
            run_input_tokens[event.run_id] += (
                event.uncached_input_tokens
                + event.cache_write_5m_tokens
                + event.cache_write_1h_tokens
                + event.cache_read_tokens
            )
            run_output_tokens[event.run_id] += event.output_tokens
            run_models[event.run_id].add(event.model)
            run_calls[event.run_id] += 1
            if event.attempt > 1:
                run_retried.add(event.run_id)

    workflows_out = []
    ordered = [w for w in KNOWN_WORKFLOWS] + sorted(
        w for w in set(by_workflow) | {r.workflow for r in runs.values()} if w not in KNOWN_WORKFLOWS
    )
    for workflow in ordered:
        bucket = by_workflow.get(workflow, Bucket())
        wf_runs = [run for run in runs.values() if run.workflow == workflow]
        successful = [run for run in wf_runs if run.status == "succeeded"]
        failed = [run for run in wf_runs if run.status == "failed"]
        running = [run for run in wf_runs if run.status == "running"]
        successful_ids = {run.id for run in successful}

        cost_successful = sum(run_cost[rid] for rid in successful_ids)
        input_successful = sum(run_input_tokens[rid] for rid in successful_ids)
        output_successful = sum(run_output_tokens[rid] for rid in successful_ids)
        runs_with_calls = [run.id for run in wf_runs if run_calls.get(run.id)]
        retried = sum(1 for rid in runs_with_calls if rid in run_retried)
        escalated = sum(1 for rid in runs_with_calls if len(run_models[rid]) > 1)
        run_attributed_cost = sum(run_cost[run.id] for run in wf_runs)

        workflows_out.append(
            {
                "workflow": workflow,
                **bucket.as_dict(),
                "runs": len(wf_runs),
                "successful_runs": len(successful),
                "failed_runs": len(failed),
                "running_runs": len(running),
                "run_success_rate_pct": _pct(len(successful), len(successful) + len(failed)),
                "cost_in_successful_runs_usd": round(cost_successful, 6),
                "cost_per_successful_run_usd": _per(cost_successful, len(successful)),
                "input_tokens_per_successful_run": _per(input_successful, len(successful), 1),
                "output_tokens_per_successful_run": _per(output_successful, len(successful), 1),
                "cost_outside_runs_usd": round(bucket.cost_usd - run_attributed_cost, 6),
                "calls_per_run": _per(sum(run_calls[rid] for rid in runs_with_calls), len(runs_with_calls), 2),
                "retry_rate_pct": _pct(retried, len(runs_with_calls)),
                "escalation_rate_pct": _pct(escalated, len(runs_with_calls)),
                "steps": [
                    {"step": step, **step_bucket.as_dict()}
                    for step, step_bucket in sorted(
                        by_workflow_step.get(workflow, {}).items(),
                        key=lambda item: -item[1].cost_usd,
                    )
                ],
                "models": [
                    {"model": model, **model_bucket.as_dict()}
                    for model, model_bucket in sorted(
                        by_workflow_model.get(workflow, {}).items(),
                        key=lambda item: -item[1].cost_usd,
                    )
                ],
                # Route share (cache / ocr / haiku / sonnet / opus) so the
                # OCR-first pipeline's LLM-fallback rate is visible (FOOD-55).
                "routes": [
                    {
                        "route": route,
                        "share_of_calls_pct": _pct(route_bucket.calls, bucket.calls),
                        **route_bucket.as_dict(),
                    }
                    for route, route_bucket in sorted(
                        by_workflow_route.get(workflow, {}).items(),
                        key=lambda item: -item[1].calls,
                    )
                ],
            }
        )

    models_out = [
        {
            "model": model,
            **bucket.as_dict(),
            "share_of_cost_pct": _pct(bucket.cost_usd, totals.cost_usd),
            "share_of_calls_pct": _pct(bucket.calls, totals.calls),
        }
        for model, bucket in sorted(by_model.items(), key=lambda item: -item[1].cost_usd)
    ]

    daily_out = []
    day = since.date()
    last_day = until.date()
    while day <= last_day:
        key = day.isoformat()
        bucket = by_day.get(key, Bucket())
        daily_out.append(
            {
                "date": key,
                "calls": bucket.calls,
                "estimated_cost_usd": round(bucket.cost_usd, 6),
                "by_workflow": {
                    workflow: round(cost, 6)
                    for workflow, cost in sorted(by_day_workflow.get(key, {}).items())
                },
            }
        )
        day += timedelta(days=1)

    return {
        "window": {"days": (until - since).days, "since": since.isoformat(), "until": until.isoformat()},
        "pricing_version": PRICING_VERSION,
        "totals": {
            **totals.as_dict(),
            "runs": len(runs),
            "successful_runs": sum(1 for run in runs.values() if run.status == "succeeded"),
        },
        "workflows": workflows_out,
        "models": models_out,
        "daily": daily_out,
    }


def recent_events(
    db: Session,
    *,
    limit: int = 50,
    workflow: str | None = None,
    status: str | None = None,
) -> list[dict]:
    limit = max(1, min(int(limit), 500))
    query = select(LlmUsageEvent).order_by(LlmUsageEvent.created_at.desc()).limit(limit)
    if workflow:
        query = query.where(LlmUsageEvent.workflow == workflow)
    if status:
        query = query.where(LlmUsageEvent.status == status)
    return [event_to_dict(event) for event in db.scalars(query)]


def event_to_dict(event: LlmUsageEvent) -> dict:
    total_input = (
        event.uncached_input_tokens
        + event.cache_write_5m_tokens
        + event.cache_write_1h_tokens
        + event.cache_read_tokens
    )
    return {
        "id": event.id,
        "created_at": _as_utc(event.created_at).isoformat(),
        "workflow": event.workflow,
        "step": event.step,
        "run_id": event.run_id,
        "attempt": event.attempt,
        "model": event.model,
        "provider": event.provider,
        "route": event.route,
        "confidence": event.confidence,
        "service_tier": event.service_tier,
        "status": event.status,
        "error_type": event.error_type,
        "stop_reason": event.stop_reason,
        "latency_ms": event.latency_ms,
        "max_tokens": event.max_tokens,
        "uncached_input_tokens": event.uncached_input_tokens,
        "cache_write_5m_tokens": event.cache_write_5m_tokens,
        "cache_write_1h_tokens": event.cache_write_1h_tokens,
        "cache_read_tokens": event.cache_read_tokens,
        "output_tokens": event.output_tokens,
        "total_input_tokens": total_input,
        "image_count": event.image_count,
        "document_count": event.document_count,
        "approx_visual_tokens": event.approx_visual_tokens,
        "estimated_cost_usd": event.estimated_cost_usd,
        "pricing_known": event.pricing_known,
        "user_id": event.user_id,
        "receipt_id": event.receipt_id,
        "meal_id": event.meal_id,
        "anthropic_request_id": event.anthropic_request_id,
    }
