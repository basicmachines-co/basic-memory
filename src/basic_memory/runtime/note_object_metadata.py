"""Portable metadata vocabulary for materialized note objects."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Self
from uuid import UUID

from basic_memory.runtime.storage import (
    RuntimeEntityId,
    RuntimeFileChecksum,
    RuntimeNoteActorKind,
    RuntimeNoteActorName,
    RuntimeNoteChangeSource,
    RuntimeNoteContentChecksum,
    RuntimeNoteContentVersion,
)

type RuntimeNoteObjectMetadataMap = Mapping[str, str]

NOTE_OBJECT_ACTOR_KIND_METADATA = "bm-actor-kind"
NOTE_OBJECT_ACTOR_KIND_MCP_CLIENT = "mcp_client"
NOTE_OBJECT_ACTOR_NAME_METADATA = "bm-actor-name"
NOTE_OBJECT_ACTOR_USER_PROFILE_ID_METADATA = "bm-actor-user-profile-id"
NOTE_OBJECT_DB_CHECKSUM_METADATA = "bm-db-checksum"
NOTE_OBJECT_DB_VERSION_METADATA = "bm-db-version"
NOTE_OBJECT_ENTITY_ID_METADATA = "bm-entity-id"
NOTE_OBJECT_FILE_CHECKSUM_METADATA = "bm-file-checksum"
NOTE_OBJECT_FILE_VERSION_METADATA = "bm-file-version"
NOTE_OBJECT_SOURCE_METADATA = "bm-note-source"
VALID_NOTE_OBJECT_ACTOR_KINDS: frozenset[RuntimeNoteActorKind] = frozenset(
    {NOTE_OBJECT_ACTOR_KIND_MCP_CLIENT}
)
VALID_NOTE_OBJECT_SOURCES: frozenset[RuntimeNoteChangeSource] = frozenset(
    {"api", "mcp", "s3_webhook"}
)
_SAFE_ACTOR_NAME_CHARS = re.compile(r"[^A-Za-z0-9 ._()+/:-]+")
_WHITESPACE = re.compile(r"\s+")
_MAX_ACTOR_NAME_LENGTH = 120


class RuntimeStorageObjectChecksumSource(StrEnum):
    """Storage checksum source used when matching an indexed file to object metadata."""

    note_file_checksum = "bm-file-checksum"
    storage_etag = "etag"


@dataclass(frozen=True, slots=True)
class RuntimeStorageObjectChecksum:
    """Checksum selected for comparing an indexed file to a storage object."""

    checksum: RuntimeFileChecksum
    source: RuntimeStorageObjectChecksumSource


@dataclass(frozen=True, slots=True)
class RuntimeNoteObjectMetadata:
    """Metadata written onto a materialized note object."""

    entity_id: RuntimeEntityId
    db_version: RuntimeNoteContentVersion
    db_checksum: RuntimeNoteContentChecksum
    actor_user_profile_id: UUID | None = None
    actor_kind: RuntimeNoteActorKind | None = None
    actor_name: RuntimeNoteActorName | None = None
    source: RuntimeNoteChangeSource | None = None

    def to_storage_metadata(self) -> dict[str, str]:
        """Convert to the string metadata shape object storage accepts."""
        metadata = {
            NOTE_OBJECT_ENTITY_ID_METADATA: str(self.entity_id),
            NOTE_OBJECT_DB_VERSION_METADATA: str(self.db_version),
            NOTE_OBJECT_DB_CHECKSUM_METADATA: self.db_checksum,
            NOTE_OBJECT_FILE_VERSION_METADATA: str(self.db_version),
            NOTE_OBJECT_FILE_CHECKSUM_METADATA: self.db_checksum,
        }
        if self.actor_user_profile_id is not None:
            metadata[NOTE_OBJECT_ACTOR_USER_PROFILE_ID_METADATA] = str(self.actor_user_profile_id)
        if self.actor_kind is not None:
            metadata[NOTE_OBJECT_ACTOR_KIND_METADATA] = self.actor_kind
        if self.actor_name is not None:
            metadata[NOTE_OBJECT_ACTOR_NAME_METADATA] = self.actor_name
        if self.source is not None:
            metadata[NOTE_OBJECT_SOURCE_METADATA] = self.source
        return metadata


def normalize_actor_name(value: object | None) -> RuntimeNoteActorName | None:
    """Return a storage/user-facing safe actor label, or None when empty."""
    if value is None:
        return None

    raw = str(value).strip()
    if not raw:
        return None

    normalized = _WHITESPACE.sub(" ", raw)
    normalized = _SAFE_ACTOR_NAME_CHARS.sub("", normalized).strip()
    return normalized[:_MAX_ACTOR_NAME_LENGTH] or None


def actor_user_profile_id_from_object_metadata(
    metadata: RuntimeNoteObjectMetadataMap | None,
) -> str | None:
    """Return the actor id explicitly written onto a note object."""
    if not metadata:
        return None

    value = metadata.get(NOTE_OBJECT_ACTOR_USER_PROFILE_ID_METADATA)
    if value is None:
        return None

    stripped = value.strip()
    return stripped or None


def actor_kind_from_object_metadata(
    metadata: RuntimeNoteObjectMetadataMap | None,
) -> RuntimeNoteActorKind | None:
    """Return the typed origin kind written onto a note object."""
    if not metadata:
        return None

    value = metadata.get(NOTE_OBJECT_ACTOR_KIND_METADATA)
    if value is None:
        return None

    stripped = value.strip()
    if stripped in VALID_NOTE_OBJECT_ACTOR_KINDS:
        return stripped
    return None


def actor_name_from_object_metadata(
    metadata: RuntimeNoteObjectMetadataMap | None,
) -> RuntimeNoteActorName | None:
    """Return the user-facing origin label written onto a note object."""
    if not metadata:
        return None

    return normalize_actor_name(metadata.get(NOTE_OBJECT_ACTOR_NAME_METADATA))


def file_checksum_from_object_metadata(
    metadata: RuntimeNoteObjectMetadataMap | None,
) -> RuntimeFileChecksum | None:
    """Return the Basic Memory content checksum mirrored onto a note object."""
    if not metadata:
        return None

    value = metadata.get(NOTE_OBJECT_FILE_CHECKSUM_METADATA)
    if value is None:
        return None

    stripped = value.strip()
    return stripped or None


def storage_object_checksum_for_index_match(
    *,
    object_checksum: RuntimeFileChecksum,
    object_metadata: RuntimeNoteObjectMetadataMap | None,
) -> RuntimeStorageObjectChecksum:
    """Return the checksum that should match an indexed markdown file."""
    file_checksum = file_checksum_from_object_metadata(object_metadata)
    if file_checksum is not None:
        return RuntimeStorageObjectChecksum(
            checksum=file_checksum,
            source=RuntimeStorageObjectChecksumSource.note_file_checksum,
        )
    return RuntimeStorageObjectChecksum(
        checksum=object_checksum,
        source=RuntimeStorageObjectChecksumSource.storage_etag,
    )


def db_version_from_object_metadata(
    metadata: RuntimeNoteObjectMetadataMap | None,
) -> RuntimeNoteContentVersion | None:
    """Return the accepted DB version mirrored onto a materialized note object."""
    if not metadata:
        return None

    value = metadata.get(NOTE_OBJECT_DB_VERSION_METADATA)
    if value is None:
        return None

    try:
        version = int(value.strip())
    except ValueError:
        return None
    return version if version > 0 else None


def source_from_object_metadata(
    metadata: RuntimeNoteObjectMetadataMap | None,
) -> RuntimeNoteChangeSource | None:
    """Return the original write source mirrored onto a note object."""
    if not metadata:
        return None

    value = metadata.get(NOTE_OBJECT_SOURCE_METADATA)
    if value is None:
        return None

    stripped = value.strip()
    if stripped in VALID_NOTE_OBJECT_SOURCES:
        return stripped
    return None


@dataclass(frozen=True, slots=True)
class RuntimeNoteObjectProvenance:
    """Trusted actor and source metadata parsed from a materialized note object."""

    actor_user_profile_id: str | None = None
    actor_kind: RuntimeNoteActorKind | None = None
    actor_name: RuntimeNoteActorName | None = None
    source: RuntimeNoteChangeSource | None = None

    @classmethod
    def from_object_metadata(cls, metadata: RuntimeNoteObjectMetadataMap | None) -> Self:
        actor_kind = actor_kind_from_object_metadata(metadata)
        actor_name = actor_name_from_object_metadata(metadata) if actor_kind is not None else None
        return cls(
            actor_user_profile_id=actor_user_profile_id_from_object_metadata(metadata),
            actor_kind=actor_kind,
            actor_name=actor_name,
            source=source_from_object_metadata(metadata),
        )


@dataclass(frozen=True, slots=True)
class RuntimeNoteActorOrigin:
    """User-facing client origin that can be safely attached to live updates."""

    actor_kind: RuntimeNoteActorKind
    actor_name: RuntimeNoteActorName

    @classmethod
    def from_actor_metadata(
        cls,
        *,
        actor_kind: RuntimeNoteActorKind | None,
        actor_name: RuntimeNoteActorName | None,
    ) -> Self | None:
        if actor_kind != NOTE_OBJECT_ACTOR_KIND_MCP_CLIENT or not actor_name:
            return None
        return cls(actor_kind=actor_kind, actor_name=actor_name)
