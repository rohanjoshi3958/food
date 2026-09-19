"""Message Batches helper + offline jobs (FOOD-57).

Mocks ``client.messages.batches``. Interactive HTTP paths must not import
this stack; they stay on ``create_cached_message`` / ``messages.create``.
"""

from __future__ import annotations

import inspect
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.config import MEAL_ANTHROPIC_MODEL, RECEIPT_ANTHROPIC_MODEL
from app.jobs.regen_meals import (
    MealWork,
    build_meal_request,
    meal_generation_system_prefix,
    persist_generated_meal,
    regen_meals,
)
from app.jobs.reprocess_receipts import (
    ReprocessReport,
    apply_parsed_receipt,
    build_nutrition_request,
    build_nutrition_work,
    build_vision_request,
    nutrition_custom_id,
    reprocess_receipts,
)
from app.models import Ingredient, Meal, Receipt
from app.services import meal_generator, meal_image, receipt_analyzer
from app.services.anthropic_batch import (
    CUSTOM_ID_RE,
    BatchItemResult,
    BatchMessageRequest,
    BatchRunInterrupted,
    BatchSubmitError,
    BatchTimeoutError,
    collect_batch_results,
    poll_message_batch,
    run_message_batch,
    submit_message_batch,
)
from app.services.anthropic_cache import CACHE_TTL_1H, EPHEMERAL_1H_CACHE_CONTROL
from app.services.meal_generator import (
    MEAL_CALORIE_MAX,
    MEAL_CALORIE_MIN,
    PreviousMealTurn,
    generate_meal_from_ingredients,
)
from app.services.receipt_analyzer import (
    NUTRITION_ESTIMATE_PROMPT,
    RECEIPT_ANALYSIS_PROMPT,
    ParsedReceipt,
    ParsedReceiptItem,
    ReceiptAnalysisError,
    parse_nutrition_message,
)
from tests.conftest import create_mock_anthropic_response


def _request(custom_id: str, tail: str = "tail") -> BatchMessageRequest:
    return BatchMessageRequest(
        custom_id=custom_id,
        call_site="receipt.analyze_image",
        model=RECEIPT_ANTHROPIC_MODEL,
        max_tokens=16,
        system_prefix=RECEIPT_ANALYSIS_PROMPT,
        messages=[{"role": "user", "content": tail}],
    )


def _succeeded(custom_id: str, text: str) -> SimpleNamespace:
    return SimpleNamespace(
        custom_id=custom_id,
        result=SimpleNamespace(
            type="succeeded",
            message=create_mock_anthropic_response(text),
            error=None,
        ),
    )


def _failed(
    custom_id: str,
    result_type: str,
    error_type: str | None = None,
    error_message: str | None = None,
) -> SimpleNamespace:
    error = None
    if error_type or error_message:
        error = SimpleNamespace(type=error_type, message=error_message)
    return SimpleNamespace(
        custom_id=custom_id,
        result=SimpleNamespace(type=result_type, message=None, error=error),
    )


class ScriptedBatches:
    """In-memory Message Batches surface. One scripted result list per create()."""

    def __init__(self, scripts: list, *, in_progress_polls: int = 0):
        self.scripts = list(scripts)
        self.creates: list[list] = []
        self.retrieve_calls: list[str] = []
        self.in_progress_polls = in_progress_polls

    def create(self, requests):
        batch_id = f"msgbatch_{len(self.creates)}"
        self.creates.append(list(requests))
        return SimpleNamespace(id=batch_id, processing_status="in_progress")

    def retrieve(self, batch_id):
        prior = sum(1 for item in self.retrieve_calls if item == batch_id)
        self.retrieve_calls.append(batch_id)
        status = "ended" if prior >= self.in_progress_polls else "in_progress"
        return SimpleNamespace(id=batch_id, processing_status=status)

    def results(self, batch_id):
        index = int(str(batch_id).rsplit("_", 1)[1])
        return self.scripts[index]


def _client(scripts: list, **kwargs) -> tuple[SimpleNamespace, ScriptedBatches]:
    batches = ScriptedBatches(scripts, **kwargs)
    client = SimpleNamespace(messages=SimpleNamespace(batches=batches))
    return client, batches


def _instant_clock(values: list[float]):
    iterator = iter(values)

    def clock() -> float:
        try:
            return next(iterator)
        except StopIteration:
            return values[-1]

    return clock


