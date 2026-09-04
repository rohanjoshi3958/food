"""Ingredient name normalization for receipt processing.

This module provides robust normalization rules to match ingredient names
from grocery receipts to canonical forms. It handles:
- Common grocery abbreviations (CHKN → chicken, BRST → breast, etc.)
- Case and whitespace normalization
- Punctuation tolerance
- Plural/singular normalization
- Product qualifiers (organic, fresh, frozen, etc.)

Normalization rules are defined separately from display names. The canonical
form is used for matching; the original or a cleaned display name is preserved
for the user interface.
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
    expanded_abbreviations: list[str]


@dataclass
class MatchResult:
    """Result of matching two ingredient names."""

    confidence: MatchConfidence
    source_normalized: str
    target_normalized: str
    reason: str | None = None


ABBREVIATION_MAP: dict[str, str] = {
    "chkn": "chicken",
    "chk": "chicken",
    "ckn": "chicken",
    "brst": "breast",
    "brsts": "breasts",
    "thgh": "thigh",
    "thghs": "thighs",
    "wng": "wing",
    "wngs": "wings",
    "drm": "drum",
    "drms": "drums",
    "drumstk": "drumstick",
    "drumstks": "drumsticks",
    "bnls": "boneless",
    "bnlss": "boneless",
    "sklss": "skinless",
    "sknls": "skinless",
    "org": "organic",
    "orgn": "organic",
    "orgnic": "organic",
    "frsh": "fresh",
    "frzn": "frozen",
    "froz": "frozen",
    "grnd": "ground",
    "grd": "ground",
    "whl": "whole",
    "bf": "beef",
    "prk": "pork",
    "trky": "turkey",
    "turk": "turkey",
    "slm": "salmon",
    "salmn": "salmon",
    "shmp": "shrimp",
    "shrmp": "shrimp",
    "tna": "tuna",
    "veg": "vegetable",
    "vegs": "vegetables",
    "veggie": "vegetable",
    "veggies": "vegetables",
    "vgtbl": "vegetable",
    "vgtbls": "vegetables",
    "frt": "fruit",
    "frts": "fruits",
    "tom": "tomato",
    "toms": "tomatoes",
    "tomt": "tomato",
    "tomts": "tomatoes",
    "pot": "potato",
    "pots": "potatoes",
    "potat": "potato",
    "potats": "potatoes",
    "onio": "onion",
    "onns": "onions",
    "crrt": "carrot",
    "crrts": "carrots",
    "brcl": "broccoli",
    "broc": "broccoli",
    "brcc": "broccoli",
    "cauliflwr": "cauliflower",
    "cauli": "cauliflower",
    "cflwr": "cauliflower",
    "spnch": "spinach",
    "letc": "lettuce",
    "lett": "lettuce",
    "rom": "romaine",
    "rmn": "romaine",
    "cucum": "cucumber",
    "cucmbr": "cucumber",
    "pepr": "pepper",
    "peprs": "peppers",
    "ppr": "pepper",
    "pprs": "peppers",
    "grn": "green",
    "rd": "red",
    "ylw": "yellow",
    "orng": "orange",
    "wht": "white",
    "brwn": "brown",
    "blk": "black",
    "sw": "sweet",
    "swt": "sweet",
    "bn": "bean",
    "bns": "beans",
    "rc": "rice",
    "psta": "pasta",
    "spag": "spaghetti",
    "spgti": "spaghetti",
    "mac": "macaroni",
    "chz": "cheese",
    "chs": "cheese",
    "ched": "cheddar",
    "chdr": "cheddar",
    "mozz": "mozzarella",
    "moz": "mozzarella",
    "parm": "parmesan",
    "parmn": "parmesan",
    "parmesn": "parmesan",
    "crm": "cream",
    "mlk": "milk",
    "yog": "yogurt",
    "ygrt": "yogurt",
    "yogt": "yogurt",
    "gk": "greek",
    "grk": "greek",
    "bttr": "butter",
    "btr": "butter",
    "marg": "margarine",
    "eg": "egg",
    "egs": "eggs",
    "brd": "bread",
    "ww": "whole wheat",
    "wwht": "whole wheat",
    "mltgrn": "multigrain",
    "multigr": "multigrain",
    "flr": "flour",
    "flour": "flour",
    "sgr": "sugar",
    "sugr": "sugar",
    "brn": "brown",
    "slt": "salt",
    "pep": "pepper",
    "seas": "seasoning",
    "ssng": "seasoning",
    "spce": "spice",
    "spcs": "spices",
    "hrb": "herb",
    "hrbs": "herbs",
    "basil": "basil",
    "bsl": "basil",
    "oregn": "oregano",
    "oreg": "oregano",
    "thym": "thyme",
    "rsemry": "rosemary",
    "rosemry": "rosemary",
    "grlc": "garlic",
    "garl": "garlic",
    "gingr": "ginger",
    "gngr": "ginger",
    "cinn": "cinnamon",
    "cinnmn": "cinnamon",
    "van": "vanilla",
    "vanla": "vanilla",
    "ol": "oil",
    "olv": "olive",
    "oliv": "olive",
    "evoo": "extra virgin olive oil",
    "veg oil": "vegetable oil",
    "canla": "canola",
    "nut": "nut",
    "nts": "nuts",
    "alm": "almond",
    "almnd": "almond",
    "almds": "almonds",
    "wlnt": "walnut",
    "wlnts": "walnuts",
    "pcan": "pecan",
    "pcans": "pecans",
    "pnut": "peanut",
    "pnuts": "peanuts",
    "pb": "peanut butter",
    "almd btr": "almond butter",
    "jly": "jelly",
    "jlly": "jelly",
    "jam": "jam",
    "bnna": "banana",
    "bnnas": "bananas",
    "bana": "banana",
    "banas": "bananas",
    "appl": "apple",
    "appls": "apples",
    "apl": "apple",
    "apls": "apples",
    "orng": "orange",
    "orngs": "oranges",
    "strwb": "strawberry",
    "strwbry": "strawberry",
    "strwbs": "strawberries",
    "blueb": "blueberry",
    "bluebry": "blueberry",
    "bluebs": "blueberries",
    "raspb": "raspberry",
    "raspbry": "raspberry",
    "raspbs": "raspberries",
    "grap": "grape",
    "grps": "grapes",
    "lemn": "lemon",
    "lmn": "lemon",
    "lmns": "lemons",
    "lim": "lime",
    "lms": "limes",
    "avoc": "avocado",
    "avcd": "avocado",
    "avcdo": "avocado",
    "avcds": "avocados",
    "mango": "mango",
    "mngos": "mangos",
    "peach": "peach",
    "pchs": "peaches",
    "pear": "pear",
    "prs": "pears",
    "melon": "melon",
    "wtrmln": "watermelon",
    "waterml": "watermelon",
    "cntlpe": "cantaloupe",
    "cantlp": "cantaloupe",
    "honeydw": "honeydew",
    "hnydw": "honeydew",
    "lt": "light",
    "lf": "low fat",
    "lowfat": "low fat",
    "ff": "fat free",
    "fatfr": "fat free",
    "nf": "nonfat",
    "nonfat": "nonfat",
    "skim": "skim",
    "twop": "2%",
    "twopct": "2%",
    "onep": "1%",
    "onepct": "1%",
    "sml": "small",
    "sm": "small",
    "med": "medium",
    "lrg": "large",
    "lg": "large",
    "xlg": "extra large",
    "xl": "extra large",
    "fam": "family",
    "pk": "pack",
    "pkg": "package",
    "ct": "count",
    "btl": "bottle",
    "cn": "can",
    "jr": "jar",
    "bg": "bag",
    "bx": "box",
    "dz": "dozen",
    "doz": "dozen",
    "ea": "each",
    "sngl": "single",
    "dbl": "double",
    "trpl": "triple",
    "asst": "assorted",
    "asstd": "assorted",
    "mix": "mixed",
    "mxd": "mixed",
    "sel": "select",
    "slct": "select",
    "prem": "premium",
    "prm": "premium",
    "val": "value",
    "econ": "economy",
    "sav": "savings",
    "hvst": "harvest",
    "harv": "harvest",
    "nat": "natural",
    "natl": "natural",
    "natur": "natural",
    "ntrl": "natural",
    "pure": "pure",
    "orig": "original",
    "originl": "original",
    "clas": "classic",
    "clsc": "classic",
    "trad": "traditional",
    "tradl": "traditional",
    "hmstyl": "homestyle",
    "hmstyle": "homestyle",
    "cntry": "country",
    "ctry": "country",
    "frm": "farm",
    "frmr": "farmer",
    "frmrs": "farmers",
    "rnchr": "rancher",
    "rnch": "ranch",
    "prdc": "produce",
    "deli": "deli",
    "bkry": "bakery",
    "bakry": "bakery",
    "frozn": "frozen",
    "refrig": "refrigerated",
    "rfrgrtd": "refrigerated",
    "unswt": "unsweetened",
    "unswtnd": "unsweetened",
    "swtnd": "sweetened",
    "swt": "sweet",
    "salted": "salted",
    "unsalt": "unsalted",
    "unsltd": "unsalted",
    "roast": "roasted",
    "rstd": "roasted",
    "raw": "raw",
    "cook": "cooked",
    "ckd": "cooked",
    "slcd": "sliced",
    "slicd": "sliced",
    "diced": "diced",
    "dcd": "diced",
    "chppd": "chopped",
    "chopd": "chopped",
    "mincd": "minced",
    "shredf": "shredded",
    "shred": "shredded",
    "shrd": "shredded",
    "whip": "whipped",
    "whpd": "whipped",
    "crshd": "crushed",
    "peld": "peeled",
    "cubed": "cubed",
    "cbd": "cubed",
}

QUALIFIER_PREFIXES: set[str] = {
    "organic",
    "org",
    "fresh",
    "frozen",
    "natural",
    "premium",
    "select",
    "choice",
    "prime",
    "lean",
    "extra lean",
    "store brand",
    "private selection",
    "signature",
    "great value",
    "kirkland",
    "market pantry",
    "good & gather",
    "simply balanced",
    "365",
    "whole foods",
    "trader joes",
    "aldi",
    "kroger",
    "safeway",
    "publix",
    "wegmans",
    "costco",
    "sams club",
    "value",
    "economy",
    "family pack",
    "bulk",
    "club pack",
}

IRREGULAR_PLURALS: dict[str, str] = {
    "tomatoes": "tomato",
    "potatoes": "potato",
    "berries": "berry",
    "cherries": "cherry",
    "leaves": "leaf",
    "loaves": "loaf",
    "halves": "half",
    "knives": "knife",
    "wives": "wife",
    "shelves": "shelf",
    "selves": "self",
    "children": "child",
    "feet": "foot",
    "teeth": "tooth",
    "geese": "goose",
    "mice": "mouse",
    "men": "man",
    "women": "woman",
    "people": "person",
    "fish": "fish",
    "sheep": "sheep",
    "deer": "deer",
    "moose": "moose",
    "salmon": "salmon",
    "trout": "trout",
    "shrimp": "shrimp",
    "squid": "squid",
    "octopi": "octopus",
    "cacti": "cactus",
    "fungi": "fungus",
    "nuclei": "nucleus",
    "radii": "radius",
    "alumni": "alumnus",
    "criteria": "criterion",
    "phenomena": "phenomenon",
    "data": "datum",
    "media": "medium",
    "analyses": "analysis",
    "bases": "basis",
    "crises": "crisis",
    "diagnoses": "diagnosis",
    "hypotheses": "hypothesis",
    "oases": "oasis",
    "parentheses": "parenthesis",
    "syntheses": "synthesis",
    "theses": "thesis",
}


def _normalize_whitespace(text: str) -> str:
    """Collapse multiple whitespace chars to single space and strip."""
    return re.sub(r"\s+", " ", text.strip())


def _remove_punctuation(text: str) -> str:
    """Remove punctuation except hyphens between words."""
    result = re.sub(r"[^\w\s-]", "", text)
    result = re.sub(r"(?<!\w)-|-(?!\w)", "", result)
    return result


def _singularize(word: str) -> str:
    """Convert a word to singular form."""
    word_lower = word.lower()
    if word_lower in IRREGULAR_PLURALS:
        return IRREGULAR_PLURALS[word_lower]
    if len(word) <= 2:
        return word
    if word_lower.endswith("ies") and len(word) > 3:
        return word[:-3] + "y"
    if word_lower.endswith("es"):
        if word_lower.endswith(("sses", "xes", "ches", "shes", "zes")):
            return word[:-2]
        if word_lower.endswith("oes") and word_lower not in ("shoes", "toes"):
            return word[:-2]
        if word_lower.endswith("ves"):
            return word[:-3] + "f"
    if word_lower.endswith("s") and not word_lower.endswith(("ss", "us", "is")):
        return word[:-1]

    return word


def _expand_abbreviations(text: str) -> tuple[str, list[str]]:
    """Expand known abbreviations in the text.

    Returns the expanded text and a list of abbreviations that were expanded.
    """
    words = text.lower().split()
    expanded = []
    expansions_made = []

    for word in words:
        cleaned = _remove_punctuation(word).lower()
        if cleaned in ABBREVIATION_MAP:
            expansion = ABBREVIATION_MAP[cleaned]
            expanded.append(expansion)
            expansions_made.append(f"{cleaned}→{expansion}")
        else:
            expanded.append(word)

    return " ".join(expanded), expansions_made


def _strip_qualifiers(text: str) -> str:
    """Remove common product qualifiers/brand prefixes from the name."""
    text_lower = text.lower()

    for qualifier in sorted(QUALIFIER_PREFIXES, key=len, reverse=True):
        pattern = re.compile(r"^" + re.escape(qualifier) + r"\s+", re.IGNORECASE)
        text_lower = pattern.sub("", text_lower)
        pattern = re.compile(r"\s+" + re.escape(qualifier) + r"$", re.IGNORECASE)
        text_lower = pattern.sub("", text_lower)

    return text_lower.strip()


def _normalize_words(text: str) -> str:
    """Singularize each word and sort for canonical comparison."""
    words = text.split()
    normalized = [_singularize(word) for word in words]
    return " ".join(normalized)


def normalize_ingredient_name(name: str) -> NormalizationResult:
    """Normalize an ingredient name for matching purposes.

    This function:
    1. Lowercases and normalizes whitespace
    2. Removes punctuation
    3. Expands known abbreviations
    4. Optionally strips qualifier prefixes
    5. Singularizes words

    Returns a NormalizationResult with:
    - original: the input name
    - normalized: cleaned but human-readable form
    - canonical: form used for matching (fully normalized)
    - expanded_abbreviations: list of abbreviations that were expanded
    """
    if not name or not name.strip():
        return NormalizationResult(
            original=name,
            normalized="",
            canonical="",
            expanded_abbreviations=[],
        )

    step1 = _normalize_whitespace(name.lower())
    step2 = _remove_punctuation(step1)
    step3, expansions = _expand_abbreviations(step2)
    step3 = _normalize_whitespace(step3)
    normalized = step3

    step4 = _strip_qualifiers(step3)
    step5 = _normalize_words(step4)
    canonical = _normalize_whitespace(step5)

    return NormalizationResult(
        original=name,
        normalized=normalized,
        canonical=canonical,
        expanded_abbreviations=expansions,
    )


def compute_canonical_key(name: str | None, unit: str | None = None) -> tuple[str, str]:
    """Compute a canonical key for ingredient matching.

    This key can be used to determine if two ingredients refer to the same
    product for merging purposes. Returns (canonical_name, normalized_unit).
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
    """Determine if two ingredient names refer to the same product.

    Args:
        source: The source ingredient name (e.g., from a receipt)
        target: The target ingredient name (e.g., from inventory)
        require_high_confidence: If True, only return HIGH/EXACT confidence
            for clear matches; otherwise may return MEDIUM for partial matches

    Returns:
        MatchResult with confidence level and normalized forms.

    Confidence levels:
    - EXACT: Canonical forms are identical
    - HIGH: Normalized forms match after abbreviation expansion
    - MEDIUM: One canonical form contains the other (partial match)
    - LOW: Some word overlap but not a clear match
    - AMBIGUOUS: Match is uncertain, needs user review
    - NO_MATCH: No reasonable match found
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

    if source_result.normalized == target_result.normalized:
        return MatchResult(
            confidence=MatchConfidence.HIGH,
            source_normalized=source_result.canonical,
            target_normalized=target_result.canonical,
            reason="Match after abbreviation expansion",
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
        extra_words = set(extra.split())
        qualifier_words = {
            _singularize(w.lower())
            for q in QUALIFIER_PREFIXES
            for w in q.split()
        }
        if extra_words.issubset(qualifier_words):
            return MatchResult(
                confidence=MatchConfidence.HIGH,
                source_normalized=source_result.canonical,
                target_normalized=target_result.canonical,
                reason="Source is core ingredient within target",
            )
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
        extra_words = set(extra.split())
        qualifier_words = {
            _singularize(w.lower())
            for q in QUALIFIER_PREFIXES
            for w in q.split()
        }
        if extra_words.issubset(qualifier_words):
            return MatchResult(
                confidence=MatchConfidence.HIGH,
                source_normalized=source_result.canonical,
                target_normalized=target_result.canonical,
                reason="Target is core ingredient within source",
            )
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
    """Find the best matching ingredient from a list of candidates.

    Args:
        source_name: The ingredient name to match
        candidates: List of (id, name) tuples to match against
        require_high_confidence: If True, only return EXACT/HIGH matches

    Returns:
        Tuple of (matched_id, MatchResult) or (None, None) if no good match.
        Returns (None, MatchResult) with AMBIGUOUS confidence if multiple
        potential matches are found.
    """
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
    elif len(high_confidence_matches) > 1:
        return (
            None,
            MatchResult(
                confidence=MatchConfidence.AMBIGUOUS,
                source_normalized=normalize_ingredient_name(source_name).canonical,
                target_normalized="",
                reason=f"Multiple high-confidence matches found: {[c[1].target_normalized for c in high_confidence_matches]}",
            ),
        )

    if require_high_confidence:
        if ambiguous_matches:
            return (
                None,
                MatchResult(
                    confidence=MatchConfidence.AMBIGUOUS,
                    source_normalized=normalize_ingredient_name(source_name).canonical,
                    target_normalized="",
                    reason=f"Ambiguous matches need user review: {[c[1].target_normalized for c in ambiguous_matches]}",
                ),
            )

    return best_match


def clean_display_name(name: str) -> str:
    """Clean an ingredient name for display purposes.

    Unlike full normalization, this preserves useful information like
    "organic" while fixing obvious issues like excessive whitespace
    and expanding obvious abbreviations.
    """
    if not name or not name.strip():
        return name

    cleaned = _normalize_whitespace(name)
    cleaned = _remove_punctuation(cleaned)
    expanded, _ = _expand_abbreviations(cleaned)
    expanded = _normalize_whitespace(expanded)

    words = expanded.split()
    result = " ".join(word.capitalize() for word in words)

    return result
