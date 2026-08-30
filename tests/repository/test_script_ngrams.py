"""Portable script n-gram analysis and full-text search regressions."""

from datetime import datetime, timezone

import pytest

from basic_memory import db
from basic_memory.models import Entity
from basic_memory.repository.script_ngrams import (
    analyze_script_query,
    build_script_ngrams,
    mixed_token_word_terms,
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


def test_mixed_token_word_terms_encode_all_word_fragments() -> None:
    assert mixed_token_word_terms("foo不適者bar ＡＢＣ適者") == (
        "bmword2c26b46b68ffc68ff99b453c1d30413413422d706483bfa0f98a5e886266e7ae",
        "bmwordfcde2b2edba56bf408601fb721fe9b5c338d10ee429ea04fae5511b68fbf8fb9",
        "bmwordba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
    )


def test_analyze_script_query_separates_word_and_ordered_script_terms() -> None:
    query = analyze_script_query("OpenAI 适者生存，サバイバル")

    assert query.word_text == "OpenAI"
    assert query.gram_phrases == (
        ("适者", "者生", "生存"),
        ("サバ", "バイ", "イバ", "バル"),
    )


def test_analyze_script_query_preserves_adjoining_word_and_script_token() -> None:
    query = analyze_script_query("foo適者bar")

    assert query.word_text is None
    assert query.gram_phrases == (
        ("適者",),
        ("bmword2c26b46b68ffc68ff99b453c1d30413413422d706483bfa0f98a5e886266e7ae",),
        ("bmwordfcde2b2edba56bf408601fb721fe9b5c338d10ee429ea04fae5511b68fbf8fb9",),
    )


def test_analyze_script_query_preserves_punctuation_separated_mixed_token() -> None:
    query = analyze_script_query("foo-適者-bar")

    assert query.word_text is None
    assert query.gram_phrases == (
        ("適者",),
        ("bmword2c26b46b68ffc68ff99b453c1d30413413422d706483bfa0f98a5e886266e7ae",),
        ("bmwordfcde2b2edba56bf408601fb721fe9b5c338d10ee429ea04fae5511b68fbf8fb9",),
    )


def test_analyze_script_query_does_not_require_script_substring_in_word_channel() -> None:
    query = analyze_script_query("foo適者")

    assert query.word_text is None
    assert query.gram_phrases == (
        ("適者",),
        ("bmword2c26b46b68ffc68ff99b453c1d30413413422d706483bfa0f98a5e886266e7ae",),
    )


def test_analyze_script_query_preserves_compatibility_bytes_in_mixed_prefix() -> None:
    query = analyze_script_query("ＡＢＣ適者")

    assert query.word_text is None
    assert query.gram_phrases == (
        ("適者",),
        ("bmwordba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",),
    )


def test_analyze_script_query_retains_trailing_word_in_auxiliary_channel() -> None:
    query = analyze_script_query("適者OpenAI")

    assert query.word_text is None
    assert query.gram_phrases == (
        ("適者",),
        ("bmword7d3194f79e645c42e4396dda38be04766810ec6a00d00aced3ffc2a0a1f1a9ef",),
    )


def test_analyze_script_query_preserves_explicit_boolean_semantics() -> None:
    query = analyze_script_query("OpenAI OR 适者生存")

    assert query.word_text == "OpenAI OR 适者生存"
    assert query.gram_phrases == ()


@pytest.mark.parametrize("text", ["NOT 生存", "生存 OR"])
def test_analyze_script_query_preserves_boundary_boolean_operators(text: str) -> None:
    query = analyze_script_query(text)

    assert query.word_text == text
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


def test_analyze_script_query_treats_compatibility_boolean_text_as_natural_language() -> None:
    query = analyze_script_query("适者 ＡＮＤ 生存")

    assert query.word_text == "ＡＮＤ"
    assert query.gram_phrases == (("适者",), ("生存",))


@pytest.mark.parametrize("separator", ["\t", "\n"])
def test_analyze_script_query_matches_backend_boolean_whitespace(separator: str) -> None:
    query = analyze_script_query(f"适者{separator}AND{separator}生存")

    assert query.word_text == "and"
    assert query.gram_phrases == (("适者",), ("生存",))


@pytest.mark.parametrize("text", ["!!!", "😀"])
def test_analyze_script_query_preserves_punctuation_only_text(text: str) -> None:
    query = analyze_script_query(text)

    assert query.word_text == text
    assert query.gram_phrases == ()


@pytest.mark.asyncio
async def test_compatibility_boolean_text_uses_script_substring_search(search_repository) -> None:
    now = datetime.now(timezone.utc)
    row = SearchIndexRow(
        project_id=search_repository.project_id,
        id=1307,
        type="entity",
        file_path="notes/compatibility-boolean.md",
        title="Compatibility Boolean",
        content_stems="不适者 ＡＮＤ 生存者",
        content_snippet="不适者 ＡＮＤ 生存者",
        permalink="notes/compatibility-boolean",
        created_at=now,
        updated_at=now,
    )
    await search_repository.index_item(row)

    results = await search_repository.search("适者 ＡＮＤ 生存")

    assert [result.id for result in results] == [1307]


@pytest.mark.asyncio
async def test_non_space_boolean_text_uses_script_substring_search(search_repository) -> None:
    now = datetime.now(timezone.utc)
    row = SearchIndexRow(
        project_id=search_repository.project_id,
        id=1308,
        type="entity",
        file_path="notes/non-space-boolean.md",
        title="Non-space Boolean",
        content_stems="不适者\tAND\t生存者",
        content_snippet="不适者\tAND\t生存者",
        permalink="notes/non-space-boolean",
        created_at=now,
        updated_at=now,
    )
    await search_repository.index_item(row)

    results = await search_repository.search("适者\tAND\t生存")

    assert [result.id for result in results] == [1308]


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["!!!", "😀"])
async def test_punctuation_only_search_does_not_return_every_row(
    search_repository,
    query: str,
) -> None:
    now = datetime.now(timezone.utc)
    row = SearchIndexRow(
        project_id=search_repository.project_id,
        id=1310,
        type="entity",
        file_path="notes/punctuation-decoy.md",
        title="Punctuation decoy",
        content_stems="ordinary searchable words",
        content_snippet="ordinary searchable words",
        permalink="notes/punctuation-decoy",
        created_at=now,
        updated_at=now,
    )
    await search_repository.index_item(row)

    assert await search_repository.search(query) == []


