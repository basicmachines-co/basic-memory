"""Portable orchestration for externally observed note-file deletes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from basic_memory.runtime import (
    RuntimeDeletedNoteEntityDeleteSource,
    RuntimeDeletedNoteReference,
    RuntimeEntityId,
    RuntimeExternalFileDeletePlan,
    RuntimeFilePath,
)


class ExternalFileDeleteEntities(Protocol):
    """Entity capability required to reconcile an externally deleted note file."""

    async def find_entity_by_file_path(
        self,
        file_path: RuntimeFilePath,
    ) -> RuntimeDeletedNoteEntityDeleteSource | None: ...

    async def delete_entity_if_file_path_matches(
        self,
        *,
        entity_id: RuntimeEntityId,
        file_path: RuntimeFilePath,
    ) -> bool: ...


class ExternalFileDeleteObjects(Protocol):
    """Storage capability required to detect stale delete notifications."""

    async def file_exists(self, file_path: RuntimeFilePath) -> bool: ...


@dataclass(frozen=True, slots=True)
class ExternalFileDeleteResult:
    """Result of reconciling one externally observed file delete."""

    plan: RuntimeExternalFileDeletePlan
    entity_deleted: bool = False
    deleted_entity: RuntimeDeletedNoteEntityDeleteSource | None = None

    @property
    def deleted_note(self) -> RuntimeDeletedNoteReference | None:
        """Return the note identity only after the entity row was deleted."""
        if not self.entity_deleted:
            return None
        return self.plan.deleted_note


async def run_external_file_delete(
    file_path: RuntimeFilePath,
    *,
    entities: ExternalFileDeleteEntities,
    objects: ExternalFileDeleteObjects,
) -> ExternalFileDeleteResult:
    """Reconcile database state after storage reports a note file was deleted."""
    entity = await entities.find_entity_by_file_path(file_path)
    if entity is None:
        return ExternalFileDeleteResult(
            plan=RuntimeExternalFileDeletePlan.missing_entity(file_path=file_path),
        )

    delete_plan = RuntimeExternalFileDeletePlan.from_existing_entity(
        entity,
        file_path=file_path,
        object_exists=await objects.file_exists(file_path),
    )
    if not delete_plan.should_delete_entity:
        return ExternalFileDeleteResult(plan=delete_plan)

    delete_request = delete_plan.require_delete_request()
    entity_deleted = await entities.delete_entity_if_file_path_matches(
        entity_id=delete_request.entity_id,
        file_path=delete_request.file_path,
    )
    if not entity_deleted:
        return ExternalFileDeleteResult(plan=delete_plan)

    return ExternalFileDeleteResult(
        plan=delete_plan,
        entity_deleted=True,
        deleted_entity=entity,
    )
