"""Shared plumbing for the eval suites.

* ``FakeClaude`` — a prompt-routed stand-in for ``anthropic.Anthropic``. It
  routes on the cached ``system`` prefix (so it keeps working as long as the
  FOOD-56 request shape holds) and looks per-item answers up from the fixture
  under test. Nothing here ever talks to the network.
* ``baselines.json`` gate — ``EvalGate.check`` records every metric for the
  terminal / GitHub step summary and fails the test when a metric crosses the
  documented threshold.
* Live mode — ``FOOD_EVAL_LIVE=1`` opts the ``live`` tests into real Anthropic
  calls (paid). They are skipped by default and in CI.
"""

from __future__ import annotations

import json
import os
import re
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Iterable

import pytest

from app.services import meal_generator, receipt_analyzer
from app.services.meal_generator import (
    MEAL_CALORIE_MAX,
    MEAL_CALORIE_MIN,
    MEAL_GENERATION_PROMPT,
)
from app.services.meal_image import PROMPT_SYSTEM as MEAL_IMAGE_PROMPT
from app.services.receipt_analyzer import (
    NUTRITION_ESTIMATE_PROMPT,
    PANTRY_MATCH_PROMPT,
    RECEIPT_ANALYSIS_PROMPT,
    UNIT_CHECK_PROMPT,
)
from tests.evals.scoring import canonical_name

EVALS_DIR = Path(__file__).parent
FIXTURES_DIR = EVALS_DIR / "fixtures"
BASELINES_PATH = EVALS_DIR / "baselines.json"

MEAL_PREFIX = MEAL_GENERATION_PROMPT.format(
    calorie_min=MEAL_CALORIE_MIN, calorie_max=MEAL_CALORIE_MAX
)

# Captured at import time, before the root conftest's autouse fixture blanks
# ANTHROPIC_API_KEY for the duration of every test.
_LIVE_API_KEY = (
    os.environ.get("FOOD_EVAL_ANTHROPIC_API_KEY")
    or os.environ.get("ANTHROPIC_API_KEY")
    or ""
)
LIVE_ENABLED = os.environ.get("FOOD_EVAL_LIVE", "").strip() in {"1", "true", "yes"}
LIVE_MODEL = os.environ.get("FOOD_EVAL_MODEL", "").strip() or None


# --------------------------------------------------------------------------- #
# Fixture loading
# --------------------------------------------------------------------------- #


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def load_receipt_fixtures() -> list[dict]:
    return [
        load_json(path)
        for path in sorted((FIXTURES_DIR / "receipts").glob("*.json"))
    ]


def load_pantry_match_fixtures() -> dict:
    return load_json(FIXTURES_DIR / "pantry_matches.json")


def load_meal_plan_fixtures() -> dict:
    return load_json(FIXTURES_DIR / "meal_plans.json")


def render_model_response(spec: Any) -> str:
    """Turn a fixture ``model_response`` into the raw text Claude would return.

    A plain string is used verbatim. A dict/list is JSON-encoded; ``wrapper``
    on the enclosing fixture can add realistic noise (``fenced`` markdown
    fences, ``prose`` a chatty preamble that breaks strict JSON parsing).
    """
    if isinstance(spec, str):
        return spec
    if isinstance(spec, dict) and "payload" in spec:
        body = spec["payload"]
        text = body if isinstance(body, str) else json.dumps(body, indent=2)
        wrapper = spec.get("wrapper", "plain")
        if wrapper == "fenced":
            return f"```json\n{text}\n```"
        if wrapper == "prose":
            return f"Sure! Here is the JSON you asked for:\n\n{text}\n\nLet me know if you need anything else."
        return text
    return json.dumps(spec, indent=2)


# --------------------------------------------------------------------------- #
# Fake Anthropic client
# --------------------------------------------------------------------------- #