@pytest.mark.asyncio
async def test_sqlite_script_search_combines_metadata_and_title_filters(
    search_repository,
    session_maker,
) -> None:
    if not isinstance(search_repository, SQLiteSearchRepository):
        pytest.skip("SQLite-specific FTS5 rowid regression")

    now = datetime.now(timezone.utc)
    async with db.scoped_session(session_maker) as session:
        entity = Entity(
            project_id=search_repository.project_id,
            title="Evolution match",
            note_type="note",
            permalink="notes/evolution-filtered",
            file_path="notes/evolution-filtered.md",
            content_type="text/markdown",
            entity_metadata={"region": "asia"},
            created_at=now,
            updated_at=now,
        )
        session.add(entity)
        await session.flush()
        entity_id = entity.id

    row = SearchIndexRow(
        project_id=search_repository.project_id,
        id=entity_id,
        type="entity",
        entity_id=entity_id,
        file_path="notes/evolution-filtered.md",
        title="Evolution match",
        content_stems="不适者生存者",
        content_snippet="不适者生存者",
        permalink="notes/evolution-filtered",
        created_at=now,
        updated_at=now,
    )
    await search_repository.index_item(row)

    results = await search_repository.search(
        "适者",
        title="Evolution match",
        metadata_filters={"region": "asia"},
    )

    assert [result.id for result in results] == [entity_id]


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
async def test_search_preserves_adjoining_word_and_script_token(search_repository) -> None:
    now = datetime.now(timezone.utc)
    row = SearchIndexRow(
        project_id=search_repository.project_id,
        id=1311,
        type="entity",
        file_path="notes/adjoining-script.md",
        title="Adjoining script",
        content_stems="foo適者bar",
        content_snippet="foo適者bar",
        permalink="notes/adjoining-script",
        created_at=now,
        updated_at=now,
    )
    await search_repository.index_item(row)

    results = await search_repository.search("foo適者bar")

    assert [result.id for result in results] == [1311]


@pytest.mark.asyncio
async def test_search_matches_script_substring_inside_longer_mixed_token(search_repository) -> None:
    now = datetime.now(timezone.utc)
    row = SearchIndexRow(
        project_id=search_repository.project_id,
        id=1312,
        type="entity",
        file_path="notes/longer-adjoining-script.md",
        title="Longer adjoining script",
        content_stems="foo不適者bar",
        content_snippet="foo不適者bar",
        permalink="notes/longer-adjoining-script",
        created_at=now,
        updated_at=now,
    )
    await search_repository.index_item(row)

    results = await search_repository.search("foo適者")

    assert [result.id for result in results] == [1312]


@pytest.mark.asyncio
async def test_search_preserves_compatibility_bytes_in_mixed_prefix(search_repository) -> None:
    now = datetime.now(timezone.utc)
    row = SearchIndexRow(
        project_id=search_repository.project_id,
        id=1314,
        type="entity",
        file_path="notes/compatibility-prefix-script.md",
        title="Compatibility prefix script",
        content_stems="ＡＢＣ適者",
        content_snippet="ＡＢＣ適者",
        permalink="notes/compatibility-prefix-script",
        created_at=now,
        updated_at=now,
    )
    await search_repository.index_item(row)

    results = await search_repository.search("ＡＢＣ適者")

    assert [result.id for result in results] == [1314]


