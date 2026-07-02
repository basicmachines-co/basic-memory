"""Portable file and project cleanup contracts for Basic Memory runtimes."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Self

from basic_memory.runtime.note_content import (
    RuntimeDeletedNoteEntityDeleteSource,
    RuntimeDeletedNoteReference,
    RuntimePendingNoteFileDelete,
    runtime_deleted_note_reference_for_entity,
)
from basic_memory.runtime.storage import (
    ProjectExternalId,
    ProjectId,
    RuntimeEntityId,
    RuntimeFileChecksum,
    RuntimeFilePath,
)

RUNTIME_FILE_SNAPSHOT_TIMESTAMP_MATCH_EPSILON_SECONDS = 0.001


class RuntimeDeleteStatus(StrEnum):
    """Normal outcomes for runtime cleanup jobs."""

    deleted = "deleted"
    missing = "missing"
    skipped = "skipped"


class RuntimeExternalFileDeleteAction(StrEnum):
    """Adapter work selected for an externally observed file delete."""

    missing_entity = "missing_entity"
    stale_object = "stale_object"
    delete_entity = "delete_entity"


@dataclass(frozen=True, slots=True)
class RuntimeExternalFileDeleteRequest:
    """Concrete adapter request for deleting one entity row by current file path."""

    entity_id: RuntimeEntityId
    file_path: RuntimeFilePath
    deleted_note: RuntimeDeletedNoteReference | None


@dataclass(frozen=True, slots=True)
class RuntimeExternalFileDeletePlan:
    """Pure decision for reconciling an externally observed file delete."""

    action: RuntimeExternalFileDeleteAction
    file_path: RuntimeFilePath
    reason: str
    entity_id: RuntimeEntityId | None = None
    deleted_note: RuntimeDeletedNoteReference | None = None

    @classmethod
    def missing_entity(cls, *, file_path: RuntimeFilePath) -> Self:
        return cls(
            action=RuntimeExternalFileDeleteAction.missing_entity,
            file_path=file_path,
            reason=f"entity already absent for {file_path}",
        )

    @classmethod
    def from_existing_entity(
        cls,
        entity: RuntimeDeletedNoteEntityDeleteSource,
        *,
        file_path: RuntimeFilePath,
        object_exists: bool,
    ) -> Self:
        if object_exists:
            return cls(
                action=RuntimeExternalFileDeleteAction.stale_object,
                file_path=file_path,
                entity_id=entity.id,
                reason=f"object exists after delete event: {file_path}",
            )

        return cls(
            action=RuntimeExternalFileDeleteAction.delete_entity,
            file_path=file_path,
            entity_id=entity.id,
            deleted_note=runtime_deleted_note_reference_for_entity(
                entity,
                file_path=file_path,
            ),
            reason=f"delete entity for externally deleted file: {file_path}",
        )

    @property
    def should_delete_entity(self) -> bool:
        return self.action == RuntimeExternalFileDeleteAction.delete_entity

    def require_delete_request(self) -> RuntimeExternalFileDeleteRequest:
        if not self.should_delete_entity or self.entity_id is None:
            raise RuntimeError(
                f"External file delete plan does not delete an entity: {self.reason}"
            )
        return RuntimeExternalFileDeleteRequest(
            entity_id=self.entity_id,
            file_path=self.file_path,
            deleted_note=self.deleted_note,
        )


@dataclass(frozen=True, slots=True)
class RuntimeProjectFileSnapshot:
    """Accepted materialized-file state captured before a project row disappears."""

    entity_id: RuntimeEntityId
    file_path: RuntimeFilePath
    file_checksum: RuntimeFileChecksum | None = None

    def to_pending_note_file_delete(self, *, project_id: ProjectId) -> RuntimePendingNoteFileDelete:
        """Return the note-file cleanup work represented by this project snapshot."""
        return RuntimePendingNoteFileDelete(
            project_id=project_id,
            entity_id=self.entity_id,
            file_path=self.file_path,
            file_checksum=self.file_checksum,
        )


@dataclass(frozen=True, slots=True)
class RuntimeDirectoryFileSnapshot:
    """Accepted materialized-file state captured before directory rows disappear."""

    entity_id: RuntimeEntityId
    file_path: RuntimeFilePath
    file_checksum: RuntimeFileChecksum | None
    last_modified_at: float | None = None
    size: int | None = None

    def to_pending_note_file_delete(self, *, project_id: ProjectId) -> RuntimePendingNoteFileDelete:
        """Return the note-file cleanup work represented by this accepted snapshot."""
        return RuntimePendingNoteFileDelete(
            project_id=project_id,
            entity_id=self.entity_id,
            file_path=self.file_path,
            file_checksum=self.file_checksum,
        )


def plan_directory_file_snapshot(
    *,
    entity_id: RuntimeEntityId,
    file_path: RuntimeFilePath,
    entity_checksum: RuntimeFileChecksum | None,
    entity_mtime: float | None,
    entity_size: int | None,
    note_file_checksum: RuntimeFileChecksum | None,
    note_file_updated_at: datetime | None,
) -> RuntimeDirectoryFileSnapshot:
    """Choose the freshest delete guard for one accepted directory-delete row."""
    note_file_updated_timestamp = (
        note_file_updated_at.timestamp() if note_file_updated_at is not None else None
    )
    accepted_last_modified_at = (
        note_file_updated_timestamp if note_file_updated_timestamp is not None else entity_mtime
    )
    accepted_checksum = (
        note_file_checksum
        if note_file_updated_timestamp is not None and note_file_checksum is not None
        else entity_checksum
    )
    timestamps_match = (
        entity_mtime is not None
        and accepted_last_modified_at is not None
        and abs(entity_mtime - accepted_last_modified_at)
        <= RUNTIME_FILE_SNAPSHOT_TIMESTAMP_MATCH_EPSILON_SECONDS
    )
    accepted_size = entity_size if entity_size is not None and timestamps_match else None

    return RuntimeDirectoryFileSnapshot(
        entity_id=entity_id,
        file_path=file_path,
        file_checksum=accepted_checksum,
        last_modified_at=accepted_last_modified_at,
        size=accepted_size,
    )


@dataclass(frozen=True, slots=True)
class RuntimeNoteFileDeleteJobRequest:
    """Queue-neutral request shape for deleting one materialized note file."""

    project_id: ProjectId
    entity_id: RuntimeEntityId
    file_path: RuntimeFilePath
    file_checksum: RuntimeFileChecksum | None = None

    def dedupe_key(self) -> str:
        """Return the logical note-file delete queue identity."""
        checksum_key = self.file_checksum or "unknown"
        return (
            f"delete-note-file:{self.project_id}:{self.entity_id}:{self.file_path}:{checksum_key}"
        )

    def routing_headers(self, headers: Mapping[str, str] | None = None) -> dict[str, str]:
        """Return queue routing headers for the note-file delete job."""
        routing_headers = dict(headers or {})
        routing_headers["project_id"] = str(self.project_id)
        return routing_headers


def plan_note_file_delete_job_request(
    file_delete: RuntimePendingNoteFileDelete,
) -> RuntimeNoteFileDeleteJobRequest:
    """Flatten accepted note cleanup work into a queue-neutral delete request."""
    return RuntimeNoteFileDeleteJobRequest(
        project_id=file_delete.project_id,
        entity_id=file_delete.entity_id,
        file_path=file_delete.file_path,
        file_checksum=file_delete.file_checksum,
    )


@dataclass(frozen=True, slots=True)
class RuntimeFileDeleteResult:
    """Summary of one guarded materialized-file cleanup."""

    entity_id: RuntimeEntityId
    file_path: RuntimeFilePath
    status: RuntimeDeleteStatus
    reason: str

    @classmethod
    def no_accepted_checksum(
        cls,
        *,
        entity_id: RuntimeEntityId,
        file_path: RuntimeFilePath,
    ) -> Self:
        return cls(
            entity_id=entity_id,
            file_path=file_path,
            status=RuntimeDeleteStatus.skipped,
            reason=f"no accepted file checksum for {file_path}",
        )

    @classmethod
    def already_absent(
        cls,
        *,
        entity_id: RuntimeEntityId,
        file_path: RuntimeFilePath,
    ) -> Self:
        return cls(
            entity_id=entity_id,
            file_path=file_path,
            status=RuntimeDeleteStatus.missing,
            reason=f"file already absent: {file_path}",
        )

    @classmethod
    def changed_before_delete(
        cls,
        *,
        entity_id: RuntimeEntityId,
        file_path: RuntimeFilePath,
    ) -> Self:
        return cls(
            entity_id=entity_id,
            file_path=file_path,
            status=RuntimeDeleteStatus.skipped,
            reason=f"file changed before delete: {file_path}",
        )

    @classmethod
    def deleted(
        cls,
        *,
        entity_id: RuntimeEntityId,
        file_path: RuntimeFilePath,
    ) -> Self:
        return cls(
            entity_id=entity_id,
            file_path=file_path,
            status=RuntimeDeleteStatus.deleted,
            reason=f"file deleted: {file_path}",
        )


@dataclass(frozen=True, slots=True)
class RuntimeNoteFileDeletePlan:
    """Pure cleanup decision before a runtime adapter deletes a materialized file."""

    result: RuntimeFileDeleteResult
    actual_checksum: RuntimeFileChecksum | None

    @property
    def should_delete_file(self) -> bool:
        """Return whether the adapter may perform the storage delete."""
        return self.result.status == RuntimeDeleteStatus.deleted


def plan_note_file_delete_cleanup(
    *,
    entity_id: RuntimeEntityId,
    file_path: RuntimeFilePath,
    accepted_checksum: RuntimeFileChecksum | None,
    actual_checksum: RuntimeFileChecksum | None,
) -> RuntimeNoteFileDeletePlan:
    """Select the safe cleanup outcome for one materialized note file."""
    if accepted_checksum is None:
        return RuntimeNoteFileDeletePlan(
            result=RuntimeFileDeleteResult.no_accepted_checksum(
                entity_id=entity_id,
                file_path=file_path,
            ),
            actual_checksum=actual_checksum,
        )

    if actual_checksum is None:
        return RuntimeNoteFileDeletePlan(
            result=RuntimeFileDeleteResult.already_absent(
                entity_id=entity_id,
                file_path=file_path,
            ),
            actual_checksum=actual_checksum,
        )

    if actual_checksum != accepted_checksum:
        return RuntimeNoteFileDeletePlan(
            result=RuntimeFileDeleteResult.changed_before_delete(
                entity_id=entity_id,
                file_path=file_path,
            ),
            actual_checksum=actual_checksum,
        )

    return RuntimeNoteFileDeletePlan(
        result=RuntimeFileDeleteResult.deleted(
            entity_id=entity_id,
            file_path=file_path,
        ),
        actual_checksum=actual_checksum,
    )


@dataclass(frozen=True, slots=True)
class RuntimeProjectDeleteResult:
    """Summary of one project cleanup run."""

    project_id: ProjectId
    project_external_id: ProjectExternalId
    status: RuntimeDeleteStatus
    deleted_project: bool
    deleted_files: int
    skipped_files: int
    missing_files: int
    reason: str

    @classmethod
    def from_file_results(
        cls,
        *,
        project_id: ProjectId,
        project_external_id: ProjectExternalId,
        status: RuntimeDeleteStatus,
        deleted_project: bool,
        file_results: list[RuntimeFileDeleteResult],
        reason: str,
    ) -> "RuntimeProjectDeleteResult":
        """Build aggregate project cleanup counters from guarded file deletes."""
        return cls(
            project_id=project_id,
            project_external_id=project_external_id,
            status=status,
            deleted_project=deleted_project,
            deleted_files=sum(
                1 for result in file_results if result.status == RuntimeDeleteStatus.deleted
            ),
            skipped_files=sum(
                1 for result in file_results if result.status == RuntimeDeleteStatus.skipped
            ),
            missing_files=sum(
                1 for result in file_results if result.status == RuntimeDeleteStatus.missing
            ),
            reason=reason,
        )
