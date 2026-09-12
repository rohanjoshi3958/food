"""Scoring for the FOOD-55 receipt eval set. Pure functions, no I/O, no LLM.

Metric definitions live in README.md; keep the two in sync.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

from app.validation import INGREDIENT_UNITS

NO_VISION_PATHS = {"cache", "ocr", "haiku"}
ESCALATED = "escalated"  # text-mode runs that failed the gate and have no LLM result
F1_MARGIN = 0.02


class LabelItem(BaseModel):
    store_item_name: str = Field(min_length=1)
    ingredient_name: str = Field(min_length=1)
    quantity: str | None
    unit: str | None
    is_food: bool


class LabelTotals(BaseModel):
    subtotal: float | None = None
    tax: float | None = None
    total: float | None = None


class Label(BaseModel):
    """Python twin of schema/label.schema.json."""

    case_id: str = Field(pattern=r"^[a-z0-9_]+$")
    store_name: str | None
    items: list[LabelItem]
    totals: LabelTotals | None = None
    labeler_notes: str | None = None

    def problems(self) -> list[str]:
        issues = []
        for index, item in enumerate(self.items):
            if item.unit is not None and item.unit not in INGREDIENT_UNITS:
                issues.append(f"items[{index}].unit {item.unit!r} is not in INGREDIENT_UNITS")
        return issues


class PredictionItem(BaseModel):
    store_item_name: str = ""
    ingredient_name: str = ""
    quantity: str | None = None
    unit: str | None = None
    is_food: bool = True


class Prediction(BaseModel):
    """One pipeline output for one case, as written by run_eval.py."""

    path: str
    store_name: str | None = None
    items: list[PredictionItem] = Field(default_factory=list)
    gate_passed: bool | None = None
    gate_reasons: list[str] = Field(default_factory=list)
    latency_ms: float | None = None
    error: str | None = None


_PUNCT = re.compile(r"[^\w\s%/]")
_SPACES = re.compile(r"\s+")


def normalize_name(name: str | None) -> str:
    text = _SPACES.sub(" ", _PUNCT.sub(" ", (name or "").lower())).strip()
    words = []
    for word in text.split():
        if len(word) > 3 and word.endswith("ies"):
            word = word[:-3] + "y"
        elif len(word) > 3 and word.endswith("es") and word[-3] in "sxz":
            word = word[:-2]
        elif len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
            word = word[:-1]
        words.append(word)
    return " ".join(words)


def _tokens(name: str | None) -> set[str]:
    return set(normalize_name(name).split())


def match_items(
    predicted: list[PredictionItem], labeled: list[LabelItem]
) -> list[tuple[int, int]]:
    """Greedy one-to-one matching: exact ingredient name, exact store text, then token overlap."""
    pairs: list[tuple[int, int]] = []
    used_pred: set[int] = set()
    used_label: set[int] = set()

    def take(score_fn, threshold: float) -> None:
        candidates = []
        for p_index, pred in enumerate(predicted):
            if p_index in used_pred:
                continue
            for l_index, label in enumerate(labeled):
                if l_index in used_label:
                    continue
                score = score_fn(pred, label)
                if score >= threshold:
                    candidates.append((score, p_index, l_index))
        for _score, p_index, l_index in sorted(candidates, reverse=True):
            if p_index in used_pred or l_index in used_label:
                continue
            used_pred.add(p_index)
            used_label.add(l_index)
            pairs.append((p_index, l_index))

    take(lambda p, l: float(normalize_name(p.ingredient_name) == normalize_name(l.ingredient_name)), 1.0)
    take(lambda p, l: float(normalize_name(p.store_item_name) == normalize_name(l.store_item_name)), 1.0)

    def jaccard(p: PredictionItem, l: LabelItem) -> float:
        a = _tokens(p.ingredient_name) | _tokens(p.store_item_name)
        b = _tokens(l.ingredient_name) | _tokens(l.store_item_name)
        return len(a & b) / len(a | b) if a and b else 0.0

    take(jaccard, 0.5)
    return sorted(pairs)


def _qty_equal(left: str | None, right: str | None) -> bool:
    if left is None or right is None:
        return left is right
    try:
        return abs(float(left) - float(right)) < 1e-6
    except ValueError:
        return left.strip() == right.strip()


@dataclass
class CaseScore:
    case_id: str
    path: str
    tp: int = 0
    fp: int = 0
    fn: int = 0
    exact: int = 0
    is_food_correct: int = 0
    store_match: bool = False
    expect_path: str | None = None
    success: bool = False
    smoke: bool = False
    error: str | None = None

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if (self.tp + self.fp) else 0.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if (self.tp + self.fn) else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0

    @property
    def path_ok(self) -> bool:
        return self.expect_path is None or self.path == self.expect_path


def score_case(
    case_id: str,
    prediction: Prediction | None,
    label: Label,
    *,
    expect_path: str | None = None,
    smoke: bool = False,
) -> CaseScore:
    score = CaseScore(case_id=case_id, path=prediction.path if prediction else "missing", expect_path=expect_path, smoke=smoke)
    if prediction is None or prediction.error or prediction.path == ESCALATED:
        score.fn = len(label.items)
        score.error = prediction.error if prediction and prediction.error else (
            "escalated without LLM result" if prediction else "no prediction"
        )
        return score

    pairs = match_items(prediction.items, label.items)
    score.tp = len(pairs)
    score.fp = len(prediction.items) - len(pairs)
    score.fn = len(label.items) - len(pairs)
    for p_index, l_index in pairs:
        pred, lab = prediction.items[p_index], label.items[l_index]
        if pred.is_food == lab.is_food:
            score.is_food_correct += 1
        if (
            pred.is_food == lab.is_food
            and _tokens(pred.ingredient_name) == _tokens(lab.ingredient_name)
            and _qty_equal(pred.quantity, lab.quantity)
            and (pred.unit or None) == (lab.unit or None)
        ):
            score.exact += 1
    score.store_match = normalize_name(prediction.store_name) == normalize_name(label.store_name)
    score.success = any(item.is_food for item in prediction.items)
    return score


@dataclass
class RunScore:
    cases: list[CaseScore] = field(default_factory=list)
    cost_per_path: dict[str, float] = field(default_factory=dict)
    # (case_id, reason) for cases that could not be scored in this run, e.g.
    # placeholder images or escalations with no baseline file to fall back to.
    skipped: list[tuple[str, str]] = field(default_factory=list)

    @property
    def tp(self) -> int:
        return sum(c.tp for c in self.cases)

    @property
    def fp(self) -> int:
        return sum(c.fp for c in self.cases)

    @property
    def fn(self) -> int:
        return sum(c.fn for c in self.cases)

    @property
    def micro_f1(self) -> float:
        p = self.tp / (self.tp + self.fp) if (self.tp + self.fp) else 0.0
        r = self.tp / (self.tp + self.fn) if (self.tp + self.fn) else 0.0
        return 2 * p * r / (p + r) if (p + r) else 0.0

    @property
    def macro_f1(self) -> float:
        return sum(c.f1 for c in self.cases) / len(self.cases) if self.cases else 0.0

    @property
    def exact_match_rate(self) -> float:
        labeled = self.tp + self.fn
        return sum(c.exact for c in self.cases) / labeled if labeled else 0.0

    @property
    def is_food_accuracy(self) -> float:
        return sum(c.is_food_correct for c in self.cases) / self.tp if self.tp else 0.0

    @property
    def store_accuracy(self) -> float:
        return sum(c.store_match for c in self.cases) / len(self.cases) if self.cases else 0.0

    @property
    def successes(self) -> list[CaseScore]:
        return [c for c in self.cases if c.success]

    @property
    def pct_no_vision(self) -> float:
        ok = self.successes
        return sum(c.path in NO_VISION_PATHS for c in ok) / len(ok) if ok else 0.0

    @property
    def cost_per_success(self) -> float:
        spent = sum(self.cost_per_path.get(c.path, 0.0) for c in self.cases)
        return spent / len(self.successes) if self.successes else float("inf")

    @property
    def smoke_failures(self) -> list[str]:
        return [c.case_id for c in self.cases if c.smoke and not (c.success and c.path_ok)]

    @property
    def path_mismatches(self) -> list[str]:
        return [f"{c.case_id}: got {c.path}, expected {c.expect_path}" for c in self.cases if not c.path_ok]


def score_run(
    predictions: dict[str, Prediction],
    labels: dict[str, Label],
    manifest_cases: list[dict],
    cost_per_path: dict[str, float] | None = None,
    *,
    fallback: dict[str, Prediction] | None = None,
) -> RunScore:
    """Score one run. ``fallback`` (usually a baseline run) stands in for cases
    the candidate escalated without producing an LLM result, mirroring what
    production would do; those cases are charged at the fallback's path."""
    run = RunScore(cost_per_path=dict(cost_per_path or {}))
    for entry in manifest_cases:
        case_id = entry["id"]
        if case_id not in labels:
            continue
        prediction = predictions.get(case_id)
        if prediction is None:
            run.skipped.append((case_id, "no prediction (image/fixture missing?)"))
            continue
        if prediction.path == ESCALATED:
            if fallback and case_id in fallback:
                prediction = fallback[case_id]
            else:
                run.skipped.append((case_id, f"escalated ({', '.join(prediction.gate_reasons) or 'gate'}) and no --baseline to fall back to"))
                continue
        run.cases.append(
            score_case(
                case_id,
                prediction,
                labels[case_id],
                expect_path=entry.get("expect_path"),
                smoke=bool(entry.get("smoke")),
            )
        )
    return run


