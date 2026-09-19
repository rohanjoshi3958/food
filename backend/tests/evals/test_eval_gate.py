"""EvalGate must fail closed when a checked metric has no threshold."""

from __future__ import annotations

import pytest

from tests.evals import conftest as eval_conftest
from tests.evals.conftest import EvalGate


@pytest.fixture
def isolated_report():
    """Keep these negative cases out of the session eval summary table."""
    previous = eval_conftest._REPORT.copy()
    eval_conftest._REPORT.clear()
    try:
        yield
    finally:
        eval_conftest._REPORT.clear()
        eval_conftest._REPORT.update(previous)


def test_check_fails_when_metric_has_no_threshold(isolated_report):
    gate = EvalGate("receipt_parse", {"receipt_parse": {"line_item_recall": {"min": 0.9}}})
    record = gate.check("typoed_metric_name", 1.0)

    assert record.kind == "info"
    assert record.passed is False
    with pytest.raises(AssertionError, match="no min/max threshold"):
        gate.assert_all()


def test_check_still_fails_a_real_threshold_miss(isolated_report):
    gate = EvalGate(
        "receipt_parse",
        {"receipt_parse": {"line_item_recall": {"min": 0.99, "baseline": 1.0}}},
    )
    gate.check("line_item_recall", 0.5)
    with pytest.raises(AssertionError, match="line_item_recall=0.500"):
        gate.assert_all()
