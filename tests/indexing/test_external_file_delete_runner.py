"""Tests for portable external file-delete reconciliation."""

from dataclasses import dataclass
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import basic_memory.indexing.external_file_delete_runner as external_file_delete_runner
from basic_memory.indexing.external_file_delete_runner import (
    ExternalFileDeleteResult,
    RepositoryExternalFileDeleteEntities,
    run_external_file_delete,
)
from basic_memory.runtime import RUNTIME_MARKDOWN_CONTENT_TYPE, RuntimeExternalFileDeleteAction


@dataclass(frozen=True, slots=True)
class FakeDeletedEntity:
    id: int
    external_id: str
    title: str
    permalink: str | None
    content_type: str = RUNTIME_MARKDOWN_CONTENT_TYPE


class FakeExternalFileEntities:
    def __init__(
        self,
        entity: FakeDeletedEntity | None,
        *,
        delete_succeeds: bool = True,
    ) -> None:
        self.entity = entity
        self.delete_succeeds = delete_succeeds
        self.find_calls: list[str] = []
        self.delete_calls: list[tuple[int, str]] = []

    async def find_entity_by_file_path(self, file_path: str) -> FakeDeletedEntity | None:
        self.find_calls.append(file_path)
        return self.entity

    async def delete_entity_if_file_path_matches(
        self,
        *,
        entity_id: int,
        file_path: str,
    ) -> bool:
        self.delete_calls.append((entity_id, file_path))
        return self.delete_succeeds


class FakeExternalFileObjects:
    def __init__(self, *, exists: bool) -> None:
        self.exists = exists
        self.exists_calls: list[str] = []

    async def file_exists(self, file_path: str) -> bool:
        self.exists_calls.append(file_path)
        return self.exists


class FakeEntityRepository:
    def __init__(self, entity: FakeDeletedEntity | None) -> None:
        self.entity = entity
        self.get_calls: list[tuple[object, str]] = []
        self.delete_calls: list[tuple[object, dict[str, object]]] = []

    async def get_by_file_path(
        self,
        session: AsyncSession,
        file_path: str,
        *,
        load_relations: bool = True,
    ) -> FakeDeletedEntity | None:
        self.get_calls.append((session, file_path))
        return self.entity

    async def delete_by_fields(
        self,
        session: AsyncSession,
        **filters: object,
    ) -> bool:
        self.delete_calls.append((session, filters))
        return True


class FakeScopedSession:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def __aenter__(self) -> AsyncSession:
        return self.session

    async def __aexit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        return None


@pytest.mark.asyncio
async def test_repository_external_file_delete_entities_use_scoped_sessions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = cast(AsyncSession, object())
    session_maker = cast(async_sessionmaker[AsyncSession], object())
    scoped_session_calls: list[async_sessionmaker[AsyncSession]] = []

    def fake_scoped_session(
        scoped_session_maker: async_sessionmaker[AsyncSession],
    ) -> FakeScopedSession:
        scoped_session_calls.append(scoped_session_maker)
        return FakeScopedSession(session)

    monkeypatch.setattr(
        external_file_delete_runner.db,
        "scoped_session",
        fake_scoped_session,
    )

    entity = FakeDeletedEntity(
        id=42,
        external_id="note-42",
        title="Deleted note",
        permalink="deleted-note",
    )
    repository = FakeEntityRepository(entity)
    adapter = RepositoryExternalFileDeleteEntities(
        session_maker=session_maker,
        entity_repository=repository,
    )

    found = await adapter.find_entity_by_file_path("notes/deleted.md")
    deleted = await adapter.delete_entity_if_file_path_matches(
        entity_id=42,
        file_path="notes/deleted.md",
    )

    assert found == entity
    assert deleted is True
    assert scoped_session_calls == [session_maker, session_maker]
    assert repository.get_calls == [(session, "notes/deleted.md")]
    assert repository.delete_calls == [(session, {"id": 42, "file_path": "notes/deleted.md"})]