@pytest.mark.asyncio
async def test_search_requires_trailing_word_in_mixed_token(search_repository) -> None:
    now = datetime.now(timezone.utc)
    matching_row = SearchIndexRow(
        project_id=search_repository.project_id,
        id=1315,
        type="entity",
        file_path="notes/trailing-mixed-word.md",
        title="Trailing mixed word",
        content_stems="適者OpenAI",
        content_snippet="適者OpenAI",
        permalink="notes/trailing-mixed-word",
        created_at=now,
        updated_at=now,
    )
    nonmatching_row = SearchIndexRow(
        project_id=search_repository.project_id,
        id=1316,
        type="entity",
        file_path="notes/script-only.md",
        title="Script only",
        content_stems="適者",
        content_snippet="適者",
        permalink="notes/script-only",
        created_at=now,
        updated_at=now,
    )
    await search_repository.index_item(matching_row)
    await search_repository.index_item(nonmatching_row)

    results = await search_repository.search("適者OpenAI")

    assert [result.id for result in results] == [1315]


@pytest.mark.asyncio
async def test_search_preserves_punctuation_separated_mixed_token(search_repository) -> None:
    now = datetime.now(timezone.utc)
    row = SearchIndexRow(
        project_id=search_repository.project_id,
        id=1313,
        type="entity",
        file_path="notes/punctuation-separated-script.md",
        title="Punctuation-separated script",
        content_stems="foo-適者-bar",
        content_snippet="foo-適者-bar",
        permalink="notes/punctuation-separated-script",
        created_at=now,
        updated_at=now,
    )
    await search_repository.index_item(row)

    results = await search_repository.search("foo-適者-bar")

    assert [result.id for result in results] == [1313]


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
async def test_sqlite_mixed_search_requires_all_words_in_one_column(search_repository) -> None:
    if not isinstance(search_repository, SQLiteSearchRepository):
        pytest.skip("SQLite preserves its established per-column word matching")

    now = datetime.now(timezone.utc)
    rows = [
        SearchIndexRow(
            project_id=search_repository.project_id,
            id=1305,
            type="entity",
            file_path="notes/same-column.md",
            title="Same column",
            content_stems="alpha beta 適者生存",
            content_snippet="alpha beta 適者生存",
            permalink="notes/same-column",
            created_at=now,
            updated_at=now,
        ),
        SearchIndexRow(
            project_id=search_repository.project_id,
            id=1306,
            type="entity",
            file_path="notes/split-columns.md",
            title="alpha",
            content_stems="beta 適者生存",
            content_snippet="beta 適者生存",
            permalink="notes/split-columns",
            created_at=now,
            updated_at=now,
        ),
    ]
    await search_repository.bulk_index_items(rows)

    results = await search_repository.search("alpha beta 適者生存")

    assert [result.id for result in results] == [1305]


@pytest.mark.asyncio
async def test_sqlite_relaxed_search_keeps_word_and_script_channels_distinct(
    search_repository,
) -> None:
    if not isinstance(search_repository, SQLiteSearchRepository):
        pytest.skip("SQLite-specific relaxed FTS5 regression")

    now = datetime.now(timezone.utc)
    row = SearchIndexRow(
        project_id=search_repository.project_id,
        id=1312,
        type="entity",
        file_path="notes/script-only.md",
        title="Script only",
        content_stems="適者",
        content_snippet="適者",
        permalink="notes/script-only",
        created_at=now,
        updated_at=now,
    )
    await search_repository.index_item(row)

    results = await search_repository.search("missing 適者", allow_relaxed=True)
    total = await search_repository.count("missing 適者", allow_relaxed=True)

    assert results == []
    assert total == 0


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
async def test_postgres_mixed_search_ignores_empty_stopword_query(search_repository) -> None:
    if not isinstance(search_repository, PostgresSearchRepository):
        pytest.skip("PostgreSQL's English dictionary removes stopwords")

    now = datetime.now(timezone.utc)
    row = SearchIndexRow(
        project_id=search_repository.project_id,
        id=1304,
        type="entity",
        file_path="notes/stopword-and-script.md",
        title="Script match",
        content_stems="適者生存",
        content_snippet="適者生存",
        permalink="notes/stopword-and-script",
        created_at=now,
        updated_at=now,
    )
    await search_repository.index_item(row)

    results = await search_repository.search("the 適者生存")

    assert [result.id for result in results] == [1304]


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
