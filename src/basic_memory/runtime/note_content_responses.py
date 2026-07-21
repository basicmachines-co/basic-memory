"""Portable accepted-note response values and serialization."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Protocol, Self

from basic_memory.runtime.storage import (
    ProjectId,
    RuntimeEntityId,
    RuntimeFileChecksum,
    RuntimeFilePath,
    RuntimeNoteChangeSource,
    RuntimeNoteContentChecksum,
    RuntimeNoteContentVersion,
)

NOTE_CONTENT_EXTERNAL_CHANGE_SYNC_ERROR = (
    "An external file change was detected before this note could be written. "
    "Refresh to review the latest content, then retry your write if you want it to win."
)

# Optional optimistic-concurrency precondition on note PUTs. Browser saves and
# the collaboration relay send the db_checksum they last synced; the accepted
# note update runner rejects the write with a structured 409 when the accepted
# row has advanced, so those clients rebase instead of clobbering the newer
# write. The "cloud" segment is part of the existing wire contract.
NOTE_CONTENT_BASE_CHECKSUM_HEADER = "x-bm-cloud-note-base-checksum"


class RuntimeAcceptedNoteEntitySource(Protocol):
    """Minimal accepted-note entity shape needed for response payloads."""

    @property
    def external_id(self) -> str: ...

    @property
    def id(self) -> RuntimeEntityId: ...

    @property
    def title(self) -> str: ...

    @property
    def note_type(self) -> str: ...

    @property
    def content_type(self) -> str: ...

    @property
    def permalink(self) -> str | None: ...

    @property
    def file_path(self) -> RuntimeFilePath: ...

    @property
    def entity_metadata(self) -> Mapping[str, object] | None: ...

    @property
    def created_at(self) -> datetime: ...

    @property
    def updated_at(self) -> datetime: ...

    @property
    def created_by(self) -> str | None: ...

    @property
    def last_updated_by(self) -> str | None: ...


class RuntimeAcceptedNoteWriteEntitySource(RuntimeAcceptedNoteEntitySource, Protocol):
    """Accepted-note entity shape needed to plan write follow-up work."""

    @property
    def project_id(self) -> ProjectId: ...


class RuntimeNoteContentStateSource(Protocol):
    """Minimal note_content row shape needed for accepted-note responses."""

    @property
    def markdown_content(self) -> str: ...

    @property
    def db_version(self) -> RuntimeNoteContentVersion: ...

    @property
    def db_checksum(self) -> RuntimeNoteContentChecksum: ...

    @property
    def file_version(self) -> int | None: ...

    @property
    def file_checksum(self) -> RuntimeFileChecksum | None: ...

    @property
    def file_write_status(self) -> str: ...

    @property
    def last_source(self) -> RuntimeNoteChangeSource | None: ...

    @property
    def file_updated_at(self) -> datetime | None: ...

    @property
    def last_materialization_error(self) -> str | None: ...


class RuntimeNoteContentResourceEntitySource(Protocol):
    """Minimal entity shape needed for note-content resource reads."""

    @property
    def content_type(self) -> str: ...


@dataclass(frozen=True, slots=True)
class RuntimeNoteContentState:
    """Accepted note_content row state before response serialization."""

    markdown_content: str
    db_version: RuntimeNoteContentVersion
    db_checksum: RuntimeNoteContentChecksum
    file_version: int | None
    file_checksum: RuntimeFileChecksum | None
    file_write_status: str
    last_source: RuntimeNoteChangeSource | None
    file_updated_at: datetime | None
    last_materialization_error: str | None

    @classmethod
    def from_source(cls, source: RuntimeNoteContentStateSource) -> Self:
        """Build typed runtime state from a loaded note_content source row."""
        return cls(
            markdown_content=source.markdown_content,
            db_version=source.db_version,
            db_checksum=source.db_checksum,
            file_version=source.file_version,
            file_checksum=source.file_checksum,
            file_write_status=source.file_write_status,
            last_source=source.last_source,
            file_updated_at=source.file_updated_at,
            last_materialization_error=source.last_materialization_error,
        )


@dataclass(frozen=True, slots=True)
class RuntimeNoteContentResource:
    """Resource response state for one accepted markdown note."""

    content: str
    content_type: str

    @classmethod
    def from_entity_and_content_state(
        cls,
        entity: RuntimeNoteContentResourceEntitySource,
        note_content: RuntimeNoteContentState,
    ) -> Self:
        """Build a resource response from typed note_content state."""
        return cls(content=note_content.markdown_content, content_type=entity.content_type)


@dataclass(frozen=True, slots=True)
class RuntimeAcceptedNoteResponse:
    """Accepted note response state before HTTP serialization."""

    external_id: str
    entity_id: RuntimeEntityId
    title: str
    note_type: str
    content_type: str
    permalink: str | None
    file_path: RuntimeFilePath
    markdown_content: str
    entity_metadata: Mapping[str, object] | None
    created_at: datetime
    updated_at: datetime
    created_by: str | None
    last_updated_by: str | None
    db_version: RuntimeNoteContentVersion
    db_checksum: RuntimeNoteContentChecksum
    file_version: int | None
    file_checksum: RuntimeFileChecksum | None
    file_write_status: str
    last_source: str | None
    file_updated_at: datetime | None
    last_materialization_error: str | None
    observations: tuple[Mapping[str, object], ...] = ()
    relations: tuple[Mapping[str, object], ...] = ()

    @classmethod
    def from_entity(
        cls,
        entity: RuntimeAcceptedNoteEntitySource,
        *,
        markdown_content: str,
        db_version: RuntimeNoteContentVersion,
        db_checksum: RuntimeNoteContentChecksum,
        file_version: int | None,
        file_checksum: RuntimeFileChecksum | None,
        file_write_status: str,
        last_source: str | None,
        file_updated_at: datetime | None,
        last_materialization_error: str | None,
    ) -> Self:
        """Build accepted-note response state from a loaded entity and content markers."""
        return cls(
            external_id=entity.external_id,
            entity_id=entity.id,
            title=entity.title,
            note_type=entity.note_type,
            content_type=entity.content_type,
            permalink=entity.permalink,
            file_path=entity.file_path,
            markdown_content=markdown_content,
            entity_metadata=entity.entity_metadata,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
            created_by=entity.created_by,
            last_updated_by=entity.last_updated_by,
            db_version=db_version,
            db_checksum=db_checksum,
            file_version=file_version,
            file_checksum=file_checksum,
            file_write_status=file_write_status,
            last_source=last_source,
            file_updated_at=file_updated_at,
            last_materialization_error=last_materialization_error,
        )

    @classmethod
    def from_entity_and_content_state(
        cls,
        entity: RuntimeAcceptedNoteEntitySource,
        note_content: RuntimeNoteContentState,
    ) -> Self:
        """Build accepted-note response state from an entity and typed content state."""
        return cls.from_entity(
            entity,
            markdown_content=note_content.markdown_content,
            db_version=note_content.db_version,
            db_checksum=note_content.db_checksum,
            file_version=note_content.file_version,
            file_checksum=note_content.file_checksum,
            file_write_status=note_content.file_write_status,
            last_source=note_content.last_source,
            file_updated_at=note_content.file_updated_at,
            last_materialization_error=note_content.last_materialization_error,
        )

    def to_response_payload(self) -> dict[str, object]:
        """Serialize to the existing v2 entity-plus-note-content response shape."""
        payload: dict[str, object] = {
            "external_id": self.external_id,
            "id": self.entity_id,
            "title": self.title,
            "note_type": self.note_type,
            "content_type": self.content_type,
            "permalink": self.permalink,
            "file_path": self.file_path,
            "content": self.markdown_content,
            "entity_metadata": (
                dict(self.entity_metadata) if self.entity_metadata is not None else None
            ),
            "observations": list(self.observations),
            "relations": list(self.relations),
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "created_by": self.created_by,
            "last_updated_by": self.last_updated_by,
            "api_version": "v2",
            "db_version": self.db_version,
            "db_checksum": self.db_checksum,
            "file_version": self.file_version,
            "file_checksum": self.file_checksum,
            "file_write_status": self.file_write_status,
            "last_source": self.last_source,
            "file_updated_at": (
                self.file_updated_at.isoformat() if self.file_updated_at is not None else None
            ),
            "last_materialization_error": self.last_materialization_error,
        }
        if self.file_write_status == "external_change_detected":
            payload["sync_error"] = NOTE_CONTENT_EXTERNAL_CHANGE_SYNC_ERROR
        return payload


def plan_accepted_note_response(
    *,
    entity: RuntimeAcceptedNoteEntitySource,
    note_content: RuntimeNoteContentStateSource,
    fallback_source: RuntimeNoteChangeSource,
) -> RuntimeAcceptedNoteResponse:
    """Build an accepted-note response from accepted note_content state."""
    note_content_state = RuntimeNoteContentState.from_source(note_content)
    if note_content_state.last_source is None:
        note_content_state = replace(note_content_state, last_source=fallback_source)
    return RuntimeAcceptedNoteResponse.from_entity_and_content_state(entity, note_content_state)


type RuntimeNoteContentResponsePayload = RuntimeAcceptedNoteResponse | Mapping[str, object]


def runtime_note_content_payload_as_dict(
    payload: RuntimeNoteContentResponsePayload,
) -> dict[str, object]:
    """Serialize a typed note-content payload into the existing JSON object contract."""
    if isinstance(payload, RuntimeAcceptedNoteResponse):
        return payload.to_response_payload()
    return dict(payload)


def runtime_note_content_payload_as_json_bytes(
    payload: RuntimeNoteContentResponsePayload,
) -> bytes:
    """Serialize a note-content payload for HTTP and queue transport boundaries."""
    return json.dumps(runtime_note_content_payload_as_dict(payload)).encode("utf-8")
