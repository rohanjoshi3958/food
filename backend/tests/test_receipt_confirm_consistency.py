"""Save either keeps every line or reports every failing line together."""

from unittest.mock import Mock, patch

from app.models import Ingredient, Receipt
from tests.conftest import (
    create_mock_anthropic_response,
    mock_pantry_match_response,
)


def _pending_receipt(test_db, test_user) -> Receipt:
    receipt = Receipt(
        user_id=test_user.id,
        filename="receipt.jpg",
        original_name="receipt.jpg",
        analysis_status="pending_review",
        draft_items=[],
    )
    test_db.add(receipt)
    test_db.commit()
    test_db.refresh(receipt)
    return receipt


def _route_failures(**kwargs):
    content = kwargs["messages"][0]["content"]
    system = kwargs.get("system")
    text = ""
    if isinstance(system, list):
        text += " ".join(block.get("text", "") for block in system if isinstance(block, dict))
    if isinstance(content, str):
        text += " " + content
    if "Match an incoming grocery item" in text:
        return mock_pantry_match_response()
    if "grocery purchase unit" in text and "plausible" in text:
        if "Paprika" in text:
            return create_mock_anthropic_response(
                '{"unit_plausible": false, "unit_warning": "Use g or oz for a dry spice."}'
            )
        if "Olive Oil" in text:
            return create_mock_anthropic_response(
                '{"unit_plausible": false, "unit_warning": "Use ml or fl oz for oil."}'
            )
        raise AssertionError("unit check ran for an unedited line")
    if "Estimate nutritional facts" in text:
        raise AssertionError("nutrition estimate ran before both unit checks failed")
    raise AssertionError(f"unexpected prompt: {text[:120]}")


@patch("app.services.receipt_analyzer.anthropic.Anthropic")
@patch("app.services.receipt_analyzer.settings.anthropic_api_key", "test-api-key")
@patch("app.config.settings.anthropic_api_key", "test-api-key")
def test_confirm_reports_every_failure_and_saves_nothing(
    mock_anthropic_class,
    client,
    test_db,
    test_user,
    auth_headers,
):
    mock_client = Mock()
    mock_anthropic_class.return_value = mock_client
    mock_client.messages.create.side_effect = _route_failures

    receipt = _pending_receipt(test_db, test_user)
    response = client.post(
        f"/api/receipts/{receipt.id}/confirm",
        json={
            "items": [
                {
                    "ingredient_name": "Paprika",
                    "quantity": "1",
                    "unit": "gallon",
                    "recheck": True,
                },
                {
                    "ingredient_name": "Olive Oil",
                    "quantity": "1",
                    "unit": "lb",
                    "recheck": True,
                },
            ]
        },
        headers=auth_headers,
    )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "Paprika" in detail
    assert "Olive Oil" in detail
    assert test_db.query(Ingredient).filter_by(user_id=test_user.id).count() == 0
    test_db.refresh(receipt)
    assert receipt.analysis_status == "pending_review"


@patch("app.services.receipt_analyzer.anthropic.Anthropic")
@patch("app.services.receipt_analyzer.settings.anthropic_api_key", "test-api-key")
@patch("app.config.settings.anthropic_api_key", "test-api-key")
def test_confirm_reuses_upload_estimate_for_unedited_lines(
    mock_anthropic_class,
    client,
    test_db,
    test_user,
    auth_headers,
):
    mock_client = Mock()
    mock_anthropic_class.return_value = mock_client
    mock_client.messages.create.side_effect = _route_failures

    receipt = _pending_receipt(test_db, test_user)
    response = client.post(
        f"/api/receipts/{receipt.id}/confirm",
        json={
            "items": [
                {
                    "ingredient_name": "Bananas",
                    "quantity": "2",
                    "unit": "lb",
                    "serving_size": "1 banana",
                    "calories": 105,
                    "recheck": False,
                }
            ]
        },
        headers=auth_headers,
    )

    assert response.status_code == 200
    saved = response.json()["ingredients"]
    assert len(saved) == 1
    assert saved[0]["calories"] == 105
    assert saved[0]["name"] == "Bananas"