class TestBatchRequestPayload:
    def test_to_api_request_uses_1h_cache_and_custom_id(self):
        request = _request("receipt-1", "variable tail")
        payload = request.to_api_request()

        assert payload["custom_id"] == "receipt-1"
        assert CUSTOM_ID_RE.fullmatch(payload["custom_id"])
        params = payload["params"]
        assert params["model"] == RECEIPT_ANTHROPIC_MODEL
        assert params["max_tokens"] == 16
        assert params["messages"] == [{"role": "user", "content": "variable tail"}]
        system = params["system"]
        assert len(system) == 1
        assert system[0]["text"] == RECEIPT_ANALYSIS_PROMPT
        assert system[0]["cache_control"] == EPHEMERAL_1H_CACHE_CONTROL
        assert "variable tail" not in system[0]["text"]

    def test_rejects_invalid_custom_id_and_zero_tokens(self):
        with pytest.raises(BatchSubmitError, match="custom_id"):
            _request("bad id with spaces").to_api_request()
        with pytest.raises(BatchSubmitError, match="max_tokens"):
            BatchMessageRequest(
                custom_id="ok",
                call_site="x",
                model="m",
                max_tokens=0,
                system_prefix="p",
                messages=[],
            ).to_api_request()


class TestSubmitPollCollect:
    def test_submit_rejects_empty_and_duplicate_ids(self):
        client, _batches = _client([])
        with pytest.raises(BatchSubmitError, match="empty"):
            submit_message_batch(client, [])
        with pytest.raises(BatchSubmitError, match="duplicate"):
            submit_message_batch(client, [_request("a"), _request("a")])

    def test_poll_waits_until_ended(self):
        sleeps: list[float] = []
        client, batches = _client([[]], in_progress_polls=2)
        batch = poll_message_batch(
            client, "msgbatch_0", interval=0.5, timeout=60, sleep=sleeps.append
        )
        assert batch.processing_status == "ended"
        assert sleeps == [0.5, 0.5]
        assert batches.retrieve_calls == ["msgbatch_0"] * 3

    def test_poll_timeout_includes_batch_id(self):
        client, _batches = _client([[]], in_progress_polls=99)
        with pytest.raises(BatchTimeoutError, match="msgbatch_0") as exc:
            poll_message_batch(
                client,
                "msgbatch_0",
                interval=1,
                timeout=10,
                sleep=lambda _: None,
                clock=_instant_clock([0.0, 5.0, 10.0, 11.0]),
            )
        assert exc.value.batch_id == "msgbatch_0"
        assert exc.value.last_status == "in_progress"

    def test_collect_maps_out_of_order_rows_by_custom_id(self):
        rows = [
            {"custom_id": "b", "result": {"type": "succeeded", "message": {"id": "m-b"}}},
            {"custom_id": "a", "result": {"type": "errored", "error": {"type": "api_error"}}},
        ]
        client = SimpleNamespace(
            messages=SimpleNamespace(
                batches=SimpleNamespace(results=lambda _id: rows)
            )
        )
        mapped = collect_batch_results(client, "msgbatch_x")
        assert set(mapped) == {"a", "b"}
        assert mapped["b"].succeeded
        assert mapped["b"].message == {"id": "m-b"}
        assert mapped["a"].type == "errored"
        assert mapped["a"].error_type == "api_error"
        assert mapped["a"].retryable is True


