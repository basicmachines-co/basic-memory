"""Portable orchestration for bounded relation resolution passes."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol

import logfire
from loguru import logger
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.indexing.models import IndexFileJobStatus
from basic_memory.models import Entity, Relation

type EntityId = int
type AffectedEntityIds = set[EntityId]
RESOLVE_RELATIONS_DEBOUNCE_SECONDS = 10


class RelationResolutionRuntime(Protocol):
    """Capability that owns relation resolution for one project."""

    async def resolve_relations(self) -> AffectedEntityIds:
        """Resolve currently visible relations and return affected source entity IDs."""

    async def count_unresolved_relations(self) -> int:
        """Return the current unresolved relation count."""


class UnresolvedRelation(Protocol):
    """Unresolved relation fields required by the resolver."""

    id: int
    from_id: int
    to_name: str
    relation_type: str


class ResolvedRelationTarget(Protocol):
    """Entity fields needed to complete an unresolved relation."""

    id: int
    title: str


class RelationResolutionRelationRepository(Protocol):
    """Repository capability for unresolved relation reads and updates."""

    async def find_unresolved_relations(
        self,
        session: AsyncSession,
    ) -> Sequence[UnresolvedRelation]:
        """Return unresolved relations currently visible in the project."""

    async def find_unresolved_relations_for_entity(
        self,
        session: AsyncSession,
        entity_id: EntityId,
    ) -> Sequence[UnresolvedRelation]:
        """Return unresolved relations for one source entity."""

    # Positional-only parameters: the concrete implementation is the generic
    # model repository, whose parameters are named for entities. `/` lets this
    # contract name the relation id honestly without renaming the shared
    # repository method.
    async def update(
        self,
        session: AsyncSession,
        relation_id: int,
        resolved_target_fields: dict[str, int | str],
        /,
    ) -> Relation | None:
        """Apply resolved target fields (to_id, to_name) to one relation row."""

    async def delete(self, session: AsyncSession, relation_id: int, /) -> bool:
        """Delete one redundant unresolved relation row."""


class RelationResolutionEntityRepository(Protocol):
    """Repository capability for refreshing affected source entities."""

    async def find_by_id(self, session: AsyncSession, entity_id: EntityId) -> Entity | None:
        """Return one source entity by database id."""


class RelationResolutionLinkResolver(Protocol):
    """Capability for resolving a relation target by link text."""

    async def resolve_link(
        self,
        link_text: str,
        *,
        strict: bool,
        session: AsyncSession,
    ) -> ResolvedRelationTarget | None:
        """Resolve a link text to an entity target."""


class RelationResolutionEntityIndexer(Protocol):
    """Capability for refreshing derived search rows after relation updates."""

    async def index_entity(self, entity: Entity) -> None:
        """Refresh derived index rows for one entity."""


@dataclass(frozen=True, slots=True)
class RepositoryRelationResolutionRuntime:
    """Resolve forward references with project-scoped repositories and services."""

    session_maker: async_sessionmaker[AsyncSession]
    relation_repository: RelationResolutionRelationRepository
    entity_repository: RelationResolutionEntityRepository
    link_resolver: RelationResolutionLinkResolver
    entity_indexer: RelationResolutionEntityIndexer

    async def count_unresolved_relations(self) -> int:
        """Return the current unresolved relation count for this project."""
        async with db.scoped_session(self.session_maker) as session:
            return len(await self.relation_repository.find_unresolved_relations(session))

    async def resolve_relations(
        self,
        entity_id: EntityId | None = None,
    ) -> AffectedEntityIds:
        """Resolve visible forward references and refresh affected entities."""
        async with db.scoped_session(self.session_maker) as session:
            if entity_id is None:
                unresolved_relations = await self.relation_repository.find_unresolved_relations(
                    session
                )
                logger.info("Resolving all forward references", count=len(unresolved_relations))
            else:
                unresolved_relations = (
                    await self.relation_repository.find_unresolved_relations_for_entity(
                        session,
                        entity_id,
                    )
                )
                logger.info(
                    f"Resolving forward references for entity {entity_id}",
                    count=len(unresolved_relations),
                )

        affected_entity_ids: AffectedEntityIds = set()

        for relation in unresolved_relations:
            logger.trace(
                "Attempting to resolve relation "
                f"relation_id={relation.id} "
                f"from_id={relation.from_id} "
                f"to_name={relation.to_name}"
            )
            async with db.scoped_session(self.session_maker) as session:
                resolved_entity = await self.link_resolver.resolve_link(
                    relation.to_name,
                    strict=True,
                    session=session,
                )

            if resolved_entity is None or resolved_entity.id == relation.from_id:
                continue

            logger.debug(
                "Resolved forward reference "
                f"relation_id={relation.id} "
                f"from_id={relation.from_id} "
                f"to_name={relation.to_name} "
                f"resolved_id={resolved_entity.id} "
                f"resolved_title={resolved_entity.title}",
            )
            try:
                async with db.scoped_session(self.session_maker) as session:
                    await self.relation_repository.update(
                        session,
                        relation.id,
                        {
                            "to_id": resolved_entity.id,
                            "to_name": resolved_entity.title,
                        },
                    )
            except IntegrityError:
                with logfire.span(
                    "indexing.relation.resolve_conflict",
                    relation_id=relation.id,
                    relation_type=relation.relation_type,
                ):
                    # Another resolved row already represents this edge. Remove
                    # the redundant unresolved row so future passes do not keep
                    # retrying the same conflict.
                    async with db.scoped_session(self.session_maker) as session:
                        await self.relation_repository.delete(session, relation.id)
            affected_entity_ids.add(relation.from_id)

        for affected_entity_id in sorted(affected_entity_ids):
            async with db.scoped_session(self.session_maker) as session:
                source_entity = await self.entity_repository.find_by_id(
                    session,
                    affected_entity_id,
                )
            if source_entity is not None:
                await self.entity_indexer.index_entity(source_entity)

        return affected_entity_ids


@dataclass(frozen=True, slots=True)
class ResolveRelationsJobRequest:
    """Queue-neutral request shape for resolving one project's forward references."""

    project_id: int
    project_path: str
    debounce_seconds: int = RESOLVE_RELATIONS_DEBOUNCE_SECONDS

    @property
    def execute_after(self) -> timedelta:
        """Return the coalescing delay for relation-resolution work."""
        return timedelta(seconds=self.debounce_seconds)

    def dedupe_key(self) -> str:
        """Return the per-project relation-resolution queue identity."""
        return f"resolve-relations:{self.project_id}"

    def routing_headers(self, headers: Mapping[str, str] | None = None) -> dict[str, str]:
        """Return queue routing headers for the relation-resolution job."""
        routing_headers = dict(headers or {})
        routing_headers["project_id"] = str(self.project_id)
        return routing_headers


