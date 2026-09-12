"""Tests for cheap local ingredient normalization (no hardcoded dictionaries)."""

from unittest.mock import MagicMock, patch

from app.services.ingredient_normalization import (
    MatchConfidence,
    clean_display_name,
    compute_canonical_key,
    find_matching_ingredient_with_confidence,
    match_ingredient_names,
    normalize_ingredient_name,
    _normalize_whitespace,
    _remove_punctuation,
)
from app.services.receipt_analyzer import PantryMatchResult, match_ingredient_to_pantry


class TestNormalizeWhitespace:
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
    def test_separator_punctuation_becomes_space(self):
        assert _normalize_whitespace(_remove_punctuation("chicken.breast")) == "chicken breast"
        assert _normalize_whitespace(_remove_punctuation("chicken/breast")) == "chicken breast"

    def test_remove_commas(self):
        assert _normalize_whitespace(_remove_punctuation("salt, pepper")) == "salt pepper"

    def test_preserve_hyphen_between_words(self):
        assert _remove_punctuation("sugar-free") == "sugar-free"

    def test_remove_leading_hyphen(self):
        assert _normalize_whitespace(_remove_punctuation("-chicken")) == "chicken"

    def test_remove_trailing_hyphen(self):
        assert _normalize_whitespace(_remove_punctuation("chicken-")) == "chicken"

    def test_remove_apostrophe(self):
        assert _normalize_whitespace(_remove_punctuation("trader joe's")) == "trader joe s"

    def test_remove_ampersand(self):
        result = _remove_punctuation("good & gather")
        assert "&" not in result


class TestNormalizeIngredientName:
    def test_simple_normalization(self):
        result = normalize_ingredient_name("Chicken Breast")
        assert result.canonical == "chicken breast"

    def test_does_not_expand_abbreviations_locally(self):
        result = normalize_ingredient_name("CHKN BRST")
        assert result.canonical == "chkn brst"

    def test_does_not_singularize_locally(self):
        result = normalize_ingredient_name("Chicken Breasts")
        assert result.canonical == "chicken breasts"

    def test_does_not_strip_qualifiers_locally(self):
        result = normalize_ingredient_name("Organic Chicken Breast")
        assert result.canonical == "organic chicken breast"

    def test_preserves_original(self):
        result = normalize_ingredient_name("CHKN BRST")
        assert result.original == "CHKN BRST"

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
    def test_same_ingredient_same_key(self):
        key1 = compute_canonical_key("Chicken Breast", "lb")
        key2 = compute_canonical_key("chicken breast", "lb")
        assert key1 == key2

    def test_abbreviations_do_not_match_locally(self):
        key1 = compute_canonical_key("Chicken Breast", "lb")
        key2 = compute_canonical_key("CHKN BRST", "lb")
        assert key1 != key2

    def test_plurals_do_not_match_locally(self):
        key1 = compute_canonical_key("Chicken Breasts", "lb")
        key2 = compute_canonical_key("Chicken Breast", "lb")
        assert key1 != key2

    def test_qualifiers_do_not_match_locally(self):
        key1 = compute_canonical_key("Organic Chicken Breast", "lb")
        key2 = compute_canonical_key("Chicken Breast", "lb")
        assert key1 != key2

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
    def test_exact_match(self):
        result = match_ingredient_names("chicken breast", "chicken breast")
        assert result.confidence == MatchConfidence.EXACT

    def test_abbreviation_does_not_match_locally(self):
        result = match_ingredient_names("CHKN BRST", "Chicken Breast")
        assert result.confidence == MatchConfidence.NO_MATCH

    def test_plural_does_not_match_locally(self):
        result = match_ingredient_names("Chicken Breasts", "Chicken Breast")
        assert result.confidence != MatchConfidence.EXACT
        assert result.confidence != MatchConfidence.HIGH

    def test_qualifier_does_not_exact_match_locally(self):
        result = match_ingredient_names("Organic Chicken Breast", "Chicken Breast")
        assert result.confidence not in (
            MatchConfidence.EXACT,
            MatchConfidence.HIGH,
        )

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

    def test_abbreviation_not_found_locally(self):
        candidates = [
            ("1", "Chicken Breast"),
            ("2", "Beef Steak"),
        ]
        matched_id, _result = find_matching_ingredient_with_confidence(
            "CHKN BRST", candidates
        )
        assert matched_id is None

    def test_no_match_returns_none(self):
        candidates = [
            ("1", "Chicken Breast"),
            ("2", "Beef Steak"),
        ]
        matched_id, _result = find_matching_ingredient_with_confidence(
            "Salmon Fillet", candidates
        )
        assert matched_id is None

    def test_multiple_candidates_ambiguous(self):
        candidates = [
            ("1", "Brown Rice"),
            ("2", "White Rice"),
        ]
        matched_id, result = find_matching_ingredient_with_confidence("Rice", candidates)
        assert matched_id is None
        if result:
            assert result.confidence == MatchConfidence.AMBIGUOUS

    def test_empty_candidates(self):
        matched_id, _result = find_matching_ingredient_with_confidence(
            "Chicken Breast", []
        )
        assert matched_id is None


