"""Tests for ingredient normalization module.

This module tests the robust ingredient name normalization used to match
receipt items to inventory entries despite naming variations.

Regression fixtures are included for common grocery receipt variations.
"""

import pytest
from app.services.ingredient_normalization import (
    MatchConfidence,
    MatchResult,
    NormalizationResult,
    clean_display_name,
    compute_canonical_key,
    find_matching_ingredient_with_confidence,
    match_ingredient_names,
    normalize_ingredient_name,
    _expand_abbreviations,
    _normalize_whitespace,
    _remove_punctuation,
    _singularize,
    _strip_qualifiers,
)


class TestNormalizeWhitespace:
    """Tests for whitespace normalization."""

    def test_collapse_multiple_spaces(self):
        assert _normalize_whitespace("chicken  breast") == "chicken breast"

    def test_collapse_tabs_and_newlines(self):
        assert _normalize_whitespace("chicken\t\nbreast") == "chicken breast"

    def test_strip_leading_trailing(self):
        assert _normalize_whitespace("  chicken breast  ") == "chicken breast"

    def test_empty_string(self):
        assert _normalize_whitespace("") == ""

    def test_only_whitespace(self):
        assert _normalize_whitespace("   ") == ""


class TestRemovePunctuation:
    """Tests for punctuation removal."""

    def test_remove_periods(self):
        assert _remove_punctuation("chicken.breast") == "chickenbreast"

    def test_remove_commas(self):
        assert _remove_punctuation("salt, pepper") == "salt pepper"

    def test_preserve_hyphen_between_words(self):
        assert _remove_punctuation("sugar-free") == "sugar-free"

    def test_remove_leading_hyphen(self):
        assert _remove_punctuation("-chicken") == "chicken"

    def test_remove_trailing_hyphen(self):
        assert _remove_punctuation("chicken-") == "chicken"

    def test_remove_apostrophe(self):
        assert _remove_punctuation("trader joe's") == "trader joes"

    def test_remove_ampersand(self):
        result = _remove_punctuation("good & gather")
        assert "&" not in result


class TestSingularize:
    """Tests for plural to singular conversion."""

    def test_regular_plural_s(self):
        assert _singularize("chickens") == "chicken"
        assert _singularize("breasts") == "breast"

    def test_es_plural(self):
        assert _singularize("tomatoes") == "tomato"
        assert _singularize("potatoes") == "potato"

    def test_ies_plural(self):
        assert _singularize("berries") == "berry"
        assert _singularize("cherries") == "cherry"

    def test_irregular_plural(self):
        assert _singularize("leaves") == "leaf"
        assert _singularize("loaves") == "loaf"

    def test_already_singular(self):
        assert _singularize("chicken") == "chicken"
        assert _singularize("rice") == "rice"

    def test_words_ending_in_ss(self):
        assert _singularize("grass") == "grass"
        assert _singularize("glass") == "glass"

    def test_fish_sheep_unchanged(self):
        assert _singularize("fish") == "fish"
        assert _singularize("sheep") == "sheep"
        assert _singularize("shrimp") == "shrimp"

    def test_short_words(self):
        assert _singularize("as") == "as"
        assert _singularize("is") == "is"


