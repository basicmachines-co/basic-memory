"""Which cached reads one accepted note change makes stale.

One place maps note events to read-cache versions, so the API write path, local
materialization and a hosted worker all retire the same reads for the same change.
"""

from collections.abc import Iterable

from basic_memory.read_cache import ReadCacheChange
from basic_memory.runtime.note_materialization_planning import RuntimePendingNoteMaterialization
from basic_memory.runtime.project_partition import (
    RuntimeAcceptedProjectNoteChange,
    RuntimeProjectNoteOperation,
)


def accepted_note_read_cache_change(
    project_change: RuntimeAcceptedProjectNoteChange | None,
    *,
    identity_changed: bool,
    detached_note_ids: Iterable[str] = (),
) -> ReadCacheChange:
    """The reads an accepted create, update, move or delete made stale.

    Creates, moves and deletes change which names resolve and which files a listing
    shows. So does an update that renames the note or changes its permalink. An
    update that only changes the body retires the note itself, the notes whose links
    it republished, and the content-wide reads (search, listings, recent activity).
    """
    if project_change is None:
        # The mutation did not record what it changed, so nothing narrower is safe.
        return ReadCacheChange.whole_project()
    match project_change.operation:
        case (
            RuntimeProjectNoteOperation.created
            | RuntimeProjectNoteOperation.moved
            | RuntimeProjectNoteOperation.deleted
        ):
            return ReadCacheChange.whole_project()
        case RuntimeProjectNoteOperation.updated:
            if identity_changed:
                return ReadCacheChange.whole_project()
            return ReadCacheChange.note_content(
                {project_change.note_external_id, *detached_note_ids}
            )


def materialization_status_read_cache_change(
    materialization: RuntimePendingNoteMaterialization,
) -> ReadCacheChange:
    """Materialization moves the note's file status from pending to its outcome.

    The status is part of the note's own response and nothing else, so only that
    note is retired.
    """
    if materialization.project_change is None:
        return ReadCacheChange.whole_project()
    return ReadCacheChange(notes=frozenset({materialization.project_change.note_external_id}))