class TestRunMessageBatchRetry:
    def test_empty_requests_do_not_call_api(self):
        client, batches = _client([])
        outcome = run_message_batch(client, [])
        assert outcome.succeeded == {}
        assert outcome.failed == {}
        assert batches.creates == []

    def test_partial_failure_retries_expired_not_invalid_request(self):
        # First batch: a succeeds, b expired (retry), c invalid (do not retry).
        # Second batch: only b, and it succeeds.
        first = [
            _succeeded("a", "ok-a"),
            _failed("b", "expired"),
            _failed("c", "errored", "invalid_request_error", "bad params"),
        ]
        second = [_succeeded("b", "ok-b")]
        client, batches = _client([first, second])

        outcome = run_message_batch(
            client,
            [_request("a"), _request("b"), _request("c")],
            max_retries=1,
            sleep=lambda _: None,
        )

        assert set(outcome.succeeded) == {"a", "b"}
        assert set(outcome.failed) == {"c"}
        assert outcome.failed["c"].error_type == "invalid_request_error"
        assert outcome.retry_rounds == 1
        assert len(batches.creates) == 2
        first_ids = [row["custom_id"] for row in batches.creates[0]]
        second_ids = [row["custom_id"] for row in batches.creates[1]]
        assert first_ids == ["a", "b", "c"]
        assert second_ids == ["b"]
        # 1h TTL is on every submitted params object, including the retry.
        for created in batches.creates:
            for row in created:
                assert (
                    row["params"]["system"][0]["cache_control"]
                    == EPHEMERAL_1H_CACHE_CONTROL
                )

    def test_server_error_is_retryable_missing_id_is_too(self):
        first = [_failed("x", "errored", "api_error", "boom")]
        # Second script omits x entirely → missing_result, but retries are spent.
        client, _batches = _client([first, []])
        outcome = run_message_batch(
            client, [_request("x")], max_retries=1, sleep=lambda _: None
        )
        assert "x" in outcome.failed
        assert outcome.failed["x"].error_type == "missing_result"
        assert outcome.retry_rounds == 1

    def test_canceled_retries_then_succeeds(self):
        client, _batches = _client(
            [[_failed("z", "canceled")], [_succeeded("z", "done")]]
        )
        outcome = run_message_batch(
            client, [_request("z")], max_retries=1, sleep=lambda _: None
        )
        assert set(outcome.succeeded) == {"z"}
        assert outcome.failed == {}

    def test_result_from_sdk_nested_error_object(self):
        row = {
            "custom_id": "n1",
            "result": {
                "type": "errored",
                "error": {"error": {"type": "overloaded_error", "message": "busy"}},
            },
        }
        item = BatchItemResult.from_sdk(row)
        assert item.retryable is True
        assert item.error_type == "overloaded_error"
        assert item.error_message == "busy"

    def test_nested_error_envelope_invalid_request_is_not_retried(self):
        row = {
            "custom_id": "x",
            "result": {
                "type": "errored",
                "error": {
                    "type": "error",
                    "error": {
                        "type": "invalid_request_error",
                        "message": "bad params",
                    },
                },
            },
        }
        item = BatchItemResult.from_sdk(row)
        assert item.error_type == "invalid_request_error"
        assert item.retryable is False

        client, batches = _client([[row]])
        outcome = run_message_batch(
            client, [_request("x")], max_retries=1, sleep=lambda _: None
        )
        assert set(outcome.failed) == {"x"}
        assert outcome.failed["x"].error_type == "invalid_request_error"
        assert len(batches.creates) == 1

    def test_billing_error_is_not_retried(self):
        row = {
            "custom_id": "bill",
            "result": {
                "type": "errored",
                "error": {"type": "billing_error", "message": "payment"},
            },
        }
        assert BatchItemResult.from_sdk(row).retryable is False
        client, batches = _client([[row]])
        outcome = run_message_batch(
            client, [_request("bill")], max_retries=1, sleep=lambda _: None
        )
        assert "bill" in outcome.failed
        assert len(batches.creates) == 1

    def test_later_chunk_failure_preserves_earlier_successes(self):
        class BoomAfterFirst:
            def __init__(self):
                self.creates: list = []

            def create(self, requests):
                if self.creates:
                    raise RuntimeError("chunk 2 exploded")
                self.creates.append(list(requests))
                return SimpleNamespace(id="msgbatch_0")

            def retrieve(self, batch_id):
                return SimpleNamespace(id=batch_id, processing_status="ended")

            def results(self, _batch_id):
                return [_succeeded("a", "ok-a")]

        client = SimpleNamespace(messages=SimpleNamespace(batches=BoomAfterFirst()))
        with pytest.raises(BatchRunInterrupted) as exc:
            run_message_batch(
                client,
                [_request("a"), _request("b")],
                chunk_size=1,
                sleep=lambda _: None,
            )
        assert set(exc.value.outcome.succeeded) == {"a"}
        assert exc.value.outcome.submitted_batch_ids == ["msgbatch_0"]


