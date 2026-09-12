"""Model routing for Claude call sites (FOOD-58).

Every Claude call site asks :func:`route_model` which model to use instead of
importing a model constant directly. The router owns one table
(:data:`POLICY`) with, per call site:

* ``default`` — the tier the site ships on today. Used whenever
  ``MODEL_ROUTING_ENABLED`` is off, so the flag's off state is exactly the
  pre-FOOD-58 behaviour.
* ``routed`` — the tier the written policy (``backend/MODEL_ROUTING.md``)
  assigns once the evals in ``backend/tests/evals`` are green for it. Used
  when the flag is on.
* ``escalate_to`` — the stronger tier to re-run on when the cheap tier
  reports low confidence (an ``ambiguous`` match, an invented pantry id, a
  negative classification that would block the user). Only ever applied when
  routing is on *and* it is stronger than the tier already chosen.

The router logs one INFO line per decision so the model mix and escalation
rate can be read off the same ``call_site=`` stream FOOD-54 aggregates.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from enum import Enum
from typing import Iterator

from app.config import (
    HAIKU_ANTHROPIC_MODEL,
    OPUS_ANTHROPIC_MODEL,
    SONNET_ANTHROPIC_MODEL,
    settings,
)

logger = logging.getLogger(__name__)


class Tier(str, Enum):
    HAIKU = "haiku"
    SONNET = "sonnet"
    OPUS = "opus"


TIER_MODELS: dict[Tier, str] = {
    Tier.HAIKU: HAIKU_ANTHROPIC_MODEL,
    Tier.SONNET: SONNET_ANTHROPIC_MODEL,
    Tier.OPUS: OPUS_ANTHROPIC_MODEL,
}

_TIER_RANK: dict[Tier, int] = {Tier.HAIKU: 0, Tier.SONNET: 1, Tier.OPUS: 2}

# Call sites that hand the router a numeric confidence escalate below this.
LOW_CONFIDENCE_THRESHOLD = 0.6


class Workflow(str, Enum):
    """What kind of work a call site does; drives the tier in the policy doc."""

    VISION_EXTRACT = "vision_extract"
    CLASSIFY_EXTRACT = "classify_extract"
    SOFT_TEXT_CLEANUP = "soft_text_cleanup"
    NUTRITION_ESTIMATE = "nutrition_estimate"
    AMBIGUOUS_INGREDIENT = "ambiguous_ingredient"
    CONSTRAINED_MEAL_PLAN = "constrained_meal_plan"


@dataclass(frozen=True)
class RoutePolicy:
    call_site: str
    workflow: Workflow
    default: Tier
    routed: Tier
    escalate_to: Tier | None
    rationale: str


POLICY: dict[str, RoutePolicy] = {
    policy.call_site: policy
    for policy in (
        RoutePolicy(
            call_site="receipt.analyze_image",
            workflow=Workflow.VISION_EXTRACT,
            default=Tier.OPUS,
            routed=Tier.OPUS,
            escalate_to=None,
            rationale=(
                "Vision extraction from a photo/PDF drives every downstream metric; "
                "stays on Opus until a labelled receipt image set scores green on a "
                "cheaper tier (tests/evals/test_live_evals.py::test_live_receipt_parse)."
            ),
        ),
        RoutePolicy(
            call_site="receipt.nutrition_estimate",
            workflow=Workflow.NUTRITION_ESTIMATE,
            default=Tier.OPUS,
            routed=Tier.OPUS,
            escalate_to=None,
            rationale=(
                "Numeric recall of label/USDA values; a wrong kcal-per-serving silently "
                "skews every meal. Stays on Opus until nutrition fixtures exist and score green."
            ),
        ),
        RoutePolicy(
            call_site="receipt.unit_check",
            workflow=Workflow.CLASSIFY_EXTRACT,
            default=Tier.OPUS,
            routed=Tier.HAIKU,
            escalate_to=Tier.OPUS,
            rationale=(
                "Binary plausibility classification. A false 'implausible' blocks the "
                "user, so a negative from Haiku is re-checked on Opus before rejecting."
            ),
        ),
        RoutePolicy(
            call_site="receipt.pantry_match",
            workflow=Workflow.AMBIGUOUS_INGREDIENT,
            default=Tier.OPUS,
            routed=Tier.SONNET,
            escalate_to=Tier.OPUS,
            rationale=(
                "Abbreviation expansion plus same-food judgement against the pantry. "
                "Sonnet by policy; 'ambiguous' or an invented id is low confidence and "
                "re-runs on Opus so near-duplicates are not created or held needlessly."
            ),
        ),
        RoutePolicy(
            call_site="receipt.ocr_text_cleanup",
            workflow=Workflow.SOFT_TEXT_CLEANUP,
            default=Tier.HAIKU,
            routed=Tier.HAIKU,
            escalate_to=Tier.SONNET,
            rationale=(
                "FOOD-55 soft fail: Haiku cleans up Tesseract text (fix character "
                "errors, split lines) when RECEIPT_OCR_FIRST is on. Rules parser "
                "and gates re-validate; escalate to Sonnet vision if the cleanup "
                "is not schema-valid."
            ),
        ),
        RoutePolicy(
            call_site="receipt.ocr_cleanup",
            workflow=Workflow.SOFT_TEXT_CLEANUP,
            default=Tier.HAIKU,
            routed=Tier.HAIKU,
            escalate_to=Tier.SONNET,
            rationale=(
                "Alias reserved by FOOD-58 before FOOD-55 landed. Prefer "
                "receipt.ocr_text_cleanup, the live call site."
            ),
        ),
        RoutePolicy(
            call_site="receipt.classify_text",
            workflow=Workflow.CLASSIFY_EXTRACT,
            default=Tier.HAIKU,
            routed=Tier.HAIKU,
            escalate_to=Tier.SONNET,
            rationale=(
                "Reserved: food / non-food and field extraction over already-OCR'd "
                "text. FOOD-55 uses the deterministic parser instead; keep the "
                "id so a later LLM classify step can route here."
            ),
        ),
        RoutePolicy(
            call_site="meal.generate",
            workflow=Workflow.CONSTRAINED_MEAL_PLAN,
            default=Tier.SONNET,
            routed=Tier.SONNET,
            escalate_to=None,
            rationale=(
                "Multi-constraint planning (calorie band, pantry maxima, single portion, "
                "avoid repeats). Sonnet is the policy tier; never downgrade to Haiku."
            ),
        ),
        RoutePolicy(
            call_site="meal.image_prompt",
            workflow=Workflow.SOFT_TEXT_CLEANUP,
            default=Tier.SONNET,
            routed=Tier.HAIKU,
            escalate_to=None,
            rationale=(
                "Writes an 80-word photo prompt and has a deterministic fallback; "
                "quality risk is cosmetic."
            ),
        ),
    )
}


@dataclass(frozen=True)
class RouteDecision:
    call_site: str
    workflow: Workflow
    tier: Tier
    model: str
    routing_enabled: bool
    reason: str  # default | policy | escalated | forced
    escalated: bool = False
    confidence: float | None = None


_forced_model: ContextVar[str | None] = ContextVar("food_forced_model", default=None)


@contextmanager
def forced_model(model: str) -> Iterator[str]:
    """Force every decision to ``model`` (eval harness only; see tests/evals)."""
    token = _forced_model.set(model)
    try:
        yield model
    finally:
        _forced_model.reset(token)


def routing_enabled() -> bool:
    return bool(settings.model_routing_enabled)


def policy_for(call_site: str) -> RoutePolicy:
    try:
        return POLICY[call_site]
    except KeyError as exc:
        known = ", ".join(sorted(POLICY))
        raise ValueError(
            f"No routing policy for call_site {call_site!r}. Add it to "
            f"app/services/model_router.py and backend/MODEL_ROUTING.md (known: {known})."
        ) from exc


def _log(decision: RouteDecision) -> None:
    logger.info(
        "model_route call_site=%s workflow=%s tier=%s model=%s reason=%s "
        "routing_enabled=%s escalated=%s confidence=%s",
        decision.call_site,
        decision.workflow.value,
        decision.tier.value,
        decision.model,
        decision.reason,
        decision.routing_enabled,
        decision.escalated,
        "" if decision.confidence is None else f"{decision.confidence:.2f}",
    )


def route_model(
    call_site: str,
    *,
    confidence: float | None = None,
    escalate: bool = False,
    enabled: bool | None = None,
) -> RouteDecision:
    """Pick the model for ``call_site`` and log the decision.

    ``confidence`` (0–1) below :data:`LOW_CONFIDENCE_THRESHOLD`, or
    ``escalate=True``, moves the decision to the policy's ``escalate_to`` tier
    when routing is on. ``enabled`` overrides the settings flag (tests).
    """
    policy = policy_for(call_site)
    is_enabled = routing_enabled() if enabled is None else enabled

    forced = _forced_model.get()
    if forced is not None:
        tier = next((t for t, m in TIER_MODELS.items() if m == forced), policy.default)
        decision = RouteDecision(
            call_site=call_site,
            workflow=policy.workflow,
            tier=tier,
            model=forced,
            routing_enabled=is_enabled,
            reason="forced",
            confidence=confidence,
        )
        _log(decision)
        return decision

    if not is_enabled:
        decision = RouteDecision(
            call_site=call_site,
            workflow=policy.workflow,
            tier=policy.default,
            model=TIER_MODELS[policy.default],
            routing_enabled=False,
            reason="default",
            confidence=confidence,
        )
        _log(decision)
        return decision

    tier = policy.routed
    reason = "policy"
    escalated = False
    low_confidence = confidence is not None and confidence < LOW_CONFIDENCE_THRESHOLD
    if (escalate or low_confidence) and policy.escalate_to is not None:
        if _TIER_RANK[policy.escalate_to] > _TIER_RANK[tier]:
            tier = policy.escalate_to
            reason = "escalated"
            escalated = True

    decision = RouteDecision(
        call_site=call_site,
        workflow=policy.workflow,
        tier=tier,
        model=TIER_MODELS[tier],
        routing_enabled=True,
        reason=reason,
        escalated=escalated,
        confidence=confidence,
    )
    _log(decision)
    return decision


def escalation_for(decision: RouteDecision) -> RouteDecision | None:
    """The stronger-tier decision to retry with after a low-confidence result.

    Returns ``None`` when routing is off, the decision was forced, the policy
    has no escalation tier, or the decision already used a tier at least as
    strong as the escalation target — so with the flag off this is always
    ``None`` and no second call is ever made.
    """
    if not decision.routing_enabled or decision.reason == "forced" or decision.escalated:
        return None
    policy = policy_for(decision.call_site)
    if policy.escalate_to is None:
        return None
    if _TIER_RANK[policy.escalate_to] <= _TIER_RANK[decision.tier]:
        return None
    return route_model(decision.call_site, escalate=True, enabled=True)