class TestCleanDisplayName:
    def test_does_not_expand_abbreviations(self):
        assert clean_display_name("CHKN BRST") == "Chkn Brst"

    def test_capitalizes_words(self):
        assert clean_display_name("chicken breast") == "Chicken Breast"

    def test_normalizes_whitespace(self):
        assert clean_display_name("chicken  breast") == "Chicken Breast"

    def test_preserves_qualifiers_in_display(self):
        result = clean_display_name("organic chicken")
        assert "Organic" in result
        assert "Chicken" in result

    def test_preserves_percent_and_slash_in_display(self):
        assert "2%" in clean_display_name("MLK 2%")
        assert "80/20" in clean_display_name("GROUND BEEF 80/20")

    def test_empty_string(self):
        assert clean_display_name("") == ""

    def test_whitespace_only(self):
        assert clean_display_name("   ") == "   "


class TestMergeKeyBackwardsCompatibility:
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


class TestAmbiguousMatchHandling:
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
        matched_id, _result = find_matching_ingredient_with_confidence("Rice", candidates)
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


class TestMatchIngredientToPantry:
    def test_empty_name_returns_empty_result(self):
        result = match_ingredient_to_pantry("", "lb", [{"id": "1", "name": "Chicken"}])
        assert result.match_id is None
        assert result.ambiguous is False

    def test_empty_pantry_skips_llm(self):
        with patch("app.services.receipt_analyzer._get_client") as mock_client:
            result = match_ingredient_to_pantry("CHKN BRST", "lb", [])
        mock_client.assert_not_called()
        assert result.match_id is None

    @patch("app.services.receipt_analyzer._get_client")
    def test_clear_match_returns_id(self, mock_get_client):
        mock_get_client.return_value.messages.create.return_value.content = [
            MagicMock(
                type="text",
                text=(
                    '{"match_id": "ing-1", "ambiguous": false, '
                    '"canonical_name": "Chicken Breast"}'
                ),
            )
        ]

        result = match_ingredient_to_pantry(
            "CHKN BRST",
            "lb",
            [{"id": "ing-1", "name": "Chicken Breast", "unit": "lb"}],
        )

        assert result.match_id == "ing-1"
        assert result.ambiguous is False
        assert result.canonical_name == "Chicken Breast"

    @patch("app.services.receipt_analyzer._get_client")
    def test_plural_match_via_llm(self, mock_get_client):
        mock_get_client.return_value.messages.create.return_value.content = [
            MagicMock(
                type="text",
                text=(
                    '{"match_id": "ing-1", "ambiguous": false, '
                    '"canonical_name": "Tomato"}'
                ),
            )
        ]

        result = match_ingredient_to_pantry(
            "Tomatoes",
            "each",
            [{"id": "ing-1", "name": "Tomato", "unit": "each"}],
        )

        assert result.match_id == "ing-1"
        assert result.canonical_name == "Tomato"

    @patch("app.services.receipt_analyzer._get_client")
    def test_qualifier_match_via_llm(self, mock_get_client):
        mock_get_client.return_value.messages.create.return_value.content = [
            MagicMock(
                type="text",
                text=(
                    '{"match_id": "ing-1", "ambiguous": false, '
                    '"canonical_name": "Chicken Breast"}'
                ),
            )
        ]

        result = match_ingredient_to_pantry(
            "Organic Chicken Breast",
            "lb",
            [{"id": "ing-1", "name": "Chicken Breast", "unit": "lb"}],
        )

        assert result.match_id == "ing-1"

    @patch("app.services.receipt_analyzer._get_client")
    def test_ambiguous_clears_match_id(self, mock_get_client):
        mock_get_client.return_value.messages.create.return_value.content = [
            MagicMock(
                type="text",
                text=(
                    '{"match_id": "ing-1", "ambiguous": true, '
                    '"canonical_name": "Rice"}'
                ),
            )
        ]

        result = match_ingredient_to_pantry(
            "Rice",
            "lb",
            [
                {"id": "ing-1", "name": "Brown Rice", "unit": "lb"},
                {"id": "ing-2", "name": "White Rice", "unit": "lb"},
            ],
        )

        assert result.match_id is None
        assert result.ambiguous is True
        assert result.canonical_name == "Rice"

    @patch("app.services.receipt_analyzer._get_client")
    def test_invented_match_id_is_rejected(self, mock_get_client):
        mock_get_client.return_value.messages.create.return_value.content = [
            MagicMock(
                type="text",
                text=(
                    '{"match_id": "not-real", "ambiguous": false, '
                    '"canonical_name": "Chicken Breast"}'
                ),
            )
        ]

        result = match_ingredient_to_pantry(
            "CHKN BRST",
            "lb",
            [{"id": "ing-1", "name": "Chicken Breast", "unit": "lb"}],
        )

        assert result.match_id is None
        assert result.canonical_name == "Chicken Breast"


