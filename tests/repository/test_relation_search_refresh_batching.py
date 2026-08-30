"""Regression coverage for bounded relation-search refresh cleanup."""

from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from basic_memory.repository import relation_repository as relation_repository_module
from basic_memory.repository.relation_repository import RelationRepository


def _expanded_refresh_id_batches(session: AsyncMock) -> list[tuple[int, ...]]:
    """Return the refresh IDs bound by each recorded DELETE statement."""
    batches: list[tuple[int, ...]] = []
    for call in session.execute.await_args_list:
        statement = call.args[0]
        compiled = statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"render_postcompile": True},
        )
        batches.append(
            tuple(
                value
                for parameter, value in compiled.params.items()
                if parameter.startswith("id_1_")
            )
        )
    return batches


@pytest.mark.asyncio
async def test_clear_pending_search_refreshes_batches_delete_parameters(monkeypatch) -> None:
    """A resolver backlog is retired without one unbounded asyncpg parameter list."""
    monkeypatch.setattr(
        relation_repository_module,
        "RELATION_SEARCH_REFRESH_DELETE_STATEMENT_SIZE",
        2,
    )
    session = AsyncMock(spec=AsyncSession)
    repository = RelationRepository(project_id=7)

    await repository.clear_pending_search_refreshes(session, [10, 11, 12, 13, 14])

    assert _expanded_refresh_id_batches(session) == [(10, 11), (12, 13), (14,)]


@pytest.mark.asyncio
async def test_generation_refresh_completion_batches_delete_parameters(monkeypatch) -> None:
    """Generation-fenced cleanup applies the same bound without losing its fence."""
    monkeypatch.setattr(
        relation_repository_module,
        "RELATION_SEARCH_REFRESH_DELETE_STATEMENT_SIZE",
        2,
    )
    session = AsyncMock(spec=AsyncSession)
    session.scalar.return_value = 1
    repository = RelationRepository(project_id=7)

    is_current = await repository.complete_search_refresh_for_generation(
        session,
        entity_id=23,
        generation=5,
        refresh_ids=[10, 11, 12, 13, 14],
    )

    assert is_current is True
    assert _expanded_refresh_id_batches(session) == [(10, 11), (12, 13), (14,)]
    session.add.assert_not_called()