@dataclass(frozen=True, slots=True)
class ProjectIndexRelationResolutionContext:
    """Project-index completion facts needed to queue relation resolution."""

    project_id: int
    project_path: str


@dataclass(frozen=True, slots=True)
class IndexFileRelationResolutionContext:
    """Index-file facts needed to decide whether relation resolution should run."""

    project_id: int
    project_path: str
    status: IndexFileJobStatus


def plan_project_index_completion_relation_resolution(
    context: ProjectIndexRelationResolutionContext,
) -> ResolveRelationsJobRequest:
    """Plan the final relation-resolution job for a completed project index."""
    return ResolveRelationsJobRequest(
        project_id=context.project_id,
        project_path=context.project_path,
    )


async def resolve_project_index_completion_relations(
    context: ProjectIndexRelationResolutionContext,
    runtime: RelationResolutionRuntime,
    *,
    max_passes: int = 3,
) -> ResolveRelationsResult:
    """Run the final relation-resolution pass for a completed project index.

    The context names the project so queue-based runtimes can plan an enqueue
    from the same completion facts; the inline path resolves directly against
    the already project-scoped runtime.
    """
    return await resolve_project_relations(runtime, max_passes=max_passes)


def plan_index_file_relation_resolution(
    context: IndexFileRelationResolutionContext,
) -> ResolveRelationsJobRequest | None:
    """Plan relation-resolution work after one incremental file index.

    Workflow-scoped bulk indexing must not call this per file — the coordinator
    runs one resolution pass at completion instead. Runtimes that track workflow
    membership (cloud) apply that gate before building this context.
    """
    if context.status != IndexFileJobStatus.processed:
        return None
    return ResolveRelationsJobRequest(
        project_id=context.project_id,
        project_path=context.project_path,
    )


@dataclass(frozen=True, slots=True)
class ResolveRelationsResult:
    """Outcome of one project-scoped resolution run."""

    unresolved_before: int
    remaining: int
    passes: int
    affected_entities: int

    @property
    def resolved(self) -> int:
        """Relations linked during this run (approximate under concurrent writes)."""
        return max(0, self.unresolved_before - self.remaining)


async def resolve_project_relations(
    runtime: RelationResolutionRuntime,
    *,
    max_passes: int = 3,
) -> ResolveRelationsResult:
    """Resolve all resolvable forward references for one project runtime.

    One pass resolves every relation that is unresolved at the moment it reads
    the table, and the loop deliberately runs one confirming pass after a
    productive pass. Queued runtimes can coalesce concurrent writes onto an
    in-flight resolve job, so the confirming pass catches writes that committed
    while the first pass was still running, while the pass cap keeps a noisy
    resolver from looping forever. Relations left after a stable pass are
    genuine forward references and remain unresolved until their target note
    exists.
    """
    unresolved_before = await runtime.count_unresolved_relations()
    affected_entities: AffectedEntityIds = set()
    passes = 0

    while passes < max_passes:
        affected = await runtime.resolve_relations()
        passes += 1
        affected_entities |= affected

        if not affected:
            break

    result = ResolveRelationsResult(
        unresolved_before=unresolved_before,
        remaining=await runtime.count_unresolved_relations(),
        passes=passes,
        affected_entities=len(affected_entities),
    )
    logger.info(
        "Resolved project relations",
        unresolved_before=result.unresolved_before,
        resolved=result.resolved,
        remaining=result.remaining,
        passes=result.passes,
        affected_entities=result.affected_entities,
    )
    return result
