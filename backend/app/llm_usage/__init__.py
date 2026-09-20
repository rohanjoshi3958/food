"""Per-workflow Claude usage instrumentation (FOOD-54).

Public surface:

- :func:`workflow_scope` — attribute Claude calls to a product workflow.
- :func:`create_message` — drop-in for ``client.messages.create`` that records
  tokens, cache tiers, vision share, latency, stop reason and estimated USD.
- :func:`pipeline_step` / :func:`record_pipeline_step` — record non-Claude
  steps (OCR, cache hits, rules) with ``route`` and ``confidence`` into the
  same event stream, for the OCR-first receipt pipeline (FOOD-55).
- :mod:`app.llm_usage.pricing` — list-price table and cost math.
"""

from app.llm_usage.context import (
    KNOWN_WORKFLOWS,
    WORKFLOW_INGREDIENT_NORMALIZE,
    WORKFLOW_MEAL_GEN,
    WORKFLOW_RECEIPT_PARSE,
    WORKFLOW_UNATTRIBUTED,
    bind_run_ids,
    current_run,
    workflow_scope,
)
from app.llm_usage.pricing import TokenUsage
from app.llm_usage.recorder import (
    KNOWN_ROUTES,
    ROUTE_CACHE,
    ROUTE_HAIKU,
    ROUTE_OCR,
    ROUTE_OPUS,
    ROUTE_SONNET,
    PipelineStep,
    UsageEvent,
    create_message,
    pipeline_step,
    record_pipeline_step,
    route_for_model,
    set_sinks,
)

__all__ = [
    "KNOWN_ROUTES",
    "KNOWN_WORKFLOWS",
    "ROUTE_CACHE",
    "ROUTE_HAIKU",
    "ROUTE_OCR",
    "ROUTE_OPUS",
    "ROUTE_SONNET",
    "WORKFLOW_INGREDIENT_NORMALIZE",
    "WORKFLOW_MEAL_GEN",
    "WORKFLOW_RECEIPT_PARSE",
    "WORKFLOW_UNATTRIBUTED",
    "PipelineStep",
    "TokenUsage",
    "UsageEvent",
    "bind_run_ids",
    "create_message",
    "current_run",
    "pipeline_step",
    "record_pipeline_step",
    "route_for_model",
    "set_sinks",
    "workflow_scope",
]
