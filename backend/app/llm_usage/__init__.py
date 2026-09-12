"""Per-workflow Claude usage instrumentation (FOOD-54).

Public surface:

- :func:`workflow_scope` — attribute Claude calls to a product workflow.
- :func:`create_message` — drop-in for ``client.messages.create`` that records
  tokens, cache tiers, vision share, latency, stop reason and estimated USD.
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
from app.llm_usage.recorder import UsageEvent, create_message, set_sinks

__all__ = [
    "KNOWN_WORKFLOWS",
    "WORKFLOW_INGREDIENT_NORMALIZE",
    "WORKFLOW_MEAL_GEN",
    "WORKFLOW_RECEIPT_PARSE",
    "WORKFLOW_UNATTRIBUTED",
    "UsageEvent",
    "bind_run_ids",
    "create_message",
    "current_run",
    "set_sinks",
    "workflow_scope",
]