class TestCreateIngredientLlmMatch:
    def test_merges_when_llm_returns_match(self, test_db, test_user):
        from app.models import Ingredient
        from app.schemas import DraftIngredientItem
        from app.services.ingredients import create_ingredient
        from app.services.receipt_analyzer import ParsedReceiptItem

        existing = Ingredient(
            id="ing-chicken",
            user_id=test_user.id,
            name="Chicken Breast",
            quantity="1",
            unit="lb",
        )
        test_db.add(existing)
        test_db.commit()

        item = DraftIngredientItem(
            ingredient_name="CHKN BRST",
            store_item_name="CHKN BRST",
            quantity="2",
            unit="lb",
            is_manual=True,
        )

        estimated = ParsedReceiptItem(
            store_item_name="CHKN BRST",
            ingredient_name="CHKN BRST",
            recognized=True,
            quantity="2",
            unit="lb",
            serving_size="4 oz",
            servings_per_container=4,
            calories=120,
        )

        with patch(
            "app.services.ingredients.check_ingredient_unit",
            return_value=None,
        ), patch(
            "app.services.ingredients.estimate_ingredient_nutrition",
            return_value=estimated,
        ), patch(
            "app.services.ingredients.match_ingredient_to_pantry",
            return_value=PantryMatchResult(
                match_id="ing-chicken",
                ambiguous=False,
                canonical_name="Chicken Breast",
            ),
        ):
            result = create_ingredient(test_db, test_user, item)

        assert result.id == "ing-chicken"
        assert result.quantity == "3"

    def test_merges_plural_via_llm(self, test_db, test_user):
        from app.models import Ingredient
        from app.schemas import DraftIngredientItem
        from app.services.ingredients import create_ingredient
        from app.services.receipt_analyzer import ParsedReceiptItem

        test_db.add(
            Ingredient(
                id="ing-tomato",
                user_id=test_user.id,
                name="Tomato",
                quantity="2",
                unit="each",
            )
        )
        test_db.commit()

        item = DraftIngredientItem(
            ingredient_name="Tomatoes",
            store_item_name="Tomatoes",
            quantity="3",
            unit="each",
            is_manual=True,
        )
        estimated = ParsedReceiptItem(
            store_item_name="Tomatoes",
            ingredient_name="Tomatoes",
            recognized=True,
            quantity="3",
            unit="each",
            calories=20,
        )

        with patch(
            "app.services.ingredients.check_ingredient_unit",
            return_value=None,
        ), patch(
            "app.services.ingredients.estimate_ingredient_nutrition",
            return_value=estimated,
        ), patch(
            "app.services.ingredients.match_ingredient_to_pantry",
            return_value=PantryMatchResult(
                match_id="ing-tomato",
                ambiguous=False,
                canonical_name="Tomato",
            ),
        ):
            result = create_ingredient(test_db, test_user, item)

        assert result.id == "ing-tomato"
        assert result.quantity == "5"

    def test_creates_new_when_llm_ambiguous(self, test_db, test_user):
        from app.models import Ingredient
        from app.schemas import DraftIngredientItem
        from app.services.ingredients import create_ingredient
        from app.services.receipt_analyzer import ParsedReceiptItem

        test_db.add(
            Ingredient(
                id="ing-brown",
                user_id=test_user.id,
                name="Brown Rice",
                quantity="1",
                unit="lb",
            )
        )
        test_db.add(
            Ingredient(
                id="ing-white",
                user_id=test_user.id,
                name="White Rice",
                quantity="1",
                unit="lb",
            )
        )
        test_db.commit()

        item = DraftIngredientItem(
            ingredient_name="Rice",
            store_item_name="Rice",
            quantity="1",
            unit="lb",
            is_manual=True,
        )
        estimated = ParsedReceiptItem(
            store_item_name="Rice",
            ingredient_name="Rice",
            recognized=True,
            quantity="1",
            unit="lb",
            calories=100,
        )

        with patch(
            "app.services.ingredients.check_ingredient_unit",
            return_value=None,
        ), patch(
            "app.services.ingredients.estimate_ingredient_nutrition",
            return_value=estimated,
        ), patch(
            "app.services.ingredients.match_ingredient_to_pantry",
            return_value=PantryMatchResult(
                match_id=None,
                ambiguous=True,
                canonical_name="Rice",
            ),
        ):
            result = create_ingredient(test_db, test_user, item)

        assert result.id not in {"ing-brown", "ing-white"}
        assert result.name == "Rice"
        assert test_db.query(Ingredient).count() == 3
