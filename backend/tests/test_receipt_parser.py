"""Unit tests for the deterministic OCR-text receipt parser (FOOD-55 slice 2)."""
import pytest

from app.services.receipt_analyzer import ParsedReceipt
from app.services.receipt_parser import expand_abbreviations, parse_receipt_text

WALMART_TEXT = """Walmart
Save money. Live better.
ST# 01234 OP# 000123 TE# 12 TR# 04567
GV WHL MLK 1 GAL 007874237 3.48 N
BANANAS 4011 2.31 lb @ 0.58/lb 1.34 N
GRND BF 80/20 021130 6.97 N
KIND BAR 2 @ 1.99 3.98 N
PAPER BAG 003700 0.10 X
SUBTOTAL 15.87
TAX 1 8.25% 0.01
TOTAL 15.88
DEBIT TEND 15.88
CHANGE DUE 0.00
"""

SPLIT_COLUMN_TEXT = """JOE'S CORNER MARKET
AVOCADOS
3.00
EGGS LRG 12 CT
4.29
COUPON
-1.00
TOTAL 6.29
"""


def _by_store_name(receipt: ParsedReceipt) -> dict[str, object]:
    return {item.store_item_name: item for item in receipt.items}


class TestStoreName:
    def test_known_store_is_canonicalized(self, ocr_receipt_text):
        outcome = parse_receipt_text(ocr_receipt_text)
        assert outcome.receipt.store_name == "Whole Foods Market"
        assert outcome.diagnostics.store_name_source == "known_store"

    def test_unknown_store_uses_first_clean_header_line(self):
        outcome = parse_receipt_text(SPLIT_COLUMN_TEXT)
        assert outcome.receipt.store_name == "Joe's Corner Market"
        assert outcome.diagnostics.store_name_source == "header_line"

    def test_address_and_phone_lines_are_skipped(self):
        outcome = parse_receipt_text(
            "512-555-0100\n01/02/2026 10:15\nCORNER GROCER\nMILK 3.49\nTOTAL 3.49\n"
        )
        assert outcome.receipt.store_name == "Corner Grocer"


class TestLineItems:
    def test_name_then_weight_line_layout(self, ocr_receipt_text):
        receipt = parse_receipt_text(ocr_receipt_text).receipt
        items = _by_store_name(receipt)

        bananas = items["ORG BNNAS"]
        assert bananas.ingredient_name == "Organic Bananas"
        assert bananas.quantity == "2.14"
        assert bananas.unit == "lb"
        assert bananas.is_food is True

        almond = items["ALMOND BUTTER 16 OZ"]
        assert almond.ingredient_name == "Almond Butter"
        assert (almond.quantity, almond.unit) == ("16", "oz")

        yogurt = items["GRK YOGURT 32OZ"]
        assert yogurt.ingredient_name == "Greek Yogurt"
        assert (yogurt.quantity, yogurt.unit) == ("32", "oz")

    def test_single_line_layout_with_inline_weight_and_count(self):
        receipt = parse_receipt_text(WALMART_TEXT).receipt
        items = _by_store_name(receipt)

        assert receipt.store_name == "Walmart"
        assert items["BANANAS"].quantity == "2.31"
        assert items["BANANAS"].unit == "lb"
        assert items["KIND BAR"].quantity == "2"
        assert items["GV WHL MLK 1 GAL"].ingredient_name == "Whole Milk"  # store-brand prefix dropped
        assert (items["GV WHL MLK 1 GAL"].quantity, items["GV WHL MLK 1 GAL"].unit) == ("1", "gallon")
        assert items["GRND BF 80/20"].ingredient_name == "Ground Beef 80/20"
        # Item codes are stripped from the store name.
        assert "021130" not in items["GRND BF 80/20"].store_item_name

    def test_price_split_onto_next_line(self):
        outcome = parse_receipt_text(SPLIT_COLUMN_TEXT)
        items = _by_store_name(outcome.receipt)
        assert set(items) == {"AVOCADOS", "EGGS LRG 12 CT", "COUPON"}
        assert (items["EGGS LRG 12 CT"].quantity, items["EGGS LRG 12 CT"].unit) == ("12", "each")
        assert outcome.diagnostics.item_price_sum == pytest.approx(6.29)

    def test_non_food_and_negative_lines_are_flagged_not_dropped(self, ocr_receipt_text):
        items = _by_store_name(parse_receipt_text(ocr_receipt_text).receipt)
        assert items["PAPER BAG"].is_food is False

        coupon = _by_store_name(parse_receipt_text(SPLIT_COLUMN_TEXT).receipt)["COUPON"]
        assert coupon.is_food is False

    def test_summary_and_payment_lines_never_become_items(self, ocr_receipt_text):
        names = {item.store_item_name.upper() for item in parse_receipt_text(ocr_receipt_text).receipt.items}
        for forbidden in ("SUBTOTAL", "TAX", "TOTAL", "VISA"):
            assert not any(forbidden in name for name in names)

    def test_produce_without_weight_leaves_unit_missing(self):
        item = _by_store_name(parse_receipt_text(SPLIT_COLUMN_TEXT).receipt)["AVOCADOS"]
        assert item.quantity == "1"
        assert item.unit is None

    def test_plural_produce_is_recognized(self):
        text = "FARM STAND\nTOMATOES 4.00\nPEACHES 6.50\nTOTAL 10.50\n"
        outcome = parse_receipt_text(text)
        assert all(item.unit is None for item in outcome.receipt.items)
        assert outcome.diagnostics.missing_qty_unit_ratio == 1.0

    def test_percentage_tax_line_is_not_an_item(self):
        text = "TARGET\n2% MILK 1 GAL 3.19 F\nT = TX 8.2500% 0.26\nTOTAL 3.45\n"
        outcome = parse_receipt_text(text)
        assert [item.store_item_name for item in outcome.receipt.items] == ["2% MILK 1 GAL"]
        assert outcome.diagnostics.tax == pytest.approx(0.26)

    def test_packaged_goods_default_to_one_each(self):
        outcome = parse_receipt_text("MARKET\nKIND BAR 1.99\nTOTAL 1.99\n")
        item = _by_store_name(outcome.receipt)["KIND BAR"]
        assert (item.quantity, item.unit) == ("1", "each")
        # Inferred 1/each is not receipt evidence; the missing-qty/unit gate must still fire.
        assert outcome.diagnostics.missing_qty_unit_count == 1

    def test_output_is_a_valid_parsed_receipt(self, ocr_receipt_text):
        receipt = parse_receipt_text(ocr_receipt_text).receipt
        ParsedReceipt.model_validate(receipt.model_dump())
        for item in receipt.items:
            assert item.store_item_name and item.ingredient_name


