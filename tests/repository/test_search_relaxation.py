"""Focused coverage for relaxed full-text query eligibility."""

import pytest

from basic_memory.repository.search_repository_base import relaxed_query_words


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
        "SPEC-16 设计",
        "foo/bar 季度",
        "季度 季度",
        "the 季度",
    ],
)
def test_relaxed_query_words_preserves_short_query_guard_after_cjk_pruning(query: str) -> None:
    """Unsafe, duplicate, or stopword terms cannot pad a one-term CJK relaxation."""
    assert relaxed_query_words(query) is None