@pytest.mark.asyncio
async def test_run_external_file_delete_deletes_matching_entity() -> None:
    entity = FakeDeletedEntity(
        id=42,
        external_id="note-42",
        title="Deleted note",
        permalink="deleted-note",
    )
    entities = FakeExternalFileEntities(entity)
    objects = FakeExternalFileObjects(exists=False)

    result = await run_external_file_delete(
        "notes/deleted.md",
        entities=entities,
        objects=objects,
    )

    assert result == ExternalFileDeleteResult(
        plan=result.plan,
        entity_deleted=True,
        deleted_entity=entity,
    )
    assert result.plan.action == RuntimeExternalFileDeleteAction.delete_entity
    assert result.deleted_note is not None
    assert result.deleted_note.external_id == "note-42"
    assert result.deleted_note.title == "Deleted note"
    assert result.deleted_note.permalink == "deleted-note"
    assert entities.find_calls == ["notes/deleted.md"]
    assert objects.exists_calls == ["notes/deleted.md"]
    assert entities.delete_calls == [(42, "notes/deleted.md")]


@pytest.mark.asyncio
async def test_run_external_file_delete_deletes_regular_file_entity_without_note_metadata() -> None:
    entity = FakeDeletedEntity(
        id=55,
        external_id="file-55",
        title="report.pdf",
        permalink=None,
        content_type="application/pdf",
    )
    entities = FakeExternalFileEntities(entity)
    objects = FakeExternalFileObjects(exists=False)

    result = await run_external_file_delete(
        "files/report.pdf",
        entities=entities,
        objects=objects,
    )

    assert result.plan.action == RuntimeExternalFileDeleteAction.delete_entity
    assert result.entity_deleted is True
    assert result.deleted_entity == entity
    assert result.deleted_note is None
    assert entities.find_calls == ["files/report.pdf"]
    assert objects.exists_calls == ["files/report.pdf"]
    assert entities.delete_calls == [(55, "files/report.pdf")]


@pytest.mark.asyncio
async def test_run_external_file_delete_skips_missing_entity_without_storage_lookup() -> None:
    entities = FakeExternalFileEntities(None)
    objects = FakeExternalFileObjects(exists=False)

    result = await run_external_file_delete(
        "notes/missing.md",
        entities=entities,
        objects=objects,
    )

    assert result.plan.action == RuntimeExternalFileDeleteAction.missing_entity
    assert result.entity_deleted is False
    assert result.deleted_note is None
    assert objects.exists_calls == []
    assert entities.delete_calls == []


@pytest.mark.asyncio
async def test_run_external_file_delete_skips_stale_delete_when_object_exists() -> None:
    entity = FakeDeletedEntity(
        id=7,
        external_id="note-7",
        title="Recreated note",
        permalink="recreated-note",
    )
    entities = FakeExternalFileEntities(entity)
    objects = FakeExternalFileObjects(exists=True)

    result = await run_external_file_delete(
        "notes/recreated.md",
        entities=entities,
        objects=objects,
    )

    assert result.plan.action == RuntimeExternalFileDeleteAction.stale_object
    assert result.entity_deleted is False
    assert result.deleted_note is None
    assert entities.delete_calls == []


@pytest.mark.asyncio
async def test_run_external_file_delete_skips_when_conditional_delete_misses() -> None:
    entity = FakeDeletedEntity(
        id=99,
        external_id="note-99",
        title="Moved note",
        permalink="moved-note",
    )
    entities = FakeExternalFileEntities(entity, delete_succeeds=False)
    objects = FakeExternalFileObjects(exists=False)

    result = await run_external_file_delete(
        "notes/old-path.md",
        entities=entities,
        objects=objects,
    )

    assert result.plan.action == RuntimeExternalFileDeleteAction.delete_entity
    assert result.entity_deleted is False
    assert result.deleted_note is None
    assert result.deleted_entity is None
    assert entities.delete_calls == [(99, "notes/old-path.md")]
