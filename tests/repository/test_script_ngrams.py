"""Portable script n-gram analysis and full-text search regressions."""

from datetime import datetime, timezone

import pytest

from basic_memory.repository.script_ngrams import (
    analyze_script_query,
    build_script_ngrams,
    script_run_grams,
    script_runs,
)
from basic_memory.repository.search_index_row import SearchIndexRow


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("适者生存", (("适", "者", "生", "存"),)),
        ("適者生存", (("適", "者", "生", "存"),)),
        ("サバイバル", (("サ", "バ", "イ", "バ", "ル"),)),
        ("생존 경쟁", (("생", "존"), ("경", "쟁"))),
        ("ภาษาไทย", (("ภ", "า", "ษ", "า", "ไ", "ท", "ย"),)),
        ("ＡＢＣ", ()),
    ],
)
def test_script_runs_cover_cjk_scripts_and_normalize_width(
    text: str,
    expected: tuple[tuple[str, ...], ...],
) -> None:
    assert script_runs(text) == expected


def test_script_run_grams_preserve_order_and_single_character_queries() -> None:
    assert script_run_grams(("适", "者", "生", "存")) == ("适者", "者生", "生存")
    assert script_run_grams(("猫",)) == ("猫",)


def test_script_runs_attach_combining_marks_to_the_previous_unit() -> None:
    text = "漢\N{VARIATION SELECTOR-1}"

    assert script_runs(text) == ((text,),)
    assert analyze_script_query(text).word_text is None


def test_build_script_ngrams_keeps_runs_from_matching_across_boundaries() -> None:
    assert build_script_ngrams("适者", "生存") == "适者 bm_script_boundary 生存"


def test_analyze_script_query_separates_word_and_ordered_script_terms() -> None:
    query = analyze_script_query("OpenAI 适者生存，サバイバル")

    assert query.word_text == "OpenAI"
    assert query.gram_phrases == (
        ("适者", "者生", "生存"),
        ("サバ", "バイ", "イバ", "バル"),
    )


def test_analyze_script_query_preserves_explicit_boolean_semantics() -> None:
    query = analyze_script_query("OpenAI OR 适者生存")

    assert query.word_text == "OpenAI OR 适者生存"
    assert query.gram_phrases == ()


@pytest.mark.asyncio
async def test_search_matches_cjk_substring_without_matching_reordered_characters(
    search_repository,
) -> None:
    now = datetime.now(timezone.utc)
    row = SearchIndexRow(
        project_id=search_repository.project_id,
        id=1294,
        type="entity",
        file_path="notes/evolution.md",
        title="進化について",
        content_stems="OpenAI 即适者生存的讨论",
        content_snippet="OpenAI 即适者生存的讨论",
        permalink="notes/evolution",
        created_at=now,
        updated_at=now,
    )
    await search_repository.index_item(row)

    assert [result.id for result in await search_repository.search("适者生存")] == [1294]
    assert [result.id for result in await search_repository.search("OpenAI 适者生存")] == [1294]
    assert await search_repository.search("适生者存") == []


@pytest.mark.asyncio
async def test_search_matches_script_text_beyond_the_parent_fts_limit(search_repository) -> None:
    now = datetime.now(timezone.utc)
    row = SearchIndexRow(
        project_id=search_repository.project_id,
        id=1295,
        type="entity",
        file_path="notes/long.md",
        title="進化 Long note",
        content_stems="bounded parent search text",
        content_snippet=f"{'x' * 9_000} 适者生存",
        permalink="notes/long",
        created_at=now,
        updated_at=now,
    )
    await search_repository.index_item(row)

    assert [result.id for result in await search_repository.search("适者生存")] == [1295]
    assert [result.id for result in await search_repository.search("進化 适者生存")] == [1295]
