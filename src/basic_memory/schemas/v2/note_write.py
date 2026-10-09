"""Validated outcomes for the path-addressed note write operation."""

from typing import Annotated, Literal, Self

from pydantic import BaseModel, Field, TypeAdapter, model_validator

from basic_memory.schemas.base import Entity
from basic_memory.schemas.v2.entity import EntityResponseV2


class WriteNoteRequest(BaseModel):
    note: Entity
    overwrite: bool = False
    # Optimistic-concurrency precondition: the accepted db_checksum the caller last
    # read. The replacement lands only while the note at the path is that revision.
    expected_checksum: str | None = None

    @model_validator(mode="after")
    def _expected_checksum_conditions_a_replacement(self) -> Self:
        # A create has no prior revision to compare, so a checksum without
        # overwrite is a caller mistake rather than a precondition.
        if self.expected_checksum is not None and not self.overwrite:
            raise ValueError("expected_checksum requires overwrite=True")
        return self


class NoteCreated(BaseModel):
    kind: Literal["created"] = "created"
    entity: EntityResponseV2


class NoteUpdated(BaseModel):
    kind: Literal["updated"] = "updated"
    entity: EntityResponseV2


class NoteAlreadyExists(BaseModel):
    kind: Literal["already_exists"] = "already_exists"
    file_path: str
    # The note that owns file_path. Optional so older servers and clients that send
    # only file_path still validate; None when no note can be named at the path.
    external_id: str | None = None
    permalink: str | None = None


class NoteTargetMoved(BaseModel):
    kind: Literal["target_moved"] = "target_moved"
    external_id: str
    title: str
    file_path: str
    permalink: str | None


class NoteLocked(BaseModel):
    kind: Literal["locked"] = "locked"
    message: str


class NoteRevisionConflict(BaseModel):
    kind: Literal["revision_conflict"] = "revision_conflict"
    file_path: str
    # None when no note owns the path any more.
    db_checksum: str | None


type WriteNoteResponse = Annotated[
    NoteCreated
    | NoteUpdated
    | NoteAlreadyExists
    | NoteTargetMoved
    | NoteLocked
    | NoteRevisionConflict,
    Field(discriminator="kind"),
]

write_note_response_adapter = TypeAdapter(WriteNoteResponse)
