"""Validated outcomes for the path-addressed note write operation."""

from typing import Annotated, Literal

from pydantic import BaseModel, Field, TypeAdapter

from basic_memory.schemas.base import Entity
from basic_memory.schemas.v2.entity import EntityResponseV2


class WriteNoteRequest(BaseModel):
    note: Entity
    overwrite: bool = False


class NoteCreated(BaseModel):
    kind: Literal["created"] = "created"
    entity: EntityResponseV2


class NoteUpdated(BaseModel):
    kind: Literal["updated"] = "updated"
    entity: EntityResponseV2


class NoteAlreadyExists(BaseModel):
    kind: Literal["already_exists"] = "already_exists"
    file_path: str


class NoteTargetMoved(BaseModel):
    kind: Literal["target_moved"] = "target_moved"
    external_id: str
    title: str
    file_path: str
    permalink: str | None


class NoteLocked(BaseModel):
    kind: Literal["locked"] = "locked"
    message: str


type WriteNoteResponse = Annotated[
    NoteCreated | NoteUpdated | NoteAlreadyExists | NoteTargetMoved | NoteLocked,
    Field(discriminator="kind"),
]

write_note_response_adapter = TypeAdapter(WriteNoteResponse)
