"""Shared note-content mutation service facade."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory.indexing import (
    AcceptedNoteCreateMutation,
    AcceptedNoteDeleteMutation,
    AcceptedNoteEditMutation,
    AcceptedNoteMoveMutation,
    AcceptedNoteMutationActor,
    AcceptedNoteMutationDependencies,
    AcceptedNoteMutationRejected,
    AcceptedNoteMutationRejection,
    AcceptedNoteMutationRejectKind,
    AcceptedNoteUpdateMutation,
    run_accepted_note_create,
    run_accepted_note_delete,
    run_accepted_note_edit,
    run_accepted_note_move,
    run_accepted_note_update,
)
from basic_memory.runtime import (
    RuntimeAcceptedNoteChange,
    RuntimeNoteContentResponsePayload,
)
from basic_memory.schemas.base import Entity as EntitySchema
from basic_memory.schemas.request import EditEntityRequest

type NoteContentMutationRejectionMapper = Callable[
    [AcceptedNoteMutationRejection],
    Exception,
]

AcceptedNoteChange = RuntimeAcceptedNoteChange[RuntimeNoteContentResponsePayload]

_REJECTION_STATUS_CODES: dict[AcceptedNoteMutationRejectKind, int] = {
    AcceptedNoteMutationRejectKind.bad_request: 400,
    AcceptedNoteMutationRejectKind.conflict: 409,
    AcceptedNoteMutationRejectKind.not_found: 404,
    AcceptedNoteMutationRejectKind.unsupported_media_type: 415,
}


class NoteContentMutationServiceError(Exception):
    """Structured note-content mutation service error for route adapters."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def note_content_mutation_error_from_rejection(
    rejection: AcceptedNoteMutationRejection,
) -> NoteContentMutationServiceError:
    """Map core accepted-note mutation rejections into route-facing errors."""
    return NoteContentMutationServiceError(
        _REJECTION_STATUS_CODES[rejection.kind],
        rejection.detail,
    )


def accepted_note_mutation_actor(
    *,
    user_profile_id: UUID | None,
    actor_kind: str | None,
    actor_name: str | None,
) -> AcceptedNoteMutationActor:
    """Build the typed accepted-note actor passed to core mutation runners."""
    return AcceptedNoteMutationActor(
        user_profile_id=user_profile_id,
        kind=actor_kind,
        name=actor_name,
    )


@asynccontextmanager
async def accepted_note_transaction(
    session_maker: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Open one DB transaction for an accepted note mutation."""
    async with session_maker() as session:
        async with session.begin():
            yield session


class NoteContentMutationService:
    """Accept note mutations into DB state through core-owned mutation runners."""

    def __init__(
        self,
        *,
        session_maker: async_sessionmaker[AsyncSession],
        mutation_dependencies: AcceptedNoteMutationDependencies,
        rejection_mapper: NoteContentMutationRejectionMapper,
    ) -> None:
        self.session_maker = session_maker
        self.mutation_dependencies = mutation_dependencies
        self.rejection_mapper = rejection_mapper

    async def create_note(
        self,
        *,
        project_external_id: str,
        data: EntitySchema,
        user_profile_id: UUID | None,
        source: str,
        actor_kind: str | None = None,
        actor_name: str | None = None,
    ) -> AcceptedNoteChange:
        """POST a new markdown note into accepted DB state."""
        try:
            async with accepted_note_transaction(self.session_maker) as session:
                return await run_accepted_note_create(
                    session,
                    request=AcceptedNoteCreateMutation(
                        project_external_id=project_external_id,
                        data=data,
                        actor=accepted_note_mutation_actor(
                            user_profile_id=user_profile_id,
                            actor_kind=actor_kind,
                            actor_name=actor_name,
                        ),
                        source=source,
                    ),
                    dependencies=self.mutation_dependencies,
                )
        except AcceptedNoteMutationRejected as error:
            raise self.rejection_mapper(error.rejection) from error

    async def update_note(
        self,
        *,
        project_external_id: str,
        entity_external_id: str,
        data: EntitySchema,
        user_profile_id: UUID | None,
        source: str,
        actor_kind: str | None = None,
        actor_name: str | None = None,
    ) -> AcceptedNoteChange:
        """PUT a markdown note by creating or replacing accepted DB state."""
        try:
            async with accepted_note_transaction(self.session_maker) as session:
                return await run_accepted_note_update(
                    session,
                    request=AcceptedNoteUpdateMutation(
                        project_external_id=project_external_id,
                        entity_external_id=entity_external_id,
                        data=data,
                        actor=accepted_note_mutation_actor(
                            user_profile_id=user_profile_id,
                            actor_kind=actor_kind,
                            actor_name=actor_name,
                        ),
                        source=source,
                    ),
                    dependencies=self.mutation_dependencies,
                )
        except AcceptedNoteMutationRejected as error:
            raise self.rejection_mapper(error.rejection) from error

    async def edit_note(
        self,
        *,
        project_external_id: str,
        entity_external_id: str,
        data: EditEntityRequest,
        user_profile_id: UUID | None,
        source: str,
        actor_kind: str | None = None,
        actor_name: str | None = None,
    ) -> AcceptedNoteChange:
        """PATCH a markdown note using the latest accepted DB content as the base."""
        try:
            async with accepted_note_transaction(self.session_maker) as session:
                return await run_accepted_note_edit(
                    session,
                    request=AcceptedNoteEditMutation(
                        project_external_id=project_external_id,
                        entity_external_id=entity_external_id,
                        data=data,
                        actor=accepted_note_mutation_actor(
                            user_profile_id=user_profile_id,
                            actor_kind=actor_kind,
                            actor_name=actor_name,
                        ),
                        source=source,
                    ),
                    dependencies=self.mutation_dependencies,
                )
        except AcceptedNoteMutationRejected as error:
            raise self.rejection_mapper(error.rejection) from error

    async def move_note(
        self,
        *,
        project_external_id: str,
        entity_external_id: str,
        destination_path: str,
        user_profile_id: UUID | None,
        source: str,
        actor_kind: str | None = None,
        actor_name: str | None = None,
    ) -> AcceptedNoteChange:
        """Move a note by accepting the new path before runtime materialization."""
        try:
            async with accepted_note_transaction(self.session_maker) as session:
                return await run_accepted_note_move(
                    session,
                    request=AcceptedNoteMoveMutation(
                        project_external_id=project_external_id,
                        entity_external_id=entity_external_id,
                        destination_path=destination_path,
                        actor=accepted_note_mutation_actor(
                            user_profile_id=user_profile_id,
                            actor_kind=actor_kind,
                            actor_name=actor_name,
                        ),
                        source=source,
                    ),
                    dependencies=self.mutation_dependencies,
                )
        except AcceptedNoteMutationRejected as error:
            raise self.rejection_mapper(error.rejection) from error

    async def delete_note(
        self,
        *,
        project_external_id: str,
        entity_external_id: str,
    ) -> AcceptedNoteChange:
        """DELETE the DB note and return the runtime follow-up change."""
        try:
            async with accepted_note_transaction(self.session_maker) as session:
                return await run_accepted_note_delete(
                    session,
                    request=AcceptedNoteDeleteMutation(
                        project_external_id=project_external_id,
                        entity_external_id=entity_external_id,
                    ),
                    dependencies=self.mutation_dependencies,
                )
        except AcceptedNoteMutationRejected as error:
            raise self.rejection_mapper(error.rejection) from error
