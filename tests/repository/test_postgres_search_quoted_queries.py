"""PostgreSQL regressions for quoted user search syntax."""

import asyncio
from datetime import datetime, timezone

import pytest

from basic_memory.repository.postgres_search_repository import PostgresSearchRepository
from basic_memory.repository.search_index_row import SearchIndexRow

pytestmark = pytest.mark.postgres


@pytest.fixture(autouse=True)
def _require_postgres_backend(db_backend: str) -> None:
    if db_backend != "postgres":
        pytest.skip("Quoted-query regressions require PostgreSQL")


@pytest.mark.asyncio
async def test_quoted_or_phrases_complete_without_tsquery_recovery(
    session_maker,
    test_project,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The production query shape should return normally, not enter a 30-second retry path."""
    repository = PostgresSearchRepository(session_maker, project_id=test_project.id)
    now = datetime.now(timezone.utc)
    await repository.bulk_index_items(
        [
            SearchIndexRow(
                project_id=test_project.id,
                id=91,
                title="Incident response",
                content_stems="incident response runbook",
                content_snippet="The incident response runbook is current.",
                permalink="operations/incident-response",
                file_path="operations/incident-response.md",
                type="entity",
                metadata={"note_type": "note"},
                created_at=now,
                updated_at=now,
            ),
            SearchIndexRow(
                project_id=test_project.id,
                id=92,
                title="Database recovery",
                content_stems="database recovery procedure",
                content_snippet="The database recovery procedure is tested.",
                permalink="operations/database-recovery",
                file_path="operations/database-recovery.md",
                type="entity",
                metadata={"note_type": "note"},
                created_at=now,
                updated_at=now,
            ),
        ]
    )

    syntax_errors: list[Exception] = []
    real_is_syntax_error = repository._is_tsquery_syntax_error

    def record_syntax_error(exception: Exception) -> bool:
        is_syntax_error = real_is_syntax_error(exception)
        if is_syntax_error:
            syntax_errors.append(exception)
        return is_syntax_error

    monkeypatch.setattr(repository, "_is_tsquery_syntax_error", record_syntax_error)

    query = '"incident response" OR "database recovery"'
    async with asyncio.timeout(2):
        results = await repository.search(search_text=query)
        total = await repository.count(search_text=query)
        adjacent_results = await repository.search(search_text='incident"response runbook"')
        grouped_adjacent_results = await repository.search(search_text='"incident"(response)')
        unmatched_results = await repository.search(search_text='"incident response OR database')

    assert {result.permalink for result in results} == {
        "operations/incident-response",
        "operations/database-recovery",
    }
    assert total == 2
    assert [result.permalink for result in adjacent_results] == ["operations/incident-response"]
    assert [result.permalink for result in grouped_adjacent_results] == [
        "operations/incident-response"
    ]
    assert unmatched_results == []
    assert syntax_errors == []
