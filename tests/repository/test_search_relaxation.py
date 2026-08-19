"""Focused coverage for relaxed full-text query eligibility."""

import pytest

from basic_memory.repository.search_query import relaxed_query_words


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("季度 报告", ["季度", "报告"]),
        ("カタカナ レポート", ["カタカナ", "レポート"]),
        ("분기 보고", ["분기", "보고"]),
    ],
)
def test_relaxed_query_words_supports_whitespace_separated_cjk_scripts(
    query: str,
    expected: list[str],
) -> None:
    """Han, kana, and Hangul terms all bypass the ASCII three-token gate."""
    assert relaxed_query_words(query) == expected


@pytest.mark.parametrize(
    "query",
    [
        "季度",
        "SPEC-16 设计",
        "foo/bar 季度",
        "季度 季度",
        "the 季度",
    ],
)
def test_relaxed_query_words_preserves_short_query_guard_after_cjk_pruning(query: str) -> None:
    """Unsafe, duplicate, or stopword terms cannot pad a one-term CJK relaxation."""
    assert relaxed_query_words(query) is None


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("как отозвать выданный доступ", ["как", "отозвать", "выданный", "доступ"]),
        ("як відкликати виданий доступ", ["як", "відкликати", "виданий", "доступ"]),
        ("πώς να ανακαλέσετε πρόσβαση", ["πώς", "να", "ανακαλέσετε", "πρόσβαση"]),
        ("כיצד לבטל גישה שניתנה", ["כיצד", "לבטל", "גישה", "שניתנה"]),
        ("كيف تلغي الوصول الممنوح", ["كيف", "تلغي", "الوصول", "الممنوح"]),
        ("ինչպես չեղարկել տրված մուտքը", ["ինչպես", "չեղարկել", "տրված", "մուտքը"]),
    ],
)
def test_relaxed_query_words_supports_non_latin_alphabetic_scripts(
    query: str,
    expected: list[str],
) -> None:
    """Non-Latin alphabetic queries reach the same guard as Latin ones.

    An ASCII-only token pattern found zero tokens in these queries, so the
    three-token guard rejected every one of them and the hybrid FTS branch
    contributed nothing — hybrid search silently became vector-only.
    """
    assert relaxed_query_words(query) == expected


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("पहुंच कैसे रद्द करें", ["पहुंच", "कैसे", "रद्द", "करें"]),  # Devanagari
        ("วิธี เพิกถอน การเข้าถึง", ["วิธี", "เพิกถอน", "การเข้าถึง"]),  # Thai
        ("como revogar acesso concedido", ["como", "revogar", "acesso", "concedido"]),
    ],
)
def test_relaxed_query_words_keeps_combining_marks_with_their_base_character(
    query: str,
    expected: list[str],
) -> None:
    """Vowel signs and diacritics stay inside the word they attach to.

    Combining marks are not alphanumeric, so treating them as separators splits
    one abugida word into syllable fragments. The token count then inflates past
    the three-token guard and relaxation ORs those fragments together.
    """
    assert relaxed_query_words(query) == expected


@pytest.mark.parametrize(
    "query",
    [
        "अंतर्राष्ट्रीयकरण",  # one Devanagari word: 7 fragments if marks split it
        "การเข้าถึง",  # one Thai word
        "pre\u0301sentation",  # one word, NFD-decomposed acute accent
    ],
)
def test_relaxed_query_words_guards_single_words_with_combining_marks(query: str) -> None:
    """A single word stays one token, so the short-query guard still rejects it."""
    assert relaxed_query_words(query) is None