class TestExpandAbbreviations:
    """Tests for abbreviation expansion."""

    def test_chicken_abbreviations(self):
        assert _expand_abbreviations("chkn")[0] == "chicken"
        assert _expand_abbreviations("chk")[0] == "chicken"
        assert _expand_abbreviations("ckn")[0] == "chicken"

    def test_breast_abbreviation(self):
        assert _expand_abbreviations("brst")[0] == "breast"
        assert _expand_abbreviations("brsts")[0] == "breasts"

    def test_organic_abbreviation(self):
        assert _expand_abbreviations("org")[0] == "organic"
        assert _expand_abbreviations("orgn")[0] == "organic"

    def test_multiple_abbreviations(self):
        result, expansions = _expand_abbreviations("org chkn brst")
        assert result == "organic chicken breast"
        assert len(expansions) == 3

    def test_mixed_abbreviations_and_words(self):
        result, expansions = _expand_abbreviations("org chicken brst")
        assert result == "organic chicken breast"
        assert len(expansions) == 2

    def test_no_abbreviations(self):
        result, expansions = _expand_abbreviations("chicken breast")
        assert result == "chicken breast"
        assert len(expansions) == 0

    def test_ground_beef_abbreviation(self):
        result, _ = _expand_abbreviations("grnd bf")
        assert result == "ground beef"

    def test_frozen_vegetables(self):
        result, _ = _expand_abbreviations("frzn vegs")
        assert result == "frozen vegetables"


class TestStripQualifiers:
    """Tests for stripping product qualifiers."""

    def test_organic_prefix(self):
        assert _strip_qualifiers("organic chicken") == "chicken"

    def test_fresh_prefix(self):
        assert _strip_qualifiers("fresh salmon") == "salmon"

    def test_frozen_prefix(self):
        assert _strip_qualifiers("frozen vegetables") == "vegetables"

    def test_store_brand_prefix(self):
        assert _strip_qualifiers("store brand milk") == "milk"

    def test_multiple_qualifiers(self):
        result = _strip_qualifiers("organic fresh chicken")
        assert "organic" not in result or "fresh" not in result

    def test_no_qualifiers(self):
        assert _strip_qualifiers("chicken breast") == "chicken breast"

    def test_premium_select(self):
        assert _strip_qualifiers("premium beef") == "beef"
        assert _strip_qualifiers("select chicken") == "chicken"


class TestNormalizeIngredientName:
    """Tests for full ingredient name normalization."""

    def test_simple_normalization(self):
        result = normalize_ingredient_name("Chicken Breast")
        assert result.canonical == "chicken breast"

    def test_abbreviation_expansion(self):
        result = normalize_ingredient_name("CHKN BRST")
        assert result.canonical == "chicken breast"
        assert "chkn→chicken" in result.expanded_abbreviations
        assert "brst→breast" in result.expanded_abbreviations

    def test_plural_normalization(self):
        result = normalize_ingredient_name("Chicken Breasts")
        assert result.canonical == "chicken breast"

    def test_organic_prefix_stripped(self):
        result = normalize_ingredient_name("ORG CHICKEN BREAST")
        assert result.canonical == "chicken breast"
        assert "organic" not in result.canonical

    def test_preserves_original(self):
        result = normalize_ingredient_name("CHKN BRST")
        assert result.original == "CHKN BRST"

    def test_normalized_form(self):
        result = normalize_ingredient_name("CHKN BRST")
        assert result.normalized == "chicken breast"

    def test_empty_string(self):
        result = normalize_ingredient_name("")
        assert result.canonical == ""
        assert result.normalized == ""

    def test_whitespace_only(self):
        result = normalize_ingredient_name("   ")
        assert result.canonical == ""

    def test_case_insensitive(self):
        result1 = normalize_ingredient_name("Chicken Breast")
        result2 = normalize_ingredient_name("CHICKEN BREAST")
        result3 = normalize_ingredient_name("chicken breast")
        assert result1.canonical == result2.canonical == result3.canonical

    def test_extra_whitespace(self):
        result1 = normalize_ingredient_name("Chicken  Breast")
        result2 = normalize_ingredient_name("Chicken Breast")
        assert result1.canonical == result2.canonical


