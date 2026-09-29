"""The DELETE route attributes its journal row to the resolved actor.

The resolver stands in for a hosted runtime that identified the caller. The local
API has no resolver, so its deletes must keep writing unattributed rows.
"""

import functools
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select

from basic_memory import db
from basic_memory.deps.services import get_note_content_mutation_service
from basic_memory.models import AcceptedProjectNoteChange, Project
from basic_memory.runtime.project_partition import RuntimeProjectNoteOperation
from basic_memory.services.note_content_writes import (
    NoteContentMutationActorContext,
    NoteContentMutationKind,
    NoteContentMutationService,
)

_PROFILE_ID = UUID("22222222-2222-4222-8222-222222222222")


@dataclass
class RecordingActorResolver:
    """Resolve every mutation to one identified actor and remember what it was asked."""

    calls: list[tuple[NoteContentMutationKind, NoteContentMutationActorContext]] = field(
        default_factory=list
    )

    def resolve_mutation_actor(
        self,
        *,
        mutation_kind: NoteContentMutationKind,
        requested: NoteContentMutationActorContext,
    ) -> NoteContentMutationActorContext:
        self.calls.append((mutation_kind, requested))
        return NoteContentMutationActorContext(
            user_profile_id=_PROFILE_ID,
            source=requested.source,
            actor_kind="user",
            actor_name="Ada",
        )


def install_actor_resolver(app: FastAPI, resolver: RecordingActorResolver) -> None:
    # FastAPI reads the override's parameters through __wrapped__, so the real
    # dependency's wiring still builds the service this override decorates.
    @functools.wraps(get_note_content_mutation_service)
    async def resolved_service(**dependencies: Any) -> NoteContentMutationService:
        service = await get_note_content_mutation_service(**dependencies)
        service.actor_resolver = resolver
        return service

    app.dependency_overrides[get_note_content_mutation_service] = resolved_service


@pytest.fixture
def actor_resolver(app: FastAPI) -> RecordingActorResolver:
    resolver = RecordingActorResolver()
    install_actor_resolver(app, resolver)
    return resolver


async def deleted_journal_rows(engine_factory, project: Project) -> list[AcceptedProjectNoteChange]:
    _, session_maker = engine_factory
    async with db.scoped_session(session_maker) as session:
        return list(
            (
                await session.scalars(
                    select(AcceptedProjectNoteChange)
                    .where(
                        AcceptedProjectNoteChange.project_id == project.id,
                        AcceptedProjectNoteChange.operation
                        == RuntimeProjectNoteOperation.deleted.value,
                    )
                    .order_by(AcceptedProjectNoteChange.partition_position)
                )
            ).all()
        )


async def _create_and_delete(client: AsyncClient, v2_project_url: str) -> None:
    created = await client.post(
        f"{v2_project_url}/knowledge/entities",
        json={"title": "Doomed", "directory": "notes", "content": "Short-lived"},
        params={"fast": False},
    )
    assert created.status_code == 202, created.text

    deleted = await client.delete(
        f"{v2_project_url}/knowledge/entities/{created.json()['external_id']}"
    )
    assert deleted.status_code == 202, deleted.text
    assert deleted.json()["deleted"] is True


@pytest.mark.asyncio
async def test_delete_route_records_resolved_actor(
    client: AsyncClient,
    v2_project_url: str,
    engine_factory,
    test_project: Project,
    actor_resolver: RecordingActorResolver,
) -> None:
    await _create_and_delete(client, v2_project_url)

    delete_calls = [call for call in actor_resolver.calls if call[0] == "delete"]
    assert delete_calls == [
        (
            "delete",
            NoteContentMutationActorContext(user_profile_id=None, source="api"),
        )
    ]

    [row] = await deleted_journal_rows(engine_factory, test_project)
    assert row.actor_user_profile_id == str(_PROFILE_ID)
    assert row.actor_kind == "user"
    assert row.actor_name == "Ada"
    assert row.source == "delete_note"


@pytest.mark.asyncio
async def test_delete_route_without_resolver_leaves_row_unattributed(
    client: AsyncClient,
    v2_project_url: str,
    engine_factory,
    test_project: Project,
) -> None:
    await _create_and_delete(client, v2_project_url)

    [row] = await deleted_journal_rows(engine_factory, test_project)
    assert row.actor_user_profile_id is None
    assert row.actor_kind is None
    assert row.actor_name is None
    assert row.source == "delete_note"