def fake_message(text: str, *, model: str = "fake") -> SimpleNamespace:
    """Shape-compatible Messages API response with integer usage counters."""
    block = SimpleNamespace(type="text", text=text)
    usage = SimpleNamespace(
        input_tokens=len(text) // 4 + 50,
        output_tokens=len(text) // 4,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
    )
    return SimpleNamespace(content=[block], usage=usage, model=model, stop_reason="end_turn")


def _messages_text(messages: list[dict]) -> str:
    parts: list[str] = []
    for message in messages:
        content = message["content"]
        if isinstance(content, str):
            parts.append(content)
            continue
        for block in content:
            if block.get("type") == "text":
                parts.append(block["text"])
    return "\n".join(parts)


def _first_match(pattern: str, text: str) -> str | None:
    match = re.search(pattern, text, flags=re.MULTILINE)
    return match.group(1).strip() if match else None


class FakeClaudeError(RuntimeError):
    """Raised when the fake receives a request it was not scripted for."""


class FakeClaude:
    """Prompt-routed fake for ``anthropic.Anthropic``.

    Routes each request on its cached ``system`` prefix:

    * receipt analysis        -> ``receipt_response``
    * nutrition estimate      -> ``nutrition[item name]`` (default: recognized, 100 kcal)
    * unit check              -> ``unit_checks[item name]`` (default: plausible)
    * pantry match            -> ``pantry_matches[incoming name]`` (default: no match)
    * meal generation         -> ``meal_responses`` in order (last one repeats)
    * meal image prompt       -> fixed text
    """

    def __init__(self) -> None:
        self.receipt_response: str | None = None
        self.nutrition: dict[str, Any] = {}
        self.unit_checks: dict[str, Any] = {}
        self.pantry_matches: dict[str, Any] = {}
        self.meal_responses: list[str] = []
        self.calls: list[dict[str, Any]] = []
        self._meal_index = 0
        self.messages = SimpleNamespace(create=self.create)

    # -- scripting helpers -------------------------------------------------- #

    def script_receipt(self, fixture: dict) -> None:
        self.receipt_response = render_model_response(fixture["model_response"])
        for name, payload in (fixture.get("nutrition_responses") or {}).items():
            self.nutrition[canonical_name(name)] = payload

    def script_pantry_match(self, incoming_name: str, payload: Any) -> None:
        self.pantry_matches[canonical_name(incoming_name)] = payload

    def script_unit_check(self, item_name: str, payload: Any) -> None:
        self.unit_checks[canonical_name(item_name)] = payload

    def script_meals(self, responses: Iterable[Any]) -> None:
        self.meal_responses = [render_model_response(item) for item in responses]
        self._meal_index = 0

    # -- call counting ------------------------------------------------------ #

    def calls_for(self, prefix: str) -> list[dict[str, Any]]:
        return [call for call in self.calls if call["system_text"] == prefix]

    @property
    def meal_calls(self) -> int:
        return len(self.calls_for(MEAL_PREFIX))

    @property
    def pantry_match_calls(self) -> int:
        return len(self.calls_for(PANTRY_MATCH_PROMPT))

    # -- the fake endpoint -------------------------------------------------- #

    def create(self, **kwargs: Any) -> SimpleNamespace:
        system = kwargs.get("system")
        if not isinstance(system, list) or len(system) != 1 or "text" not in system[0]:
            raise FakeClaudeError(
                "FakeClaude expects the FOOD-56 request shape: exactly one cached system block"
            )
        system_text = system[0]["text"]
        tail = _messages_text(kwargs["messages"])
        self.calls.append(
            {
                "model": kwargs.get("model"),
                "system_text": system_text,
                "messages": kwargs["messages"],
                "tail": tail,
            }
        )

        if system_text == RECEIPT_ANALYSIS_PROMPT:
            if self.receipt_response is None:
                raise FakeClaudeError("no receipt response scripted")
            return fake_message(self.receipt_response, model=kwargs.get("model", "fake"))

        if system_text == NUTRITION_ESTIMATE_PROMPT:
            item = _first_match(r"^- Item: (.*)$", tail) or ""
            payload = self.nutrition.get(
                canonical_name(item),
                {
                    "recognized": True,
                    "serving_size": "1 serving",
                    "servings_per_container": 1,
                    "calories": 100,
                    "nutrition_notes": "eval default",
                },
            )
            return fake_message(render_model_response(payload))

        if system_text == UNIT_CHECK_PROMPT:
            item = _first_match(r"^- Item: (.*)$", tail) or ""
            payload = self.unit_checks.get(
                canonical_name(item), {"unit_plausible": True, "unit_warning": None}
            )
            return fake_message(render_model_response(payload))

        if system_text == PANTRY_MATCH_PROMPT:
            item = _first_match(r"^- name: (.*)$", tail) or ""
            payload = self.pantry_matches.get(
                canonical_name(item),
                {"match_id": None, "ambiguous": False, "canonical_name": None},
            )
            return fake_message(render_model_response(payload))

        if system_text == MEAL_PREFIX:
            if not self.meal_responses:
                raise FakeClaudeError("no meal responses scripted")
            index = min(self._meal_index, len(self.meal_responses) - 1)
            self._meal_index += 1
            return fake_message(self.meal_responses[index])

        if system_text == MEAL_IMAGE_PROMPT:
            return fake_message("Overhead photo of the finished dish on a ceramic plate.")

        raise FakeClaudeError(f"unrouted system prefix: {system_text[:60]!r}")


