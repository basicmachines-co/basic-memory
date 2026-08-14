"""PostgreSQL coverage for project-index deletion versus note materialization."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any, override

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from basic_memory import db
import basic_memory.indexing.project_index_maintenance as project_index_maintenance_module
from basic_memory.indexing.note_materialization_runner import (
    NoteMaterializationSessionLock,
    RepositoryNoteMaterializationPreflight,
)
from basic_memory.indexing.project_index_maintenance import (
    ProjectIndexDeleteBatch,
    RepositoryProjectIndexMaintenanceStore,
)
from basic_memory.models import Entity, NoteContent, Project
from basic_memory.repository.note_content_repository import NoteContentRepository
from basic_memory.runtime.note_content import (
    RuntimeNoteMaterializationJobRequest,
    RuntimeNoteMaterializationStatus,
)


@dataclass(slots=True)
class BlockingAbsentPathVerifier:
    """Hold the delete between its DB candidate snapshot and live absence verdict."""

    entered: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)

    async def confirm_deleted_paths(self, paths: Sequence[str]) -> frozenset[str]:
        self.entered.set()
        await self.release.wait()
        return frozenset(paths)


@dataclass(slots=True)
class StartedPreflightLock(NoteMaterializationSessionLock):
    """Expose that materialization reached its transaction before the guarded claim."""

    started: asyncio.Event = field(default_factory=asyncio.Event)

    @override
    async def lock_note_materialization(
        self,
        session: AsyncSession,
        *,
        project_id: int,
        entity_id: int,
    ) -> None:
        del session, project_id, entity_id
        self.started.set()


async def create_synced_note(
    session_maker,
    *,
    project_id: int,
    file_path: str,
) -> tuple[int, RuntimeNoteMaterializationJobRequest]:
    """Create the fully synchronized state that is eligible for external deletion."""
    db_checksum = "accepted-checksum"
    async with db.scoped_session(session_maker) as session:
        entity = Entity(
            project_id=project_id,
            title="Delete race",
            note_type="note",
            content_type="text/markdown",
            file_path=file_path,
            checksum=db_checksum,
        )
        session.add(entity)
        await session.flush()
        entity_id = entity.id
        await NoteContentRepository(project_id=project_id).create(
            session,
            NoteContent(
                entity_id=entity_id,
                markdown_content="# Delete race\n",
                db_version=1,
                db_checksum=db_checksum,
                file_version=1,
                file_checksum=db_checksum,
                file_write_status="synced",
            ),
        )

    return entity_id, RuntimeNoteMaterializationJobRequest(
        project_id=project_id,
        entity_id=entity_id,
        db_version=1,
        db_checksum=db_checksum,
        source="api",
    )


@pytest.mark.asyncio
async def test_materialization_claim_wins_before_project_index_delete_revalidation(
    engine_factory,
    test_project: Project,
) -> None:
    """A claim that commits during the storage probe changes lineage and survives."""
    engine, session_maker = engine_factory
    if engine.dialect.name != "postgresql":
        pytest.skip("row-lock revalidation requires PostgreSQL")

    file_path = "notes/materialization-wins.md"
    entity_id, request = await create_synced_note(
        session_maker,
        project_id=test_project.id,
        file_path=file_path,
    )
    verifier = BlockingAbsentPathVerifier()
    store = RepositoryProjectIndexMaintenanceStore(
        session_maker=session_maker,
        project_id=test_project.id,
        delete_path_verifier=verifier,
    )

    delete_task = asyncio.create_task(
        store.apply_project_index_delete_batch(
            ProjectIndexDeleteBatch(completed_batches=1, paths=(file_path,))
        )
    )
    await asyncio.wait_for(verifier.entered.wait(), timeout=2)

    preflight_result = await asyncio.wait_for(
        RepositoryNoteMaterializationPreflight(
            session_maker=session_maker
        ).prepare_note_materialization(request),
        timeout=2,
    )
    verifier.release.set()
    delete_result = await asyncio.wait_for(delete_task, timeout=2)

    async with session_maker() as verification_session:
        entity = await verification_session.get(Entity, entity_id)
        note_content = await verification_session.get(NoteContent, entity_id)

    assert preflight_result.prepared_write is not None
    assert delete_result.deleted_entities == 0
    assert delete_result.skipped_paths == (file_path,)
    assert entity is not None
    assert note_content is not None
    assert note_content.file_write_status == "writing"


@pytest.mark.asyncio
async def test_project_index_delete_wins_before_materialization_claim(
    engine_factory,
    test_project: Project,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A claim blocked behind deletion returns missing instead of StaleDataError."""
    engine, session_maker = engine_factory
    if engine.dialect.name != "postgresql":
        pytest.skip("row-lock revalidation requires PostgreSQL")

    file_path = "notes/delete-wins.md"
    entity_id, request = await create_synced_note(
        session_maker,
        project_id=test_project.id,
        file_path=file_path,
    )
    delete_locked = asyncio.Event()
    release_delete = asyncio.Event()
    original_lock = project_index_maintenance_module.lock_note_content_before_entity_mutation

    async def pause_after_delete_lock(
        session: AsyncSession,
        *,
        project_id: int,
        entity_ids,
    ) -> None:
        await original_lock(
            session,
            project_id=project_id,
            entity_ids=entity_ids,
        )
        if not delete_locked.is_set():
            delete_locked.set()
            await release_delete.wait()

    monkeypatch.setattr(
        project_index_maintenance_module,
        "lock_note_content_before_entity_mutation",
        pause_after_delete_lock,
    )
    store = RepositoryProjectIndexMaintenanceStore(
        session_maker=session_maker,
        project_id=test_project.id,
        delete_path_verifier=project_index_maintenance_module.TrustPlannedProjectIndexDeleteVerifier(),
    )
    preflight_lock = StartedPreflightLock()
    preflight = RepositoryNoteMaterializationPreflight(
        session_maker=session_maker,
        session_lock=preflight_lock,
    )

    delete_task: asyncio.Task[Any] | None = None
    preflight_task: asyncio.Task[Any] | None = None
    try:
        delete_task = asyncio.create_task(
            store.apply_project_index_delete_batch(
                ProjectIndexDeleteBatch(completed_batches=1, paths=(file_path,))
            )
        )
        await asyncio.wait_for(delete_locked.wait(), timeout=2)

        preflight_task = asyncio.create_task(preflight.prepare_note_materialization(request))
        await asyncio.wait_for(preflight_lock.started.wait(), timeout=2)
        await asyncio.sleep(0.1)
        assert not preflight_task.done()

        release_delete.set()
        delete_result = await asyncio.wait_for(delete_task, timeout=2)
        preflight_result = await asyncio.wait_for(preflight_task, timeout=2)
    finally:
        release_delete.set()
        for task in (delete_task, preflight_task):
            if task is not None and not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

    async with session_maker() as verification_session:
        entity = await verification_session.get(Entity, entity_id)
        note_content = await verification_session.get(NoteContent, entity_id)

    assert delete_result.deleted_entities == 1
    assert preflight_result.terminal_result is not None
    assert preflight_result.terminal_result.status is RuntimeNoteMaterializationStatus.missing
    assert entity is None
    assert note_content is None
