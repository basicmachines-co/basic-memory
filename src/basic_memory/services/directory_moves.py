"""Directory moves through the same accepted-write path as single note moves.

A Markdown note's accepted content lives in note_content, so moving one is an accepted
mutation: the note's path, search row and pending graph publication change in one
transaction, and the runtime materializes the bytes at the new path afterwards. Moving the
stored object directly would leave note_content at the old path, where a later
materialization writes the note back. Regular files (images, PDFs) have no accepted
content, so their stored bytes are the only copy and move directly.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import nullcontext
from typing import Protocol

from basic_memory import db
from basic_memory.models import Entity
from basic_memory.read_cache import ReadCache, invalidate_cache
from basic_memory.runtime.note_content_responses import runtime_note_content_payload_as_dict
from basic_memory.runtime.storage import runtime_content_type_is_markdown
from basic_memory.schemas.response import DirectoryMoveError, DirectoryMoveResult
from basic_memory.services.entity_service import EntityService
from basic_memory.services.exceptions import EntityNotFoundError
from basic_memory.services.note_content_writes import (
    AcceptedNoteChange,
    NoteContentMutationService,
    NoteContentMutationServiceError,
)
from basic_memory.services.search_service import SearchService

type MovedEntityFollowups = Callable[[int], None]


class AcceptedNoteMaterializer(Protocol):
    """Runtime hand-off that publishes and materializes one accepted note change."""

    async def materialize_write_change(self, accepted: AcceptedNoteChange) -> AcceptedNoteChange:
        """Queue or perform the storage work for an accepted change."""
        ...


async def move_directory(
    *,
    source_directory: str,
    destination_directory: str,
    project_external_id: str,
    note_mutations: NoteContentMutationService,
    materializer: AcceptedNoteMaterializer,
    entity_service: EntityService,
    search_service: SearchService,
    read_cache: ReadCache | None,
    schedule_followups: MovedEntityFollowups,
) -> DirectoryMoveResult:
    """Move every entity under ``source_directory`` to ``destination_directory``.

    Each entity moves on its own, so one refused move (a destination conflict, say) is
    reported and the rest proceed, as the endpoint has always reported partial moves.
    """
    source = source_directory.strip("/")
    destination = destination_directory.strip("/")
    async with db.scoped_session(entity_service.session_maker) as session:
        entities = await entity_service.repository.find_by_directory_prefix(session, source)

    moved_files: list[str] = []
    errors: list[DirectoryMoveError] = []
    for entity in entities:
        # The prefix lookup can match the source in another casing (SQLite LIKE is
        # case-insensitive), so keep the entity's own path below the matched prefix. The
        # project root matches every entity, whose whole path moves under the destination.
        relative_path = entity.file_path[len(source) + 1 :] if source else entity.file_path
        destination_path = f"{destination}/{relative_path}"

        if runtime_content_type_is_markdown(entity):
            try:
                accepted = await note_mutations.move_note(
                    project_external_id=project_external_id,
                    entity_external_id=entity.external_id,
                    destination_path=destination_path,
                    user_profile_id=None,
                    source="api",
                )
            except NoteContentMutationServiceError as error:
                errors.append(DirectoryMoveError(path=entity.file_path, error=str(error)))
                continue
            await materializer.materialize_write_change(accepted)
            # The accepted move can adopt an existing folder's casing, so report where the
            # note now is rather than where it was asked to go.
            moved_path = str(runtime_note_content_payload_as_dict(accepted.payload)["file_path"])
        else:
            try:
                moved_path = (
                    await move_regular_file(
                        file_path=entity.file_path,
                        destination_path=destination_path,
                        project_external_id=project_external_id,
                        entity_service=entity_service,
                        search_service=search_service,
                        read_cache=read_cache,
                    )
                ).file_path
            except (ValueError, EntityNotFoundError) as error:
                errors.append(DirectoryMoveError(path=entity.file_path, error=str(error)))
                continue

        schedule_followups(entity.id)
        moved_files.append(moved_path)

    return DirectoryMoveResult(
        total_files=len(entities),
        successful_moves=len(moved_files),
        failed_moves=len(errors),
        moved_files=moved_files,
        errors=errors,
    )


async def move_regular_file(
    *,
    file_path: str,
    destination_path: str,
    project_external_id: str,
    entity_service: EntityService,
    search_service: SearchService,
    read_cache: ReadCache | None,
) -> Entity:
    """Move one regular file's stored bytes and its index entry."""
    # The move publishes storage and DB state before it returns, so the read-cache
    # generation bump must finish even when the caller is cancelled mid-move.
    invalidation_scope = (
        invalidate_cache(read_cache, project_external_id)
        if read_cache is not None
        else nullcontext()
    )
    async with invalidation_scope:
        moved_entity = await entity_service.move_entity(
            identifier=file_path,
            destination_path=destination_path,
        )
        await search_service.index_entity(moved_entity)
    return moved_entity