@dataclass
class Verdict:
    passed: bool
    reasons: list[str]
    status: Literal["PASS", "FAIL"]


def compare(
    candidate: RunScore,
    baseline: RunScore | None,
    *,
    f1_margin: float = F1_MARGIN,
    strict: bool = False,
) -> Verdict:
    """Ship bar: item F1 within ``f1_margin`` of baseline, no smoke failures.
    Cheaper-but-worse is a FAIL regardless of $/parse. ``strict`` also fails
    on skipped cases and on a missing baseline (use it for the real gate)."""
    reasons: list[str] = []
    if baseline is not None:
        floor = baseline.micro_f1 - f1_margin
        if candidate.micro_f1 < floor:
            reasons.append(
                f"item F1 {candidate.micro_f1:.3f} below baseline floor {floor:.3f} "
                f"(baseline {baseline.micro_f1:.3f} - {f1_margin:.2f}); cheaper-but-worse"
            )
    elif strict:
        reasons.append("no baseline run supplied; F1 bar cannot be enforced")
    if candidate.smoke_failures:
        reasons.append("smoke regressions: " + ", ".join(candidate.smoke_failures))
    if strict and candidate.skipped:
        reasons.append("unscored cases: " + ", ".join(f"{c} ({why})" for c, why in candidate.skipped))
    passed = not reasons
    return Verdict(passed=passed, reasons=reasons, status="PASS" if passed else "FAIL")
