"""Portable orchestration for bounded relation resolution passes."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol

import logfire
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.indexing.accepted_note_search import accepted_search_content_from_markdown
from basic_memory.indexing.models import IndexFileJobStatus
from basic_memory.models import Entity
from basic_memory.repository.relation_repository import (
    PendingRelationSearchRefresh,
    ResolvedRelationWrite,
    ResolvedRelationWriteResult,
)

type EntityId = int
type AffectedEntityIds = set[EntityId]
RESOLVE_RELATIONS_DEBOUNCE_SECONDS = 10
RELATION_RESOLUTION_WRITE_BATCH_SIZE = 250


@dataclass(frozen=True, slots=True)
class ResolvedRelationWriteBatch:
    """Independently committable writes whose uniqueness domains remain intact."""

    writes: tuple[ResolvedRelationWrite, ...]


def plan_resolved_relation_write_batches(
    writes: Sequence[ResolvedRelationWrite],
    *,
    target_size: int = RELATION_RESOLUTION_WRITE_BATCH_SIZE,
) -> tuple[ResolvedRelationWriteBatch, ...]:
    """Pack writes without splitting a source/relation-type target collision domain.

    Resolved uniqueness includes ``from_id`` and ``relation_type``. Aliases that
    resolve to the same target must be planned together so one deterministic
    winner is chosen before any batch commits.
    """
    if target_size < 1:
        raise ValueError("Relation write batch target size must be positive")

    writes_by_collision_domain: dict[tuple[int, str], list[ResolvedRelationWrite]] = {}
    for write in sorted(writes, key=lambda item: item.relation_id):
        collision_domain = (write.from_id, write.relation_type)
        writes_by_collision_domain.setdefault(collision_domain, []).append(write)

    batches: list[ResolvedRelationWriteBatch] = []
    pending_batch: list[ResolvedRelationWrite] = []
    for collision_domain_writes in writes_by_collision_domain.values():
        if pending_batch and len(pending_batch) + len(collision_domain_writes) > target_size:
            batches.append(ResolvedRelationWriteBatch(tuple(pending_batch)))
            pending_batch = []
        pending_batch.extend(collision_domain_writes)

    if pending_batch:
        batches.append(ResolvedRelationWriteBatch(tuple(pending_batch)))
    return tuple(batches)


class RelationResolutionRuntime(Protocol):
    """Capability that owns relation resolution for one project."""

    async def resolve_relations(self) -> AffectedEntityIds:
        """Resolve currently visible relations and return affected source entity IDs."""

    async def count_unresolved_relations(self) -> int:
        """Return the current unresolved relation count."""


class UnresolvedRelation(Protocol):
    """Unresolved relation fields required by the resolver."""

    @property
    def id(self) -> int: ...

    @property
    def from_id(self) -> int: ...

    @property
    def generation(self) -> int: ...

    @property
    def to_name(self) -> str: ...

    @property
    def relation_type(self) -> str: ...


class ResolvedRelationTarget(Protocol):
    """Entity fields needed to complete an unresolved relation."""

    @property
    def id(self) -> int: ...

    @property
    def external_id(self) -> str: ...

    @property
    def title(self) -> str: ...


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

    async def apply_resolved_targets(
        self,
        session: AsyncSession,
        writes: Sequence[ResolvedRelationWrite],
    ) -> ResolvedRelationWriteResult:
        """Apply canonical targets and remove duplicate edges as one batch."""

    async def list_pending_search_refreshes(
        self,
        session: AsyncSession,
        *,
        entity_id: EntityId | None = None,
    ) -> Sequence[PendingRelationSearchRefresh]:
        """Return durable relation-search refresh work."""

    async def clear_pending_search_refreshes(
        self,
        session: AsyncSession,
        refresh_ids: Sequence[int],
    ) -> None:
        """Retire successfully completed relation-search refresh work."""


class RelationResolutionEntityRepository(Protocol):
    """Repository capability for refreshing affected source entities."""

    async def find_by_id(self, session: AsyncSession, entity_id: EntityId) -> Entity | None:
        """Return one source entity by database id."""


class BatchRelationResolutionEntityRepository(Protocol):
    """Repository capability for loading an affected source-entity batch."""

    async def find_by_ids(
        self,
        session: AsyncSession,
        ids: list[EntityId],
    ) -> Sequence[Entity]:
        """Return source entities by database id."""


class RelationResolutionNoteContent(Protocol):
    """Accepted note-content fields needed for a state-aware search refresh."""

    @property
    def entity_id(self) -> EntityId: ...

    @property
    def markdown_content(self) -> str: ...

    @property
    def file_write_status(self) -> str: ...


class BatchRelationResolutionNoteContentRepository(Protocol):
    """Repository capability for loading accepted content for affected sources."""

    async def find_by_ids(
        self,
        session: AsyncSession,
        ids: list[EntityId],
    ) -> Sequence[RelationResolutionNoteContent]:
        """Return accepted note content by source entity id."""


class RelationTargetBatchResolver(Protocol):
    """Capability for resolving one project-wide batch of relation targets."""

    async def resolve_relation_targets(
        self,
        link_texts: Sequence[str],
        *,
        session: AsyncSession,
    ) -> Mapping[str, ResolvedRelationTarget | None]:
        """Resolve strict link targets without per-target database round-trips."""


class RelationResolutionEntityIndexer(Protocol):
    """Capability for refreshing derived search rows after relation updates."""

    async def index_entity(self, entity: Entity) -> None:
        """Refresh derived index rows for one entity."""


class BatchRelationResolutionEntityIndexer(Protocol):
    """Capability for refreshing a batch of derived entity search rows."""

    async def index_entities(
        self,
        entities: Sequence[Entity],
        *,
        content_by_entity_id: Mapping[EntityId, str],
    ) -> RelationSearchRefreshResult | None:
        """Refresh derived rows, optionally reporting bounded per-entity outcomes.

        ``None`` remains the complete-success result for legacy extension adapters.
        """


@dataclass(frozen=True, slots=True)
class RelationSearchRefreshResult:
    """Bounded per-entity outcomes from a relation-driven search refresh."""

    missing_content_entity_ids: frozenset[EntityId] = frozenset()


def accepted_search_content_for_pending_notes(
    note_contents: Sequence[RelationResolutionNoteContent],
) -> dict[EntityId, str]:
    """Return accepted search content while Markdown projections are not synchronized."""
    return {
        note_content.entity_id: accepted_search_content_from_markdown(note_content.markdown_content)
        for note_content in note_contents
        if note_content.file_write_status != "synced"
    }


@dataclass(frozen=True, slots=True)
class RepositoryRelationResolutionRuntime:
    """Resolve forward references with project-scoped repositories and services."""

    session_maker: async_sessionmaker[AsyncSession]
    relation_repository: RelationResolutionRelationRepository
    entity_repository: BatchRelationResolutionEntityRepository
    note_content_repository: BatchRelationResolutionNoteContentRepository
    target_resolver: RelationTargetBatchResolver
    entity_indexer: BatchRelationResolutionEntityIndexer

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

            target_names = list(
                dict.fromkeys(relation.to_name for relation in unresolved_relations)
            )
            resolved_targets_by_link_text = (
                await self.target_resolver.resolve_relation_targets(
                    target_names,
                    session=session,
                )
                if target_names
                else {}
            )

        writes: list[ResolvedRelationWrite] = []
        for relation in unresolved_relations:
            logger.trace(
                "Attempting to resolve relation "
                f"relation_id={relation.id} "
                f"from_id={relation.from_id} "
                f"to_name={relation.to_name}"
            )
            resolved_entity = resolved_targets_by_link_text[relation.to_name]
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
            writes.append(
                ResolvedRelationWrite(
                    relation_id=relation.id,
                    from_id=relation.from_id,
                    generation=relation.generation,
                    original_target_name=relation.to_name,
                    target_id=resolved_entity.id,
                    target_external_id=resolved_entity.external_id,
                    relation_type=relation.relation_type,
                )
            )

        duplicate_relation_ids: list[int] = []
        stale_relation_ids: list[int] = []
        for write_batch in plan_resolved_relation_write_batches(writes):
            async with db.scoped_session(self.session_maker) as session:
                write_result = await self.relation_repository.apply_resolved_targets(
                    session,
                    write_batch.writes,
                )
                duplicate_relation_ids.extend(write_result.duplicate_relation_ids)
                stale_relation_ids.extend(write_result.stale_relation_ids)

        if duplicate_relation_ids:
            with logfire.span(
                "indexing.relation.resolve_conflicts",
                relation_ids=duplicate_relation_ids,
                conflict_count=len(duplicate_relation_ids),
            ):
                logger.debug(
                    "Removed redundant unresolved relations",
                    relation_ids=duplicate_relation_ids,
                )

        if stale_relation_ids:
            with logfire.span(
                "indexing.relation.skip_stale_writes",
                relation_ids=stale_relation_ids,
                stale_count=len(stale_relation_ids),
            ):
                logger.debug(
                    "Skipped relation writes whose source or target identity changed",
                    relation_ids=stale_relation_ids,
                )

        async with db.scoped_session(self.session_maker) as session:
            pending_refreshes = await self.relation_repository.list_pending_search_refreshes(
                session,
                entity_id=entity_id,
            )
            affected_entity_ids: AffectedEntityIds = {
                refresh.entity_id for refresh in pending_refreshes
            }
            source_entities: Sequence[Entity] = ()
            note_contents: Sequence[RelationResolutionNoteContent] = ()
            if affected_entity_ids:
                sorted_affected_entity_ids = sorted(affected_entity_ids)
                source_entities = await self.entity_repository.find_by_ids(
                    session,
                    sorted_affected_entity_ids,
                )
                note_contents = await self.note_content_repository.find_by_ids(
                    session,
                    sorted_affected_entity_ids,
                )

        if affected_entity_ids:
            # Trigger: an accepted note has not reached a synchronized Markdown projection.
            # Why: relation repair must refresh its search row without racing the async file
            # materializer, while synchronized notes still need missing files to surface.
            # Outcome: pending sources use accepted DB content; all others retain disk fallback.
            content_by_entity_id = accepted_search_content_for_pending_notes(note_contents)
            refresh_result = await self.entity_indexer.index_entities(
                sorted(source_entities, key=lambda entity: entity.id),
                content_by_entity_id=content_by_entity_id,
            )
            if refresh_result is not None and refresh_result.missing_content_entity_ids:
                # A synchronized entity whose backing object is gone cannot be
                # repaired by retrying relation resolution. Orphan cleanup owns
                # DB/storage convergence; retire this observed refresh marker.
                logger.warning(
                    "Skipped relation search refresh for missing entity content",
                    entity_ids=sorted(refresh_result.missing_content_entity_ids),
                    entity_count=len(refresh_result.missing_content_entity_ids),
                )
            async with db.scoped_session(self.session_maker) as session:
                await self.relation_repository.clear_pending_search_refreshes(
                    session,
                    [refresh.id for refresh in pending_refreshes],
                )

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
    """Project-index completion facts needed to queue relation resolution.

    The wide identity types are deliberate: downstream runtimes rebuild this
    context from legacy workflow metadata, where project_id may arrive as a
    string and either field may be missing. Planning coerces or skips instead
    of pushing malformed identity into a queue request.
    """

    project_id: int | str | None
    project_path: str | None


@dataclass(frozen=True, slots=True)
class IndexFileRelationResolutionContext:
    """Index-file facts needed to decide whether relation resolution should run."""

    project_id: int
    project_path: str
    status: IndexFileJobStatus


def plan_project_index_completion_relation_resolution(
    context: ProjectIndexRelationResolutionContext,
) -> ResolveRelationsJobRequest | None:
    """Plan the final relation-resolution job for a completed project index."""
    if context.project_id is None or context.project_path is None:
        return None
    return ResolveRelationsJobRequest(
        project_id=int(context.project_id),
        project_path=context.project_path,
    )


async def resolve_project_index_completion_relations(
    context: ProjectIndexRelationResolutionContext,
    runtime: RelationResolutionRuntime,
    *,
    max_passes: int = 3,
) -> ResolveRelationsResult | None:
    """Run the final relation-resolution pass for a completed project index.

    The context names the project so queue-based runtimes can plan an enqueue
    from the same completion facts; the inline path resolves directly against
    the already project-scoped runtime. A context without complete project
    identity plans no request, so the resolution pass is skipped.
    """
    request = plan_project_index_completion_relation_resolution(context)
    if request is None:
        return None
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