class TestInteractivePathsUnchanged:
    def test_interactive_modules_do_not_import_batch_or_jobs(self):
        for module in (receipt_analyzer, meal_generator, meal_image):
            source = inspect.getsource(module)
            assert "anthropic_batch" not in source
            assert "messages.batches" not in source
            assert "app.jobs" not in source

    def test_routers_do_not_import_batch(self):
        from app.routers import meals, receipts

        for module in (receipts, meals):
            source = inspect.getsource(module)
            assert "anthropic_batch" not in source
            assert "app.jobs" not in source

    def test_sync_meal_generate_still_calls_messages_create(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        client = MagicMock()
        client.messages.create.return_value = create_mock_anthropic_response(
            json.dumps(
                {
                    "name": "Oat Bowl",
                    "description": "A bowl.",
                    "ingredients_used": [{"name": "Oats", "amount": "100 g"}],
                    "instructions": ["Cook."],
                }
            )
        )
        monkeypatch.setattr(
            meal_generator.anthropic, "Anthropic", lambda *a, **k: client
        )
        monkeypatch.setattr(
            meal_generator, "_estimate_meal_calories", lambda *a, **k: 650
        )
        pantry = [
            Ingredient(
                id="ing-1",
                user_id="user-1",
                name="Oats",
                quantity="500",
                unit="g",
                serving_size="50 g",
                servings_per_container=10,
                calories=150,
            )
        ]
        meal = generate_meal_from_ingredients(pantry)
        assert meal.name == "Oat Bowl"
        client.messages.create.assert_called_once()
        kwargs = client.messages.create.call_args.kwargs
        assert kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}
        assert "ttl" not in kwargs["system"][0]["cache_control"]