class TestComputeCanonicalKey:
    """Tests for canonical key computation."""

    def test_same_ingredient_same_key(self):
        key1 = compute_canonical_key("Chicken Breast", "lb")
        key2 = compute_canonical_key("CHKN BRST", "lb")
        assert key1 == key2

    def test_different_units_different_keys(self):
        key1 = compute_canonical_key("Chicken Breast", "lb")
        key2 = compute_canonical_key("Chicken Breast", "oz")
        assert key1 != key2

    def test_unit_normalization(self):
        key1 = compute_canonical_key("Rice", "pound")
        key2 = compute_canonical_key("Rice", "lb")
        assert key1 == key2

    def test_none_name(self):
        key = compute_canonical_key(None, "lb")
        assert key == ("", "")

    def test_none_unit(self):
        key1 = compute_canonical_key("Chicken", None)
        key2 = compute_canonical_key("Chicken", None)
        assert key1 == key2


class TestMatchIngredientNames:
    """Tests for ingredient name matching."""

    def test_exact_match(self):
        result = match_ingredient_names("chicken breast", "chicken breast")
        assert result.confidence == MatchConfidence.EXACT

    def test_abbreviation_match(self):
        result = match_ingredient_names("CHKN BRST", "Chicken Breast")
        assert result.confidence in (MatchConfidence.EXACT, MatchConfidence.HIGH)

    def test_plural_match(self):
        result = match_ingredient_names("Chicken Breasts", "Chicken Breast")
        assert result.confidence in (MatchConfidence.EXACT, MatchConfidence.HIGH)

    def test_organic_qualifier_match(self):
        result = match_ingredient_names("ORG CHICKEN BREAST", "Chicken Breast")
        assert result.confidence in (MatchConfidence.EXACT, MatchConfidence.HIGH)

    def test_no_match_different_ingredients(self):
        result = match_ingredient_names("Chicken Breast", "Beef Steak")
        assert result.confidence == MatchConfidence.NO_MATCH

    def test_ambiguous_partial_match(self):
        result = match_ingredient_names(
            "Chicken", "Chicken Breast", require_high_confidence=True
        )
        assert result.confidence in (
            MatchConfidence.AMBIGUOUS,
            MatchConfidence.MEDIUM,
            MatchConfidence.NO_MATCH,
        )

    def test_empty_source(self):
        result = match_ingredient_names("", "Chicken Breast")
        assert result.confidence == MatchConfidence.NO_MATCH

    def test_empty_target(self):
        result = match_ingredient_names("Chicken Breast", "")
        assert result.confidence == MatchConfidence.NO_MATCH


class TestFindMatchingIngredient:
    """Tests for finding best matching ingredient from candidates."""

    def test_exact_match_found(self):
        candidates = [
            ("1", "Chicken Breast"),
            ("2", "Beef Steak"),
            ("3", "Salmon Fillet"),
        ]
        matched_id, result = find_matching_ingredient_with_confidence(
            "Chicken Breast", candidates
        )
        assert matched_id == "1"
        assert result.confidence == MatchConfidence.EXACT

    def test_abbreviation_match_found(self):
        candidates = [
            ("1", "Chicken Breast"),
            ("2", "Beef Steak"),
        ]
        matched_id, result = find_matching_ingredient_with_confidence(
            "CHKN BRST", candidates
        )
        assert matched_id == "1"

    def test_no_match_returns_none(self):
        candidates = [
            ("1", "Chicken Breast"),
            ("2", "Beef Steak"),
        ]
        matched_id, result = find_matching_ingredient_with_confidence(
            "Salmon Fillet", candidates
        )
        assert matched_id is None

    def test_multiple_high_confidence_returns_ambiguous(self):
        candidates = [
            ("1", "Brown Rice"),
            ("2", "White Rice"),
        ]
        matched_id, result = find_matching_ingredient_with_confidence("Rice", candidates)
        assert matched_id is None
        if result:
            assert result.confidence == MatchConfidence.AMBIGUOUS

    def test_empty_candidates(self):
        matched_id, result = find_matching_ingredient_with_confidence(
            "Chicken Breast", []
        )
        assert matched_id is None


