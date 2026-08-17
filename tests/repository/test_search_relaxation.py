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
    "query",
    [
        "पहुंच कैसे रद्द करें",  # Devanagari
        "วิธี เพิกถอน การเข้าถึง",  # Thai
    ],
)
def test_relaxed_query_words_still_splits_scripts_with_combining_marks(query: str) -> None:
    """Known limitation: abugidas split on combining marks, which `\\w` excludes.

    Vowel signs and viramas are non-spacing marks, so a `\\w`-based pattern cuts
    each syllable cluster into fragments. Relaxation still engages — the token
    count only grows — but the resulting OR terms are word fragments rather than
    words. Proper support needs grapheme-cluster segmentation; this test pins the
    current behaviour so a future change is a deliberate one.
    """
    words = relaxed_query_words(query)
    assert words is not None
    assert len(words) > len(query.split())


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
