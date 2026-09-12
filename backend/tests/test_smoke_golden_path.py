"""
Golden-path smoke test: signup → upload receipt → confirm → generate meal → cookbook.

This is the QA gate for cost-optimization work. Every stage runs through the
real FastAPI routers with a prompt-routed fake Anthropic client, so the test
checks two things at once:

1. Product behavior: the flow still works end to end and the user-visible
   results (pantry contents, meal macros, cookbook entry, pantry deduction)
   are unchanged.
2. Cost baseline: how many Claude calls each stage makes and which model each
   stage uses. A cost PR that changes these numbers must update the baselines
   here *deliberately* (and explain why in the PR).

The fake dispatches on prompt text rather than call order, so it is safe with
the ThreadPoolExecutor used for nutrition enrichment and can be reused by
other tests.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from app.config import MEAL_ANTHROPIC_MODEL, RECEIPT_ANTHROPIC_MODEL

from tests.conftest import create_mock_anthropic_response


# ---------------------------------------------------------------------------
# Golden fixture data
# ---------------------------------------------------------------------------

GOLDEN_RECEIPT = {
    "store_name": "Golden Grocer",
    "items": [
        {
            "store_item_name": "CHKN BRST BNLS",
            "ingredient_name": "Chicken Breast",
            "is_food": True,
            "quantity": "2",
            "unit": "lb",
        },
        {
            "store_item_name": "LONG GRAIN WHT RICE",
            "ingredient_name": "White Rice",
            "is_food": True,
            "quantity": "2",
            "unit": "lb",
        },
        {
            "store_item_name": "PAPER BAG FEE",
            "ingredient_name": "Paper Bag",
            "is_food": False,
            "quantity": "1",
            "unit": "each",
        },
    ],
}

# Serving sizes are in oz so the pantry-unit (lb) math is exact:
#   chicken: 4 oz serving → 4 servings per lb
#   rice:    2 oz serving → 8 servings per lb
GOLDEN_NUTRITION = {
    "Chicken Breast": {
        "recognized": True,
        "quantity": "2",
        "unit": "lb",
        "serving_size": "4 oz (112g)",
        "servings_per_container": 4,
        "calories": 187,
        "protein_g": 35,
        "carbs_g": 0,
        "fat_g": 4,
        "fiber_g": 0,
        "sodium_mg": 84,
        "nutrition_notes": "USDA skinless chicken breast",
    },
    "White Rice": {
        "recognized": True,
        "quantity": "2",
        "unit": "lb",
        "serving_size": "2 oz (56g)",
        "servings_per_container": 8,
        "calories": 200,
        "protein_g": 4,
        "carbs_g": 44,
        "fat_g": 0.5,
        "fiber_g": 0.6,
        "sodium_mg": 0,
        "nutrition_notes": "USDA long-grain white rice, dry",
    },
}

# 8 oz chicken (2 servings × 187) + 4 oz rice (2 servings × 200) = 774 kcal,
# inside the 500–800 kcal target so generation needs exactly one Claude call.
GOLDEN_MEAL = {
    "name": "Chicken and Rice Bowl",
    "description": "Seared chicken breast over fluffy white rice for one.",
    "ingredients_used": [
        {"name": "Chicken Breast", "amount": "8 oz"},
        {"name": "White Rice", "amount": "4 oz"},
    ],
    "instructions": [
        "Rinse the rice and simmer it in water until tender.",
        "Season the chicken and sear it until cooked through.",
        "Slice the chicken and serve it over the rice.",
    ],
}

EXPECTED_MEAL_CALORIES = 774

# --- Cost baseline (Claude calls per stage) --------------------------------
# Receipt with N food items today costs:
#   upload:  1 receipt scan + N nutrition estimates
#   confirm: N pantry-match (canonicalize) + N × (unit check + nutrition + pantry match)
# For N = 2 that is 3 + 8 = 11 calls on the receipt model.
GOLDEN_FOOD_ITEM_COUNT = 2
EXPECTED_UPLOAD_CALLS = 1 + GOLDEN_FOOD_ITEM_COUNT
EXPECTED_CONFIRM_CALLS = GOLDEN_FOOD_ITEM_COUNT + 3 * GOLDEN_FOOD_ITEM_COUNT
EXPECTED_GENERATE_CALLS = 1
EXPECTED_COMPLETE_CALLS = 0  # skip_photo=true must not call Claude or OpenAI


# ---------------------------------------------------------------------------
# Prompt-routed fake Anthropic client
# ---------------------------------------------------------------------------


def _prompt_text(kwargs: dict) -> str:
    """Return the last user message as text (image blocks stripped)."""
    messages = kwargs.get("messages") or []
    if not messages:
        return ""
    content = messages[-1].get("content", "")
    if isinstance(content, str):
        return content
    return " ".join(
        block.get("text", "") for block in content if block.get("type") == "text"
    )


def _has_image_block(kwargs: dict) -> bool:
    messages = kwargs.get("messages") or []
    if not messages:
        return False
    content = messages[0].get("content", "")
    return isinstance(content, list) and any(
        block.get("type") in {"image", "document"} for block in content
    )


class FakeClaude:
    """Routes ``messages.create`` calls to canned JSON based on the prompt."""

    def __init__(
        self,
        receipt: dict,
        nutrition: dict[str, dict],
        meal: dict,
        *,
        meal_responses: list[dict] | None = None,
    ) -> None:
        self.receipt = receipt
        self.nutrition = nutrition
        self.meal_queue = list(meal_responses) if meal_responses else [meal]
        self.calls: list[dict] = []

    def _respond(self, **kwargs) -> Mock:
        self.calls.append(kwargs)
        text = _prompt_text(kwargs)

        if _has_image_block(kwargs) or "Analyze this grocery store receipt" in text:
            return create_mock_anthropic_response(json.dumps(self.receipt))

        if "Estimate nutritional facts" in text:
            for name, estimate in self.nutrition.items():
                if f"- Item: {name}" in text:
                    return create_mock_anthropic_response(
                        json.dumps({"recognized": True, **estimate})
                    )
            raise AssertionError(f"No golden nutrition for prompt:\n{text}")

        if "Assess whether this grocery purchase unit" in text:
            return create_mock_anthropic_response(
                json.dumps({"unit_plausible": True, "unit_warning": None})
            )

        if "Match an incoming grocery item" in text:
            return create_mock_anthropic_response(
                json.dumps(
                    {"match_id": None, "ambiguous": False, "canonical_name": None}
                )
            )

        if "helpful home chef" in text or "Propose a" in text or "Suggest a" in text:
            if not self.meal_queue:
                raise AssertionError("Meal generator asked for more meals than scripted.")
            payload = self.meal_queue.pop(0)
            return create_mock_anthropic_response(json.dumps(payload))

        raise AssertionError(f"Unrecognized Claude prompt:\n{text[:400]}")

    def build_client(self, *args, **kwargs) -> Mock:
        client = Mock()
        client.messages.create.side_effect = self._respond
        return client

    def calls_since(self, start: int) -> list[dict]:
        return self.calls[start:]


@pytest.fixture
def fake_claude(monkeypatch, tmp_path):
    """Patch Anthropic everywhere, provide a key, and isolate upload dirs."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "receipts"))
    monkeypatch.setenv("MEAL_UPLOAD_DIR", str(tmp_path / "meals"))
    monkeypatch.setenv("COOKBOOK_UPLOAD_DIR", str(tmp_path / "cookbook"))

    fake = FakeClaude(GOLDEN_RECEIPT, GOLDEN_NUTRITION, GOLDEN_MEAL)
    # receipt_analyzer, meal_generator and meal_image all do
    # ``anthropic.Anthropic(api_key=...)`` at call time, so one patch covers all.
    with patch("anthropic.Anthropic", side_effect=fake.build_client):
        yield fake


