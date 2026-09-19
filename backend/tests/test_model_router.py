"""Model routing policy + helper (FOOD-58).

Pins three things:

1. With ``MODEL_ROUTING_ENABLED`` off (the default) every call site resolves
   to exactly the model it used before routing existed, and no call site ever
   makes an escalation call. This is the "no behaviour change" guarantee.
2. With the flag on, the written policy applies and low-confidence results
   escalate to the stronger tier exactly once.
3. Every ``call_site=`` string in the services has a policy entry, and the
   policy table stays consistent with the shared model constants.
"""

from __future__ import annotations

import inspect
import json
import logging
import re

import pytest

from app.config import (
    HAIKU_ANTHROPIC_MODEL,
    MEAL_ANTHROPIC_MODEL,
    OPUS_ANTHROPIC_MODEL,
    RECEIPT_ANTHROPIC_MODEL,
    RECEIPT_HAIKU_MODEL,
    SONNET_ANTHROPIC_MODEL,
    get_settings,
)
from app.services import meal_generator, meal_image, receipt_analyzer
from app.services.model_router import (
    LOW_CONFIDENCE_THRESHOLD,
    POLICY,
    TIER_MODELS,
    Tier,
    escalation_for,
    forced_model,
    policy_for,
    route_model,
    routing_enabled,
)
from app.services.receipt_analyzer import check_ingredient_unit, match_ingredient_to_pantry
from tests.conftest import create_mock_anthropic_response

LIVE_CALL_SITES = {
    "receipt.analyze_image": RECEIPT_ANTHROPIC_MODEL,
    "receipt.nutrition_estimate": RECEIPT_ANTHROPIC_MODEL,
    "receipt.unit_check": RECEIPT_ANTHROPIC_MODEL,
    "receipt.pantry_match": RECEIPT_ANTHROPIC_MODEL,
    "meal.generate": MEAL_ANTHROPIC_MODEL,
    "meal.image_prompt": MEAL_ANTHROPIC_MODEL,
}


@pytest.fixture
def routing_on(monkeypatch):
    monkeypatch.setenv("MODEL_ROUTING_ENABLED", "true")
    assert routing_enabled()


@pytest.fixture
def routing_off(monkeypatch):
    monkeypatch.setenv("MODEL_ROUTING_ENABLED", "false")
    assert not routing_enabled()


class TestSharedConstants:
    def test_tier_ids_are_anthropic_api_aliases(self):
        assert OPUS_ANTHROPIC_MODEL == "claude-opus-5"
        assert SONNET_ANTHROPIC_MODEL == "claude-sonnet-5"
        assert HAIKU_ANTHROPIC_MODEL == "claude-haiku-4-5"
        assert TIER_MODELS == {
            Tier.HAIKU: HAIKU_ANTHROPIC_MODEL,
            Tier.SONNET: SONNET_ANTHROPIC_MODEL,
            Tier.OPUS: OPUS_ANTHROPIC_MODEL,
        }

    def test_pipeline_defaults_are_unchanged(self):
        assert RECEIPT_ANTHROPIC_MODEL == OPUS_ANTHROPIC_MODEL
        assert MEAL_ANTHROPIC_MODEL == SONNET_ANTHROPIC_MODEL
        assert RECEIPT_HAIKU_MODEL == HAIKU_ANTHROPIC_MODEL

    def test_flag_defaults_off(self, monkeypatch):
        monkeypatch.delenv("MODEL_ROUTING_ENABLED", raising=False)
        assert get_settings().model_routing_enabled is False


