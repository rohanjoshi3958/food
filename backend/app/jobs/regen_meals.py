"""Nightly meal regeneration via Message Batches (FOOD-57).

Interactive ``POST /api/meals/generate`` stays on the sync Messages API and
keeps its calorie / similarity retry loop. This CLI submits **one**
``meal.generate`` request per user pantry as a batch (50% off, 1h cache TTL).

Calorie retries are intentionally not in this job: they are extra conversation
turns that depend on the previous assistant JSON. If the user already has a
meal, that JSON is included so the model proposes a different dish (same as
interactive "try another") — still a single batch item, not a retry loop.
Out-of-range answers are finalized the same way a single successful sync
attempt is (clamp + one-person scale).

By default nothing is written. Pass ``--apply`` to replace that user's
current ``meals`` row the same way the generate endpoint does.

Usage (from ``backend/``)::

    python -m app.jobs.regen_meals
    python -m app.jobs.regen_meals --user-id <uuid> --apply
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass, field
from typing import Any, Sequence

import anthropic
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.models import Ingredient, Meal, User
from app.services.anthropic_batch import (
    BatchMessageRequest,
    BatchRunInterrupted,
    run_message_batch,
)
from app.services.anthropic_cache import message_text_blocks
from app.services.ingredient_deduction import serialize_meal_ingredients
from app.services.meal_generator import (
    FOLLOW_UP_PROMPT,
    MEAL_CALORIE_MAX,
    MEAL_CALORIE_MIN,
    MEAL_GENERATION_PROMPT,
    MEAL_GENERATION_USER_PROMPT,
    GeneratedMeal,
    MealGenerationError,
    PreviousMealTurn,
    _assistant_turn_content,
    _finalize_meal_amounts,
    _format_ingredients,
    _parse_generated_meal,
    format_ingredients_used,
)
from app.services.meal_nutrition import calculate_meal_macros
from app.services.model_router import route_model

logger = logging.getLogger(__name__)

MEAL_CHUNK_SIZE = 100
MEAL_MAX_TOKENS = 4096


@dataclass
class MealWork:
    custom_id: str
    user_id: str
    pantry: list[Ingredient]


@dataclass
class RegenReport:
    ok: dict[str, str] = field(default_factory=dict)
    failed: dict[str, str] = field(default_factory=dict)
    skipped_empty_pantry: list[str] = field(default_factory=list)
    applied: list[str] = field(default_factory=list)
    apply_failed: dict[str, str] = field(default_factory=dict)
    batch_ids: list[str] = field(default_factory=list)
    interrupted: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "failed": self.failed,
            "skipped_empty_pantry": self.skipped_empty_pantry,
            "applied": self.applied,
            "apply_failed": self.apply_failed,
            "batch_ids": self.batch_ids,
            "interrupted": self.interrupted,
        }

    def exit_status(self) -> int:
        if self.failed or self.apply_failed or self.interrupted:
            return 1
        return 0


def _require_api_key() -> None:
    if not settings.anthropic_api_key:
        raise SystemExit(
            "ANTHROPIC_API_KEY is not configured. Add it to the environment / .env."
        )


def get_client() -> anthropic.Anthropic:
    _require_api_key()
    return anthropic.Anthropic(api_key=settings.anthropic_api_key)


def meal_generation_system_prefix() -> str:
    return MEAL_GENERATION_PROMPT.format(
        calorie_min=MEAL_CALORIE_MIN,
        calorie_max=MEAL_CALORIE_MAX,
    )


def meal_generation_messages(
    ingredients: list[Ingredient],
    previous_meal: PreviousMealTurn | None = None,
) -> list[dict[str, Any]]:
    """First-turn conversation only — same prefix/tail split as the sync path."""
    messages: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": MEAL_GENERATION_USER_PROMPT.format(
                ingredients=_format_ingredients(ingredients),
            ),
        }
    ]
    if previous_meal and previous_meal.name.strip():
        messages.append(
            {
                "role": "assistant",
                "content": _assistant_turn_content(previous_meal),
            }
        )
        messages.append(
            {
                "role": "user",
                "content": FOLLOW_UP_PROMPT.format(
                    calorie_min=MEAL_CALORIE_MIN,
                    calorie_max=MEAL_CALORIE_MAX,
                ),
            }
        )
    return messages


def build_meal_request(
    user_id: str,
    ingredients: list[Ingredient],
    previous_meal: PreviousMealTurn | None = None,
) -> BatchMessageRequest:
    return BatchMessageRequest(
        custom_id=user_id,
        call_site="meal.generate",
        model=route_model("meal.generate").model,
        max_tokens=MEAL_MAX_TOKENS,
        system_prefix=meal_generation_system_prefix(),
        messages=meal_generation_messages(ingredients, previous_meal),
    )


def parse_meal_message(message: Any) -> GeneratedMeal:
    text_blocks = message_text_blocks(message)
    if not text_blocks:
        raise MealGenerationError("Anthropic returned an empty response.")
    return _parse_generated_meal(text_blocks[-1])


def load_user_pantries(
    db: Session,
    *,
    user_ids: Sequence[str] | None = None,
    limit: int | None = None,
) -> list[MealWork]:
    query = db.query(User).order_by(User.createdAt.desc())
    if user_ids:
        query = query.filter(User.id.in_(list(user_ids)))
    if limit is not None:
        query = query.limit(limit)
    work: list[MealWork] = []
    for user in query.all():
        pantry = (
            db.query(Ingredient)
            .filter(Ingredient.user_id == user.id)
            .order_by(Ingredient.created_at.desc())
            .all()
        )
        work.append(MealWork(custom_id=user.id, user_id=user.id, pantry=pantry))
    return work


def _previous_meal_for(db: Session, user_id: str) -> PreviousMealTurn | None:
    meal = (
        db.query(Meal)
        .filter(Meal.user_id == user_id)
        .order_by(Meal.created_at.desc())
        .first()
    )
    if meal is None or not meal.name:
        return None
    return PreviousMealTurn(
        name=meal.name,
        description=meal.description,
        ingredients_used=meal.ingredients_used,
        instructions=meal.instructions,
    )


def persist_generated_meal(
    db: Session, user_id: str, suggestion: GeneratedMeal
) -> Meal:
    pantry = (
        db.query(Ingredient)
        .filter(Ingredient.user_id == user_id)
        .order_by(Ingredient.created_at.desc())
        .all()
    )
    db.query(Meal).filter(Meal.user_id == user_id).delete()
    used_data = serialize_meal_ingredients(suggestion.ingredients_used)
    macros = calculate_meal_macros(pantry, used_data)
    meal = Meal(
        user_id=user_id,
        name=suggestion.name.strip(),
        description=suggestion.description.strip(),
        ingredients_used=format_ingredients_used(suggestion.ingredients_used),
        ingredients_used_data=used_data,
        instructions=suggestion.instructions.strip(),
        **macros.as_dict(),
    )
    db.add(meal)
    db.commit()
    db.refresh(meal)
    return meal


def regen_meals(
    db: Session,
    client: Any,
    work_items: list[MealWork],
    *,
    apply: bool = False,
    poll_interval: float = 30.0,
    poll_timeout: float = 24 * 60 * 60,
    max_retries: int = 1,
    sleep=None,
    clock=None,
) -> RegenReport:
    report = RegenReport()
    requests: list[BatchMessageRequest] = []
    pantry_by_user = {item.user_id: item.pantry for item in work_items}

    for item in work_items:
        if not item.pantry:
            report.skipped_empty_pantry.append(item.user_id)
            continue
        # Include the current meal when present so nightly regen asks for a
        # different dish. Still one batch request — not the calorie-retry loop.
        requests.append(
            build_meal_request(
                item.user_id,
                item.pantry,
                _previous_meal_for(db, item.user_id),
            )
        )

    if not requests:
        return report

    run_kw: dict[str, Any] = {
        "poll_interval": poll_interval,
        "poll_timeout": poll_timeout,
        "max_retries": max_retries,
        "chunk_size": MEAL_CHUNK_SIZE,
    }
    if sleep is not None:
        run_kw["sleep"] = sleep
    if clock is not None:
        run_kw["clock"] = clock

    try:
        outcome = run_message_batch(client, requests, **run_kw)
    except BatchRunInterrupted as exc:
        outcome = exc.outcome
        report.interrupted = str(exc)
    report.batch_ids.extend(outcome.submitted_batch_ids)

    for custom_id, item in outcome.succeeded.items():
        try:
            parsed = parse_meal_message(item.message)
            parsed.ingredients_used = _finalize_meal_amounts(
                pantry_by_user[custom_id], parsed.ingredients_used
            )
        except (MealGenerationError, json.JSONDecodeError, ValueError, KeyError) as exc:
            report.failed[custom_id] = str(exc)
            continue
        report.ok[custom_id] = parsed.name
        if apply:
            try:
                persist_generated_meal(db, custom_id, parsed)
                report.applied.append(custom_id)
            except Exception as exc:
                db.rollback()
                logger.exception("Failed to persist meal for user %s", custom_id)
                report.apply_failed[custom_id] = str(exc)

    for custom_id, item in outcome.failed.items():
        report.failed[custom_id] = item.error_message or item.error_type or item.type

    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Regenerate one meal per pantry through Anthropic Message Batches. "
            "Interactive generate stays on the sync Messages API."
        )
    )
    parser.add_argument(
        "--user-id",
        action="append",
        dest="user_ids",
        default=[],
        help="Limit to one user id (repeatable).",
    )
    parser.add_argument("--limit", type=int, help="Max users, most recently created first.")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Replace each user's current meals row (same as POST /api/meals/generate).",
    )
    parser.add_argument("--poll-interval", type=float, default=30.0)
    parser.add_argument("--poll-timeout", type=float, default=24 * 60 * 60)
    parser.add_argument("--max-retries", type=int, default=1)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args = build_parser().parse_args(argv)
    client = get_client()
    db = SessionLocal()
    try:
        work = load_user_pantries(
            db, user_ids=args.user_ids or None, limit=args.limit
        )
        report = regen_meals(
            db,
            client,
            work,
            apply=args.apply,
            poll_interval=args.poll_interval,
            poll_timeout=args.poll_timeout,
            max_retries=args.max_retries,
        )
        print(json.dumps(report.to_json(), indent=2))
        return report.exit_status()
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
