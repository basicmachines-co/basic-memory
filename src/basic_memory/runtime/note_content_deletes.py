"""Portable accepted-note delete and previous-file cleanup contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Self

from basic_memory.runtime.storage import (
    NoteExternalId,
    ProjectId,
    RuntimeContentTypeSource,
    RuntimeEntityId,
    RuntimeFileChecksum,
    RuntimeFilePath,
    runtime_content_type_is_markdown,
)


class RuntimeDeletedNoteEntitySource(RuntimeContentTypeSource, Protocol):
    """Minimal deleted-note entity shape needed before row cleanup.

    The wide identity types are deliberate: downstream runtimes feed loosely
    typed entity projections through this seam, so the delete live-update
    identity is validated where the reference is built rather than trusted
    from the declared shape.
    """

    @property
    def external_id(self) -> object | None: ...

    @property
    def title(self) -> object | None: ...

    @property
    def permalink(self) -> object | None: ...


class RuntimeDeletedNoteEntityDeleteSource(RuntimeDeletedNoteEntitySource, Protocol):
    """Deleted-note entity shape needed for conditional row cleanup."""

    @property
    def id(self) -> RuntimeEntityId: ...


class RuntimeDeletedNoteEntityChecksumSource(Protocol):
    """Minimal deleted-note entity shape needed to guard file cleanup."""

    @property
    def checksum(self) -> RuntimeFileChecksum | None: ...


class RuntimeDeletedNoteFileDeleteEntitySource(
    RuntimeDeletedNoteEntityDeleteSource,
    RuntimeDeletedNoteEntityChecksumSource,
    Protocol,
):
    """Deleted-note entity shape needed to plan file cleanup after row delete."""

    @property
    def file_path(self) -> RuntimeFilePath: ...


class RuntimeDeletedNoteFileChecksumSource(Protocol):
    """Minimal note_content shape needed to guard file cleanup."""

    @property
    def file_checksum(self) -> RuntimeFileChecksum | None: ...


class RuntimeMaterializedNoteSource(Protocol):
    """Minimal note_content row shape needed to clean up a materialized file."""

    @property
    def file_checksum(self) -> RuntimeFileChecksum | None: ...


@dataclass(frozen=True, slots=True)
class RuntimeDeletedNoteReference:
    """Deleted note identity captured before removing its entity row."""

    external_id: NoteExternalId
    title: str
    permalink: str

    @classmethod
    def from_entity(
        cls,
        entity: RuntimeDeletedNoteEntitySource,
        *,
        file_path: RuntimeFilePath,
    ) -> Self:
        return cls(
            external_id=required_runtime_deleted_note_text(
                entity.external_id,
                field_name="external_id",
                file_path=file_path,
            ),
            title=required_runtime_deleted_note_text(
                entity.title,
                field_name="title",
                file_path=file_path,
            ),
            permalink=runtime_deleted_note_permalink(
                entity.permalink,
                file_path=file_path,
            ),
        )


@dataclass(frozen=True, slots=True)
class RuntimeDeletedNoteResponse:
    """Route-facing deleted-note response assembled from typed runtime identity."""

    deleted: bool
    external_id: NoteExternalId | None = None
    title: str | None = None
    permalink: str | None = None
    file_path: RuntimeFilePath | None = None
    file_delete_status: str | None = None

    @classmethod
    def missing(cls) -> Self:
        return cls(deleted=False)

    @classmethod
    def pending_file_delete(
        cls,
        *,
        entity: RuntimeDeletedNoteEntitySource,
        file_path: RuntimeFilePath,
    ) -> Self:
        deleted_note = RuntimeDeletedNoteReference.from_entity(entity, file_path=file_path)
        return cls(
            deleted=True,
            external_id=deleted_note.external_id,
            title=deleted_note.title,
            permalink=deleted_note.permalink,
            file_path=file_path,
            file_delete_status="pending",
        )

    def as_payload(self) -> dict[str, object]:
        """Serialize to the existing delete response payload shape."""
        if not self.deleted:
            return {"deleted": False}

        if self.external_id is None:
            raise RuntimeError("Deleted note response is missing external_id")
        if self.title is None:
            raise RuntimeError("Deleted note response is missing title")
        if self.permalink is None:
            raise RuntimeError("Deleted note response is missing permalink")
        if self.file_path is None:
            raise RuntimeError("Deleted note response is missing file_path")
        if self.file_delete_status is None:
            raise RuntimeError("Deleted note response is missing file_delete_status")

        return {
            "deleted": True,
            "external_id": self.external_id,
            "title": self.title,
            "permalink": self.permalink,
            "file_path": self.file_path,
            "file_delete_status": self.file_delete_status,
        }


def select_deleted_note_file_checksum(
    *,
    note_content: RuntimeDeletedNoteFileChecksumSource | None,
    entity: RuntimeDeletedNoteEntityChecksumSource,
) -> RuntimeFileChecksum | None:
    """Choose the best accepted file checksum to guard deleted-note cleanup."""
    if note_content is not None and note_content.file_checksum is not None:
        return note_content.file_checksum
    return entity.checksum


def required_runtime_deleted_note_text(
    value: object,
    *,
    field_name: str,
    file_path: RuntimeFilePath,
) -> str:
    """Return required deleted-note text for downstream live-update contracts."""
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"Deleted entity for {file_path} is missing {field_name}")
    return value.strip()


def runtime_deleted_note_permalink(
    value: object,
    *,
    file_path: RuntimeFilePath,
) -> str:
    """Return deleted-note permalink text, falling back to the file path."""
    if isinstance(value, str) and value.strip():
        return value.strip()

    fallback = str(file_path).strip()
    if not fallback:
        raise RuntimeError(f"Deleted entity for {file_path} is missing permalink")
    return fallback


def runtime_deleted_note_reference_for_entity(
    entity: RuntimeDeletedNoteEntitySource,
    *,
    file_path: RuntimeFilePath,
) -> RuntimeDeletedNoteReference | None:
    """Return deleted-note metadata for markdown entities, not regular file entities."""
    if not runtime_content_type_is_markdown(entity):
        return None
    return RuntimeDeletedNoteReference.from_entity(entity, file_path=file_path)


@dataclass(frozen=True, slots=True)
class RuntimePendingNoteFileDelete:
    """Delete job arguments captured before a note file changes ownership."""

    project_id: ProjectId
    entity_id: RuntimeEntityId
    file_path: RuntimeFilePath
    file_checksum: RuntimeFileChecksum | None = None
    # The note's live path after the move that scheduled this cleanup. Object
    # storage treats case-different keys as distinct; a local adapter re-checks
    # it against the physical filesystem before deleting because a case-only
    # rename can alias old and new onto the same inode.
    live_file_path: RuntimeFilePath | None = None


def plan_previous_note_file_delete(
    *,
    project_id: ProjectId,
    entity_id: RuntimeEntityId,
    existing_file_path: RuntimeFilePath | None,
    accepted_file_path: RuntimeFilePath,
    file_checksum: RuntimeFileChecksum | None,
) -> RuntimePendingNoteFileDelete | None:
    """Return old-file cleanup work when an accepted note move has materialized storage."""
    if existing_file_path is None or existing_file_path == accepted_file_path:
        return None
    if file_checksum is None:
        return None
    return RuntimePendingNoteFileDelete(
        project_id=project_id,
        entity_id=entity_id,
        file_path=existing_file_path,
        file_checksum=file_checksum,
        live_file_path=accepted_file_path,
    )


def plan_previous_materialized_note_file_delete(
    *,
    project_id: ProjectId,
    entity_id: RuntimeEntityId,
    existing_file_path: RuntimeFilePath | None,
    accepted_file_path: RuntimeFilePath,
    current_note_content: RuntimeMaterializedNoteSource | None,
) -> RuntimePendingNoteFileDelete | None:
    """Return old-file cleanup work when a moved note has materialized file state."""
    file_checksum = current_note_content.file_checksum if current_note_content is not None else None
    return plan_previous_note_file_delete(
        project_id=project_id,
        entity_id=entity_id,
        existing_file_path=existing_file_path,
        accepted_file_path=accepted_file_path,
        file_checksum=file_checksum,
    )
