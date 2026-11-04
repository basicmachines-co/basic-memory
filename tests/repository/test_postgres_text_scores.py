"""Postgres text scores: prefix ranking and title-only search (#1981, #1983)."""

from datetime import datetime, timezone

import pytest

from basic_memory.repository.postgres_search_repository import PostgresSearchRepository
from basic_memory.repository.search_index_row import SearchIndexRow

pytestmark = pytest.mark.postgres

# The reporter's notes: four "project status" pairs, plus one longer word in M and N.
PAIRS = (
    "Project status first. Then project status second. Project status third. "
    "And project status fourth."
)
NOTES = {
    "notes/b": PAIRS,
    "notes/m": f"{PAIRS}\nOld file: project-status-monthly-report.md",
    "notes/n": f"{PAIRS}\nSee the project-tracker skill.",
}


@pytest.fixture(autouse=True)
def _require_postgres_backend(db_backend):
    if db_backend != "postgres":
        pytest.skip("Postgres text scoring tests require BASIC_MEMORY_TEST_POSTGRES=1")


def _row(project_id: int, row_id: int, permalink: str, title: str, content: str) -> SearchIndexRow:
    now = datetime.now(timezone.utc)
    return SearchIndexRow(
        project_id=project_id,
        id=row_id,
        title=title,
        content_stems=content,
        content_snippet=content,
        permalink=permalink,
        file_path=f"{permalink}.md",
        type="entity",
        entity_id=row_id,
        metadata={"note_type": "note"},
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_a_longer_word_does_not_collapse_a_prefix_query_score(session_maker, test_project):
    """A note naming project-tracker keeps the score of its "project status" pairs.

    ts_rank compares each prefix operand only at the last matching lexeme, so a
    longer word starting with "project" used to cut M and N to a quarter of B.
    """
    repo = PostgresSearchRepository(session_maker, project_id=test_project.id)
    for row_id, (permalink, content) in enumerate(NOTES.items(), start=1):
        # Titles stay clear of the query words, as in the report.
        await repo.index_item(_row(test_project.id, row_id, permalink, f"Note {row_id}", content))

    results = await repo.search(search_text="project status")
    scores = {result.permalink: result.score for result in results}

    assert set(scores) == set(NOTES)
    baseline = scores["notes/b"]
    assert baseline is not None and baseline > 0
    for permalink in ("notes/m", "notes/n"):
        score = scores[permalink]
        assert score is not None and score >= 0.8 * baseline, scores


@pytest.mark.asyncio
async def test_a_partial_word_still_ranks_by_prefix(session_maker, test_project):
    repo = PostgresSearchRepository(session_maker, project_id=test_project.id)
    await repo.index_item(_row(test_project.id, 1, "notes/b", "Note 1", PAIRS))

    results = await repo.search(search_text="proj stat")

    assert [result.permalink for result in results] == ["notes/b"]
    assert results[0].score is not None and results[0].score > 0


@pytest.mark.asyncio
async def test_title_search_scores_every_hit(session_maker, test_project):
    """Title-only search reports a positive score, exact title first (#1983)."""
    repo = PostgresSearchRepository(session_maker, project_id=test_project.id)
    await repo.index_item(_row(test_project.id, 1, "notes/smoke", "Core 1.0 smoke", "one"))
    await repo.index_item(
        _row(test_project.id, 2, "notes/smoke-target", "Core 1.0 smoke target", "two")
    )

    results = await repo.search(title="Core 1.0 smoke")

    assert [result.permalink for result in results] == ["notes/smoke", "notes/smoke-target"]
    assert all(result.score is not None and result.score > 0 for result in results), [
        result.score for result in results
    ]
