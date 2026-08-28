"""Pure tokenization primitives for the derived CJK bigram search-token channel."""

import pytest

from basic_memory.repository.search_query import (
    cjk_bigram_tokens,
    cjk_search_tokens,
    contains_cjk,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", ()),
        ("适", ("适",)),
        ("适者生存", ("适者", "者生", "生存")),
        ("東京都", ("東京", "京都")),
        ("かなカナ", ("かな", "なカ", "カナ")),
        ("한국어", ("한국", "국어")),
    ],
)
def test_cjk_bigram_tokens_use_overlapping_windows(text: str, expected: tuple[str, ...]) -> None:
    assert cjk_bigram_tokens(text) == expected


def test_cjk_search_tokens_exclude_non_cjk_and_preserve_run_boundaries() -> None:
    assert cjk_search_tokens("iPhone很好用", "计划", "开始") == "很好 好用 计划 开始"


def test_cjk_search_tokens_skips_empty_and_none_fields() -> None:
    assert cjk_search_tokens("", None, "适者") == "适者"


@pytest.mark.parametrize(
    ("text", "expected"),
    [("plain ASCII", False), ("مرحبا", False), ("适者", True), ("カナ", True), ("한국", True)],
)
def test_contains_cjk_covers_supported_ranges(text: str, expected: bool) -> None:
    assert contains_cjk(text) is expected
