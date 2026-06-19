from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory.indexing.note_content_read_repair_runner import (
    NoteContentReadRepairTarget,
    apply_note_content_read_repair,
    prepare_note_content_read_repair,
)
from basic_memory.runtime import RuntimeNoteContentReadRepairStatus


@dataclass(frozen=True, slots=True)
class _Project:
    id: int
    path: str


@dataclass(frozen=True, slots=True)
class _Entity:
    id: int
    content_type: str
    file_path: str


@dataclass(frozen=True, slots=True)
class _NoteContent:
    markdown_content: str


class _ProjectRepository:
    def __init__(self, project: _Project | None) -> None:
        self.project = project
        self.external_ids: list[str] = []

    async def get_by_external_id(
        self,
        session: AsyncSession,
        external_id: str,
    ) -> _Project | None:
        assert session is not None
        self.external_ids.append(external_id)
        return self.project


class _EntityRepository:
    def __init__(self, entity: _Entity | None) -> None:
        self.entity = entity
        self.external_ids: list[str] = []

    async def get_by_external_id(
        self,
        session: AsyncSession,
        external_id: str,
    ) -> _Entity | None:
        assert session is not None
        self.external_ids.append(external_id)
        return self.entity


class _NoteContentRepository:
    def __init__(self, note_content: _NoteContent | None) -> None:
        self.note_content = note_content
        self.entity_ids: list[int] = []

    async def get_by_entity_id(
        self,
        session: AsyncSession,
        entity_id: int,
    ) -> _NoteContent | None:
        assert session is not None
        self.entity_ids.append(entity_id)
        return self.note_content


@pytest.mark.asyncio
async def test_prepare_note_content_read_repair_returns_storage_target_for_missing_row() -> None:
    session = cast(AsyncSession, object())
    project = _Project(id=7, path="/app/data/main")
    entity = _Entity(id=42, content_type="text/markdown", file_path="notes/repair.md")
    project_repository = _ProjectRepository(project)
    entity_repository = _EntityRepository(entity)
    note_content_repository = _NoteContentRepository(None)

    preflight = await prepare_note_content_read_repair(
        session,
        project_external_id="project-123",
        entity_external_id="note-456",
        project_repository_factory=lambda: project_repository,
        entity_repository_factory=lambda project_id: entity_repository,
        note_content_repository_factory=lambda project_id: note_content_repository,
    )

    assert preflight.status is RuntimeNoteContentReadRepairStatus.read_file
    assert preflight.should_read_file
    assert preflight.require_target() == NoteContentReadRepairTarget(
        project=project,
        entity=entity,
    )
    assert project_repository.external_ids == ["project-123"]
    assert entity_repository.external_ids == ["note-456"]
    assert note_content_repository.entity_ids == [42]


@pytest.mark.asyncio
async def test_prepare_note_content_read_repair_reports_existing_row_as_repaired() -> None:
    session = cast(AsyncSession, object())
    project = _Project(id=7, path="/app/data/main")
    entity = _Entity(id=42, content_type="text/markdown", file_path="notes/repair.md")
    note_content_repository = _NoteContentRepository(_NoteContent(markdown_content="# Present\n"))

    preflight = await prepare_note_content_read_repair(
        session,
        project_external_id="project-123",
        entity_external_id="note-456",
        project_repository_factory=lambda: _ProjectRepository(project),
        entity_repository_factory=lambda project_id: _EntityRepository(entity),
        note_content_repository_factory=lambda project_id: note_content_repository,
    )

    assert preflight.status is RuntimeNoteContentReadRepairStatus.already_present
    assert preflight.repaired
    assert not preflight.should_read_file
    with pytest.raises(RuntimeError, match="does not contain a target"):
        preflight.require_target()


@pytest.mark.asyncio
async def test_prepare_note_content_read_repair_skips_note_lookup_for_non_markdown() -> None:
    session = cast(AsyncSession, object())
    project = _Project(id=7, path="/app/data/main")
    entity = _Entity(id=42, content_type="image/png", file_path="images/diagram.png")

    def fail_note_content_repository(_project_id: int) -> _NoteContentRepository:
        raise AssertionError("non-markdown read repair should not check note_content")

    preflight = await prepare_note_content_read_repair(
        session,
        project_external_id="project-123",
        entity_external_id="note-456",
        project_repository_factory=lambda: _ProjectRepository(project),
        entity_repository_factory=lambda project_id: _EntityRepository(entity),
        note_content_repository_factory=fail_note_content_repository,
    )

    assert preflight.status is RuntimeNoteContentReadRepairStatus.entity_missing
    assert not preflight.repaired
    assert not preflight.should_read_file


@pytest.mark.asyncio
async def test_apply_note_content_read_repair_uses_project_reconciler() -> None:
    project = _Project(id=7, path="/app/data/main")
    entity = _Entity(id=42, content_type="text/markdown", file_path="notes/repair.md")
    target = NoteContentReadRepairTarget(project=project, entity=entity)
    session_maker = cast(async_sessionmaker[AsyncSession], object())
    observed_at = datetime(2026, 4, 13, 15, 0, tzinfo=UTC)
    calls: list[tuple[_Entity, str, datetime | None, str]] = []
    factory_calls: list[tuple[int, async_sessionmaker[AsyncSession]]] = []

    class FakeReconciler:
        async def reconcile(
            self,
            *,
            entity: _Entity,
            markdown_content: str,
            observed_at: datetime | None,
            source: str,
        ) -> None:
            calls.append((entity, markdown_content, observed_at, source))

    def fake_reconciler_factory(
        project_id: int,
        received_session_maker: async_sessionmaker[AsyncSession],
    ) -> FakeReconciler:
        factory_calls.append((project_id, received_session_maker))
        return FakeReconciler()

    await apply_note_content_read_repair(
        target,
        session_maker=session_maker,
        markdown_content="# Repaired\n",
        observed_at=observed_at,
        source="read_repair",
        reconciler_factory=fake_reconciler_factory,
    )

    assert factory_calls == [(7, session_maker)]
    assert calls == [(entity, "# Repaired\n", observed_at, "read_repair")]