@pytest.mark.parametrize(
    "query",
    [
        "отозвать доступ",  # fewer than three tokens
        "спека 16 доступ",  # pure-digit token
        '"точная фраза"',  # quoted: user intent is explicit
        "доступ OR токен",  # explicit boolean: user intent is explicit
    ],
)
def test_relaxed_query_words_applies_existing_guards_to_non_latin(query: str) -> None:
    """Non-Latin queries gain no exemption from the short-query and identifier guards."""
    assert relaxed_query_words(query) is None


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("می‌روم خانه", None),  # two Persian words, one joined by ZWNJ
        ("نمی‌خواهم دسترسی را لغو", ["نمی‌خواهم", "دسترسی", "را", "لغو"]),
        ("क‍ष विशेष पहुंच", ["क‍ष", "विशेष", "पहुंच"]),  # explicit ZWJ conjunct
    ],
)
def test_relaxed_query_words_keeps_join_controls_inside_words(
    query: str,
    expected: list[str] | None,
) -> None:
    """U+200C/U+200D are written inside a word, so they must not split its token.

    Splitting on them inflates the token count: a two-word Persian query looks
    like three tokens, clears the three-token guard, and relaxes into fragments.
    """
    assert relaxed_query_words(query) == expected


@pytest.mark.parametrize(
    "query",
    [
        "SPEC Ⅻ design",  # Nl: Roman numeral twelve
        "spec ½ design",  # No: vulgar fraction one half
        "٣ ٤ ٥",  # Arabic-Indic digits
    ],
)
def test_relaxed_query_words_rejects_unicode_numeric_tokens(query: str) -> None:
    """The identifier guard classifies numbers Unicode-wide, not just as ASCII digits.

    `isdigit()` is false for Nl/No characters, so admitting every alphanumeric
    character would let identifier-like queries slip past the numeric guard.
    """
    assert relaxed_query_words(query) is None


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("п’ять проектів", None),  # two Ukrainian words, U+2019
        ("об'єкт доступу", None),  # two Ukrainian words, ASCII apostrophe
        (
            "скасувати п’ять виданих об'єктів",
            ["скасувати", "п’ять", "виданих", "об'єктів"],
        ),
    ],
)
def test_relaxed_query_words_keeps_apostrophes_inside_words(
    query: str,
    expected: list[str] | None,
) -> None:
    """A word-internal apostrophe must not split one word into several tokens.

    Splitting on it turned a two-word Ukrainian query into three tokens, which
    cleared the three-token guard and relaxed into one-letter fragments.
    """
    assert relaxed_query_words(query) == expected


def test_relaxed_query_words_apostrophe_does_not_shield_numeric_tokens() -> None:
    """An apostrophe joins letters only, so a digit stays a token of its own.

    Were `16's` read as one token it would not be numeric, and the query would
    escape the identifier guard that rejects `SPEC 16 design`.
    """
    assert relaxed_query_words("SPEC 16's design") is None


def test_relaxed_query_words_keeps_ascii_contractions_whole() -> None:
    """ASCII contractions become one token instead of a word plus a stray letter.

    This is the one place where relaxed terms differ from the previous ASCII
    behaviour. It only ever lowers the token count, so no query that the guards
    used to reject can start relaxing because of it.
    """
    assert relaxed_query_words("don't touch this") == ["don't", "touch"]


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("数据 三 分析", ["数据", "三", "分析"]),
        ("日本 十 経済 統計", ["日本", "十", "経済", "統計"]),
        ("データ 二 分析 結果", ["データ", "二", "分析", "結果"]),
    ],
)
def test_relaxed_query_words_treats_han_numerals_as_content_words(
    query: str,
    expected: list[str],
) -> None:
    """The CJK guard stays on `isdigit()`, so a numeral word does not veto relaxation.

    143 characters in U+3000–U+9FFF are `isnumeric()` without being `isdigit()`.
    Classifying them as identifiers would reject ordinary CJK prose and switch
    relaxation back off for the queries it was turned on for.
    """
    assert relaxed_query_words(query) == expected


@pytest.mark.parametrize(
    "query",
    [
        "SPEC 16 设计",  # ASCII digits
        "SPEC Ⅻ 设计",  # Roman numeral: a number character, not a Han word
        "SPEC ½ 设计",  # vulgar fraction
    ],
)
def test_relaxed_query_words_rejects_number_characters_in_cjk_queries(query: str) -> None:
    """Adding a CJK term must not smuggle an identifier past the numeric guard.

    Han numerals are category Lo — letters — so they stay content words, while
    every category-N character is caught in both branches alike.
    """
    assert relaxed_query_words(query) is None


