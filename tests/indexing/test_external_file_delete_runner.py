"""Tests for portable external file-delete reconciliation."""

from dataclasses import dataclass

import pytest

from basic_memory.indexing.external_file_delete_runner import (
    ExternalFileDeleteResult,
    run_external_file_delete,
)
from basic_memory.runtime import RuntimeExternalFileDeleteAction


@dataclass(frozen=True, slots=True)
class FakeDeletedEntity:
    id: int
    external_id: str
    title: str
    permalink: str


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
