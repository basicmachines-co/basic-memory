"""Portable orchestration for bounded relation resolution passes."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol
from uuid import UUID

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.indexing.models import IndexFileJobStatus
from basic_memory.repository import RelationRepository

type EntityId = int
type AffectedEntityIds = set[EntityId]
RESOLVE_RELATIONS_DEBOUNCE_SECONDS = 10


class RelationResolutionPass(Protocol):
    """Capability that performs one relation-resolution pass."""

    async def resolve_relations(self) -> AffectedEntityIds:
        """Resolve currently visible relations and return affected source entity IDs."""


class UnresolvedRelationCounter(Protocol):
    """Capability that counts currently unresolved relations."""

    async def count_unresolved_relations(self) -> int:
        """Return the current unresolved relation count."""


class SyncServiceRelationResolver(Protocol):
    """Minimal SyncService shape needed to resolve one project's relations."""

    relation_repository: RelationRepository
    session_maker: async_sessionmaker[AsyncSession]

    async def resolve_relations(self) -> AffectedEntityIds:
        """Resolve currently visible relations and return affected source entity IDs."""


@dataclass(frozen=True, slots=True)
class SyncServiceRelationResolutionAdapter:
    """Expose project-scoped sync relation operations as runtime capabilities."""

    sync_service: SyncServiceRelationResolver

    async def resolve_relations(self) -> AffectedEntityIds:
        """Run one relation-resolution pass through the project-scoped sync service."""
        return await self.sync_service.resolve_relations()

    async def count_unresolved_relations(self) -> int:
        """Count unresolved relations through the same project-scoped repository."""
        relation_repository = self.sync_service.relation_repository
        async with db.scoped_session(self.sync_service.session_maker) as session:
            return len(await relation_repository.find_unresolved_relations(session))


@dataclass(frozen=True, slots=True)
class ResolveRelationsJobRequest:
    """Queue-neutral request shape for resolving one project's forward references."""

    tenant_id: UUID
    project_id: int
    project_path: str
    debounce_seconds: int = RESOLVE_RELATIONS_DEBOUNCE_SECONDS

    @property
    def execute_after(self) -> timedelta:
        """Return the coalescing delay for relation-resolution work."""
        return timedelta(seconds=self.debounce_seconds)

    def dedupe_key(self) -> str:
        """Return the per-(tenant, project) relation-resolution queue identity."""
        return f"resolve-relations:{self.tenant_id}:{self.project_id}"

    def routing_headers(self, headers: Mapping[str, str] | None = None) -> dict[str, str]:
        """Return queue routing headers for the relation-resolution job."""
        routing_headers = dict(headers or {})
        routing_headers.update(
            {
                "tenant_id": str(self.tenant_id),
                "project_id": str(self.project_id),
            }
        )
        return routing_headers


@dataclass(frozen=True, slots=True)
class ProjectIndexRelationResolutionContext:
    """Project-index completion facts needed to queue relation resolution."""

    tenant_id: UUID
    project_id: int | str | None
    project_path: str | None


@dataclass(frozen=True, slots=True)
class IndexFileRelationResolutionContext:
    """Index-file facts needed to decide whether relation resolution should run."""

    tenant_id: UUID
    project_id: int
    project_path: str
    workflow_id: UUID | None
    status: IndexFileJobStatus


def plan_project_index_completion_relation_resolution(
    context: ProjectIndexRelationResolutionContext,
) -> ResolveRelationsJobRequest | None:
    """Plan the final relation-resolution job for a completed project index."""
    if context.project_id is None or context.project_path is None:
        return None
    return ResolveRelationsJobRequest(
        tenant_id=context.tenant_id,
        project_id=int(context.project_id),
        project_path=context.project_path,
    )


def plan_index_file_relation_resolution(
    context: IndexFileRelationResolutionContext,
) -> ResolveRelationsJobRequest | None:
    """Plan relation-resolution work after one incremental file index."""
    if context.workflow_id is not None:
        return None
    if context.status != IndexFileJobStatus.processed:
        return None
    return ResolveRelationsJobRequest(
        tenant_id=context.tenant_id,
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


async def resolve_relations_until_stable(
    *,
    resolver: RelationResolutionPass,
    unresolved_counter: UnresolvedRelationCounter,
    max_passes: int = 3,
) -> ResolveRelationsResult:
    """Resolve all relations visible to the supplied capabilities.

    The loop deliberately runs one confirming pass after a productive pass. This
    lets queue workers catch writes that committed while the first pass was still
    running, while the pass cap keeps a noisy resolver from looping forever.
    """
    unresolved_before = await unresolved_counter.count_unresolved_relations()
    affected_entities: AffectedEntityIds = set()
    passes = 0

    while passes < max_passes:
        affected = await resolver.resolve_relations()
        passes += 1
        affected_entities |= affected

        if not affected:
            break

    remaining = await unresolved_counter.count_unresolved_relations()
    return ResolveRelationsResult(
        unresolved_before=unresolved_before,
        remaining=remaining,
        passes=passes,
        affected_entities=len(affected_entities),
    )


async def resolve_project_relations(
    sync_service: SyncServiceRelationResolver,
    *,
    max_passes: int = 3,
) -> ResolveRelationsResult:
    """Resolve all resolvable forward references for one project-scoped sync service.

    ``SyncService.resolve_relations`` resolves every relation that is unresolved
    at the moment it reads the table. Queued runtimes can coalesce concurrent
    writes onto an in-flight resolve job, so run until one pass changes nothing
    or the pass cap is reached. Relations left after a stable pass are genuine
    forward references and remain unresolved until their target note exists.
    """
    adapter = SyncServiceRelationResolutionAdapter(sync_service)
    result = await resolve_relations_until_stable(
        resolver=adapter,
        unresolved_counter=adapter,
        max_passes=max_passes,
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
