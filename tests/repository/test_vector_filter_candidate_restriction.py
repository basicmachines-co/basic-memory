"""A filtered vector search must not lose candidates to a scan window (#1431).

Vector and hybrid retrieval do not evaluate structured filters themselves. They build a
candidate set from embeddings and then ask an FTS-mode pass which of those candidates the
filter admits. While that pass was a plain capped page over the filter's *whole* match
set, any candidate whose row fell outside the page was read as disallowed -- a wrong
answer that reruns identically, not a derived-state race that a later write repairs.

The loss is not even arbitrary. A filter-only pass carries ``search_text=None``, so there
is no relevance signal in the ordering: PostgreSQL falls through to its
``search_index.id ASC`` tiebreak and keeps the earliest-indexed rows, and SQLite has no
tiebreak at all. Newer content is what disappears.

The fix pushes the candidate keys into the filter query, so the pass answers "which of
*these* rows does the filter admit" instead of "here is a page of everything it admits".

Every database test here runs against whichever backend the session is configured for --
SQLite by default, PostgreSQL under BASIC_MEMORY_TEST_POSTGRES=1 -- through the shared
``search_repository`` fixture, because a restriction that admitted different rows per
dialect would hand semantic search a different candidate set on each.
"""

from datetime import datetime, timezone
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import text

from basic_memory import db
from basic_memory.repository.embedding_provider import EmbeddingProvider
from basic_memory.repository.search_repository_base import (
    VECTOR_FILTER_SCAN_LIMIT,
    VECTOR_HYDRATION_BATCH_SIZE,
    candidate_key_restriction_condition,
)
from basic_memory.schemas.search import SearchItemType, SearchRetrievalMode

# The scope the admitted rows share, plus a sibling scope the filter must reject.
SCOPE = "notes"
REJECTED_SCOPE = "archive"
# One row past the old window: the smallest seed that makes the window bind at all.
FILLER_ROW_COUNT = VECTOR_FILTER_SCAN_LIMIT + 1
# Indexed after every filler and numbered above them, so it is last under both the
# PostgreSQL id tiebreak and SQLite's insertion order -- i.e. genuinely outside the page.
TARGET_ROW_ID = 10_000_000
TARGET_CONTENT = "the answer this query is looking for"
# Enough extra candidates to force the candidate list past the shared bind bound.
EXTRA_CANDIDATE_COUNT = VECTOR_HYDRATION_BATCH_SIZE + 50
REJECTED_ROW_IDS = range(20_000_000, 20_000_005)

_INSERT_SEARCH_ROW = """
    INSERT INTO search_index (
        id, title, content_stems, content_snippet, script_ngrams, permalink,
        file_path, type, metadata, from_id, to_id, relation_type,
        entity_id, category, created_at, updated_at, project_id
    ) VALUES (
        :id, :title, :content_stems, :content_snippet, :script_ngrams, :permalink,
        :file_path, :type, :metadata, :from_id, :to_id, :relation_type,
        :entity_id, :category, :created_at, :updated_at, :project_id
    )
"""

# Seeding 50k rows through one executemany holds a huge parameter list in the driver for
# no benefit; a few thousand at a time keeps both drivers comfortable.
_SEED_CHUNK_SIZE = 5000


