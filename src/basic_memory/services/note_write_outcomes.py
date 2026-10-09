"""Expected outcomes of writing a note at a requested project path."""

from dataclasses import dataclass

from basic_memory.indexing.accepted_note_mutation_runner import (
    AcceptedNoteMutationChange,
    AcceptedNoteMutationRejection,
)


@dataclass(frozen=True, slots=True)
class NoteLocation:
    external_id: str
    title: str
    file_path: str
    permalink: str | None


@dataclass(frozen=True, slots=True)
class Created:
    change: AcceptedNoteMutationChange


@dataclass(frozen=True, slots=True)
class Updated:
    change: AcceptedNoteMutationChange


@dataclass(frozen=True, slots=True)
class AlreadyExists:
    """A note already owns the requested path and the write did not overwrite it.

    ``note`` is the note found at the path. It is None only when a concurrent create
    refused the path and no note is found there afterward.
    """

    file_path: str
    note: NoteLocation | None = None


@dataclass(frozen=True, slots=True)
class TargetMoved:
    note: NoteLocation


@dataclass(frozen=True, slots=True)
class Locked:
    message: str


@dataclass(frozen=True, slots=True)
class RevisionConflict:
    """The note at the path is no longer the revision the caller expected to replace.

    ``current_db_checksum`` is None when no note owns the path any more.
    """

    file_path: str
    current_db_checksum: str | None


@dataclass(frozen=True, slots=True)
class Rejected:
    rejection: AcceptedNoteMutationRejection


type WriteOutcome = (
    Created | Updated | AlreadyExists | TargetMoved | Locked | RevisionConflict | Rejected
)