class TestPolicyTable:
    @pytest.mark.parametrize("module", [receipt_analyzer, meal_generator, meal_image])
    def test_every_call_site_in_source_has_a_policy(self, module):
        source = inspect.getsource(module)
        for call_site in re.findall(r'call_site="([^"]+)"', source):
            assert call_site in POLICY, f"{module.__name__}: no routing policy for {call_site}"
            # Call sites must take the model from the router, not a constant.
        assert "model=RECEIPT_ANTHROPIC_MODEL" not in source
        assert "model=MEAL_ANTHROPIC_MODEL" not in source

    @pytest.mark.parametrize("call_site,expected_model", sorted(LIVE_CALL_SITES.items()))
    def test_default_tier_matches_pre_routing_model(self, call_site, expected_model):
        assert TIER_MODELS[POLICY[call_site].default] == expected_model

    def test_receipt_text_steps_default_to_shared_haiku_constant(self):
        for call_site in ("receipt.ocr_cleanup", "receipt.classify_text"):
            assert TIER_MODELS[POLICY[call_site].default] == RECEIPT_HAIKU_MODEL

    def test_escalation_is_always_stronger_than_routed(self):
        rank = {Tier.HAIKU: 0, Tier.SONNET: 1, Tier.OPUS: 2}
        for policy in POLICY.values():
            if policy.escalate_to is not None:
                assert rank[policy.escalate_to] > rank[policy.routed], policy.call_site

    def test_meal_generation_never_routes_to_haiku(self):
        policy = POLICY["meal.generate"]
        assert policy.default is Tier.SONNET
        assert policy.routed is not Tier.HAIKU

    def test_vision_and_nutrition_stay_on_opus_until_evals_green(self):
        for call_site in ("receipt.analyze_image", "receipt.nutrition_estimate"):
            assert POLICY[call_site].routed is Tier.OPUS

    def test_unknown_call_site_is_loud(self):
        with pytest.raises(ValueError, match="No routing policy"):
            policy_for("receipt.made_up")
        with pytest.raises(ValueError):
            route_model("receipt.made_up")


class TestRouteModelFlagOff:
    @pytest.mark.parametrize("call_site,expected_model", sorted(LIVE_CALL_SITES.items()))
    def test_returns_default_model(self, routing_off, call_site, expected_model):
        decision = route_model(call_site)
        assert decision.model == expected_model
        assert decision.reason == "default"
        assert decision.routing_enabled is False
        assert decision.escalated is False

    @pytest.mark.parametrize("call_site", sorted(POLICY))
    def test_never_escalates(self, routing_off, call_site):
        decision = route_model(call_site, confidence=0.0, escalate=True)
        assert decision.tier is POLICY[call_site].default
        assert decision.escalated is False
        assert escalation_for(decision) is None

    def test_explicit_enabled_override_wins_over_settings(self, routing_off):
        decision = route_model("receipt.unit_check", enabled=True)
        assert decision.model == HAIKU_ANTHROPIC_MODEL
        assert decision.reason == "policy"


class TestRouteModelFlagOn:
    def test_policy_tiers_apply(self, routing_on):
        assert route_model("receipt.unit_check").model == HAIKU_ANTHROPIC_MODEL
        assert route_model("receipt.pantry_match").model == SONNET_ANTHROPIC_MODEL
        assert route_model("receipt.analyze_image").model == OPUS_ANTHROPIC_MODEL
        assert route_model("receipt.nutrition_estimate").model == OPUS_ANTHROPIC_MODEL
        assert route_model("meal.generate").model == SONNET_ANTHROPIC_MODEL
        assert route_model("meal.image_prompt").model == HAIKU_ANTHROPIC_MODEL
        assert route_model("receipt.ocr_cleanup").model == RECEIPT_HAIKU_MODEL

    def test_low_confidence_escalates_once(self, routing_on):
        decision = route_model("receipt.pantry_match", confidence=LOW_CONFIDENCE_THRESHOLD - 0.1)
        assert decision.model == OPUS_ANTHROPIC_MODEL
        assert decision.reason == "escalated"
        assert decision.escalated is True
        assert escalation_for(decision) is None

    def test_confident_result_stays_on_routed_tier(self, routing_on):
        decision = route_model("receipt.pantry_match", confidence=0.95)
        assert decision.model == SONNET_ANTHROPIC_MODEL
        assert decision.escalated is False

    def test_escalation_for_returns_stronger_tier(self, routing_on):
        decision = route_model("receipt.unit_check")
        escalated = escalation_for(decision)
        assert escalated is not None
        assert escalated.model == OPUS_ANTHROPIC_MODEL
        assert escalated.reason == "escalated"

    def test_no_escalation_when_policy_has_none(self, routing_on):
        decision = route_model("receipt.analyze_image", escalate=True)
        assert decision.model == OPUS_ANTHROPIC_MODEL
        assert decision.escalated is False
        assert escalation_for(decision) is None

    def test_forced_model_overrides_policy_and_blocks_escalation(self, routing_on):
        with forced_model("claude-haiku-4-5"):
            decision = route_model("receipt.analyze_image", confidence=0.0)
            assert decision.model == "claude-haiku-4-5"
            assert decision.tier is Tier.HAIKU
            assert decision.reason == "forced"
            assert escalation_for(decision) is None
        assert route_model("receipt.analyze_image").model == OPUS_ANTHROPIC_MODEL

    def test_decision_is_logged_with_model_and_reason(self, routing_on, caplog):
        with caplog.at_level(logging.INFO, logger="app.services.model_router"):
            route_model("receipt.pantry_match", confidence=0.2)
        line = caplog.records[-1].getMessage()
        assert "model_route call_site=receipt.pantry_match" in line
        assert f"model={OPUS_ANTHROPIC_MODEL}" in line
        assert "reason=escalated" in line
        assert "routing_enabled=True" in line
        assert "confidence=0.20" in line