def _row_params(
    project_id: int,
    row_id: int,
    name: str,
    content: str,
    *,
    scope: str = SCOPE,
    row_type: str = SearchItemType.ENTITY.value,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    return {
        "id": row_id,
        "title": name,
        "content_stems": content,
        "content_snippet": content,
        "script_ngrams": "",
        "permalink": f"{scope}/{row_type}/{name}",
        "file_path": f"{scope}/{name}.md",
        "type": row_type,
        "metadata": None,
        "from_id": None,
        "to_id": None,
        "relation_type": None,
        "entity_id": row_id,
        "category": None,
        "created_at": now,
        "updated_at": now,
        "project_id": project_id,
    }


async def _insert_rows(session_maker, rows: list[dict[str, Any]]) -> None:
    async with db.scoped_session(session_maker) as session:
        for start in range(0, len(rows), _SEED_CHUNK_SIZE):
            await session.execute(text(_INSERT_SEARCH_ROW), rows[start : start + _SEED_CHUNK_SIZE])
        await session.commit()


@pytest.fixture
async def over_window_project(search_repository, session_maker) -> None:
    """Seed a project whose filter match set genuinely exceeds the old scan window.

    Rows go straight into ``search_index`` rather than through entity indexing: the defect
    lives in the search-row intersection, and 50k parsed notes would cost minutes to prove
    the same thing. The target row is written last and numbered highest so it lands past
    the old page under either backend's ordering.
    """
    project_id = search_repository.project_id
    filler = [
        _row_params(project_id, row_id, f"filler-{row_id}", "filler note body")
        for row_id in range(FILLER_ROW_COUNT)
    ]
    rejected = [
        _row_params(
            project_id,
            row_id,
            f"outside-{row_id}",
            "out of scope body",
            scope=REJECTED_SCOPE,
        )
        for row_id in REJECTED_ROW_IDS
    ]
    target = [_row_params(project_id, TARGET_ROW_ID, "target", TARGET_CONTENT)]
    await _insert_rows(session_maker, filler + rejected + target)


def _fake_embedding_provider() -> EmbeddingProvider:
    return cast(
        EmbeddingProvider,
        type(
            "EP",
            (),
            {"embed_query": AsyncMock(return_value=[0.0] * 384), "dimensions": 384},
        )(),
    )


def _semantic_repo(search_repository):
    """Enable semantic retrieval on a repository the test config left keyword-only."""
    search_repository._semantic_enabled = True
    search_repository._semantic_min_similarity = 0.0
    search_repository._embedding_provider = _fake_embedding_provider()
    return search_repository


def _vector_chunks(row_ids: list[int]) -> list[dict[str, Any]]:
    """One vector hit per row, ranked in the order given."""
    return [
        {
            "chunk_key": f"{SearchItemType.ENTITY.value}:{row_id}:0",
            "best_similarity": 0.99 - index * 0.001,
            "chunk_text": TARGET_CONTENT,
            "entity_id": row_id,
        }
        for index, row_id in enumerate(row_ids)
    ]


@pytest.mark.slow
@pytest.mark.asyncio
async def test_filtered_vector_search_keeps_a_candidate_past_the_scan_window(
    search_repository, over_window_project
):
    """A highly similar row survives the filter even when it sorts past the old page."""
    repo = _semantic_repo(search_repository)

    with (
        patch.object(repo, "_ensure_vector_tables", new_callable=AsyncMock),
        patch.object(repo, "_prepare_vector_session", new_callable=AsyncMock),
        patch.object(
            repo,
            "_run_vector_query",
            new_callable=AsyncMock,
            return_value=_vector_chunks([TARGET_ROW_ID]),
        ),
    ):
        results = await repo.search(
            search_text="the answer",
            file_path_prefix=SCOPE,
            retrieval_mode=SearchRetrievalMode.VECTOR,
            limit=10,
        )

    assert [row.id for row in results] == [TARGET_ROW_ID]


@pytest.mark.slow
@pytest.mark.asyncio
async def test_filter_pass_answers_every_candidate_within_the_bind_bound(
    search_repository, over_window_project
):
    """A candidate pool past the bind bound is split, and each half still gets a verdict.

    Both engines cap bind parameters, so the restriction cannot be one unbounded ``IN``
    list. Splitting it must not turn into the same silent truncation it replaced: every
    admitted candidate comes back, every out-of-scope one is rejected, and no single
    statement carries more keys than the shared bound.
    """
    repo = _semantic_repo(search_repository)
    admitted = [TARGET_ROW_ID, *range(EXTRA_CANDIDATE_COUNT)]
    candidates = [*admitted, *REJECTED_ROW_IDS]
    assert len(candidates) > VECTOR_HYDRATION_BATCH_SIZE

    batched_key_counts: list[int] = []
    original_search = repo.search

    async def recording_search(*args, **kwargs):
        if kwargs.get("candidate_keys") is not None:
            batched_key_counts.append(len(kwargs["candidate_keys"]))
        return await original_search(*args, **kwargs)

    with (
        patch.object(repo, "_ensure_vector_tables", new_callable=AsyncMock),
        patch.object(repo, "_prepare_vector_session", new_callable=AsyncMock),
        patch.object(
            repo,
            "_run_vector_query",
            new_callable=AsyncMock,
            return_value=_vector_chunks(candidates),
        ),
        patch.object(repo, "search", recording_search),
    ):
        results = await repo._search_vector_only(
            search_text="the answer",
            permalink=None,
            permalink_match=None,
            title=None,
            note_types=None,
            after_date=None,
            search_item_types=None,
            categories=None,
            metadata_filters=None,
            file_path_prefix=SCOPE,
            temporal=None,
            limit=len(candidates),
            offset=0,
        )

    assert {row.id for row in results} == set(admitted)
    assert len(batched_key_counts) > 1
    assert max(batched_key_counts) <= VECTOR_HYDRATION_BATCH_SIZE
    assert sum(batched_key_counts) == len(candidates)


@pytest.mark.asyncio
async def test_restriction_separates_rows_that_share_an_id(search_repository, session_maker):
    """The restriction is by ``(type, id)``, since row types number independently."""
    project_id = search_repository.project_id
    await _insert_rows(
        session_maker,
        [
            _row_params(project_id, 7, "shared-id-entity", "body"),
            _row_params(
                project_id,
                7,
                "shared-id-observation",
                "body",
                row_type=SearchItemType.OBSERVATION.value,
            ),
        ],
    )

    rows = await search_repository.search(
        limit=10,
        candidate_keys=[(SearchItemType.OBSERVATION.value, 7)],
    )

    assert [(row.type, row.id) for row in rows] == [(SearchItemType.OBSERVATION.value, 7)]


@pytest.mark.asyncio
async def test_an_empty_candidate_set_admits_nothing(search_repository, session_maker):
    """No candidates is a real state -- a search whose every hit was already dropped."""
    await _insert_rows(
        session_maker,
        [_row_params(search_repository.project_id, 7, "only-row", "body")],
    )

    assert await search_repository.search(limit=10, candidate_keys=[]) == []


def test_restriction_groups_ids_under_one_predicate_per_row_type():
    """Type-scoped ``IN`` lists bind one parameter per key, not two."""
    params: dict[str, Any] = {}
    condition = candidate_key_restriction_condition(
        [("entity", 1), ("observation", 2), ("entity", 3), ("entity", 1)],
        params,
    )

    assert condition == (
        "((search_index.type = :candidate_type_0 "
        "AND search_index.id IN (:candidate_id_0_0, :candidate_id_0_1)) "
        "OR (search_index.type = :candidate_type_1 "
        "AND search_index.id IN (:candidate_id_1_0)))"
    )
    assert params == {
        "candidate_type_0": "entity",
        "candidate_id_0_0": 1,
        "candidate_id_0_1": 3,
        "candidate_type_1": "observation",
        "candidate_id_1_0": 2,
    }


def test_restriction_of_no_keys_is_false_not_vacuously_true():
    """An empty ``OR`` would collapse to a predicate that admits the whole project."""
    params: dict[str, Any] = {}

    assert candidate_key_restriction_condition([], params) == "1 = 0"
    assert params == {}
