from backend.services.item_mapping import normalize_item_name


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
