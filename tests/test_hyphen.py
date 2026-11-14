# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""0.34.0 (hyphen.py): where an English word may be divided at a line end
(Liang's patterns, as TeX uses them)."""
import pytest

from pdfmanager import hyphen


def shown(word):
    points = hyphen.points(word)
    return "-".join(word[a:b] for a, b in zip([0] + points, points + [len(word)]))


@pytest.mark.parametrize("word, divided", [
    ("hyphenation", "hy-phen-ation"),
    ("extraordinarily", "ex-tra-or-di-nar-ily"),
    ("computer", "com-puter"),  # (not com-put-er: three letters stay after a division)
    ("supercalifragilisticexpialidocious", "su-per-cal-ifrag-ilis-tic-ex-pi-ali-do-cious"),  # as TeX gives it
    ("typesetting", "type-set-ting"),
    ("quarterly", "quar-terly"),
    ("associate", "as-so-ciate"),   # from the list of exceptions
    ("present", "present"),         # an exception with no division (pre-sent or pres-ent)
    ("table", "ta-ble"),
])
def test_known_divisions(word, divided):
    assert shown(word) == divided


def test_at_least_two_letters_before_and_three_after():
    for word in ("extraordinarily", "international", "responsibility", "abode", "idea", "area"):
        for p in hyphen.points(word):
            assert 2 <= p <= len(word) - 3, (word, p)


def test_capitals_keep_their_places():
    assert shown("Hyphenation") == "Hy-phen-ation" and shown("COMPUTER") == "COM-PUTER"


def test_words_that_are_left_whole():
    for word in ("", "a", "the", "2024-0117", "x86", "naïve", "über", "user@example.com", "INV-2026"):
        assert hyphen.points(word) == [], word


def test_only_english_is_divided():
    assert hyphen.points("hyphenation", "en-GB") and hyphen.points("hyphenation", "en")
    assert hyphen.points("hyphenation", None)
    assert hyphen.points("Silbentrennung", "de") == [] and hyphen.points("hyphenation", "fr-FR") == []