def _signup(client, email: str = "qa@example.com") -> dict:
    response = client.post(
        "/api/auth/register",
        json={"name": "QA Smoke", "email": email, "password": "Sm0ke-Test!"},
    )
    assert response.status_code == 201, response.text
    return response.json()["user"]


def _upload_receipt(client, image_path: Path) -> dict:
    with open(image_path, "rb") as handle:
        response = client.post(
            "/api/receipts/upload",
            files={"file": ("golden.jpg", handle, "image/jpeg")},
            data={"manual_items": "[]"},
        )
    assert response.status_code == 201, response.text
    return response.json()


class TestGoldenPathSmoke:
    def test_signup_upload_generate_cookbook(
        self, fake_claude: FakeClaude, client, mock_receipt_image
    ):
        # --- Stage 1: signup (no AI) -------------------------------------
        user = _signup(client)
        assert user["email"] == "qa@example.com"
        assert client.get("/api/auth/me").status_code == 200
        assert len(fake_claude.calls) == 0

        # --- Stage 2: upload receipt → draft review ---------------------
        receipt = _upload_receipt(client, mock_receipt_image)
        upload_calls = fake_claude.calls_since(0)

        assert receipt["analysis_status"] == "pending_review"
        assert receipt["store_name"] == "Golden Grocer"
        drafts = {item["ingredient_name"]: item for item in receipt["draft_items"]}
        assert set(drafts) == {"Chicken Breast", "White Rice"}, "non-food rows must be dropped"
        assert drafts["Chicken Breast"]["calories"] == 187
        assert drafts["White Rice"]["serving_size"] == "2 oz (56g)"

        assert len(upload_calls) == EXPECTED_UPLOAD_CALLS
        assert {c["model"] for c in upload_calls} == {RECEIPT_ANTHROPIC_MODEL}

        # --- Stage 3: confirm → pantry ----------------------------------
        confirm_start = len(fake_claude.calls)
        response = client.post(
            f"/api/receipts/{receipt['id']}/confirm",
            json={"items": receipt["draft_items"]},
        )
        assert response.status_code == 200, response.text
        confirmed = response.json()
        confirm_calls = fake_claude.calls_since(confirm_start)

        assert confirmed["analysis_status"] == "completed"
        assert len(confirmed["ingredients"]) == 2

        pantry = {item["name"]: item for item in client.get("/api/ingredients").json()}
        assert set(pantry) == {"Chicken Breast", "White Rice"}
        assert pantry["Chicken Breast"]["quantity"] == "2"
        assert pantry["Chicken Breast"]["unit"] == "lb"
        # servings_per_container is recomputed from serving size vs pantry unit
        assert pantry["Chicken Breast"]["servings_per_container"] == 4
        assert pantry["White Rice"]["servings_per_container"] == 8

        assert len(confirm_calls) == EXPECTED_CONFIRM_CALLS
        assert {c["model"] for c in confirm_calls} == {RECEIPT_ANTHROPIC_MODEL}

        # --- Stage 4: generate meal --------------------------------------
        generate_start = len(fake_claude.calls)
        response = client.post("/api/meals/generate", json={})
        assert response.status_code == 201, response.text
        meal = response.json()
        generate_calls = fake_claude.calls_since(generate_start)

        assert meal["name"] == "Chicken and Rice Bowl"
        assert meal["calories"] == EXPECTED_MEAL_CALORIES
        assert meal["protein_g"] == pytest.approx(78.0)
        assert meal["photo_url"] is None
        # Amounts are clamped/normalized to pantry units (8 oz → 0.5 lb).
        assert "Chicken Breast: 0.5 lb" in meal["ingredients_used"]
        assert "White Rice: 0.25 lb" in meal["ingredients_used"]
        # Instructions are numbered, one step per line.
        assert meal["instructions"].splitlines()[0].startswith("1. ")
        assert len(meal["instructions"].splitlines()) == 3

        assert len(generate_calls) == EXPECTED_GENERATE_CALLS
        assert {c["model"] for c in generate_calls} == {MEAL_ANTHROPIC_MODEL}
        # Pantry maximums must reach the prompt so the model can respect them.
        prompt = _prompt_text(generate_calls[0])
        assert "maximum available: 2 lb (do not exceed)" in prompt

        # --- Stage 5: add to cookbook (skip photo) ----------------------
        complete_start = len(fake_claude.calls)
        response = client.post(f"/api/meals/{meal['id']}/complete?skip_photo=true")
        assert response.status_code == 200, response.text
        completed = response.json()
        assert completed["name"] == "Chicken and Rice Bowl"
        assert len(fake_claude.calls_since(complete_start)) == EXPECTED_COMPLETE_CALLS

        cookbook = client.get("/api/cookbook").json()
        assert len(cookbook) == 1
        entry = cookbook[0]
        assert entry["title"] == "Chicken and Rice Bowl"
        assert entry["calories"] == EXPECTED_MEAL_CALORIES
        assert entry["photo_url"] is None
        assert entry["instructions"] == meal["instructions"]

        # The draft meal is consumed once it lands in the cookbook.
        assert client.get("/api/meals").json() == []
        assert client.get(f"/api/meals/{meal['id']}").status_code == 404

        # Pantry is deducted by the amounts actually used.
        pantry = {item["name"]: item for item in client.get("/api/ingredients").json()}
        assert pantry["Chicken Breast"]["quantity"] == "1.5"
        assert pantry["White Rice"]["quantity"] == "1.75"

        # --- Whole-flow cost baseline ------------------------------------
        assert len(fake_claude.calls) == (
            EXPECTED_UPLOAD_CALLS
            + EXPECTED_CONFIRM_CALLS
            + EXPECTED_GENERATE_CALLS
            + EXPECTED_COMPLETE_CALLS
        )

    def test_complete_without_photo_and_without_openai_key_fails_cleanly(
        self, fake_claude: FakeClaude, client, mock_receipt_image
    ):
        """No OpenAI key + no photo + no skip must not create a cookbook entry."""
        _signup(client, email="qa2@example.com")
        receipt = _upload_receipt(client, mock_receipt_image)
        assert (
            client.post(
                f"/api/receipts/{receipt['id']}/confirm",
                json={"items": receipt["draft_items"]},
            ).status_code
            == 200
        )
        meal = client.post("/api/meals/generate", json={}).json()

        response = client.post(f"/api/meals/{meal['id']}/complete")
        assert response.status_code == 422
        assert "OpenAI API key" in response.json()["detail"]

        assert client.get("/api/cookbook").json() == []
        # Meal is still available so the user can retry / upload a photo.
        assert client.get(f"/api/meals/{meal['id']}").status_code == 200
        pantry = {item["name"]: item for item in client.get("/api/ingredients").json()}
        assert pantry["Chicken Breast"]["quantity"] == "2", "no deduction on failure"

    def test_generate_meal_with_empty_pantry_makes_no_ai_calls(
        self, fake_claude: FakeClaude, client
    ):
        _signup(client, email="qa3@example.com")
        response = client.post("/api/meals/generate", json={})
        assert response.status_code == 422
        assert "Add ingredients" in response.json()["detail"]
        assert fake_claude.calls == []