@pytest.mark.parametrize(
    "query",
    [
        "SPEC 1️⃣ design",  # keycap digit: digit plus VS16 plus enclosing keycap
        "spec 1́ design",  # digit carrying a combining acute
    ],
)
def test_relaxed_query_words_sees_numbers_through_combining_marks(query: str) -> None:
    """A mark attached to a digit must not disguise it from the numeric guard.

    Marks stay inside the token so the word is counted once, but classification
    looks at the token without them — otherwise a decorated digit walks an
    identifier-like query straight past the guard.
    """
    assert relaxed_query_words(query) is None


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("пере­вод доступа", None),  # soft hyphen from copied formatted text
        ("сло⁠во доступа", None),  # word joiner
        ("сло﻿во доступа", None),  # zero-width no-break space
        ("сло᠎во доступа", None),  # Mongolian vowel separator
        ("сло‏во доступа", None),  # right-to-left mark
    ],
)
def test_relaxed_query_words_ignores_word_internal_format_characters(
    query: str,
    expected: list[str] | None,
) -> None:
    """Invisible format characters inside a word must not split its token.

    Text pasted from formatted documents carries them, and splitting there
    inflates the token count exactly as the join-control case did.
    """
    assert relaxed_query_words(query) == expected


def test_relaxed_query_words_treats_zero_width_space_as_a_word_boundary() -> None:
    """U+200B separates words in Thai and Khmer, so it must keep splitting.

    Grouping it into the word would collapse a whole phrase into one token and
    switch relaxation off for the one form of those scripts that reaches the
    guard at all.
    """
    assert relaxed_query_words("ฉัน​จะลอง​ชำระเงิน") == [
        "ฉัน",
        "จะลอง",
        "ชำระเงิน",
    ]


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("ג׳ון סמית כתב", ["ג׳ון", "סמית", "כתב"]),  # geresh: foreign sounds
        ("ר״ת של המשפט", ["ר״ת", "של", "המשפט"]),  # gershayim: acronym
        ("col·lecció de dades", ["col·lecció", "de", "dades"]),  # Catalan middle dot
    ],
)
def test_relaxed_query_words_keeps_letter_joining_punctuation(
    query: str,
    expected: list[str],
) -> None:
    """The apostrophe rule covers every UAX #29 MidLetter character taken here.

    Hebrew writes geresh and gershayim inside words constantly, so splitting on
    them turns a three-word query into fragments — the same failure the
    apostrophe case had, in a script this change exists to support.
    """
    assert relaxed_query_words(query) == expected


@pytest.mark.parametrize(
    "query",
    [
        "spec 1.2 design",  # full stop: UAX #29 would join "1.2" into one token
        "spec 1:2 design",  # colon
    ],
)
def test_relaxed_query_words_splits_on_structural_punctuation(query: str) -> None:
    """Colon and full stop are deliberately outside the MidLetter set taken here.

    UAX #29 joins digits across a full stop, and "1.2" is not a category-N token,
    so it would slip past the numeric guard. Both also carry structure in
    permalinks, paths and version strings.
    """
    assert relaxed_query_words(query) is None


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("גּ׳ון סמית כתב", ["גּ׳ון", "סמית", "כתב"]),  # dagesh between letter and geresh
        ("pré d'accord test", ["pré", "d'accord", "test"]),  # NFD before apostrophe
    ],
)
def test_relaxed_query_words_finds_the_base_letter_through_marks(
    query: str,
    expected: list[str],
) -> None:
    """The joining rule looks for a letter, not for the last character.

    Pointed Hebrew and decomposed Latin put a mark between the letter and the
    punctuation, so reading only the character before it sees the mark and
    splits a word the rule is meant to keep whole.
    """
    assert relaxed_query_words(query) == expected
