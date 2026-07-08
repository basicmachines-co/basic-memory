"""Portable planning for deferred forward-reference relation updates."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import case, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.indexing.link_resolution import LinkText, resolve_project_link_texts
from basic_memory.models import Entity, Relation

type ForwardReferenceEntityId = int
type ForwardReferenceRelationId = int


class UnresolvedForwardReference(Protocol):
    """Minimal unresolved relation shape needed for exact target planning."""

    @property
    def id(self) -> ForwardReferenceRelationId:
        """Return the unresolved relation primary key."""

    @property
    def from_id(self) -> ForwardReferenceEntityId:
        """Return the source entity id for the unresolved relation."""

    @property
    def to_name(self) -> LinkText | None:
        """Return the unresolved target link text."""


class ForwardReferenceRelationSource(Protocol):
    """Capability that lists unresolved forward-reference relations."""

    async def list_unresolved_forward_references(
        self,
    ) -> tuple[UnresolvedForwardReference, ...]:
        """Return unresolved relation rows for one project."""


class ForwardReferenceEntityRefreshRuntime(Protocol):
    """Capability that refreshes search rows for one forward-reference target."""

    async def refresh_forward_reference_entity(
        self,
        entity_id: ForwardReferenceEntityId,
    ) -> bool:
        """Refresh one entity and return whether the entity still exists."""


class ForwardReferenceEntityRepository(Protocol):
    """Repository capability required to load forward-reference target entities."""

    async def find_by_id(
        self,
        session: AsyncSession,
        entity_id: ForwardReferenceEntityId,
    ) -> Entity | None:
        """Return one entity by id."""


class ForwardReferenceEntityIndexer(Protocol):
    """Search capability required to refresh one forward-reference target entity."""

    async def index_entity(self, entity: Entity) -> object:
        """Refresh one entity in the search index."""


@dataclass(frozen=True, slots=True)
class ForwardReferenceUpdate:
    """One unresolved relation that can be filled with an exact target entity."""

    relation_id: ForwardReferenceRelationId
    source_entity_id: ForwardReferenceEntityId
    target_entity_id: ForwardReferenceEntityId
    link_text: LinkText


@dataclass(frozen=True, slots=True)
class ForwardReferenceResolutionPlan:
    """Planned bulk updates and search refresh targets for forward references."""

    unresolved_before: int
    link_texts: tuple[LinkText, ...]
    updates: tuple[ForwardReferenceUpdate, ...]
    entity_ids_to_refresh: frozenset[ForwardReferenceEntityId]

    @property
    def resolved_count(self) -> int:
        """Return how many relation rows can be updated."""
        return len(self.updates)

    @property
    def remaining_count(self) -> int:
        """Return how many initially unresolved rows remain unresolved."""
        return max(0, self.unresolved_before - self.resolved_count)

    @property
    def has_updates(self) -> bool:
        """Return whether the executor has any relation rows to update."""
        return bool(self.updates)


class ForwardReferenceResolutionRuntime(Protocol):
    """Capability that resolves link text and applies exact relation updates."""

    async def resolve_forward_reference_link_texts(
        self,
        link_texts: Sequence[LinkText],
    ) -> Mapping[LinkText, ForwardReferenceEntityId | None]:
        """Resolve link texts to exact target entity ids."""

    async def apply_forward_reference_updates(
        self,
        updates: Sequence[ForwardReferenceUpdate],
    ) -> None:
        """Persist exact relation target updates."""


@dataclass(frozen=True, slots=True)
class RepositoryForwardReferenceRelationSource:
    """Load unresolved forward-reference relations with explicit sessions."""

    session_maker: async_sessionmaker[AsyncSession]
    project_id: int

    async def list_unresolved_forward_references(
        self,
    ) -> tuple[UnresolvedForwardReference, ...]:
        async with db.scoped_session(self.session_maker) as session:
            result = await session.execute(
                select(Relation).where(
                    Relation.project_id == self.project_id,
                    Relation.to_id.is_(None),
                )
            )
            return tuple(result.scalars().all())


@dataclass(frozen=True, slots=True)
class RepositoryForwardReferenceResolutionRuntime:
    """Resolve link text and persist exact relation targets with explicit sessions."""

    session_maker: async_sessionmaker[AsyncSession]
    project_id: int

    async def resolve_forward_reference_link_texts(
        self,
        link_texts: Sequence[LinkText],
    ) -> Mapping[LinkText, ForwardReferenceEntityId | None]:
        return await resolve_project_link_texts(
            link_texts,
            session_maker=self.session_maker,
            project_id=self.project_id,
        )

    async def apply_forward_reference_updates(
        self,
        updates: Sequence[ForwardReferenceUpdate],
    ) -> None:
        if not updates:
            return

        relation_ids = [update.relation_id for update in updates]
        target_entity_ids_by_relation_id = {
            update.relation_id: update.target_entity_id for update in updates
        }

        async with db.scoped_session(self.session_maker) as session:
            stmt = (
                update(Relation)
                .where(Relation.id.in_(relation_ids))
                .values(to_id=case(target_entity_ids_by_relation_id, value=Relation.id))
            )
            await session.execute(stmt)


@dataclass(frozen=True, slots=True)
class RepositoryForwardReferenceEntityRefreshRuntime:
    """Refresh forward-reference target entity search rows with explicit sessions."""

    session_maker: async_sessionmaker[AsyncSession]
    entity_repository: ForwardReferenceEntityRepository
    entity_indexer: ForwardReferenceEntityIndexer

    async def refresh_forward_reference_entity(
        self,
        entity_id: ForwardReferenceEntityId,
    ) -> bool:
        async with db.scoped_session(self.session_maker) as session:
            entity = await self.entity_repository.find_by_id(session, entity_id)
        if entity is None:
            return False
        await self.entity_indexer.index_entity(entity)
        return True


@dataclass(frozen=True, slots=True)
class ForwardReferenceEntityRefreshFailure:
    """One target entity whose search refresh raised."""

    entity_id: ForwardReferenceEntityId
    error: Exception


@dataclass(frozen=True, slots=True)
class ForwardReferenceEntityRefreshRun:
    """Search refresh results for target entities touched by forward refs."""

    successful_entity_ids: frozenset[ForwardReferenceEntityId]
    missing_entity_ids: frozenset[ForwardReferenceEntityId]
    failures: tuple[ForwardReferenceEntityRefreshFailure, ...]

    @property
    def failed_entity_ids(self) -> frozenset[ForwardReferenceEntityId]:
        """Return entity ids whose refresh raised."""
        return frozenset(failure.entity_id for failure in self.failures)


@dataclass(frozen=True, slots=True)
class ForwardReferenceResolutionRun:
    """Applied forward-reference updates and follow-up search refresh targets."""

    plan: ForwardReferenceResolutionPlan
    resolved_link_text_count: int

    @property
    def unresolved_before(self) -> int:
        """Return how many unresolved rows were considered."""
        return self.plan.unresolved_before

    @property
    def link_texts(self) -> tuple[LinkText, ...]:
        """Return unique link texts considered by the run."""
        return self.plan.link_texts

    @property
    def resolved_count(self) -> int:
        """Return how many relation rows were updated."""
        return self.plan.resolved_count

    @property
    def remaining_count(self) -> int:
        """Return how many initially unresolved rows remain unresolved."""
        return self.plan.remaining_count

    @property
    def entity_ids_to_refresh(self) -> frozenset[ForwardReferenceEntityId]:
        """Return exact target entity ids whose search rows should be refreshed."""
        return self.plan.entity_ids_to_refresh


def collect_forward_reference_link_texts(
    unresolved_relations: Sequence[UnresolvedForwardReference],
) -> tuple[LinkText, ...]:
    """Collect unique unresolved link texts in first-seen order."""
    link_texts: dict[LinkText, None] = {}
    for relation in unresolved_relations:
        if relation.to_name:
            link_texts.setdefault(relation.to_name, None)
    return tuple(link_texts)


def plan_forward_reference_resolution(
    unresolved_relations: Sequence[UnresolvedForwardReference],
    resolved_targets: Mapping[LinkText, ForwardReferenceEntityId | None],
) -> ForwardReferenceResolutionPlan:
    """Plan exact target updates for a batch of unresolved relation rows."""
    updates: list[ForwardReferenceUpdate] = []
    entity_ids_to_refresh: set[ForwardReferenceEntityId] = set()

    for relation in unresolved_relations:
        link_text = relation.to_name
        if not link_text:
            continue

        target_entity_id = resolved_targets.get(link_text)
        if target_entity_id is None or target_entity_id == relation.from_id:
            continue

        updates.append(
            ForwardReferenceUpdate(
                relation_id=relation.id,
                source_entity_id=relation.from_id,
                target_entity_id=target_entity_id,
                link_text=link_text,
            )
        )
        entity_ids_to_refresh.add(target_entity_id)

    return ForwardReferenceResolutionPlan(
        unresolved_before=len(unresolved_relations),
        link_texts=collect_forward_reference_link_texts(unresolved_relations),
        updates=tuple(updates),
        entity_ids_to_refresh=frozenset(entity_ids_to_refresh),
    )


async def run_forward_reference_resolution(
    runtime: ForwardReferenceResolutionRuntime,
    unresolved_relations: Sequence[UnresolvedForwardReference],
) -> ForwardReferenceResolutionRun:
    """Resolve link texts, apply exact relation updates, and return refresh targets."""
    link_texts = collect_forward_reference_link_texts(unresolved_relations)
    resolved_targets = (
        await runtime.resolve_forward_reference_link_texts(link_texts) if link_texts else {}
    )
    plan = plan_forward_reference_resolution(unresolved_relations, resolved_targets)
    if plan.has_updates:
        await runtime.apply_forward_reference_updates(plan.updates)

    return ForwardReferenceResolutionRun(
        plan=plan,
        resolved_link_text_count=sum(
            1 for link_text in link_texts if resolved_targets.get(link_text) is not None
        ),
    )


async def run_forward_reference_entity_refresh(
    runtime: ForwardReferenceEntityRefreshRuntime,
    entity_ids: Iterable[ForwardReferenceEntityId],
) -> ForwardReferenceEntityRefreshRun:
    """Refresh forward-reference target search rows and report per-entity failures."""
    successful_entity_ids: set[ForwardReferenceEntityId] = set()
    missing_entity_ids: set[ForwardReferenceEntityId] = set()
    failures: list[ForwardReferenceEntityRefreshFailure] = []

    for entity_id in entity_ids:
        try:
            if await runtime.refresh_forward_reference_entity(entity_id):
                successful_entity_ids.add(entity_id)
            else:
                missing_entity_ids.add(entity_id)
        except Exception as exc:
            failures.append(
                ForwardReferenceEntityRefreshFailure(
                    entity_id=entity_id,
                    error=exc,
                )
            )

    return ForwardReferenceEntityRefreshRun(
        successful_entity_ids=frozenset(successful_entity_ids),
        missing_entity_ids=frozenset(missing_entity_ids),
        failures=tuple(failures),
    )