def _pantry_response(match_id, ambiguous, canonical="Rice"):
    return create_mock_anthropic_response(
        json.dumps({"match_id": match_id, "ambiguous": ambiguous, "canonical_name": canonical})
    )


class TestCallSiteEscalationWiring:
    PANTRY = [
        {"id": "rice-brown", "name": "Brown Rice", "unit": "lb"},
        {"id": "rice-white", "name": "White Rice", "unit": "lb"},
    ]

    def test_pantry_match_flag_off_is_single_opus_call(self, routing_off, monkeypatch):
        client = _FakeClient([_pantry_response(None, True)])
        monkeypatch.setattr(receipt_analyzer, "_get_client", lambda: client)

        result = match_ingredient_to_pantry("Rice", "lb", self.PANTRY)

        assert result.ambiguous is True
        assert [call["model"] for call in client.calls] == [RECEIPT_ANTHROPIC_MODEL]

    def test_pantry_match_ambiguous_escalates_to_opus(self, routing_on, monkeypatch):
        client = _FakeClient(
            [_pantry_response(None, True), _pantry_response("rice-brown", False, "Brown Rice")]
        )
        monkeypatch.setattr(receipt_analyzer, "_get_client", lambda: client)

        result = match_ingredient_to_pantry("Brown Rice", "lb", self.PANTRY)

        assert result.match_id == "rice-brown"
        assert result.ambiguous is False
        assert [call["model"] for call in client.calls] == [
            SONNET_ANTHROPIC_MODEL,
            OPUS_ANTHROPIC_MODEL,
        ]

    def test_pantry_match_invented_id_escalates(self, routing_on, monkeypatch):
        client = _FakeClient(
            [_pantry_response("not-a-real-id", False), _pantry_response(None, False, "Quinoa")]
        )
        monkeypatch.setattr(receipt_analyzer, "_get_client", lambda: client)

        result = match_ingredient_to_pantry("Quinoa", "lb", self.PANTRY)

        assert result.match_id is None
        assert result.canonical_name == "Quinoa"
        assert len(client.calls) == 2

    def test_pantry_match_malformed_missing_ambiguous_escalates(
        self, routing_on, monkeypatch
    ):
        """``{"match_id": null}`` is schema-invalid and must not look confident."""
        client = _FakeClient(
            [
                create_mock_anthropic_response(json.dumps({"match_id": None})),
                _pantry_response("rice-white", False, "White Rice"),
            ]
        )
        monkeypatch.setattr(receipt_analyzer, "_get_client", lambda: client)

        result = match_ingredient_to_pantry("White Rice", "lb", self.PANTRY)

        assert result.match_id == "rice-white"
        assert result.ambiguous is False
        assert [call["model"] for call in client.calls] == [
            SONNET_ANTHROPIC_MODEL,
            OPUS_ANTHROPIC_MODEL,
        ]

    def test_pantry_match_malformed_flag_off_is_still_one_call(
        self, routing_off, monkeypatch
    ):
        """Flag off: same as pre-FOOD-58 — no second call, result is a new row."""
        client = _FakeClient(
            [create_mock_anthropic_response(json.dumps({"match_id": None}))]
        )
        monkeypatch.setattr(receipt_analyzer, "_get_client", lambda: client)

        result = match_ingredient_to_pantry("White Rice", "lb", self.PANTRY)

        assert result.match_id is None
        assert result.ambiguous is False
        assert [call["model"] for call in client.calls] == [RECEIPT_ANTHROPIC_MODEL]

    def test_pantry_match_omitted_canonical_name_is_not_schema_invalid(
        self, routing_on, monkeypatch
    ):
        client = _FakeClient(
            [create_mock_anthropic_response(json.dumps({"match_id": None, "ambiguous": False}))]
        )
        monkeypatch.setattr(receipt_analyzer, "_get_client", lambda: client)

        result = match_ingredient_to_pantry("Quinoa", "lb", self.PANTRY)

        assert result.match_id is None
        assert result.ambiguous is False
        assert result.canonical_name is None
        assert [call["model"] for call in client.calls] == [SONNET_ANTHROPIC_MODEL]

    def test_pantry_match_confident_answer_stays_on_sonnet(self, routing_on, monkeypatch):
        client = _FakeClient([_pantry_response("rice-white", False, "White Rice")])
        monkeypatch.setattr(receipt_analyzer, "_get_client", lambda: client)

        result = match_ingredient_to_pantry("White Rice", "lb", self.PANTRY)

        assert result.match_id == "rice-white"
        assert [call["model"] for call in client.calls] == [SONNET_ANTHROPIC_MODEL]

    def test_pantry_match_escalated_still_returns_ambiguous_when_opus_agrees(
        self, routing_on, monkeypatch
    ):
        client = _FakeClient([_pantry_response(None, True), _pantry_response(None, True)])
        monkeypatch.setattr(receipt_analyzer, "_get_client", lambda: client)

        result = match_ingredient_to_pantry("Rice", "lb", self.PANTRY)

        assert result.ambiguous is True
        assert result.match_id is None
        assert len(client.calls) == 2

    def test_unit_check_negative_from_haiku_is_confirmed_on_opus(self, routing_on, monkeypatch):
        client = _FakeClient(
            [
                create_mock_anthropic_response(
                    json.dumps({"unit_plausible": False, "unit_warning": "Use each."})
                ),
                create_mock_anthropic_response(
                    json.dumps({"unit_plausible": True, "unit_warning": None})
                ),
            ]
        )
        monkeypatch.setattr(receipt_analyzer, "_get_client", lambda: client)

        warning = check_ingredient_unit("Bananas", "bunch")

        assert warning is None
        assert [call["model"] for call in client.calls] == [
            HAIKU_ANTHROPIC_MODEL,
            OPUS_ANTHROPIC_MODEL,
        ]

    def test_unit_check_positive_from_haiku_is_final(self, routing_on, monkeypatch):
        client = _FakeClient(
            [create_mock_anthropic_response(json.dumps({"unit_plausible": True, "unit_warning": None}))]
        )
        monkeypatch.setattr(receipt_analyzer, "_get_client", lambda: client)

        assert check_ingredient_unit("Bananas", "bunch") is None
        assert [call["model"] for call in client.calls] == [HAIKU_ANTHROPIC_MODEL]

    def test_unit_check_flag_off_rejects_without_second_call(self, routing_off, monkeypatch):
        client = _FakeClient(
            [
                create_mock_anthropic_response(
                    json.dumps({"unit_plausible": False, "unit_warning": "Use each."})
                )
            ]
        )
        monkeypatch.setattr(receipt_analyzer, "_get_client", lambda: client)

        assert check_ingredient_unit("Watermelon", "gallon") == "Use each."
        assert [call["model"] for call in client.calls] == [RECEIPT_ANTHROPIC_MODEL]


class _FakeClient:
    """Records ``messages.create`` kwargs and replays scripted responses."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[dict] = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("more Claude calls than scripted responses")
        return self._responses.pop(0)
