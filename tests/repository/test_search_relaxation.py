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
