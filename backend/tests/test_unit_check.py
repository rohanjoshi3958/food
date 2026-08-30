"""Tests for AI-based unit plausibility checks."""

from unittest.mock import patch

from app.services.receipt_analyzer import (
    _unit_warning_from_payload,
    check_ingredient_unit,
)


class TestUnitWarningFromPayload:
    def test_returns_none_when_plausible(self):
        assert (
            _unit_warning_from_payload(
                {"unit_plausible": True, "unit_warning": "ignored"}
            )
            is None
        )

    def test_returns_warning_when_not_plausible(self):
        assert (
            _unit_warning_from_payload(
                {
                    "unit_plausible": False,
                    "unit_warning": "Use each or lb for watermelon.",
                }
            )
            == "Use each or lb for watermelon."
        )


class TestCheckIngredientUnit:
    def test_returns_none_without_name_or_unit(self):
        assert check_ingredient_unit("", "gallon") is None
        assert check_ingredient_unit("watermelon", "") is None

    @patch("app.services.receipt_analyzer._get_client")
    def test_returns_warning_from_model(self, mock_get_client):
        mock_get_client.return_value.messages.create.return_value.content = [
            type(
                "Block",
                (),
                {
                    "type": "text",
                    "text": '{"unit_plausible": false, "unit_warning": "Use each or lb."}',
                },
            )()
        ]

        warning = check_ingredient_unit("watermelon", "gallon")

        assert warning == "Use each or lb."
