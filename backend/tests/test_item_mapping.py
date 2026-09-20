from backend.services.item_mapping import normalize_item_name, _local_fuzzy_match_key


def test_case_and_whitespace_insensitive():
    assert normalize_item_name("Amul Milk") == normalize_item_name("amul   milk")


def test_number_unit_spacing_variants_all_match():
    # The exact case that motivated this: a supplier (or the AI's own OCR
    # read) writing the same pack size with/without a space, in any case,
    # must all collapse to one normalized form so exact-match (and aliasing)
    # catches it for free, without needing the AI fuzzy-match fallback.
    variants = [
        "Amul Milk 500ml",
        "Amul milk 500 ML",
        "AMUL MILK 500 ml",
        "amul milk 500ML",
    ]
    normalized = {normalize_item_name(v) for v in variants}
    assert len(normalized) == 1
    assert normalized.pop() == "amul milk 500ml"


def test_does_not_merge_unrelated_words_without_a_digit():
    # Only a digit-word boundary is collapsed -- ordinary word spacing must
    # be preserved.
    assert normalize_item_name("Steel Rod") == "steel rod"


def test_parentheses_spacing_normalized():
    assert normalize_item_name("Sugar ( 1kg )") == normalize_item_name("Sugar (1kg)")


def _candidates():
    return [normalize_item_name(n) for n in ("Amul Milk 500ml", "Amul Curd 500ml", "Mother Dairy Ghee 1L")]


def test_local_fuzzy_catches_lookalike_character_swap():
    # A common AI/OCR extraction slip: 0 (zero) read as O (letter).
    norm = normalize_item_name("Amul Milk 5OOml")
    assert _local_fuzzy_match_key(norm, _candidates()) == normalize_item_name("Amul Milk 500ml")


def test_local_fuzzy_catches_extra_character():
    norm = normalize_item_name("Amul Milkk 500ml")
    assert _local_fuzzy_match_key(norm, _candidates()) == normalize_item_name("Amul Milk 500ml")


def test_local_fuzzy_catches_missing_character():
    norm = normalize_item_name("Amul Mlk 500ml")
    assert _local_fuzzy_match_key(norm, _candidates()) == normalize_item_name("Amul Milk 500ml")


def test_local_fuzzy_does_not_guess_a_genuinely_different_item():
    # Must not silently pick a plausible-but-wrong item when nothing is a
    # confident match -- this auto-accepts with no human/AI review, so a
    # false positive here would post against the wrong stock item.
    norm = normalize_item_name("Amul Butter 500g")
    assert _local_fuzzy_match_key(norm, _candidates()) is None


def test_local_fuzzy_returns_none_for_empty_candidates():
    assert _local_fuzzy_match_key(normalize_item_name("Anything"), []) is None