class TestReceiptReprocessJob:
    def _receipt(self, test_db, test_user, tmp_path, receipt_id: str = "rec-aaaa-1"):
        path = tmp_path / f"{receipt_id}.png"
        path.write_bytes(b"\x89PNG fake")
        row = Receipt(
            id=receipt_id,
            user_id=test_user.id,
            filename=str(path),
            original_name="r.png",
            analysis_status="completed",
            draft_items=[{"ingredient_name": "keep-me"}],
        )
        test_db.add(row)
        test_db.commit()
        return row

    def test_vision_request_payload(self, test_db, test_user, tmp_path):
        receipt = self._receipt(test_db, test_user, tmp_path)
        request = build_vision_request(receipt)
        assert request is not None
        payload = request.to_api_request()
        assert payload["custom_id"] == receipt.id
        params = payload["params"]
        assert params["model"] == RECEIPT_ANTHROPIC_MODEL
        assert params["system"][0]["cache_control"]["ttl"] == CACHE_TTL_1H
        assert params["system"][0]["text"] == RECEIPT_ANALYSIS_PROMPT
        content = params["messages"][0]["content"]
        assert content[0]["type"] == "image"
        assert content[0]["source"]["data"] not in params["system"][0]["text"]

    def test_missing_file_returns_none(self, test_db, test_user):
        row = Receipt(
            id="rec-missing",
            user_id=test_user.id,
            filename="/tmp/does-not-exist-food-57.png",
            original_name="gone.png",
            analysis_status="completed",
        )
        test_db.add(row)
        test_db.commit()
        assert build_vision_request(row) is None

    def test_nutrition_custom_id_maps_back_to_item(self):
        parsed = ParsedReceipt(
            store_name="Shop",
            items=[
                ParsedReceiptItem(
                    store_item_name="BAG",
                    ingredient_name="Bag",
                    is_food=False,
                ),
                ParsedReceiptItem(
                    store_item_name="OATS",
                    ingredient_name="Oats",
                    is_food=True,
                    quantity="1",
                    unit="each",
                ),
            ],
        )
        work = build_nutrition_work("rec-1", parsed)
        assert len(work) == 1
        assert work[0].item_index == 1
        assert work[0].custom_id == nutrition_custom_id("rec-1", 1)
        payload = build_nutrition_request(work[0]).to_api_request()
        assert payload["params"]["system"][0]["text"] == NUTRITION_ESTIMATE_PROMPT
        assert payload["params"]["system"][0]["cache_control"]["ttl"] == "1h"
        assert "Oats" in payload["params"]["messages"][0]["content"]
        assert "Oats" not in payload["params"]["system"][0]["text"]

    def test_reprocess_apply_writes_analysis_result_only(
        self, test_db, test_user, tmp_path
    ):
        receipt = self._receipt(test_db, test_user, tmp_path, "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        vision_json = json.dumps(
            {
                "store_name": "Batch Mart",
                "items": [
                    {
                        "store_item_name": "OATS",
                        "ingredient_name": "Oats",
                        "is_food": True,
                        "quantity": "1",
                        "unit": "each",
                    }
                ],
            }
        )
        nutrition_json = json.dumps(
            {
                "recognized": True,
                "quantity": "1",
                "unit": "each",
                "serving_size": "40 g",
                "servings_per_container": 10,
                "calories": 150,
            }
        )
        nut_id = nutrition_custom_id(receipt.id, 0)
        client, _batches = _client(
            [
                [_succeeded(receipt.id, vision_json)],
                [_succeeded(nut_id, nutrition_json)],
            ]
        )

        report = reprocess_receipts(
            test_db,
            client,
            [receipt],
            apply=True,
            sleep=lambda _: None,
        )

        assert receipt.id in report.applied
        test_db.refresh(receipt)
        assert receipt.analysis_result["store_name"] == "Batch Mart"
        assert receipt.analysis_result["items"][0]["calories"] == 150
        assert receipt.analysis_status == "completed"
        assert receipt.draft_items == [{"ingredient_name": "keep-me"}]

    def test_apply_parsed_receipt_helper(self, test_db, test_user, tmp_path):
        receipt = self._receipt(test_db, test_user, tmp_path)
        parsed = ParsedReceipt(store_name="X", items=[])
        apply_parsed_receipt(test_db, receipt.id, parsed)
        test_db.refresh(receipt)
        assert receipt.analysis_result["store_name"] == "X"

    def test_exit_status_includes_nutrition_and_apply_failures(self):
        assert ReprocessReport().exit_status() == 0
        assert ReprocessReport(nutrition_failed={"n": "boom"}).exit_status() == 1
        assert ReprocessReport(vision_failed={"v": "no"}).exit_status() == 1
        assert ReprocessReport(apply_failed={"a": "db"}).exit_status() == 1
        assert ReprocessReport(interrupted="chunk 2").exit_status() == 1

    def test_apply_continues_after_one_persist_failure(
        self, test_db, test_user, tmp_path, monkeypatch
    ):
        first = self._receipt(test_db, test_user, tmp_path, "rec-one")
        second = self._receipt(test_db, test_user, tmp_path, "rec-two")
        vision_json = json.dumps(
            {
                "store_name": "Shop",
                "items": [
                    {
                        "store_item_name": "OATS",
                        "ingredient_name": "Oats",
                        "is_food": True,
                    }
                ],
            }
        )
        real_apply = apply_parsed_receipt

        def flaky(db, receipt_id, parsed):
            if receipt_id == first.id:
                raise RuntimeError("db down")
            return real_apply(db, receipt_id, parsed)

        monkeypatch.setattr(
            "app.jobs.reprocess_receipts.apply_parsed_receipt", flaky
        )
        client, _batches = _client(
            [[_succeeded(first.id, vision_json), _succeeded(second.id, vision_json)]]
        )
        report = reprocess_receipts(
            test_db,
            client,
            [first, second],
            apply=True,
            skip_nutrition=True,
            sleep=lambda _: None,
        )
        assert first.id in report.apply_failed
        assert second.id in report.applied
        assert report.exit_status() == 1
        test_db.refresh(second)
        assert second.analysis_result["store_name"] == "Shop"

    def test_nutrition_non_object_payload_is_analysis_error(self):
        with pytest.raises(ReceiptAnalysisError):
            parse_nutrition_message(
                create_mock_anthropic_response("[1, 2]"),
                ingredient_name="Oats",
                quantity="1",
                unit="each",
            )


