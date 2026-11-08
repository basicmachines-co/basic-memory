"""Which cached reads one accepted note change makes stale.

One place maps note events to read-cache versions, so the API write path, local
materialization and a hosted worker all retire the same reads for the same change.
"""

from collections.abc import Iterable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.read_cache import ReadCacheChange
from basic_memory.repository.relation_repository import RelationRepository
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
    linked_note_ids: Iterable[str] | None = None,
) -> ReadCacheChange:
    """The reads an accepted create, update, move or delete made stale.

    ``detached_note_ids`` are the notes whose links this write republished.
    ``linked_note_ids`` are the notes on either end of the note's resolved links,
    whose rendered relations show its name and path; ``None`` means the caller did
    not look them up.

    - A create adds a name: name lookups, the folder structure and content-wide reads
      change, and no other note's response can show the new note yet. Notes whose
      forward links now resolve to it are retired by relation resolution
      (``resolved_relations_read_cache_change``) when it rewrites them.
    - A move, a delete, or an update that renames the note or changes its permalink
      also changes the responses of the notes linked to it.
    - An update that only changes the body retires the note itself, the notes whose
      links it republished, and the content-wide reads.
    """
    if project_change is None:
        # The mutation did not record what it changed, so nothing narrower is safe.
        return ReadCacheChange.whole_project()
    note_ids = {project_change.note_external_id, *detached_note_ids}
    match project_change.operation:
        case RuntimeProjectNoteOperation.created:
            return ReadCacheChange.note_identity(note_ids)
        case RuntimeProjectNoteOperation.updated if not identity_changed:
            return ReadCacheChange.note_content(note_ids)
        case (
            RuntimeProjectNoteOperation.moved
            | RuntimeProjectNoteOperation.deleted
            | RuntimeProjectNoteOperation.updated
        ):
            # Trigger: the note's name or path changed, or it is gone.
            # Why: linked notes render its permalink and title in their relations,
            #   and without their ids we cannot name every response that changed.
            # Outcome: retire the linked notes, or the whole project if unknown.
            if linked_note_ids is None:
                return ReadCacheChange.whole_project()
            return ReadCacheChange.note_identity({*note_ids, *linked_note_ids})


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


async def resolved_relations_read_cache_change(
    session_maker: async_sessionmaker[AsyncSession],
    *,
    project_id: int,
    source_entity_ids: Iterable[int],
) -> ReadCacheChange:
    """The reads a relation-resolution pass made stale.

    The resolved source notes and their entity-rendered targets show new links,
    and the sources' search rows were re-indexed. A pass that resolved nothing
    changed nothing, so nothing is retired.
    """
    source_ids = sorted(set(source_entity_ids))
    if not source_ids:
        return ReadCacheChange()
    async with db.scoped_session(session_maker) as session:
        note_ids = await RelationRepository(project_id=project_id).note_ids_touched_by_resolution(
            session,
            source_entity_ids=source_ids,
        )
    return ReadCacheChange.note_content(note_ids)
