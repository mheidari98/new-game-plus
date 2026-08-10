"""Title normalisation and the fuzzy-match numeric guard.

The behaviours asserted here are specifications carried over from the
predecessor project, where each one was a real bug in production.
"""

from ngp.titles import normalize_title, numbers_compatible


class TestNumbersCompatible:
    """The sequel trap: these pairs score above any sane fuzzy threshold
    while being different games, so numbering must gate every fuzzy match."""

    def test_rejects_mortal_kombat_11_against_mortal_kombat_1(self):
        assert numbers_compatible("Mortal Kombat 11", "Mortal Kombat 1") is False

    def test_rejects_dying_light_2_against_dying_light(self):
        assert numbers_compatible("Dying Light 2", "Dying Light") is False

    def test_accepts_identical_titles(self):
        assert numbers_compatible("Celeste", "Celeste") is True

    def test_accepts_same_number_written_differently(self):
        # Roman numerals are folded to digits on both sides of the comparison.
        assert numbers_compatible("Final Fantasy VII", "Final Fantasy 7") is True

    def test_ignores_edition_noise_that_carries_no_number(self):
        assert numbers_compatible(
            "The Witcher 3: Wild Hunt - Complete Edition", "The Witcher 3"
        ) is True


class TestNormalizeTitle:
    def test_trademark_symbol_does_not_become_the_letters_tm(self):
        # NFKD decomposes U+2122 into "TM". Symbols must be stripped *before*
        # normalising, or this title becomes "ea sports fctm 26".
        assert normalize_title("EA SPORTS FC™ 26") == "ea sports fc 26"

    def test_strips_platform_and_edition_noise(self):
        assert normalize_title("Hogwarts Legacy PS4 Version") == "hogwarts legacy"

    def test_folds_standalone_roman_numerals_to_digits(self):
        assert normalize_title("Kingdom Come: Deliverance II") == "kingdom come deliverance 2"

    def test_curly_apostrophe_matches_straight_apostrophe(self):
        assert normalize_title("Marvel’s Spider-Man") == normalize_title("Marvel's Spider-Man")

    def test_empty_input_is_empty_output(self):
        assert normalize_title("") == ""
