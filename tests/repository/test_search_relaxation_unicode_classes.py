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

import time
import unicodedata

import pytest

from basic_memory.repository.search_query import (
    RELAXATION_WORD_INTERNAL_PUNCTUATION,
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
PUNCTUATION_CHARACTERS = _characters_in_categories("Pc", "Pd", "Ps", "Pe", "Pi", "Pf", "Po")

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


def test_only_the_declared_punctuation_joins_a_word() -> None:
    """Nothing else in the punctuation classes may join two letters.

    The joining set is a chosen subset of UAX #29 MidLetter, so it has to stay a
    subset: any other punctuation that started joining would merge two words into
    one term and quietly change what the backend searches for.
    """
    joining = [
        char
        for char in PUNCTUATION_CHARACTERS
        if char not in RELAXATION_WORD_INTERNAL_PUNCTUATION
        and len(relaxation_word_tokens(f"сло{char}во доступа")) != 3
    ]
    assert not joining, f"punctuation that joined a word — {_describe(joining)}"


def test_declared_punctuation_is_exactly_the_chosen_midletter_subset() -> None:
    """Pin the set itself: the sweep above skips whatever it holds.

    Removing a character silently drops it from every sweep here, and adding one
    silently exempts it, so both have to be a deliberate edit rather than a side
    effect. The named cases in test_search_relaxation.py pin what each is for.
    """
    assert (
        RELAXATION_WORD_INTERNAL_PUNCTUATION
        == "'\u2018\u2019\uff07\u00b7\u0387\u055f\u05f3\u05f4\u2027"
    )


def test_declared_punctuation_joins_letters_only() -> None:
    """Each joiner must join letters, and none may join digits.

    Joining digits is what makes the exclusions below necessary: a term like
    "1.2" is not a category-N token, so it would walk an identifier-like query
    past the numeric guard.
    """
    not_joining = [
        char
        for char in RELAXATION_WORD_INTERNAL_PUNCTUATION
        if len(relaxation_word_tokens(f"сло{char}во доступа")) != 2
    ]
    assert not not_joining, f"declared joiners that split letters — {_describe(not_joining)}"

    joining_digits = [
        char
        for char in RELAXATION_WORD_INTERNAL_PUNCTUATION
        if relaxed_query_words(f"spec 1{char}2 design") is not None
    ]
    assert not joining_digits, (
        f"declared joiners that shielded a digit — {_describe(joining_digits)}"
    )


@pytest.mark.parametrize("structural", [":", "."])
def test_colon_and_full_stop_split_although_uax29_joins_them(structural: str) -> None:
    """The two deliberate departures from MidLetter, written as literals.

    UAX #29 returns "1.2" as a single token. Such a token is not category N, so
    it would slip past the numeric guard that rejects "SPEC 16". Both characters
    also carry structure here — permalinks, paths, version strings — so both keep
    splitting, and a query built on them stays ineligible for relaxation.
    """
    assert len(relaxation_word_tokens(f"сло{structural}во доступа")) == 3
    assert relaxed_query_words(f"spec 1{structural}2 design") is None


# UAX #29 Word_Break values for the punctuation that joins words, transcribed
# from the standard. Python's unicodedata does not expose the property, so the
# class is pinned here as data: the partition below then has to account for every
# member, and a character cannot be forgotten, only deliberately excluded.
UAX29_WORD_JOINING_PUNCTUATION = {
    "MidLetter": ":··՟״‧︓﹕：",
    "MidNumLet": ".‘’․﹒＇．",
    "Single_Quote": "'",
}
# Excluded on purpose: these carry structure in this project rather than sitting
# inside words — "tag:example" is documented query syntax, and permalinks and
# file names are built on the full stop.
STRUCTURAL_PUNCTUATION = ":︓﹕：.․﹒．"


def test_the_joining_set_accounts_for_every_word_joining_character() -> None:
    """Every UAX #29 word-joining character is either taken or named structural.

    Review surfaced these one at a time — the Armenian abbreviation mark and the
    fullwidth apostrophe were the last two. Partitioning the class means a
    missing character fails here rather than in another round.
    """
    standard = set("".join(UAX29_WORD_JOINING_PUNCTUATION.values()))
    taken = set(RELAXATION_WORD_INTERNAL_PUNCTUATION)
    excluded = set(STRUCTURAL_PUNCTUATION)

    unaccounted = standard - taken - excluded
    assert not unaccounted, (
        f"word-joining characters neither taken nor excluded — {_describe(sorted(unaccounted))}"
    )

    contradictory = taken & excluded
    assert not contradictory, (
        f"characters both taken and excluded — {_describe(sorted(contradictory))}"
    )


def test_taken_characters_outside_the_standard_are_justified() -> None:
    """U+05F3 is the one addition: UAX #29 classes geresh as a letter, not punctuation.

    Python sees it as Po, so the joining rule has to name it explicitly to reach
    the same result the standard does for Hebrew.
    """
    standard = set("".join(UAX29_WORD_JOINING_PUNCTUATION.values()))
    assert set(RELAXATION_WORD_INTERNAL_PUNCTUATION) - standard == {"׳"}


@pytest.mark.parametrize("structural", sorted(STRUCTURAL_PUNCTUATION))
def test_structural_punctuation_keeps_splitting(structural: str) -> None:
    """Joining across these would merge a qualifier with its value, or a name with its extension."""
    assert len(relaxation_word_tokens(f"сло{structural}во доступа")) == 3


def test_tokenizing_scales_linearly_with_query_length() -> None:
    """Doubling the query must roughly double the work, not more.

    The joining rule has to look at what follows the punctuation. Reading that
    with a slice copies the rest of the query at every joiner, which is
    quadratic: an unbounded query full of apostrophes then ties up the worker
    that tokenizes it. The ratio is asserted rather than a duration, so the test
    does not depend on how fast the machine is.
    """
    query = "a'b " * (128 * 1024 // 4)

    def elapsed(text: str) -> float:
        start = time.perf_counter()
        relaxation_word_tokens(text)
        return time.perf_counter() - start

    # Fastest of three runs each: the ratio, not the duration, is what is being
    # asserted, so a slow or busy machine does not turn this red. Measured here,
    # the indexed reader scales at ×2.0 and the sliced one at ×3.2.
    single = min(elapsed(query) for _ in range(3))
    double = min(elapsed(query * 2) for _ in range(3))

    assert double < single * 2.5, (
        f"tokenizing scaled worse than linearly: {single * 1000:.1f} ms then {double * 1000:.1f} ms"
    )


def test_trailing_format_trim_scales_linearly() -> None:
    """A word ending in a long run of format characters must trim in one pass.

    Format characters are word-internal, so a token collects them all before the
    trailing trim runs. Trimming one character at a time copies the shrinking
    token at every step, which is quadratic: an unbounded query ending in enough
    soft hyphens then ties up the worker that tokenizes it. As above, the ratio
    is asserted rather than a duration.
    """

    def elapsed(count: int) -> float:
        text = "a" + "\u00ad" * count
        start = time.perf_counter()
        relaxation_word_tokens(text)
        return time.perf_counter() - start

    # Sized so the trim dominates the measurement: at shorter lengths the
    # tokenizer's linear per-character work dilutes the quadratic term below
    # the ratio threshold and a regression would pass unnoticed.
    single = min(elapsed(256 * 1024) for _ in range(3))
    double = min(elapsed(512 * 1024) for _ in range(3))

    assert double < single * 2.5, (
        f"trailing trim scaled worse than linearly: {single * 1000:.1f} ms then {double * 1000:.1f} ms"
    )
