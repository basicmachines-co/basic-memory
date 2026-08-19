"""Exhaustive sweeps over the Unicode classes the relaxation guards depend on.

The case-based tests next door pin individual queries. These pin the rules those
cases are instances of, by walking every character in the class rather than the
ones review happened to surface: a soft hyphen, a Mongolian vowel separator and
a keycap digit were each found one at a time, and each was one member of a class
already covered here.

The rules match Unicode word segmentation (UAX #29) — WB4 ignores format and
combining characters inside a word, WB6/WB7 keep an apostrophe between letters —
with two deliberate departures, both pinned below: U+200B splits, because it
marks word boundaries in Thai and Khmer, and Han numerals stay content words
rather than identifiers.
"""

import unicodedata

import pytest

from basic_memory.repository.search_query import (
    RELAXATION_WORD_SEPARATOR_FORMATS,
    relaxation_word_tokens,
    relaxed_query_words,
)


def _characters_in_categories(*categories: str) -> list[str]:
    """Every assigned code point in the given general categories."""
    wanted = set(categories)
    return [
        char
        for code_point in range(0x110000)
        if unicodedata.category(char := chr(code_point)) in wanted
    ]


FORMAT_CHARACTERS = _characters_in_categories("Cf")
COMBINING_MARKS = _characters_in_categories("Mn", "Mc", "Me")
NUMBER_CHARACTERS = _characters_in_categories("Nd", "Nl", "No")

# Numerals written as letters (category Lo). Unicode gives them a numeric value,
# so isdigit()/isnumeric() answer True, but they are ordinary words in CJK prose.
HAN_NUMERALS = ["三", "四", "五", "十", "百", "千", "万", "億", "零"]


def _describe(characters: list[str], limit: int = 8) -> str:
    """Render failing characters as code points, since most are invisible."""
    shown = " ".join(f"U+{ord(char):04X}" for char in characters[:limit])
    return f"{len(characters)}: {shown}{' …' if len(characters) > limit else ''}"


def test_every_format_character_stays_inside_the_word() -> None:
    """No format character may split a word, apart from the declared separators.

    Splitting inflates the token count, which is the direction that walks a
    query past the three-token guard and relaxes it into fragments.
    """
    splitting = [
        char
        for char in FORMAT_CHARACTERS
        if char not in RELAXATION_WORD_SEPARATOR_FORMATS
        and len(relaxation_word_tokens(f"сло{char}во доступа")) != 2
    ]
    assert not splitting, f"format characters that split a word — {_describe(splitting)}"


def test_zero_width_space_stays_a_word_separator() -> None:
    """U+200B marks word boundaries in Thai and Khmer, so it must keep splitting.

    Grouping it into the word would collapse a whole phrase into one token and
    switch relaxation off for the one form of those scripts that reaches the
    guard at all.

    Written as a literal rather than read from the constant: a test parametrized
    over the exception list disappears when the list is emptied, which is exactly
    the change it exists to catch.
    """
    assert len(relaxation_word_tokens("сло\u200bво доступа")) == 3


def test_zero_width_space_is_the_only_declared_separator() -> None:
    """The sweep above skips whatever this constant holds, so its contents are load-bearing.

    Adding a character here silently removes it from that sweep, so the addition
    has to be a deliberate edit here rather than a side effect elsewhere.
    """
    assert RELAXATION_WORD_SEPARATOR_FORMATS == "\u200b"


def test_no_combining_mark_splits_a_word() -> None:
    """Marks attach to the character before them, so they cannot end a token.

    Counting them as separators cuts abugidas and decomposed text into syllable
    fragments — one word then looks like several tokens.
    """
    splitting = [
        char for char in COMBINING_MARKS if len(relaxation_word_tokens(f"сло{char}во доступа")) != 2
    ]
    assert not splitting, f"combining marks that split a word — {_describe(splitting)}"


def test_every_number_character_is_caught_by_the_identifier_guard() -> None:
    """A bare number term makes a query identifier-like, in any script.

    The guard exists for "SPEC 16"; Roman numerals, vulgar fractions and
    non-ASCII digits are the same shape and must not slip through it.
    """
    admitted = [
        char for char in NUMBER_CHARACTERS if relaxed_query_words(f"spec {char} design") is not None
    ]
    assert not admitted, f"number characters that cleared the guard — {_describe(admitted)}"


@pytest.mark.parametrize("numeral", HAN_NUMERALS)
def test_han_numerals_stay_content_words(numeral: str) -> None:
    """Han numerals are letters (category Lo) and ordinary words in CJK prose.

    Classifying them as identifiers would reject "数据 三 分析" and switch
    relaxation off for the queries it was turned on for.
    """
    assert unicodedata.category(numeral) == "Lo"
    assert relaxed_query_words(f"数据 {numeral} 分析") == ["数据", numeral, "分析"]
