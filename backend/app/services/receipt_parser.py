"""Deterministic receipt-text parser (FOOD-55 slice 2).

Turns OCR text into the same ``ParsedReceipt`` shape the vision model returns
plus ``ParseDiagnostics`` for the confidence gates. Rules only, no LLM calls.
U.S. grocery receipts only, matching the rest of the app.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.services.receipt_analyzer import ParsedReceipt, ParsedReceiptItem

# --- Store name -------------------------------------------------------------

KNOWN_STORES: dict[str, str] = {
    "whole foods": "Whole Foods Market",
    "trader joe": "Trader Joe's",
    "walmart": "Walmart",
    "wal-mart": "Walmart",
    "costco": "Costco Wholesale",
    "kroger": "Kroger",
    "safeway": "Safeway",
    "target": "Target",
    "aldi": "ALDI",
    "publix": "Publix",
    "h-e-b": "H-E-B",
    "heb": "H-E-B",
    "wegmans": "Wegmans",
    "sprouts": "Sprouts Farmers Market",
    "albertsons": "Albertsons",
    "meijer": "Meijer",
    "stop & shop": "Stop & Shop",
    "giant": "Giant",
    "food lion": "Food Lion",
    "winco": "WinCo Foods",
    "fred meyer": "Fred Meyer",
    "harris teeter": "Harris Teeter",
    "shoprite": "ShopRite",
    "sam's club": "Sam's Club",
    "ralphs": "Ralphs",
    "vons": "Vons",
    "hy-vee": "Hy-Vee",
}

_HEADER_NOISE = re.compile(
    r"(\d{3}[-.\s]\d{3}[-.\s]\d{4}"  # phone
    r"|\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b"  # date
    r"|\b\d{1,2}:\d{2}\b"  # time
    r"|\b(st|ste|suite|ave|avenue|blvd|rd|road|hwy|highway|dr|drive|street|ln|lane)\b\.?"
    r"|\b[a-z]{2}\s+\d{5}(-\d{4})?\b"  # state + zip
    r"|\b(store|str|tel|phone|manager|mgr|cashier|welcome|thank|receipt|order|transaction|reg|register)\b"
    r"|\b\d{5,}\b)",
    re.IGNORECASE,
)

# --- Summary / non-item lines ----------------------------------------------

_SUMMARY_TOTAL = re.compile(r"^\s*\**\s*(grand\s+)?total\b", re.IGNORECASE)
_SUMMARY_SUBTOTAL = re.compile(r"^\s*\**\s*sub\s*-?\s*total\b", re.IGNORECASE)
_SUMMARY_TAX = re.compile(
    r"^\s*\**\s*(sales\s+)?(tax|tx)\b|\b(tax|tx)\s+\d|\d\.\d+\s*%", re.IGNORECASE
)
_SUMMARY_OTHER = re.compile(
    r"\b(change|cash|visa|mastercard|master\s*card|amex|american\s+express|discover|debit|credit"
    r"|tender|balance|amount\s+due|payment|paid|approved|auth|account|card|chip|items?\s+sold"
    r"|total\s+savings|you\s+saved|savings|reward|points|loyalty|member|customer|return\s+policy"
    r"|thank|visit|survey|www\.|\.com|http|barcode|refund|net\s+sales|round\s*up|tip)\b",
    re.IGNORECASE,
)

_NON_FOOD_NAME = re.compile(
    r"\b(bag|bags|paper\s+bag|plastic\s+bag|bottle\s+dep(osit)?|crv|deposit|fee|coupon|cpn"
    r"|discount|disc|promo|md\s+disc|bag\s+credit|donation|gift\s+card|lottery|tobacco|cigarette"
    r"|cig|battery|batteries|detergent|soap|shampoo|toothpaste|tissues?|toilet\s+paper|paper\s+towels?"
    r"|foil|wrap|trash|napkins?|diapers?|wipes|deodorant|razors?|vitamins?|supplements?|dog\s+food|cat\s+food"
    r"|pet|litter|candles?|light\s*bulbs?|magazines?|greeting\s+cards?|floral|flowers)\b",
    re.IGNORECASE,
)

# Store brands spelled out in full ("GOOD&GATHER EGGS", "GREAT VALUE MILK").
_STORE_BRAND_WORDS = re.compile(
    r"^(good\s*&\s*gather|great\s+value|kirkland(\s+signature)?|market\s+pantry|simply\s+balanced"
    r"|365(\s+everyday\s+value)?|trader\s+joe'?s|o\s+organics|signature\s+select|private\s+selection"
    r"|simple\s+truth|h-?e-?b|whole\s+foods|sprouts|publix|kroger|wegmans|meijer|aldi|target|walmart)\b\s*",
    re.IGNORECASE,
)

# --- Item / quantity patterns -----------------------------------------------

_PRICE = r"(?P<neg>-\s*)?\$?\s?(?P<price>\d{1,4}[.,]\d{2})(?P<trailneg>\s*-)?"
_ITEM_LINE = re.compile(
    rf"^(?P<name>.*?[A-Za-z].*?)\s+{_PRICE}\s*(?P<flag>[A-Za-z*]{{1,2}})?\s*$"
)
_PRICE_ONLY = re.compile(rf"^\s*{_PRICE}\s*(?P<flag>[A-Za-z*]{{1,2}})?\s*$")
_LEADING_CODE = re.compile(r"^\s*\d{4,}\s+")
_TRAILING_CODE = re.compile(r"\s+\d{4,}\s*$")
_INLINE_CODE = re.compile(r"\s\d{6,}(?=\s|$)")

_UNIT_WORDS = r"(lbs?|lb\.|kg|oz|ea|each)"
# "2.14 lb @ 1.29 /lb" or "2.14 lb @ $1.29/ lb" (weight line)
_WEIGHT_LINE = re.compile(
    rf"(?P<qty>\d+(?:\.\d+)?)\s*(?P<unit>{_UNIT_WORDS})\s*@\s*\$?\s?(?P<unit_price>\d+(?:\.\d+)?)\s*/?\s*(lb|kg|oz|ea|each)?",
    re.IGNORECASE,
)
# "2 @ 1.99" / "2 X 1.99" / "2 @ $1.99 ea"
_COUNT_LINE = re.compile(
    r"(?<![\d.])(?P<qty>\d{1,3})\s*(?:@|x|X)\s*\$?\s?(?P<unit_price>\d+[.,]\d{2})",
)
_QTY_LINE = re.compile(r"\bqty\s*:?\s*(?P<qty>\d{1,3})\b", re.IGNORECASE)

_SIZE_TOKEN = re.compile(
    r"(?<![\w.])(?P<qty>\d+(?:\.\d+)?)\s*-?\s*(?P<unit>fl\.?\s*oz|oz|lbs?|gal(?:lon)?|qt|quart|pt|pint|ct|cnt|pk|pack|doz(?:en)?|ml|l|ltr|liter|g|kg)\b\.?",
    re.IGNORECASE,
)
_SIZE_UNIT_MAP = {
    "fl oz": "fl oz",
    "floz": "fl oz",
    "fl. oz": "fl oz",
    "oz": "oz",
    "lb": "lb",
    "lbs": "lb",
    "gal": "gallon",
    "gallon": "gallon",
    "qt": "quart",
    "quart": "quart",
    "pt": "pint",
    "pint": "pint",
    "ct": "each",
    "cnt": "each",
    "pk": "pack",
    "pack": "pack",
    "doz": "dozen",
    "dozen": "dozen",
    "ml": "ml",
    "l": "l",
    "ltr": "l",
    "liter": "l",
    "g": "g",
    "kg": "kg",
}

# Receipt shorthand → plain English. Only tokens that are unambiguous on a
# grocery receipt; anything else is left as-is for the LLM rungs downstream.
ABBREVIATIONS: dict[str, str] = {
    "org": "organic",
    "orgnc": "organic",
    "ognc": "organic",
    "chkn": "chicken",
    "chk": "chicken",
    "ckn": "chicken",
    "brst": "breast",
    "brsts": "breasts",
    "bnls": "boneless",
    "sknls": "skinless",
    "grnd": "ground",
    "gr": "ground",
    "bf": "beef",
    "prk": "pork",
    "trky": "turkey",
    "bnna": "banana",
    "bnnas": "bananas",
    "bana": "banana",
    "avo": "avocado",
    "avoc": "avocado",
    "tom": "tomato",
    "toms": "tomatoes",
    "tmto": "tomato",
    "pot": "potato",
    "pots": "potatoes",
    "ptato": "potato",
    "onn": "onion",
    "ylw": "yellow",
    "grn": "green",
    "rd": "red",
    "wht": "white",
    "whl": "whole",
    "mlk": "milk",
    "chs": "cheese",
    "chse": "cheese",
    "chdr": "cheddar",
    "shrd": "shredded",
    "yog": "yogurt",
    "ygrt": "yogurt",
    "yogt": "yogurt",
    "grk": "greek",
    "btr": "butter",
    "almd": "almond",
    "alm": "almond",
    "pnt": "peanut",
    "pb": "peanut butter",
    "strwbry": "strawberry",
    "strwb": "strawberry",
    "blubry": "blueberry",
    "rasp": "raspberry",
    "spin": "spinach",
    "lett": "lettuce",
    "brcli": "broccoli",
    "broc": "broccoli",
    "crrt": "carrot",
    "crrts": "carrots",
    "cuc": "cucumber",
    "ppr": "pepper",
    "pprs": "peppers",
    "garl": "garlic",
    "lem": "lemon",
    "lm": "lime",
    "orng": "orange",
    "apl": "apple",
    "apls": "apples",
    "brd": "bread",
    "wt": "wheat",
    "ww": "whole wheat",
    "trtl": "tortilla",
    "trtla": "tortilla",
    "rce": "rice",
    "bn": "bean",
    "bns": "beans",
    "blk": "black",
    "sce": "sauce",
    "psta": "pasta",
    "spag": "spaghetti",
    "olv": "olive",
    "vngr": "vinegar",
    "sgr": "sugar",
    "flr": "flour",
    "hny": "honey",
    "ckies": "cookies",
    "chc": "chocolate",
    "choc": "chocolate",
    "crm": "cream",
    "hvy": "heavy",
    "frz": "frozen",
    "frzn": "frozen",
    "frsh": "fresh",
    "swt": "sweet",
    "unswt": "unsweetened",
    "slcd": "sliced",
    "slc": "sliced",
    "lrg": "large",
    "lg": "large",
    "sm": "small",
    "med": "medium",
    "fam": "family",
    "pkg": "package",
    "sf": "sugar free",
    "gf": "gluten free",
    "ff": "fat free",
    "lf": "low fat",
    "ns": "no salt",
    "oj": "orange juice",
    "jce": "juice",
    "wtr": "water",
    "sprk": "sparkling",
    "sda": "soda",
    "cof": "coffee",
    "cffe": "coffee",
    "eggs": "eggs",
    "egg": "egg",
    "salm": "salmon",
    "shrmp": "shrimp",
    "tna": "tuna",
    "bcn": "bacon",
    "sasg": "sausage",
    "ssg": "sausage",
    "hm": "ham",
}

# Produce is usually sold by weight or by count; without a weight line we
# cannot tell, so the unit stays missing and the gate counts it.
_PRODUCE_HINT = re.compile(
    r"\b(banana|apple|orange|lemon|lime|avocado|tomato|potato|onion|pepper|grape|peach|pear|plum"
    r"|mango|cherry|cherries|berry|berries|melon|squash|zucchini|cucumber|carrot|broccoli|cauliflower"
    r"|lettuce|spinach|kale|celery|garlic|ginger|corn|mushroom|cabbage|asparagus|grapefruit|kiwi|nectarine)(?:es|s)?\b",
    re.IGNORECASE,
)

# Store-brand prefixes that carry no meaning for the pantry (Great Value,
# Kirkland Signature, Good & Gather, Market Pantry, Simply Balanced, ...).
STORE_BRAND_PREFIXES = {"gv", "ks", "gg", "mp", "sb", "ht", "tj", "pl", "sig"}

_LOWER_WORDS = {"and", "or", "of", "with", "in", "the", "a", "n"}


@dataclass
class ParsedLine:
    """One recognized item line plus the raw evidence the gates care about."""

    store_item_name: str
    ingredient_name: str
    price: float | None
    is_food: bool
    quantity: str | None
    unit: str | None
    explicit_quantity: bool
    explicit_unit: bool


@dataclass
class ParseDiagnostics:
    line_count: int = 0
    item_count: int = 0
    food_item_count: int = 0
    subtotal: float | None = None
    total: float | None = None
    tax: float | None = None
    item_price_sum: float = 0.0
    # Food items where neither quantity nor unit came from the receipt.
    missing_qty_unit_count: int = 0
    store_name_source: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def missing_qty_unit_ratio(self) -> float:
        if self.food_item_count == 0:
            return 1.0
        return self.missing_qty_unit_count / self.food_item_count


@dataclass
class ParseOutcome:
    receipt: ParsedReceipt
    diagnostics: ParseDiagnostics
    lines: list[ParsedLine]


# Frequent Tesseract confusions on thermal receipts.
_OCR_OZ = re.compile(r"(?<=\d)\s*O?0Z\b", re.IGNORECASE)
_OCR_DOLLAR = re.compile(r"(?<![A-Za-z0-9])S(?=\d{1,4}[.,]\d{2}\b)")
_OCR_PRICE_L = re.compile(r"(?<![A-Za-z0-9])[lI](?=\d{0,3}[.,]\d{2}\b)")
_OCR_WORD = re.compile(r"[A-Za-z0-9]{3,}")
_DIGIT_TO_LETTER = str.maketrans({"0": "O", "1": "I", "3": "E", "4": "A", "5": "S", "8": "B"})


def _fix_word(match: re.Match) -> str:
    word = match.group(0)
    letters = sum(ch.isalpha() for ch in word)
    digits = len(word) - letters
    # "T0TAL", "BNN4S", "0RG": a mostly-alphabetic word with a stray digit.
    # Leave size tokens ("16OZ"), codes, and trailing counts ("BUTTER16") alone.
    if (
        digits == 0
        or letters < 2
        or digits * 2 > letters
        or word[-1].isdigit()
        or _SIZE_TOKEN.fullmatch(word)
    ):
        return word
    return word.translate(_DIGIT_TO_LETTER)


def clean_ocr_line(line: str) -> str:
    """Fix character confusions that break size/price/summary patterns
    ("320Z" -> "32 OZ", "S9.99" -> "$9.99", "l.48" -> "1.48", "T0TAL" -> "TOTAL")."""
    line = _OCR_OZ.sub(" OZ", line)
    line = _OCR_DOLLAR.sub("$", line)
    line = _OCR_PRICE_L.sub("1", line)
    line = _OCR_WORD.sub(_fix_word, line)
    return line


def _to_float(text: str) -> float | None:
    try:
        return float(text.replace(",", ".").replace(" ", ""))
    except ValueError:
        return None


def _format_qty(value: float) -> str:
    if value == int(value):
        return str(int(value))
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _last_price(line: str) -> float | None:
    matches = re.findall(r"\$?\s?(\d{1,5}[.,]\d{2})(?!\d)", line)
    if not matches:
        return None
    return _to_float(matches[-1])


def _clean_store_item_name(name: str) -> str:
    cleaned = _LEADING_CODE.sub("", name)
    cleaned = _TRAILING_CODE.sub("", cleaned)
    cleaned = _INLINE_CODE.sub("", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -*:")
    return cleaned


def expand_abbreviations(store_item_name: str) -> str:
    """Rule-based expansion of receipt shorthand into a readable grocery name."""
    text = _SIZE_TOKEN.sub(" ", store_item_name)
    stripped = _STORE_BRAND_WORDS.sub("", text.strip())
    if _letter_count(stripped) >= 2:
        text = stripped
    text = re.sub(r"[^\w%'&/-]+", " ", text)
    words: list[str] = []
    raw_tokens = text.split()
    for position, raw in enumerate(raw_tokens):
        token = raw.strip("-/'")
        if not token or token.isdigit():
            continue
        lowered = token.lower()
        if position == 0 and len(raw_tokens) > 1 and lowered in STORE_BRAND_PREFIXES:
            continue
        expansion = ABBREVIATIONS.get(lowered)
        if expansion:
            words.extend(expansion.split())
            continue
        words.append(lowered)

    if not words:
        return store_item_name.strip().title()

    titled = [
        word if (index > 0 and word in _LOWER_WORDS) else word.capitalize()
        for index, word in enumerate(words)
    ]
    return " ".join(titled)


def _size_from_name(name: str) -> tuple[str | None, str | None]:
    match = _SIZE_TOKEN.search(name)
    if not match:
        return None, None
    unit_key = re.sub(r"\.", "", match.group("unit").lower())
    unit_key = re.sub(r"\s+", " ", unit_key)
    unit = _SIZE_UNIT_MAP.get(unit_key) or _SIZE_UNIT_MAP.get(unit_key.replace(" ", ""))
    if unit is None:
        return None, None
    qty = _to_float(match.group("qty"))
    if qty is None or qty <= 0:
        return None, None
    return _format_qty(qty), unit


@dataclass(frozen=True)
class _Modifier:
    """A quantity modifier ("2.14 lb @ 0.69/lb", "2 @ 1.99", "QTY 2")."""

    quantity: str
    unit: str | None
    start: int
    end: int


def _modifier_from_line(line: str) -> _Modifier | None:
    match = _WEIGHT_LINE.search(line)
    if match:
        qty = _to_float(match.group("qty"))
        if qty is not None and qty > 0:
            unit_raw = match.group("unit").lower().rstrip(".")
            unit = {"lbs": "lb", "lb": "lb", "kg": "kg", "oz": "oz", "ea": "each", "each": "each"}[unit_raw]
            return _Modifier(_format_qty(qty), unit, match.start(), match.end())

    match = _QTY_LINE.search(line) or _COUNT_LINE.search(line)
    if match:
        qty = int(match.group("qty"))
        if qty > 0:
            return _Modifier(str(qty), None, match.start(), match.end())
    return None


def _letter_count(text: str) -> int:
    return sum(ch.isalpha() for ch in text)


def _is_summary(line: str) -> bool:
    return bool(
        _SUMMARY_TOTAL.match(line)
        or _SUMMARY_SUBTOTAL.match(line)
        or _SUMMARY_TAX.search(line)
        or _SUMMARY_OTHER.search(line)
    )


def _detect_store_name(lines: list[str], first_item_index: int) -> tuple[str | None, str | None]:
    haystack = "\n".join(lines[:15]).lower()
    for key, canonical in KNOWN_STORES.items():
        if key in haystack:
            return canonical, "known_store"

    header = lines[:first_item_index] if first_item_index > 0 else lines[:12]
    for line in header:
        candidate = line.strip(" *-_=")
        letters = _letter_count(candidate)
        if letters < 3 or letters / max(len(candidate), 1) < 0.6:
            continue
        if _HEADER_NOISE.search(candidate) or _is_summary(candidate):
            continue
        return expand_abbreviations(candidate), "header_line"
    return None, None


def _apply_modifier(target: ParsedLine, modifier: _Modifier) -> None:
    target.quantity = modifier.quantity
    target.explicit_quantity = True
    if modifier.unit is not None:
        target.unit = modifier.unit
        target.explicit_unit = True


def parse_receipt_text(text: str) -> ParseOutcome:
    raw_lines = [clean_ocr_line(line).strip() for line in text.splitlines()]
    lines = [line for line in raw_lines if line]
    diagnostics = ParseDiagnostics(line_count=len(lines))

    parsed: list[ParsedLine] = []
    pending: _Modifier | None = None
    # Kroger/Whole Foods print the name on one line and the weight + price on
    # the next; remember a name-only line until we see what follows it.
    pending_name: str | None = None
    first_item_index = -1
    seen_total = False

    for index, line in enumerate(lines):
        if _is_summary(line):
            price = _last_price(line)
            if _SUMMARY_SUBTOTAL.match(line):
                diagnostics.subtotal = price
            elif _SUMMARY_TOTAL.match(line):
                if price is not None:
                    diagnostics.total = price
                    seen_total = True
            elif _SUMMARY_TAX.search(line) and diagnostics.tax is None:
                diagnostics.tax = price
            pending_name = None
            continue

        if seen_total:
            # Anything after TOTAL is payment/footer noise.
            continue

        item_match = _ITEM_LINE.match(line)
        modifier = _modifier_from_line(line)
        inline_modifier: _Modifier | None = None
        price: float | None = None

        if modifier is not None:
            prefix = _clean_store_item_name(line[: modifier.start])
            trailing_price = _last_price(line[modifier.end :])
            if item_match is not None and _letter_count(prefix) >= 2:
                # Walmart-style single line: "BANANAS 2.14 lb @ 0.69/lb 1.48 F".
                inline_modifier = modifier
                name = prefix
                price = trailing_price
            elif pending_name is not None:
                # "ORG BNNAS" / "2.14 lb @ 0.69/lb 1.48 F"
                inline_modifier = modifier
                name = pending_name
                price = trailing_price
                pending_name = None
            else:
                # Standalone modifier line: applies to the item above when it
                # has no quantity yet, otherwise to the next item.
                if parsed and not parsed[-1].explicit_quantity:
                    _apply_modifier(parsed[-1], modifier)
                else:
                    pending = modifier
                continue
        elif item_match is None:
            price_only = _PRICE_ONLY.match(line)
            if pending_name is not None and price_only:
                # "ALMOND BUTTER" / "9.99 F": OCR split the price column off.
                name = pending_name
                pending_name = None
                price = _to_float(price_only.group("price"))
                if price_only.group("neg") or price_only.group("trailneg"):
                    price = -(price or 0.0)
            else:
                candidate = _clean_store_item_name(line)
                pending_name = (
                    candidate
                    if _letter_count(candidate) >= 3 and not _HEADER_NOISE.search(candidate)
                    else None
                )
                continue
        else:
            name = _clean_store_item_name(item_match.group("name"))
            pending_name = None
            price = _to_float(item_match.group("price"))
            if item_match.group("neg") or item_match.group("trailneg"):
                price = -(price or 0.0)

        if _letter_count(name) < 2:
            continue

        if first_item_index < 0:
            first_item_index = index

        is_food = not (_NON_FOOD_NAME.search(name) or (price is not None and price < 0))
        quantity, unit = _size_from_name(name)
        explicit_quantity = quantity is not None
        explicit_unit = unit is not None

        applied = inline_modifier or pending
        if applied is not None:
            quantity = applied.quantity
            explicit_quantity = True
            if applied.unit is not None:
                unit = applied.unit
                explicit_unit = True
            pending = None

        ingredient_name = expand_abbreviations(name)

        if quantity is None:
            # A single priced line is one purchase.
            quantity = "1"
        if unit is None and not _PRODUCE_HINT.search(ingredient_name):
            unit = "each"

        parsed.append(
            ParsedLine(
                store_item_name=name,
                ingredient_name=ingredient_name,
                price=price,
                is_food=is_food,
                quantity=quantity,
                unit=unit,
                explicit_quantity=explicit_quantity,
                explicit_unit=explicit_unit,
            )
        )

    store_name, source = _detect_store_name(lines, first_item_index)
    diagnostics.store_name_source = source

    items: list[ParsedReceiptItem] = []
    for line in parsed:
        items.append(
            ParsedReceiptItem(
                store_item_name=line.store_item_name,
                ingredient_name=line.ingredient_name,
                is_food=line.is_food,
                quantity=line.quantity,
                unit=line.unit,
            )
        )
        if line.price is not None:
            diagnostics.item_price_sum += line.price
        if line.is_food:
            diagnostics.food_item_count += 1
            if not line.explicit_quantity and line.unit is None:
                diagnostics.missing_qty_unit_count += 1

    diagnostics.item_count = len(items)
    diagnostics.item_price_sum = round(diagnostics.item_price_sum, 2)

    return ParseOutcome(
        receipt=ParsedReceipt(store_name=store_name, items=items),
        diagnostics=diagnostics,
        lines=parsed,
    )
