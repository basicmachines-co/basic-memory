"""Portable script n-gram analysis and full-text search regressions."""

from datetime import datetime, timezone

import pytest

from basic_memory.repository.script_ngrams import (
    analyze_script_query,
    build_script_ngrams,
    script_run_grams,
    script_runs,
)
from basic_memory.repository.postgres_search_repository import PostgresSearchRepository
from basic_memory.repository.search_index_row import SearchIndexRow
from basic_memory.repository.sqlite_search_repository import SQLiteSearchRepository


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("适者生存", (("适", "者", "生", "存"),)),
        ("適者生存", (("適", "者", "生", "存"),)),
        ("サバイバル", (("サ", "バ", "イ", "バ", "ル"),)),
        ("생존 경쟁", (("생", "존"), ("경", "쟁"))),
        ("ภาษาไทย", (("ภ", "า", "ษ", "า", "ไ", "ท", "ย"),)),
        ("時々", (("時", "々"),)),
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
    assert build_script_ngrams("适者", "生存") == ("适 者 适者 bm_script_boundary 生 存 生存")


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


def test_analyze_script_query_treats_lowercase_boolean_words_as_natural_language() -> None:
    query = analyze_script_query("OpenAI and 适者生存")

    assert query.word_text == "OpenAI and"
    assert query.gram_phrases == (("适者", "者生", "生存"),)


def test_analyze_script_query_preserves_quoted_mixed_script_semantics() -> None:
    query = analyze_script_query('"OpenAI 适者生存"')

    assert query.word_text == '"OpenAI 适者生存"'
    assert query.gram_phrases == ()


def test_analyze_script_query_preserves_compatibility_characters_in_word_text() -> None:
    query = analyze_script_query("ＡＢＣ ﬁnance 适者生存")

    assert query.word_text == "ＡＢＣ ﬁnance"
    assert query.gram_phrases == (("适者", "者生", "生存"),)


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
        content_stems="OpenAI and 即适者生存的讨论与黑猫，時々更新",
        content_snippet="OpenAI and 即适者生存的讨论与黑猫，時々更新",
        permalink="notes/evolution",
        created_at=now,
        updated_at=now,
    )
    await search_repository.index_item(row)

    assert [result.id for result in await search_repository.search("适者生存")] == [1294]
    assert [result.id for result in await search_repository.search("OpenAI 适者生存")] == [1294]
    assert [result.id for result in await search_repository.search("OpenAI and 适者生存")] == [1294]
    assert [result.id for result in await search_repository.search("猫")] == [1294]
    assert [result.id for result in await search_repository.search("時々")] == [1294]
    assert await search_repository.search("适生者存") == []


@pytest.mark.asyncio
async def test_mixed_word_and_script_search_preserves_fts_ranking(search_repository) -> None:
    now = datetime.now(timezone.utc)
    rows = [
        SearchIndexRow(
            project_id=search_repository.project_id,
            id=1296,
            type="entity",
            file_path="notes/strong-match.md",
            title="OpenAI OpenAI OpenAI",
            content_stems="OpenAI 即适者生存",
            content_snippet="OpenAI 即适者生存",
            permalink="notes/strong-match",
            created_at=now,
            updated_at=now,
        ),
        SearchIndexRow(
            project_id=search_repository.project_id,
            id=1297,
            type="entity",
            file_path="notes/weaker-match.md",
            title="Weaker match",
            content_stems="OpenAI 即适者生存",
            content_snippet="OpenAI 即适者生存",
            permalink="notes/weaker-match",
            created_at=now,
            updated_at=now,
        ),
    ]
    await search_repository.bulk_index_items(rows)

    results = await search_repository.search("OpenAI 适者生存")

    assert [result.id for result in results] == [1296, 1297]
    assert all(result.score != 0.0 for result in results)