class TestMealRegenJob:
    def test_first_turn_payload_keeps_pantry_after_breakpoint(self):
        pantry = [
            Ingredient(
                id="ing-1",
                user_id="user-1",
                name="Rolled Oats",
                quantity="500",
                unit="g",
                serving_size="50 g",
                servings_per_container=10,
                calories=150,
            )
        ]
        request = build_meal_request("user-1", pantry)
        payload = request.to_api_request()
        assert payload["custom_id"] == "user-1"
        params = payload["params"]
        assert params["model"] == MEAL_ANTHROPIC_MODEL
        assert params["system"][0]["cache_control"] == EPHEMERAL_1H_CACHE_CONTROL
        assert params["system"][0]["text"] == meal_generation_system_prefix()
        assert "Rolled Oats" not in params["system"][0]["text"]
        assert params["messages"][0]["content"].startswith("Available ingredients:")
        assert "Rolled Oats" in params["messages"][0]["content"]

    def test_previous_meal_appends_follow_up_turn(self):
        pantry = [
            Ingredient(
                id="ing-1",
                user_id="user-1",
                name="Oats",
                quantity="500",
                unit="g",
            )
        ]
        previous = PreviousMealTurn(name="Oat Bowl", description="A bowl.")
        request = build_meal_request("user-1", pantry, previous)
        roles = [message["role"] for message in request.messages]
        assert roles == ["user", "assistant", "user"]
        assert "Oat Bowl" in request.messages[1]["content"]
        assert f"{MEAL_CALORIE_MIN}" in request.messages[2]["content"]
        assert f"{MEAL_CALORIE_MAX}" in request.messages[2]["content"]

    def test_regen_apply_writes_meal_row(self, test_db, test_user):
        ingredient = Ingredient(
            user_id=test_user.id,
            name="Oats",
            quantity="500",
            unit="g",
            serving_size="50 g",
            servings_per_container=10,
            calories=150,
        )
        test_db.add(ingredient)
        test_db.commit()

        meal_json = json.dumps(
            {
                "name": "Batch Oats",
                "description": "Overnight oats.",
                "ingredients_used": [{"name": "Oats", "amount": "100 g"}],
                "instructions": ["Stir.", "Chill."],
            }
        )
        client, _batches = _client([[_succeeded(test_user.id, meal_json)]])

        report = regen_meals(
            test_db,
            client,
            [MealWork(custom_id=test_user.id, user_id=test_user.id, pantry=[ingredient])],
            apply=True,
            sleep=lambda _: None,
        )
        assert report.ok[test_user.id] == "Batch Oats"
        assert test_user.id in report.applied
        stored = test_db.query(Meal).filter(Meal.user_id == test_user.id).one()
        assert stored.name == "Batch Oats"

    def test_persist_helper_replaces_existing_meal(self, test_db, test_user):
        from app.services.meal_generator import GeneratedMeal, MealIngredientUse

        test_db.add(
            Ingredient(
                user_id=test_user.id,
                name="Oats",
                quantity="500",
                unit="g",
                calories=150,
            )
        )
        test_db.add(Meal(user_id=test_user.id, name="Old"))
        test_db.commit()
        persist_generated_meal(
            test_db,
            test_user.id,
            GeneratedMeal(
                name="New",
                description="d",
                ingredients_used=[MealIngredientUse(name="Oats", amount="50 g")],
                instructions="1. Eat.",
            ),
        )
        names = [row.name for row in test_db.query(Meal).all()]
        assert names == ["New"]

    def test_apply_continues_after_one_user_persist_failure(
        self, test_db, test_user, monkeypatch
    ):
        from app.auth_utils import hash_password
        from app.models import User

        other = User(
            email="other-batch@example.com",
            name="Other",
            password=hash_password("pw12345678"),
        )
        test_db.add(other)
        test_db.commit()
        test_db.refresh(other)

        for user in (test_user, other):
            test_db.add(
                Ingredient(
                    user_id=user.id,
                    name="Oats",
                    quantity="500",
                    unit="g",
                    calories=150,
                )
            )
        test_db.commit()

        meal_json = json.dumps(
            {
                "name": "Batch Oats",
                "description": "Overnight oats.",
                "ingredients_used": [{"name": "Oats", "amount": "100 g"}],
                "instructions": ["Stir."],
            }
        )
        real = persist_generated_meal

        def flaky(db, user_id, suggestion):
            if user_id == test_user.id:
                raise RuntimeError("persist failed")
            return real(db, user_id, suggestion)

        monkeypatch.setattr("app.jobs.regen_meals.persist_generated_meal", flaky)
        client, _batches = _client(
            [[_succeeded(test_user.id, meal_json), _succeeded(other.id, meal_json)]]
        )
        oats = {
            user.id: test_db.query(Ingredient).filter(Ingredient.user_id == user.id).all()
            for user in (test_user, other)
        }
        report = regen_meals(
            test_db,
            client,
            [
                MealWork(
                    custom_id=test_user.id,
                    user_id=test_user.id,
                    pantry=oats[test_user.id],
                ),
                MealWork(custom_id=other.id, user_id=other.id, pantry=oats[other.id]),
            ],
            apply=True,
            sleep=lambda _: None,
        )
        assert test_user.id in report.apply_failed
        assert other.id in report.applied
        assert report.exit_status() == 1
