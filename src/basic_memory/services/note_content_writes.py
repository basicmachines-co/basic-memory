"""Runtime-neutral note-content mutation service facade."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID

from loguru import logger
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
    AcceptedNoteMutationResult,
    AcceptedNoteUpdateMutation,
    run_accepted_note_create,
    run_accepted_note_delete,
    run_accepted_note_edit,
    run_accepted_note_move,
    run_accepted_note_update,
)
from basic_memory.indexing.relation_persistence import (
    RelationGenerationPublication,
    RelationGenerationPublisher,
)
from basic_memory.runtime.note_content import (
    RuntimeAcceptedNoteChange,
    RuntimeNoteContentResponsePayload,
)
from basic_memory.read_cache import (
    ReadCacheInvalidator,
    finish_project_read_cache_invalidation,
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


# Route adapters place error.detail directly into the HTTP response body, so it
# is either a plain message string or an already-serialized structured detail
# (currently only the base-checksum conflict wire dict, issue #1445).
type NoteContentMutationErrorDetail = str | dict[str, str | None]


class NoteContentMutationServiceError(Exception):
    """Structured note-content mutation service error for route adapters."""

    def __init__(self, status_code: int, detail: NoteContentMutationErrorDetail) -> None:
        super().__init__(str(detail))
        self.status_code = status_code
        self.detail = detail


def note_content_mutation_error_from_rejection(
    rejection: AcceptedNoteMutationRejection,
) -> NoteContentMutationServiceError:
    """Map core accepted-note mutation rejections into route-facing errors."""
    detail = rejection.detail
    # This mapping is the wire boundary: typed rejection details serialize to the
    # JSON dict that HTTP routes place verbatim into the 4xx response body.
    return NoteContentMutationServiceError(
        rejection.kind.http_status_code,
        detail if isinstance(detail, str) else detail.as_json_dict(),
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
        read_cache: ReadCacheInvalidator | None = None,
    ) -> None:
        self.session_maker = session_maker
        self.mutation_dependencies = mutation_dependencies
        self.content_freshener = content_freshener
        self.actor_resolver = actor_resolver
        self.read_cache = read_cache

    async def _publish_relation_generation(
        self,
        publication: RelationGenerationPublication | None,
    ) -> None:
        """Publish accepted graph intent only after note-content commit."""
        if publication is None:
            return

        repository = self.mutation_dependencies.write_repositories.relation_repository(
            publication.project_id
        )
        observation_repository = (
            self.mutation_dependencies.write_repositories.observation_repository(
                publication.project_id
            )
        )
        section_repository = self.mutation_dependencies.write_repositories.section_repository(
            publication.project_id
        )
        publisher = RelationGenerationPublisher(
            relation_repository=repository,
            observation_repository=observation_repository,
            section_repository=section_repository,
            session_maker=self.session_maker,
        )
        await publisher.publish(
            entity_id=publication.entity_id,
            generation=publication.generation,
            relations=publication.relations,
            observations=publication.observations,
            sections=publication.sections,
        )

    async def _finish_mutation(
        self,
        result: AcceptedNoteMutationResult,
    ) -> AcceptedNoteChange:
        """Run post-commit graph publication and expose the accepted response."""
        try:
            await self._publish_relation_generation(result.relation_publication)
        except Exception:
            publication = result.relation_publication
            # Trigger: derived graph publication fails after accepted content committed.
            # Why: failing the response would strand file materialization even though the
            # canonical DB write succeeded; a later index pass can republish the same generation.
            # Outcome: preserve the accepted change and surface the repairable failure in logs.
            logger.exception(
                "Graph publication failed after accepted note commit; continuing "
                "materialization: entity_id={} generation={}",
                publication.entity_id if publication is not None else None,
                publication.generation if publication is not None else None,
            )
        return result.change

    @asynccontextmanager
    async def _mutation_cache_scope(
        self,
        project_external_id: str,
        *,
        invalidate_on_rejection: bool = False,
    ) -> AsyncIterator[None]:
        """Invalidate after a mutation can publish authoritative state."""
        read_cache = self.read_cache
        if read_cache is None:
            yield
            return

        try:
            yield
        except AcceptedNoteMutationRejected:
            if invalidate_on_rejection:
                await finish_project_read_cache_invalidation(
                    read_cache,
                    project_external_id,
                )
            raise
        except BaseException:
            # Transaction exit can raise after its commit reached the database.
            # Invalidation is safe after an earlier rollback and required after
            # any commit whose response was interrupted.
            await finish_project_read_cache_invalidation(
                read_cache,
                project_external_id,
            )
            raise
        else:
            await finish_project_read_cache_invalidation(
                read_cache,
                project_external_id,
            )

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
    ) -> bool:
        """Converge observed file state and report whether it may have published state."""
        if self.content_freshener is None:
            return False

        try:
            await self.content_freshener.freshen_note_content(
                project_external_id=project_external_id,
                entity_external_id=entity_external_id,
            )
        except BaseException:
            # Freshening can commit entity and note-content state before a later
            # indexing follow-up raises. Invalidate before propagating so those
            # partial publications cannot retain the previous cache generation.
            if self.read_cache is not None:
                await finish_project_read_cache_invalidation(
                    self.read_cache,
                    project_external_id,
                )
            raise
        return True

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
            async with self._mutation_cache_scope(project_external_id):
                async with accepted_note_transaction(self.session_maker) as session:
                    result = await run_accepted_note_create(
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
                accepted = await self._finish_mutation(result)
            return accepted
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
        base_checksum: str | None = None,
        actor_kind: str | None = None,
        actor_name: str | None = None,
    ) -> AcceptedNoteChange:
        """PUT a markdown note by creating or replacing accepted DB state.

        ``base_checksum`` is an optional optimistic-concurrency precondition: the
        db_checksum the caller last synced. When supplied, the update runner
        rejects the write with a structured 409 if the accepted checksum has
        moved, so the caller rebases instead of clobbering the newer write
        (issue #1445). It stays optional so callers without a synced base still
        write.
        """
        actor_context = self._resolve_actor(
            "update",
            user_profile_id=user_profile_id,
            source=source,
            actor_kind=actor_kind,
            actor_name=actor_name,
        )
        freshening_may_have_published = False
        try:
            freshening_may_have_published = await self.freshen_existing_note_content(
                project_external_id=project_external_id,
                entity_external_id=entity_external_id,
            )
            async with self._mutation_cache_scope(
                project_external_id,
                invalidate_on_rejection=freshening_may_have_published,
            ):
                async with accepted_note_transaction(self.session_maker) as session:
                    result = await run_accepted_note_update(
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
                            base_checksum=base_checksum,
                        ),
                        dependencies=self.mutation_dependencies,
                    )
                accepted = await self._finish_mutation(result)
        except AcceptedNoteMutationRejected as error:
            raise note_content_mutation_error_from_rejection(error.rejection) from error
        return accepted

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
        freshening_may_have_published = False
        try:
            freshening_may_have_published = await self.freshen_existing_note_content(
                project_external_id=project_external_id,
                entity_external_id=entity_external_id,
            )
            async with self._mutation_cache_scope(
                project_external_id,
                invalidate_on_rejection=freshening_may_have_published,
            ):
                async with accepted_note_transaction(self.session_maker) as session:
                    result = await run_accepted_note_edit(
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
                accepted = await self._finish_mutation(result)
        except AcceptedNoteMutationRejected as error:
            raise note_content_mutation_error_from_rejection(error.rejection) from error
        return accepted

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
        freshening_may_have_published = False
        try:
            freshening_may_have_published = await self.freshen_existing_note_content(
                project_external_id=project_external_id,
                entity_external_id=entity_external_id,
            )
            async with self._mutation_cache_scope(
                project_external_id,
                invalidate_on_rejection=freshening_may_have_published,
            ):
                async with accepted_note_transaction(self.session_maker) as session:
                    result = await run_accepted_note_move(
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
                accepted = await self._finish_mutation(result)
        except AcceptedNoteMutationRejected as error:
            raise note_content_mutation_error_from_rejection(error.rejection) from error
        return accepted

    async def delete_note(
        self,
        *,
        project_external_id: str,
        entity_external_id: str,
    ) -> AcceptedNoteChange:
        """DELETE the DB note and return the runtime follow-up change."""
        freshening_may_have_published = False
        try:
            freshening_may_have_published = await self.freshen_existing_note_content(
                project_external_id=project_external_id,
                entity_external_id=entity_external_id,
            )
            async with self._mutation_cache_scope(
                project_external_id,
                invalidate_on_rejection=freshening_may_have_published,
            ):
                async with accepted_note_transaction(self.session_maker) as session:
                    result = await run_accepted_note_delete(
                        session,
                        request=AcceptedNoteDeleteMutation(
                            project_external_id=project_external_id,
                            entity_external_id=entity_external_id,
                        ),
                        dependencies=self.mutation_dependencies,
                    )
                accepted = await self._finish_mutation(result)
        except AcceptedNoteMutationRejected as error:
            raise note_content_mutation_error_from_rejection(error.rejection) from error
        return accepted
