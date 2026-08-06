"""Entity/Observation lock-order regressions for accepted writes and indexing."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from basic_memory import db
from basic_memory.markdown.schemas import (
    EntityFrontmatter,
    EntityMarkdown,
    Observation as MarkdownObservation,
)
from basic_memory.repository.observation_repository import (
    AcceptedObservationWrite,
    ObservationRepository,
)
from basic_memory.schemas import Entity as EntitySchema
from basic_memory.schemas.v2 import EntityResponseV2
from basic_memory.services.entity_service import EntityService


def _indexed_markdown(*, title: str, created: datetime) -> EntityMarkdown:
    """Build the file snapshot shared by the concurrent indexing regression."""
    return EntityMarkdown(
        frontmatter=EntityFrontmatter(metadata={"title": title, "type": "note"}),
        observations=[
            MarkdownObservation(content="Initial observation", category="fact"),
            MarkdownObservation(content="Accepted edit observation", category="fact"),
        ],
        relations=[],
        created=created,
        modified=datetime.now(tz=UTC),
    )


@pytest.mark.asyncio
async def test_indexing_replaces_entity_and_observations_on_both_database_backends(
    entity_service: EntityService,
) -> None:
    """The ordered write preserves file-first entity and graph semantics."""
    created = await entity_service.create_entity(
        EntitySchema(
            title="Lock order semantics",
            directory="notes",
            content="# Lock order semantics\n\n- [fact] Initial observation",
        )
    )

    markdown = _indexed_markdown(title="Updated lock order semantics", created=created.created_at)
    updated = await entity_service.update_entity_and_observations(
        Path(created.file_path),
        markdown,
        existing_entity=created,
    )

    assert updated.title == "Updated lock order semantics"
    async with db.scoped_session(entity_service.session_maker) as session:
        observations = await entity_service.observation_repository.find_by_entity(
            session,
            updated.id,
        )
    assert [observation.content for observation in observations] == [
        "Initial observation",
        "Accepted edit observation",
    ]


@pytest.mark.asyncio
async def test_accepted_edit_and_indexing_lock_entity_before_observations(
    client: AsyncClient,
    db_backend: str,
    entity_service: EntityService,
    monkeypatch: pytest.MonkeyPatch,
    v2_project_url: str,
) -> None:
    """Concurrent accepted and file-first writes must share Entity -> Observation order."""
    if db_backend != "postgres":
        pytest.skip("row-lock ordering requires PostgreSQL")

    create_response = await client.post(
        f"{v2_project_url}/knowledge/entities",
        json={
            "title": "Concurrent lock order",
            "directory": "notes",
            "content": "# Concurrent lock order\n\n- [fact] Initial observation",
        },
    )
    assert create_response.status_code == 202
    created = EntityResponseV2.model_validate(create_response.json())
    async with db.scoped_session(entity_service.session_maker) as session:
        existing_entity = await entity_service.repository.get_by_file_path(
            session,
            created.file_path,
            load_relations=False,
        )
    assert existing_entity is not None

    accepted_graph_reached = asyncio.Event()
    release_accepted_graph = asyncio.Event()
    indexing_observation_write_started = asyncio.Event()

    original_replace = ObservationRepository.replace_accepted_observations

    async def pause_accepted_graph_replace(
        repository: ObservationRepository,
        session: AsyncSession,
        entity_id: int,
        observations: Sequence[AcceptedObservationWrite],
    ) -> None:
        accepted_graph_reached.set()
        await release_accepted_graph.wait()
        await original_replace(repository, session, entity_id, observations)

    monkeypatch.setattr(
        ObservationRepository,
        "replace_accepted_observations",
        pause_accepted_graph_replace,
    )

    original_delete = entity_service.observation_repository.delete_by_fields

    async def observe_indexing_graph_replace(
        session: AsyncSession,
        **filters: Any,
    ) -> bool:
        indexing_observation_write_started.set()
        return await original_delete(session, **filters)

    monkeypatch.setattr(
        entity_service.observation_repository,
        "delete_by_fields",
        observe_indexing_graph_replace,
    )

    edit_task = asyncio.create_task(
        client.patch(
            f"{v2_project_url}/knowledge/entities/{created.external_id}",
            json={
                "operation": "append",
                "content": "\n\n- [fact] Accepted edit observation",
            },
        )
    )
    index_task: asyncio.Task[Any] | None = None
    try:
        await asyncio.wait_for(accepted_graph_reached.wait(), timeout=2)

        index_task = asyncio.create_task(
            entity_service.update_entity_and_observations(
                Path(created.file_path),
                _indexed_markdown(
                    title="Concurrent lock order",
                    created=created.created_at,
                ),
                existing_entity=existing_entity,
            )
        )

        # The accepted edit owns the Entity row while paused at its graph phase.
        # Indexing must wait on that row instead of touching Observations first.
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(indexing_observation_write_started.wait(), timeout=0.5)

        release_accepted_graph.set()
        edit_response, indexed = await asyncio.wait_for(
            asyncio.gather(edit_task, index_task),
            timeout=5,
        )
    finally:
        release_accepted_graph.set()
        for task in (edit_task, index_task):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(
            *(task for task in (edit_task, index_task) if task is not None),
            return_exceptions=True,
        )

    assert edit_response.status_code == 202
    assert indexed.id == created.id
    assert indexing_observation_write_started.is_set()