class TestCleanDisplayName:
    """Tests for display name cleaning."""

    def test_expands_abbreviations(self):
        assert clean_display_name("CHKN BRST") == "Chicken Breast"

    def test_capitalizes_words(self):
        assert clean_display_name("chicken breast") == "Chicken Breast"

    def test_normalizes_whitespace(self):
        assert clean_display_name("chicken  breast") == "Chicken Breast"

    def test_preserves_useful_qualifiers(self):
        result = clean_display_name("organic chicken")
        assert "Organic" in result
        assert "Chicken" in result

    def test_empty_string(self):
        assert clean_display_name("") == ""

    def test_whitespace_only(self):
        assert clean_display_name("   ") == "   "


class TestGroceryReceiptVariations:
    """Regression tests for common grocery receipt naming variations.

    These fixtures represent real-world receipt text variations that
    should be normalized correctly.
    """

    @pytest.mark.parametrize(
        "receipt_text,expected_canonical",
        [
            # Chicken variations
            ("CHKN BRST", "chicken breast"),
            ("CHICKEN BREAST", "chicken breast"),
            ("Chicken Breasts", "chicken breast"),
            ("BNLS SKNLS CHKN BRST", "boneless skinless chicken breast"),
            ("BONELESS SKINLESS CHICKEN BREAST", "boneless skinless chicken breast"),
            ("ORG CHICKEN BREAST", "chicken breast"),
            ("ORGANIC CHICKEN BREASTS", "chicken breast"),
            ("FRESH CHICKEN BREAST", "chicken breast"),
            # Ground meat variations
            ("GRND BF", "ground beef"),
            ("GROUND BEEF", "ground beef"),
            ("GRD BEEF 80/20", "ground beef 8020"),
            ("LEAN GROUND BEEF", "ground beef"),
            ("GRND TRKY", "ground turkey"),
            # Produce variations
            ("ORG BNNAS", "banana"),
            ("ORGANIC BANANAS", "banana"),
            ("Bananas", "banana"),
            ("FRESH BANANAS", "banana"),
            ("RED APPLES", "red apple"),
            ("APPLES RED DEL", "apple red del"),
            ("GRN PEPRS", "green pepper"),
            ("GREEN PEPPERS", "green pepper"),
            ("BROCCOLI CROWNS", "broccoli crown"),
            ("BRCL CRWNS", "broccoli crwn"),
            # Dairy variations
            ("MLK 2%", "milk 2"),
            ("2% MILK", "2 milk"),
            ("WHOLE MILK GAL", "whole milk gal"),
            ("GRK YOGURT", "greek yogurt"),
            ("GREEK YOGURT", "greek yogurt"),
            ("SHRD CHED CHS", "shredded cheddar cheese"),
            ("SHREDDED CHEDDAR CHEESE", "shredded cheddar cheese"),
            # Bread variations
            ("WW BREAD", "whole wheat bread"),
            ("WHOLE WHEAT BREAD", "whole wheat bread"),
            ("MLTGRN BREAD", "multigrain bread"),
            # Pantry items
            ("EVOO", "extra virgin olive oil"),
            ("OLIVE OIL EV", "olive oil ev"),
            ("PB CREAMY", "peanut butter creamy"),
            ("PEANUT BUTTER", "peanut butter"),
            ("BRN RICE", "brown rice"),
            ("BROWN RICE", "brown rice"),
        ],
    )
    def test_receipt_text_normalizes_correctly(self, receipt_text, expected_canonical):
        result = normalize_ingredient_name(receipt_text)
        assert result.canonical == expected_canonical

    @pytest.mark.parametrize(
        "receipt_text,inventory_name,should_match",
        [
            # Exact and abbreviation matches
            ("CHKN BRST", "Chicken Breast", True),
            ("Chicken breasts", "Chicken Breast", True),
            ("ORG CHICKEN BREAST", "Chicken Breast", True),
            ("BNLS SKNLS CHKN BRST", "Boneless Skinless Chicken Breast", True),
            # Ground meat matches
            ("GRND BF", "Ground Beef", True),
            ("GRND TRKY", "Ground Turkey", True),
            # Produce matches
            ("ORG BNNAS", "Bananas", True),
            ("GRN PEPRS", "Green Peppers", True),
            # Should NOT match different ingredients
            ("CHKN BRST", "Chicken Thigh", False),
            ("GRND BF", "Ground Turkey", False),
            ("Bananas", "Apples", False),
            # Should NOT match partial ingredients aggressively
            ("Chicken", "Chicken Breast", False),
            ("Rice", "Brown Rice", False),
        ],
    )
    def test_receipt_to_inventory_matching(
        self, receipt_text, inventory_name, should_match
    ):
        result = match_ingredient_names(
            receipt_text, inventory_name, require_high_confidence=True
        )
        is_match = result.confidence in (MatchConfidence.EXACT, MatchConfidence.HIGH)
        assert is_match == should_match, (
            f"Expected {'match' if should_match else 'no match'} between "
            f"'{receipt_text}' and '{inventory_name}', got {result.confidence}"
        )