@pytest.mark.asyncio
async def test_search_ranking_includes_every_script_run(search_repository) -> None:
    now = datetime.now(timezone.utc)
    row = SearchIndexRow(
        project_id=search_repository.project_id,
        id=1300,
        type="entity",
        file_path="notes/multiple-runs.md",
        title="Multiple runs",
        content_stems="适者 生存 生存",
        content_snippet="适者 生存 生存",
        permalink="notes/multiple-runs",
        created_at=now,
        updated_at=now,
    )
    await search_repository.index_item(row)

    first_run_results = await search_repository.search("适者")
    all_run_results = await search_repository.search("适者 生存")

    assert [result.id for result in all_run_results] == [1300]
    assert all_run_results[0].score != first_run_results[0].score


@pytest.mark.asyncio
async def test_postgres_ranking_adds_contributions_from_every_script_run(
    search_repository,
) -> None:
    if not isinstance(search_repository, PostgresSearchRepository):
        pytest.skip("PostgreSQL combines independently ranked script phrases")

    now = datetime.now(timezone.utc)
    shared_first_run = "適者 " * 5
    rows = [
        SearchIndexRow(
            project_id=search_repository.project_id,
            id=1302,
            type="entity",
            file_path="notes/one-secondary-match.md",
            title="One secondary match",
            content_stems=f"{shared_first_run}生存",
            content_snippet=f"{shared_first_run}生存",
            permalink="notes/one-secondary-match",
            created_at=now,
            updated_at=now,
        ),
        SearchIndexRow(
            project_id=search_repository.project_id,
            id=1303,
            type="entity",
            file_path="notes/many-secondary-matches.md",
            title="Many secondary matches",
            content_stems=f"{shared_first_run}{'生存 ' * 5}",
            content_snippet=f"{shared_first_run}{'生存 ' * 5}",
            permalink="notes/many-secondary-matches",
            created_at=now,
            updated_at=now,
        ),
    ]
    await search_repository.bulk_index_items(rows)

    results = await search_repository.search("適者 生存")

    assert [result.id for result in results] == [1303, 1302]
    assert results[0].score > results[1].score


@pytest.mark.asyncio
async def test_quoted_mixed_script_search_preserves_phrase_adjacency(search_repository) -> None:
    if not isinstance(search_repository, SQLiteSearchRepository):
        pytest.skip("SQLite-specific quoted FTS5 regression")

    now = datetime.now(timezone.utc)
    rows = [
        SearchIndexRow(
            project_id=search_repository.project_id,
            id=1298,
            type="entity",
            file_path="notes/adjacent.md",
            title="Adjacent",
            content_stems="OpenAI 适者生存",
            content_snippet="OpenAI 适者生存",
            permalink="notes/adjacent",
            created_at=now,
            updated_at=now,
        ),
        SearchIndexRow(
            project_id=search_repository.project_id,
            id=1299,
            type="entity",
            file_path="notes/separated.md",
            title="Separated",
            content_stems="OpenAI words far away from 适者生存",
            content_snippet="OpenAI words far away from 适者生存",
            permalink="notes/separated",
            created_at=now,
            updated_at=now,
        ),
    ]
    await search_repository.bulk_index_items(rows)

    results = await search_repository.search('"OpenAI 适者生存"')

    assert [result.id for result in results] == [1298]


@pytest.mark.asyncio
async def test_word_search_preserves_nfkc_sensitive_compatibility_characters(
    search_repository,
) -> None:
    now = datetime.now(timezone.utc)
    row = SearchIndexRow(
        project_id=search_repository.project_id,
        id=1301,
        type="entity",
        file_path="notes/compatibility.md",
        title="Compatibility",
        content_stems="ＡＢＣ ﬁnance",
        content_snippet="ＡＢＣ ﬁnance",
        permalink="notes/compatibility",
        created_at=now,
        updated_at=now,
    )
    await search_repository.index_item(row)

    results = await search_repository.search("ＡＢＣ ﬁnance")

    assert [result.id for result in results] == [1301]


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
