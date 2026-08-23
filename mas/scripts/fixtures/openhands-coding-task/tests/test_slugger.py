from slugger import slugify


def test_slugify_normalizes_words_and_punctuation() -> None:
    assert slugify("  Hello, AIAT!  ") == "hello-aiat"


def test_slugify_collapses_separators_and_drops_non_ascii_marks() -> None:
    assert slugify("Crème   brûlée / v1") == "creme-brulee-v1"


def test_slugify_empty_or_separator_only_values_are_empty() -> None:
    assert slugify("--- ___ ...") == ""
    assert slugify("") == ""