class TestDiagnostics:
    def test_totals_and_sums(self, ocr_receipt_text):
        diag = parse_receipt_text(ocr_receipt_text).diagnostics
        assert diag.subtotal == pytest.approx(17.06)
        assert diag.total == pytest.approx(17.06)
        assert diag.tax == pytest.approx(0.0)
        assert diag.item_price_sum == pytest.approx(17.06)
        assert diag.item_count == 4
        assert diag.food_item_count == 3
        assert diag.missing_qty_unit_ratio == 0.0

    def test_missing_qty_unit_ratio_counts_only_food(self):
        diag = parse_receipt_text(SPLIT_COLUMN_TEXT).diagnostics
        # Avocados missing unit, eggs have 12 ct; coupon is non-food.
        assert diag.food_item_count == 2
        assert diag.missing_qty_unit_count == 1
        assert diag.missing_qty_unit_ratio == pytest.approx(0.5)

    def test_inferred_defaults_count_as_missing_qty_unit(self):
        """Inferred 1/each is not receipt evidence; it must not satisfy the gate."""
        outcome = parse_receipt_text("MARKET\nKIND BAR 1.99\nGRND BF 80/20 6.97\nTOTAL 8.96\n")
        assert outcome.diagnostics.food_item_count == 2
        assert outcome.diagnostics.missing_qty_unit_count == 2
        assert outcome.diagnostics.missing_qty_unit_ratio == pytest.approx(1.0)

    def test_explicit_quantity_does_not_count_as_missing_even_if_unit_inferred(self):
        outcome = parse_receipt_text(WALMART_TEXT)
        items = {line.store_item_name: line for line in outcome.lines}
        # KIND BAR "2 @" is printed; GRND BF has no qty/unit on the receipt.
        assert items["KIND BAR"].explicit_quantity is True
        assert items["GRND BF 80/20"].explicit_quantity is False
        assert items["GRND BF 80/20"].explicit_unit is False
        assert items["GRND BF 80/20"].unit == "each"
        assert outcome.diagnostics.food_item_count == 4
        assert outcome.diagnostics.missing_qty_unit_count == 1

    def test_garbage_text_yields_zero_items(self):
        outcome = parse_receipt_text("~~~ !! ##\n1lI|\nsdfg 2\n")
        assert outcome.receipt.items == []
        assert outcome.diagnostics.food_item_count == 0
        assert outcome.diagnostics.missing_qty_unit_ratio == 1.0

    def test_empty_text(self):
        outcome = parse_receipt_text("")
        assert outcome.receipt.items == []
        assert outcome.receipt.store_name is None


class TestAbbreviations:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("CHKN BRST", "Chicken Breast"),
            ("ORG BNNAS", "Organic Bananas"),
            ("GRND BF", "Ground Beef"),
            ("GRK YOGURT 32OZ", "Greek Yogurt"),
            ("WHL MLK 1 GAL", "Whole Milk"),
            ("KIND BAR", "Kind Bar"),
        ],
    )
    def test_expansions(self, raw, expected):
        assert expand_abbreviations(raw) == expected

    def test_unknown_tokens_are_title_cased_not_dropped(self):
        assert expand_abbreviations("XYZZY SNACK") == "Xyzzy Snack"
