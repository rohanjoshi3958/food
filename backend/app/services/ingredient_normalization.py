"""Cheap local ingredient name cleanup for matching hot paths.

Abbreviation expansion, plural handling, qualifier stripping, and pantry
matching happen via the LLM at ingredient intake time
(see receipt_analyzer.match_ingredient_to_pantry).

This module only does fast, deterministic cleanup: case, whitespace, and
punctuation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class MatchConfidence(Enum):
    """Confidence level for ingredient matching."""

    EXACT = "exact"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    AMBIGUOUS = "ambiguous"
    NO_MATCH = "no_match"


@dataclass
class NormalizationResult:
    """Result of normalizing an ingredient name."""

    original: str
    normalized: str
    canonical: str


@dataclass
class MatchResult:
    """Result of matching two ingredient names."""

    confidence: MatchConfidence
    source_normalized: str
    target_normalized: str
    reason: str | None = None


def _normalize_whitespace(text: str) -> str:
    """Collapse multiple whitespace chars to single space and strip."""
    return re.sub(r"\s+", " ", text.strip())


def _remove_punctuation(text: str) -> str:
    """Remove punctuation for matching, keeping token boundaries.

    Separator punctuation (``.``, ``/``, etc.) becomes a space so names like
    ``CHKN.BRST`` and ``CHKN/BRST`` stay two tokens. Hyphens between words are
    preserved.
    """
    result = re.sub(r"[^\w\s-]", " ", text)
    result = re.sub(r"(?<!\w)-|-(?!\w)", " ", result)
    return result


def _remove_punctuation_for_display(text: str) -> str:
    """Light punctuation cleanup for display labels.

    Preserves meaningful characters like ``%`` and ``/`` so labels such as
    ``MLK 2%`` and ``GROUND BEEF 80/20`` stay readable.
    """
    result = re.sub(r"_+", " ", text)
    result = re.sub(r"[^\w\s%/-]", " ", result)
    result = re.sub(r"(?<!\w)-|-(?!\w)", " ", result)
    return result


def normalize_ingredient_name(name: str) -> NormalizationResult:
    """Normalize an ingredient name for cheap local matching.

    Only lowercases, collapses whitespace, and strips punctuation.
    Abbreviations, plurals, and product qualifiers are handled by the LLM
    at ingredient intake time.
    """
    if not name or not name.strip():
        return NormalizationResult(
            original=name,
            normalized="",
            canonical="",
        )

    step1 = _normalize_whitespace(name.lower())
    step2 = _remove_punctuation(step1)
    canonical = _normalize_whitespace(step2)

    return NormalizationResult(
        original=name,
        normalized=canonical,
        canonical=canonical,
    )


def compute_canonical_key(name: str | None, unit: str | None = None) -> tuple[str, str]:
    """Compute a canonical key for ingredient matching.

    Returns (canonical_name, normalized_unit). Name-side matching beyond
    case/whitespace/punctuation is done by the LLM at intake.
    """
    from app.services.ingredient_deduction import normalize_unit

    if not name:
        return ("", "")

    result = normalize_ingredient_name(name)
    normalized_unit = normalize_unit(unit) or ""

    return (result.canonical, normalized_unit)


def match_ingredient_names(
    source: str,
    target: str,
    require_high_confidence: bool = True,
) -> MatchResult:
    """Cheap local name comparison (no plural/qualifier/abbrev dictionaries).

    Confidence levels:
    - EXACT / HIGH: cleaned forms are identical (or same word set)
    - MEDIUM / AMBIGUOUS: one cleaned form contains the other
    - LOW / NO_MATCH: little or no overlap
    """
    source_result = normalize_ingredient_name(source)
    target_result = normalize_ingredient_name(target)

    if not source_result.canonical or not target_result.canonical:
        return MatchResult(
            confidence=MatchConfidence.NO_MATCH,
            source_normalized=source_result.canonical,
            target_normalized=target_result.canonical,
            reason="Empty name after normalization",
        )

    if source_result.canonical == target_result.canonical:
        return MatchResult(
            confidence=MatchConfidence.EXACT,
            source_normalized=source_result.canonical,
            target_normalized=target_result.canonical,
        )

    source_words = set(source_result.canonical.split())
    target_words = set(target_result.canonical.split())

    if source_words == target_words:
        return MatchResult(
            confidence=MatchConfidence.HIGH,
            source_normalized=source_result.canonical,
            target_normalized=target_result.canonical,
            reason="Same words in different order",
        )

    if source_result.canonical in target_result.canonical:
        extra = target_result.canonical.replace(source_result.canonical, "").strip()
        if require_high_confidence:
            return MatchResult(
                confidence=MatchConfidence.AMBIGUOUS,
                source_normalized=source_result.canonical,
                target_normalized=target_result.canonical,
                reason=f"Source contained in target but extra words: {extra}",
            )
        return MatchResult(
            confidence=MatchConfidence.MEDIUM,
            source_normalized=source_result.canonical,
            target_normalized=target_result.canonical,
            reason="Source contained in target",
        )

    if target_result.canonical in source_result.canonical:
        extra = source_result.canonical.replace(target_result.canonical, "").strip()
        if require_high_confidence:
            return MatchResult(
                confidence=MatchConfidence.AMBIGUOUS,
                source_normalized=source_result.canonical,
                target_normalized=target_result.canonical,
                reason=f"Target contained in source but extra words: {extra}",
            )
        return MatchResult(
            confidence=MatchConfidence.MEDIUM,
            source_normalized=source_result.canonical,
            target_normalized=target_result.canonical,
            reason="Target contained in source",
        )

    intersection = source_words & target_words
    union = source_words | target_words
    if len(intersection) >= 2 and len(intersection) / len(union) >= 0.5:
        return MatchResult(
            confidence=MatchConfidence.AMBIGUOUS,
            source_normalized=source_result.canonical,
            target_normalized=target_result.canonical,
            reason=f"Significant word overlap: {intersection}",
        )

    if intersection and len(intersection) / len(union) >= 0.3:
        return MatchResult(
            confidence=MatchConfidence.LOW,
            source_normalized=source_result.canonical,
            target_normalized=target_result.canonical,
            reason=f"Some word overlap: {intersection}",
        )

    return MatchResult(
        confidence=MatchConfidence.NO_MATCH,
        source_normalized=source_result.canonical,
        target_normalized=target_result.canonical,
        reason="No significant overlap",
    )


def find_matching_ingredient_with_confidence(
    source_name: str,
    candidates: list[tuple[str, str]],
    require_high_confidence: bool = True,
) -> tuple[str | None, MatchResult | None]:
    """Find the best matching ingredient from a list of candidates (cheap local)."""
    best_match: tuple[str | None, MatchResult | None] = (None, None)
    high_confidence_matches: list[tuple[str, MatchResult]] = []
    ambiguous_matches: list[tuple[str, MatchResult]] = []

    for candidate_id, candidate_name in candidates:
        result = match_ingredient_names(
            source_name, candidate_name, require_high_confidence
        )

        if result.confidence == MatchConfidence.EXACT:
            return (candidate_id, result)

        if result.confidence == MatchConfidence.HIGH:
            high_confidence_matches.append((candidate_id, result))
        elif result.confidence == MatchConfidence.AMBIGUOUS:
            ambiguous_matches.append((candidate_id, result))

    if len(high_confidence_matches) == 1:
        return high_confidence_matches[0]
    if len(high_confidence_matches) > 1:
        return (
            None,
            MatchResult(
                confidence=MatchConfidence.AMBIGUOUS,
                source_normalized=normalize_ingredient_name(source_name).canonical,
                target_normalized="",
                reason=(
                    "Multiple high-confidence matches found: "
                    f"{[c[1].target_normalized for c in high_confidence_matches]}"
                ),
            ),
        )

    if require_high_confidence and ambiguous_matches:
        return (
            None,
            MatchResult(
                confidence=MatchConfidence.AMBIGUOUS,
                source_normalized=normalize_ingredient_name(source_name).canonical,
                target_normalized="",
                reason=(
                    "Ambiguous matches need user review: "
                    f"{[c[1].target_normalized for c in ambiguous_matches]}"
                ),
            ),
        )

    return best_match


def clean_display_name(name: str) -> str:
    """Clean an ingredient name for display (no LLM expansions)."""
    if not name or not name.strip():
        return name

    cleaned = _normalize_whitespace(name)
    cleaned = _remove_punctuation_for_display(cleaned)
    cleaned = _normalize_whitespace(cleaned)

    words = cleaned.split()
    return " ".join(word.capitalize() for word in words)
