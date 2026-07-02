"""Shared note-content mutation service facade."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory.indexing.accepted_note_mutation_runner import (
    AcceptedNoteCreateMutation,
    AcceptedNoteDeleteMutation,
    AcceptedNoteEditMutation,
    AcceptedNoteMoveMutation,
    AcceptedNoteMutationActor,
    AcceptedNoteMutationDependencies,
    AcceptedNoteMutationRejected,
    AcceptedNoteMutationRejection,
    AcceptedNoteUpdateMutation,
    run_accepted_note_create,
    run_accepted_note_delete,
    run_accepted_note_edit,
    run_accepted_note_move,
    run_accepted_note_update,
)
from basic_memory.runtime.note_content import (
    RuntimeAcceptedNoteChange,
    RuntimeNoteContentResponsePayload,
)
from basic_memory.schemas.base import Entity as EntitySchema
from basic_memory.schemas.request import EditEntityRequest

AcceptedNoteChange = RuntimeAcceptedNoteChange[RuntimeNoteContentResponsePayload]


class NoteContentMutationFreshener(Protocol):
    """Refresh current runtime file state before mutating an existing note."""

    async def freshen_note_content(
        self,
        *,
        project_external_id: str,
        entity_external_id: str,
    ) -> None: ...


type NoteContentMutationKind = Literal["create", "update", "edit", "move"]


@dataclass(frozen=True, slots=True)
class NoteContentMutationActorContext:
    """Who and what originated one accepted note mutation."""

    user_profile_id: UUID | None
    source: str
    actor_kind: str | None = None
    actor_name: str | None = None


class NoteContentMutationActorResolver(Protocol):
    """Resolve the actor context for one mutation at the runtime boundary.

    Routes pass through whatever actor values they were called with; a runtime
    adapter (e.g. cloud) can replace them with request-derived identity — user
    profile, source header, MCP actor headers — without subclassing the service.
    """

    def resolve_mutation_actor(
        self,
        *,
        mutation_kind: NoteContentMutationKind,
        requested: NoteContentMutationActorContext,
    ) -> NoteContentMutationActorContext: ...


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
        rejection.kind.http_status_code,
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
        content_freshener: NoteContentMutationFreshener | None = None,
        actor_resolver: NoteContentMutationActorResolver | None = None,
    ) -> None:
        self.session_maker = session_maker
        self.mutation_dependencies = mutation_dependencies
        self.content_freshener = content_freshener
        self.actor_resolver = actor_resolver

    def _resolve_actor(
        self,
        mutation_kind: NoteContentMutationKind,
        *,
        user_profile_id: UUID | None,
        source: str,
        actor_kind: str | None,
        actor_name: str | None,
    ) -> NoteContentMutationActorContext:
        requested = NoteContentMutationActorContext(
            user_profile_id=user_profile_id,
            source=source,
            actor_kind=actor_kind,
            actor_name=actor_name,
        )
        if self.actor_resolver is None:
            return requested
        return self.actor_resolver.resolve_mutation_actor(
            mutation_kind=mutation_kind,
            requested=requested,
        )

    async def freshen_existing_note_content(
        self,
        *,
        project_external_id: str,
        entity_external_id: str,
    ) -> None:
        """Let the runtime converge observed file state before an existing-note mutation."""
        if self.content_freshener is None:
            return
        await self.content_freshener.freshen_note_content(
            project_external_id=project_external_id,
            entity_external_id=entity_external_id,
        )

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
        actor_context = self._resolve_actor(
            "create",
            user_profile_id=user_profile_id,
            source=source,
            actor_kind=actor_kind,
            actor_name=actor_name,
        )
        try:
            async with accepted_note_transaction(self.session_maker) as session:
                return await run_accepted_note_create(
                    session,
                    request=AcceptedNoteCreateMutation(
                        project_external_id=project_external_id,
                        data=data,
                        actor=accepted_note_mutation_actor(
                            user_profile_id=actor_context.user_profile_id,
                            actor_kind=actor_context.actor_kind,
                            actor_name=actor_context.actor_name,
                        ),
                        source=actor_context.source,
                    ),
                    dependencies=self.mutation_dependencies,
                )
        except AcceptedNoteMutationRejected as error:
            raise note_content_mutation_error_from_rejection(error.rejection) from error

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
        actor_context = self._resolve_actor(
            "update",
            user_profile_id=user_profile_id,
            source=source,
            actor_kind=actor_kind,
            actor_name=actor_name,
        )
        try:
            await self.freshen_existing_note_content(
                project_external_id=project_external_id,
                entity_external_id=entity_external_id,
            )
            async with accepted_note_transaction(self.session_maker) as session:
                return await run_accepted_note_update(
                    session,
                    request=AcceptedNoteUpdateMutation(
                        project_external_id=project_external_id,
                        entity_external_id=entity_external_id,
                        data=data,
                        actor=accepted_note_mutation_actor(
                            user_profile_id=actor_context.user_profile_id,
                            actor_kind=actor_context.actor_kind,
                            actor_name=actor_context.actor_name,
                        ),
                        source=actor_context.source,
                    ),
                    dependencies=self.mutation_dependencies,
                )
        except AcceptedNoteMutationRejected as error:
            raise note_content_mutation_error_from_rejection(error.rejection) from error

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
        actor_context = self._resolve_actor(
            "edit",
            user_profile_id=user_profile_id,
            source=source,
            actor_kind=actor_kind,
            actor_name=actor_name,
        )
        try:
            await self.freshen_existing_note_content(
                project_external_id=project_external_id,
                entity_external_id=entity_external_id,
            )
            async with accepted_note_transaction(self.session_maker) as session:
                return await run_accepted_note_edit(
                    session,
                    request=AcceptedNoteEditMutation(
                        project_external_id=project_external_id,
                        entity_external_id=entity_external_id,
                        data=data,
                        actor=accepted_note_mutation_actor(
                            user_profile_id=actor_context.user_profile_id,
                            actor_kind=actor_context.actor_kind,
                            actor_name=actor_context.actor_name,
                        ),
                        source=actor_context.source,
                    ),
                    dependencies=self.mutation_dependencies,
                )
        except AcceptedNoteMutationRejected as error:
            raise note_content_mutation_error_from_rejection(error.rejection) from error

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
        actor_context = self._resolve_actor(
            "move",
            user_profile_id=user_profile_id,
            source=source,
            actor_kind=actor_kind,
            actor_name=actor_name,
        )
        try:
            await self.freshen_existing_note_content(
                project_external_id=project_external_id,
                entity_external_id=entity_external_id,
            )
            async with accepted_note_transaction(self.session_maker) as session:
                return await run_accepted_note_move(
                    session,
                    request=AcceptedNoteMoveMutation(
                        project_external_id=project_external_id,
                        entity_external_id=entity_external_id,
                        destination_path=destination_path,
                        actor=accepted_note_mutation_actor(
                            user_profile_id=actor_context.user_profile_id,
                            actor_kind=actor_context.actor_kind,
                            actor_name=actor_context.actor_name,
                        ),
                        source=actor_context.source,
                    ),
                    dependencies=self.mutation_dependencies,
                )
        except AcceptedNoteMutationRejected as error:
            raise note_content_mutation_error_from_rejection(error.rejection) from error

    async def delete_note(
        self,
        *,
        project_external_id: str,
        entity_external_id: str,
    ) -> AcceptedNoteChange:
        """DELETE the DB note and return the runtime follow-up change."""
        try:
            await self.freshen_existing_note_content(
                project_external_id=project_external_id,
                entity_external_id=entity_external_id,
            )
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
            raise note_content_mutation_error_from_rejection(error.rejection) from error
