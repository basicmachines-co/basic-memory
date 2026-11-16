"""V2 project change journal response schemas.

A project records every accepted note change in an ordered journal. These
schemas describe one page of that journal, read from a client-held cursor.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

type ProjectChangeOperation = Literal["created", "updated", "moved", "deleted"]


class ProjectChange(BaseModel):
    """One accepted note change at one position in the project's journal."""

    position: int = Field(
        ge=1,
        description=(
            "This change's position in the project's journal. Positions start at 1 "
            "and increase by exactly one per accepted change."
        ),
    )
    operation: ProjectChangeOperation = Field(
        description=(
            "What happened to the note: created, updated (new content or metadata), "
            "moved (new file path), or deleted."
        ),
    )
    note_external_id: str = Field(
        description=(
            "The note's stable external id. A deleted note keeps its id here after "
            "the note itself is gone."
        ),
    )
    permalink: str = Field(description="The note's permalink after this change.")
    title: str = Field(description="The note's title after this change.")
    file_path: str = Field(
        description=(
            "The note's file path, relative to the project root, after this change. "
            "For a delete, the path the note had when it was deleted."
        ),
    )
    previous_file_path: str | None = Field(
        default=None,
        description=(
            "The note's file path before this change when the change moved it; otherwise null."
        ),
    )
    accepted_at: datetime = Field(
        description="When the change was accepted, as a timezone-aware timestamp.",
    )
    source: str = Field(
        description=(
            "The write path that accepted the change, such as create_note or "
            "delete_note, or storage for a change made straight in storage and "
            "picked up by the project index."
        ),
    )
    db_version: int | None = Field(
        default=None,
        description="The note's content version after this change, when one was recorded.",
    )
    db_checksum: str | None = Field(
        default=None,
        description="The checksum of the note's accepted content after this change, when one was recorded.",
    )
    actor_kind: str | None = Field(
        default=None,
        description="The kind of actor that made the change, such as user, when it was identified.",
    )
    actor_name: str | None = Field(
        default=None,
        description="The display name of the actor that made the change, when it was identified.",
    )


class ProjectChangesResponse(BaseModel):
    """One page of a project's change journal, read after a client-held position."""

    head: int = Field(
        ge=0,
        description=(
            "The project's latest journal position when this request was read. Every "
            "change at or below head is committed. 0 means the journal is empty."
        ),
    )
    after: int = Field(
        ge=0,
        description="The position this page was read after, as the request gave it.",
    )
    next_after: int = Field(
        ge=0,
        description=(
            "Pass this as after on the next request. It is the position of the last "
            "change in this page, or after when the page is empty, and never exceeds head."
        ),
    )
    has_more: bool = Field(
        description=(
            "True when changes up to head remain beyond this page (next_after < head). "
            "False means the client has every change up to head."
        ),
    )
    changes: list[ProjectChange] = Field(
        description="Changes with after < position <= head, in position order, at most limit of them.",
    )


class CursorAheadOfHeadDetail(BaseModel):
    """Why a change-journal read was refused: the cursor is past the journal's head."""

    code: Literal["cursor_ahead_of_head"] = Field(
        description="Machine-readable reason. The client must discard its cursor and resync.",
    )
    head: int = Field(
        ge=0,
        description="The project's journal position when the request was read.",
    )


class CursorAheadOfHeadResponse(BaseModel):
    """The HTTP 409 body returned when after is greater than the project's head."""

    detail: CursorAheadOfHeadDetail