class TestMergeKeyBackwardsCompatibility:
    """Tests to ensure _merge_key behavior is backwards compatible."""

    def test_same_name_same_unit_same_key(self):
        key1 = compute_canonical_key("Chicken Breast", "oz")
        key2 = compute_canonical_key("Chicken Breast", "oz")
        assert key1 == key2

    def test_case_insensitive(self):
        key1 = compute_canonical_key("Chicken Breast", "oz")
        key2 = compute_canonical_key("chicken breast", "oz")
        assert key1 == key2

    def test_whitespace_normalized(self):
        key1 = compute_canonical_key("Chicken  Breast", "oz")
        key2 = compute_canonical_key("Chicken Breast", "oz")
        assert key1 == key2

    def test_unit_aliases(self):
        key1 = compute_canonical_key("Rice", "gram")
        key2 = compute_canonical_key("Rice", "g")
        assert key1 == key2

    def test_abbreviations_now_match(self):
        key1 = compute_canonical_key("CHKN BRST", "lb")
        key2 = compute_canonical_key("Chicken Breast", "lb")
        assert key1 == key2

    def test_plurals_now_match(self):
        key1 = compute_canonical_key("Chicken Breasts", "lb")
        key2 = compute_canonical_key("Chicken Breast", "lb")
        assert key1 == key2


class TestAmbiguousMatchHandling:
    """Tests for ambiguous match detection and handling."""

    def test_partial_match_is_ambiguous(self):
        result = match_ingredient_names(
            "Chicken", "Chicken Breast", require_high_confidence=True
        )
        assert result.confidence != MatchConfidence.HIGH

    def test_multiple_candidates_ambiguous(self):
        candidates = [
            ("1", "Brown Rice"),
            ("2", "White Rice"),
            ("3", "Jasmine Rice"),
        ]
        matched_id, result = find_matching_ingredient_with_confidence("Rice", candidates)
        assert matched_id is None

    def test_distinct_ingredient_not_ambiguous(self):
        candidates = [
            ("1", "Brown Rice"),
            ("2", "Chicken Breast"),
        ]
        matched_id, result = find_matching_ingredient_with_confidence(
            "Brown Rice", candidates
        )
        assert matched_id == "1"
        assert result.confidence == MatchConfidence.EXACT


class TestUnitCompatibility:
    """Tests for unit handling in merging."""

    def test_compatible_units_same_key(self):
        key1 = compute_canonical_key("Chicken Breast", "lb")
        key2 = compute_canonical_key("Chicken Breast", "pound")
        assert key1 == key2

    def test_incompatible_units_different_keys(self):
        key1 = compute_canonical_key("Chicken Breast", "lb")
        key2 = compute_canonical_key("Chicken Breast", "each")
        assert key1 != key2

    def test_none_units_same_key(self):
        key1 = compute_canonical_key("Chicken Breast", None)
        key2 = compute_canonical_key("Chicken Breast", None)
        assert key1 == key2
