"""Pure scoring helpers shared by the eval suites.

Everything here is deterministic and free of I/O so the same functions can
score mocked CI runs and live-model runs identically.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.services.ingredient_deduction import normalize_unit, parse_number
from app.services.ingredient_normalization import normalize_ingredient_name


def canonical_name(name: str | None) -> str:
    return normalize_ingredient_name(name or "").canonical


def names_equivalent(left: str | None, right: str | None) -> bool:
    """Plural-tolerant equality on the cheap canonical form.

    ``Banana`` == ``Bananas`` and ``Tomatoes`` == ``Tomato``; qualifiers and
    abbreviations are *not* forgiven, so ``Grnd Bf`` != ``Ground Beef``.
    """
    a = canonical_name(left)
    b = canonical_name(right)
    if not a or not b:
        return False
    if a == b:
        return True
    return _singular(a) == _singular(b)


def _singular(word: str) -> str:
    tokens = word.split()
    if not tokens:
        return word
    last = tokens[-1]
    if last.endswith("ies") and len(last) > 4:
        last = last[:-3] + "y"
    elif last.endswith("oes") and len(last) > 4:
        last = last[:-2]
    elif last.endswith("es") and len(last) > 4 and last[-3] in "sxz":
        last = last[:-2]
    elif last.endswith("s") and not last.endswith("ss") and len(last) > 3:
        last = last[:-1]
    tokens[-1] = last
    return " ".join(tokens)


def units_equivalent(left: str | None, right: str | None) -> bool:
    return (normalize_unit(left) or "") == (normalize_unit(right) or "")


def quantities_equivalent(left: str | None, right: str | None) -> bool:
    a = parse_number(str(left or ""))
    b = parse_number(str(right or ""))
    if a is None or b is None:
        return (left or "").strip() == (right or "").strip()
    return abs(a - b) < 1e-6


def receipt_line_key(store_item_name: str | None) -> str:
    return " ".join((store_item_name or "").upper().split())


def ratio(numerator: int, denominator: int) -> float:
    """Safe rate; an empty denominator scores 1.0 so optional metrics never fail."""
    if denominator == 0:
        return 1.0
    return numerator / denominator


@dataclass
class Tally:
    """Hit / total counter with a human-readable list of misses."""

    hits: int = 0
    total: int = 0
    misses: list[str] = field(default_factory=list)

    def add(self, ok: bool, label: str) -> None:
        self.total += 1
        if ok:
            self.hits += 1
        else:
            self.misses.append(label)

    @property
    def rate(self) -> float:
        return ratio(self.hits, self.total)

    def describe(self, limit: int = 12) -> str:
        head = f"{self.hits}/{self.total}"
        if not self.misses:
            return head
        shown = "; ".join(self.misses[:limit])
        more = "" if len(self.misses) <= limit else f" (+{len(self.misses) - limit} more)"
        return f"{head} — misses: {shown}{more}"