@pytest.fixture
def fake_claude(monkeypatch) -> FakeClaude:
    """Route every Claude call site through one ``FakeClaude`` instance."""
    fake = FakeClaude()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "eval-fake-key")
    monkeypatch.setattr(receipt_analyzer, "_get_client", lambda: fake)
    monkeypatch.setattr(
        meal_generator.anthropic, "Anthropic", lambda *args, **kwargs: fake
    )
    return fake


# --------------------------------------------------------------------------- #
# Baseline gate + report
# --------------------------------------------------------------------------- #


@dataclass
class MetricRecord:
    suite: str
    metric: str
    value: float
    baseline: float | None
    threshold: float | None
    kind: str  # "min" | "max" | "info"
    detail: str = ""
    passed: bool = True


_REPORT: "OrderedDict[tuple[str, str], MetricRecord]" = OrderedDict()


def _load_baselines() -> dict:
    return load_json(BASELINES_PATH)


class EvalGate:
    """Look up a metric's threshold in ``baselines.json``, record it, assert it."""

    def __init__(self, suite: str, baselines: dict, mode: str = "mocked") -> None:
        self.suite = suite
        self.baselines = baselines.get(suite, {})
        self.mode = mode
        self.failures: list[str] = []

    def record(self, metric: str, value: float, detail: str = "") -> MetricRecord:
        spec = self.baselines.get(metric, {})
        if "min" in spec:
            kind, threshold = "min", float(spec["min"])
            passed = value >= threshold
        elif "max" in spec:
            kind, threshold = "max", float(spec["max"])
            passed = value <= threshold
        else:
            kind, threshold, passed = "info", None, True
        record = MetricRecord(
            suite=f"{self.suite}" + (f" [{self.mode}]" if self.mode != "mocked" else ""),
            metric=metric,
            value=value,
            baseline=spec.get("baseline"),
            threshold=threshold,
            kind=kind,
            detail=detail,
            passed=passed,
        )
        _REPORT[(record.suite, metric)] = record
        return record

    def check(self, metric: str, value: float, detail: str = "") -> MetricRecord:
        """Record a metric and remember a failure; ``assert_all`` raises them together.

        ``check`` is a CI gate: a missing or misspelled ``baselines.json``
        entry (``kind == "info"``) is a failure so a typo cannot silently
        disable a locked bar. Informational-only numbers should use
        :meth:`record`, not this method.
        """
        record = self.record(metric, value, detail)
        if record.kind == "info":
            record.passed = False
            self.failures.append(
                f"{metric} has no min/max threshold in baselines.json"
            )
        elif not record.passed:
            op = ">=" if record.kind == "min" else "<="
            self.failures.append(
                f"{metric}={value:.3f} must be {op} {record.threshold:.3f} "
                f"(documented baseline {record.baseline}). {detail}".rstrip()
            )
        return record

    def assert_all(self) -> None:
        if not self.failures:
            return
        joined = "\n  - ".join(self.failures)
        raise AssertionError(
            f"[{self.suite}] quality below documented baseline:\n  - {joined}\n"
            "If this drop is intentional (prompt or model tier change), re-run the live "
            "evals and update backend/tests/evals/baselines.json in the same PR."
        )


@pytest.fixture(scope="session")
def baselines() -> dict:
    return _load_baselines()


@pytest.fixture
def gate(baselines) -> Callable[[str], EvalGate]:
    def factory(suite: str, mode: str = "mocked") -> EvalGate:
        return EvalGate(suite, baselines, mode=mode)

    return factory


def _format_value(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.3f}"


def pytest_terminal_summary(terminalreporter, exitstatus, config) -> None:  # noqa: ARG001
    if not _REPORT:
        return
    rows = list(_REPORT.values())
    terminalreporter.section("Food quality evals (FOOD-58)")
    header = f"{'suite':<28} {'metric':<28} {'value':>7} {'baseline':>9} {'gate':>10}  status"
    terminalreporter.write_line(header)
    terminalreporter.write_line("-" * len(header))
    for row in rows:
        gate_label = "—" if row.threshold is None else f"{row.kind} {row.threshold:.3f}"
        status = "ok" if row.passed else "FAIL"
        terminalreporter.write_line(
            f"{row.suite:<28} {row.metric:<28} {_format_value(row.value):>7} "
            f"{_format_value(row.baseline):>9} {gate_label:>10}  {status}"
        )

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        lines = [
            "### Food quality evals (FOOD-58)",
            "",
            "| Suite | Metric | Value | Baseline | Gate | Status |",
            "| --- | --- | ---: | ---: | --- | --- |",
        ]
        for row in rows:
            gate_label = "—" if row.threshold is None else f"{row.kind} {row.threshold:.3f}"
            status = "ok" if row.passed else "**FAIL**"
            lines.append(
                f"| {row.suite} | `{row.metric}` | {_format_value(row.value)} | "
                f"{_format_value(row.baseline)} | {gate_label} | {status} |"
            )
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n\n")


# --------------------------------------------------------------------------- #
# Live mode (opt-in, paid)
# --------------------------------------------------------------------------- #


@pytest.fixture
def live_anthropic(monkeypatch):
    """Restore a real API key and, optionally, force one model for live evals.

    Skips unless ``FOOD_EVAL_LIVE=1``. When ``FOOD_EVAL_MODEL`` is set (e.g.
    ``claude-haiku-4-5``) every call site is forced onto that model through
    ``model_router.forced_model`` so a tier can be scored end to end before
    it is routed in production. Yields the forced model id or ``None``.
    """
    if not LIVE_ENABLED:
        pytest.skip("live evals are opt-in: set FOOD_EVAL_LIVE=1 (paid Anthropic calls)")
    if not _LIVE_API_KEY:
        pytest.skip("live evals need ANTHROPIC_API_KEY or FOOD_EVAL_ANTHROPIC_API_KEY")
    monkeypatch.setenv("ANTHROPIC_API_KEY", _LIVE_API_KEY)

    from app.services.model_router import forced_model

    if LIVE_MODEL:
        with forced_model(LIVE_MODEL):
            yield LIVE_MODEL
    else:
        yield None
