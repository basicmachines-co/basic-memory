"""Repository for managing Relation objects."""

from dataclasses import dataclass
from itertools import batched
from typing import override, Sequence, List, Optional

from sqlalchemy import (
    Integer,
    String,
    Text,
    and_,
    case,
    delete,
    exists,
    literal,
    or_,
    select,
    tuple_,
    union_all,
    update,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload, aliased
from sqlalchemy.orm.attributes import InstrumentedAttribute
from sqlalchemy.orm.interfaces import LoaderOption
from sqlalchemy.sql import Select
from sqlalchemy.sql.elements import ColumnElement

from basic_memory.models import Entity, NoteContent, Observation, Relation, RelationSearchRefresh
from basic_memory.repository.repository import Repository


RESOLVED_RELATION_WRITE_STATEMENT_SIZE = 250
RELATION_GENERATION_WRITE_STATEMENT_SIZE = 250
RELATION_SEARCH_REFRESH_DELETE_STATEMENT_SIZE = 500
LEGACY_RELATION_GENERATION = 0


@dataclass(frozen=True, slots=True)
class AcceptedRelationWrite:
    """One outgoing relation parsed from accepted markdown.

    ``target_id`` is reserved for an ambiguity-safe self-link. Other targets
    stay unresolved so the generation-fenced resolver owns their backfill.
    """

    relation_type: str
    target_name: str
    context: str | None
    target_id: int | None = None


@dataclass(frozen=True, slots=True)
class RelationGenerationWriteResult:
    """Whether one guarded relation-generation statement still owned its source."""

    generation_is_current: bool


def current_relation_generation_statement(
    *,
    project_id: int,
    entity_id: int,
    generation: int,
) -> Select[tuple[int]]:
    """Build the source-generation fence acquired before a projection write.

    Lock-order invariant: every transaction that can reach a note's
    ``NoteContent`` and then lock its ``Entity`` or ``Relation`` rows acquires
    all relevant ``NoteContent`` rows first, sorted by entity ID for bulk work.
    Relation publication may lock ``Entity`` through foreign-key enforcement;
    accepted mutations and cascading deletes must claim the same sources before
    preparing or flushing later-table changes.

    PostgreSQL renders ``FOR UPDATE`` and holds the note-content row through the
    relation statement. SQLite intentionally omits the clause; its first guarded
    relation mutation then enters SQLite's single-writer serialization.
    """
    return (
        select(NoteContent.entity_id)
        .where(
            NoteContent.project_id == project_id,
            NoteContent.entity_id == entity_id,
            NoteContent.db_version == generation,
        )
        .with_for_update()
    )


async def lock_note_content_before_entity_mutation(
    session: AsyncSession,
    *,
    project_id: int,
    entity_ids: Sequence[int],
) -> None:
    """Lock accepted note rows in canonical order before mutating their entities.

    See ``current_relation_generation_statement`` for the authoritative
    NoteContent-before-Entity lock-order invariant. Sorting the complete set
    before the first entity mutation also keeps overlapping bulk operations
    from acquiring source fences in opposite orders.
    """
    ordered_entity_ids = tuple(sorted(set(entity_ids)))
    if not ordered_entity_ids:
        return

    await session.execute(
        select(NoteContent.entity_id)
        .where(
            NoteContent.project_id == project_id,
            NoteContent.entity_id.in_(ordered_entity_ids),
        )
        .order_by(NoteContent.entity_id)
        .with_for_update()
    )


async def lock_project_note_content_before_project_mutation(
    session: AsyncSession,
    *,
    project_id: int,
) -> None:
    """Lock a project's accepted notes before a cascading project mutation.

    See ``current_relation_generation_statement`` for the canonical lock-order
    invariant. A project hard delete can cascade through Entity and Relation,
    so it claims every NoteContent source in sorted order before locking Project.
    """
    await session.execute(
        select(NoteContent.entity_id)
        .where(NoteContent.project_id == project_id)
        .order_by(NoteContent.entity_id)
        .with_for_update()
    )


def current_relation_generation_predicate(
    *,
    project_id: int,
    entity_id: int | InstrumentedAttribute[int],
    generation: int | InstrumentedAttribute[int],
) -> ColumnElement[bool]:
    """Require an accepted generation or an unbootstrapped legacy source.

    The note-content migration did not backfill existing entities. Its relation
    rows remain at generation zero until canonical markdown is reconciled. Keep
    those rows usable only while the source still has no ``NoteContent``; once
    bootstrapped, the ordinary generation fence owns every subsequent mutation.
    """
    source_has_note_content = exists().where(
        NoteContent.project_id == project_id,
        NoteContent.entity_id == entity_id,
    )
    generation_is_legacy = (
        literal(generation) == LEGACY_RELATION_GENERATION
        if isinstance(generation, int)
        else generation == LEGACY_RELATION_GENERATION
    )
    return or_(
        exists().where(
            NoteContent.project_id == project_id,
            NoteContent.entity_id == entity_id,
            NoteContent.db_version == generation,
        ),
        and_(generation_is_legacy, ~source_has_note_content),
    )


@dataclass(frozen=True, slots=True)
class ResolvedRelationWrite:
    """Compare-and-set command for one unresolved relation target."""

    relation_id: int
    from_id: int
    generation: int
    original_target_name: str
    target_id: int
    target_external_id: str
    relation_type: str

    @property
    def unresolved_identity(self) -> tuple[int, int, None, str, str, int]:
        """Return the row identity that must still exist before mutation."""
        return (
            self.relation_id,
            self.from_id,
            None,
            self.original_target_name,
            self.relation_type,
            self.generation,
        )

    @property
    def target_identity(self) -> tuple[int, str]:
        """Return the stable target identity that must survive until mutation."""
        return self.target_id, self.target_external_id


@dataclass(frozen=True, slots=True)
class ResolvedRelationWriteResult:
    """Outcome of applying one set of resolved relation targets."""

    affected_entity_ids: frozenset[int]
    duplicate_relation_ids: tuple[int, ...]
    stale_relation_ids: tuple[int, ...] = ()


def current_resolved_relation_write_predicate(
    write: ResolvedRelationWrite,
    *,
    project_id: int,
) -> ColumnElement[bool]:
    """Require source generation, unresolved edge, and target identity to remain current."""
    return and_(
        Relation.project_id == project_id,
        Relation.id == write.relation_id,
        Relation.from_id == write.from_id,
        Relation.to_id.is_(None),
        Relation.to_name == write.original_target_name,
        Relation.relation_type == write.relation_type,
        Relation.generation == write.generation,
        current_relation_generation_predicate(
            project_id=project_id,
            entity_id=write.from_id,
            generation=write.generation,
        ),
        exists().where(
            Entity.id == write.target_id,
            Entity.external_id == write.target_external_id,
            Entity.project_id == project_id,
        ),
    )


@dataclass(frozen=True, slots=True)
class ExistingRelationSnapshot:
    """Locked relation identity used for guarded resolver cleanup."""

    relation_id: int
    from_id: int
    to_id: int | None
    to_name: str
    relation_type: str
    generation: int

    @property
    def target_key(self) -> tuple[int, int, str] | None:
        """Return the resolved uniqueness identity when this row has a target."""
        if self.to_id is None:
            return None
        return self.from_id, self.to_id, self.relation_type


def current_relation_snapshot_predicate(
    snapshot: ExistingRelationSnapshot,
    *,
    project_id: int,
    expected_generation: int,
) -> ColumnElement[bool]:
    """Delete a snapshotted row only while its source still owns the planned generation."""
    target_predicate = (
        Relation.to_id.is_(None) if snapshot.to_id is None else Relation.to_id == snapshot.to_id
    )
    return and_(
        Relation.project_id == project_id,
        Relation.id == snapshot.relation_id,
        Relation.from_id == snapshot.from_id,
        target_predicate,
        Relation.to_name == snapshot.to_name,
        Relation.relation_type == snapshot.relation_type,
        Relation.generation == snapshot.generation,
        current_relation_generation_predicate(
            project_id=project_id,
            entity_id=snapshot.from_id,
            generation=expected_generation,
        ),
    )


@dataclass(frozen=True, slots=True)
class PendingRelationSearchRefresh:
    """One durable relation-search reconciliation item."""

    id: int
    entity_id: int


@dataclass(frozen=True, slots=True)
class CurrentRelationGenerationSearchRefresh:
    """One generation-coherent entity snapshot and its visible refresh work."""

    entity: Entity
    refresh_ids: tuple[int, ...]


class RelationRepository(Repository[Relation]):
    """Repository for Relation model with memory-specific operations."""

    project_id: int

    def __init__(self, project_id: int):
        """Initialize with project_id filter.

        Args:
            project_id: Project ID to filter all operations by
        """
        super().__init__(Relation, project_id=project_id)

    async def find_relation(
        self,
        session: AsyncSession,
        from_permalink: str,
        to_permalink: str,
        relation_type: str,
    ) -> Optional[Relation]:
        """Find a relation by its from and to path IDs."""
        from_entity = aliased(Entity)
        to_entity = aliased(Entity)

        query = (
            select(Relation)
            .join(from_entity, Relation.from_id == from_entity.id)
            .join(to_entity, Relation.to_id == to_entity.id)
            .where(
                and_(
                    from_entity.permalink == from_permalink,
                    to_entity.permalink == to_permalink,
                    Relation.relation_type == relation_type,
                )
            )
        )
        query = self._add_project_filter(query)
        return await self.find_one(session, query)

    async def find_by_entities(
        self, session: AsyncSession, from_id: int, to_id: int
    ) -> Sequence[Relation]:
        """Find all relations between two entities."""
        query = self.select().where((Relation.from_id == from_id) & (Relation.to_id == to_id))
        result = await self.execute_query(session, query)
        return result.scalars().all()

    async def find_by_type(self, session: AsyncSession, relation_type: str) -> Sequence[Relation]:
        """Find all relations of a specific type."""
        query = self.select().filter(Relation.relation_type == relation_type)
        result = await self.execute_query(session, query)
        return result.scalars().all()

    async def upsert_relation_generation(
        self,
        session: AsyncSession,
        *,
        entity_id: int,
        generation: int,
        relations: Sequence[AcceptedRelationWrite],
    ) -> RelationGenerationWriteResult:
        """Publish one bounded relation chunk only while its source generation is current."""
        relations_by_identity: dict[tuple[str, str], AcceptedRelationWrite] = {}
        for relation in relations:
            if relation.target_id is not None and relation.target_id != entity_id:
                raise ValueError("Only the source entity may be pre-resolved during publication")
            identity = relation.relation_type, relation.target_name
            relations_by_identity.setdefault(identity, relation)

        ordered_relations: list[AcceptedRelationWrite] = []
        resolved_identities: set[tuple[str, int]] = set()
        for identity in sorted(relations_by_identity):
            relation = relations_by_identity[identity]
            if relation.target_id is not None:
                resolved_identity = relation.relation_type, relation.target_id
                if resolved_identity in resolved_identities:
                    continue
                resolved_identities.add(resolved_identity)
            ordered_relations.append(relation)

        if not ordered_relations:
            raise ValueError("Relation generation upsert requires at least one relation")
        if len(ordered_relations) > RELATION_GENERATION_WRITE_STATEMENT_SIZE:
            raise ValueError(
                "Relation generation upsert exceeds the bounded statement size "
                f"of {RELATION_GENERATION_WRITE_STATEMENT_SIZE}"
            )

        current_generation = await session.scalar(
            current_relation_generation_statement(
                project_id=self.project_id,
                entity_id=entity_id,
                generation=generation,
            )
        )
        if current_generation is None:
            return RelationGenerationWriteResult(generation_is_current=False)

        pre_resolved_relations = [
            relation for relation in ordered_relations if relation.target_id is not None
        ]
        if pre_resolved_relations:
            # Constraint: name identity is the upsert arbiter, while safe self-links also occupy
            # the resolved identity domain. Replace an older alias in this same transaction so
            # changing the authored self-link cannot collide before generation cleanup runs.
            await session.execute(
                delete(Relation).where(
                    Relation.project_id == self.project_id,
                    Relation.from_id == entity_id,
                    or_(
                        *(
                            and_(
                                Relation.to_id == relation.target_id,
                                Relation.relation_type == relation.relation_type,
                                Relation.to_name != relation.target_name,
                            )
                            for relation in pre_resolved_relations
                        )
                    ),
                    current_relation_generation_predicate(
                        project_id=self.project_id,
                        entity_id=entity_id,
                        generation=generation,
                    ),
                )
            )

        desired_relations = union_all(
            *(
                select(
                    literal(relation.target_id, type_=Integer).label("to_id"),
                    literal(relation.target_name, type_=String).label("to_name"),
                    literal(relation.relation_type, type_=String).label("relation_type"),
                    literal(relation.context, type_=Text).label("context"),
                )
                for relation in ordered_relations
            )
        ).subquery()
        generation_is_current = current_relation_generation_predicate(
            project_id=self.project_id,
            entity_id=entity_id,
            generation=generation,
        )
        rows = (
            select(
                literal(self.project_id).label("project_id"),
                literal(entity_id).label("from_id"),
                desired_relations.c.to_id,
                desired_relations.c.to_name,
                desired_relations.c.relation_type,
                desired_relations.c.context,
                literal(generation).label("generation"),
            )
            .select_from(desired_relations)
            .where(generation_is_current)
        )

        dialect_name = session.bind.dialect.name if session.bind else "sqlite"
        insert_statement = (
            pg_insert(Relation) if dialect_name == "postgresql" else sqlite_insert(Relation)
        ).from_select(
            [
                Relation.project_id,
                Relation.from_id,
                Relation.to_id,
                Relation.to_name,
                Relation.relation_type,
                Relation.context,
                Relation.generation,
            ],
            rows,
        )
        statement = insert_statement.on_conflict_do_update(
            index_elements=[Relation.from_id, Relation.to_name, Relation.relation_type],
            set_={
                "generation": insert_statement.excluded.generation,
                "context": insert_statement.excluded.context,
                "to_id": insert_statement.excluded.to_id,
            },
            where=Relation.generation < insert_statement.excluded.generation,
        )
        await session.execute(statement)
        return RelationGenerationWriteResult(generation_is_current=True)

    async def begin_relation_generation_publication(
        self,
        session: AsyncSession,
        *,
        entity_id: int,
        generation: int,
    ) -> RelationGenerationWriteResult:
        """Persist retry work before publishing any relation chunk."""
        current_generation = await session.scalar(
            current_relation_generation_statement(
                project_id=self.project_id,
                entity_id=entity_id,
                generation=generation,
            )
        )
        if current_generation is None:
            return RelationGenerationWriteResult(generation_is_current=False)

        session.add(
            RelationSearchRefresh(
                project_id=self.project_id,
                entity_id=entity_id,
                publication_generation=generation,
            )
        )
        return RelationGenerationWriteResult(generation_is_current=True)

    async def cleanup_relation_generations(
        self,
        session: AsyncSession,
        *,
        entity_id: int,
        generation: int,
    ) -> RelationGenerationWriteResult:
        """Delete superseded rows only while the cleanup generation remains current."""
        current_generation = await session.scalar(
            current_relation_generation_statement(
                project_id=self.project_id,
                entity_id=entity_id,
                generation=generation,
            )
        )
        if current_generation is None:
            return RelationGenerationWriteResult(generation_is_current=False)

        await session.execute(
            delete(Relation).where(
                Relation.project_id == self.project_id,
                Relation.from_id == entity_id,
                Relation.generation < generation,
                current_relation_generation_predicate(
                    project_id=self.project_id,
                    entity_id=entity_id,
                    generation=generation,
                ),
            )
        )

        # Successful cleanup converts every retry for this or an older generation
        # into search-refresh work in the same transaction. A newer generation's
        # marker remains pending for its own publisher.
        converted = await session.execute(
            update(RelationSearchRefresh)
            .where(
                RelationSearchRefresh.project_id == self.project_id,
                RelationSearchRefresh.entity_id == entity_id,
                RelationSearchRefresh.publication_generation.is_not(None),
                RelationSearchRefresh.publication_generation <= generation,
            )
            .values(publication_generation=None)
            .returning(RelationSearchRefresh.id)
        )
        if converted.scalars().first() is None:
            session.add(
                RelationSearchRefresh(
                    project_id=self.project_id,
                    entity_id=entity_id,
                )
            )
        return RelationGenerationWriteResult(generation_is_current=True)

    async def find_unresolved_relations(self, session: AsyncSession) -> Sequence[Relation]:
        """Find unresolved relations owned by their source's current generation."""
        query = self.select().filter(
            Relation.to_id.is_(None),
            current_relation_generation_predicate(
                project_id=self.project_id,
                entity_id=Relation.from_id,
                generation=Relation.generation,
            ),
        )
        result = await self.execute_query(session, query)
        return result.scalars().all()

    async def find_unresolved_relations_for_entity(
        self, session: AsyncSession, entity_id: int
    ) -> Sequence[Relation]:
        """Find unresolved relations for a specific entity.

        Args:
            entity_id: The entity whose unresolved outgoing relations to find.

        Returns:
            List of unresolved relations where this entity is the source.
        """
        query = self.select().filter(
            Relation.from_id == entity_id,
            Relation.to_id.is_(None),
            current_relation_generation_predicate(
                project_id=self.project_id,
                entity_id=Relation.from_id,
                generation=Relation.generation,
            ),
        )
        result = await self.execute_query(session, query)
        return result.scalars().all()

    async def list_pending_search_refreshes(
        self,
        session: AsyncSession,
        *,
        entity_id: int | None = None,
    ) -> list[PendingRelationSearchRefresh]:
        """Return committed relation changes whose source projection needs refresh."""
        query = (
            select(RelationSearchRefresh.id, RelationSearchRefresh.entity_id)
            .where(
                RelationSearchRefresh.project_id == self.project_id,
                RelationSearchRefresh.publication_generation.is_(None),
            )
            .order_by(RelationSearchRefresh.id)
        )
        if entity_id is not None:
            query = query.where(RelationSearchRefresh.entity_id == entity_id)
        result = await session.execute(query)
        return [
            PendingRelationSearchRefresh(id=refresh_id, entity_id=refresh_entity_id)
            for refresh_id, refresh_entity_id in result.tuples().all()
        ]

    async def load_search_refresh_for_generation(
        self,
        session: AsyncSession,
        *,
        entity_id: int,
        generation: int,
    ) -> CurrentRelationGenerationSearchRefresh | None:
        """Load refresh inputs only while the published generation is still accepted.

        Entity state, the generation fence, and the exact pending marker IDs are
        observed by one statement. If a newer accepted generation already won,
        the old publisher performs no search write and leaves all markers for the
        newer flow or the durable resolver retry.
        """
        result = await session.execute(
            select(Entity, RelationSearchRefresh.id)
            .join(
                NoteContent,
                and_(
                    NoteContent.project_id == Entity.project_id,
                    NoteContent.entity_id == Entity.id,
                ),
            )
            .outerjoin(
                RelationSearchRefresh,
                and_(
                    RelationSearchRefresh.project_id == Entity.project_id,
                    RelationSearchRefresh.entity_id == Entity.id,
                    RelationSearchRefresh.publication_generation.is_(None),
                ),
            )
            .where(
                Entity.project_id == self.project_id,
                Entity.id == entity_id,
                NoteContent.db_version == generation,
            )
            .options(
                joinedload(Entity.observations).joinedload(Observation.entity),
                joinedload(Entity.outgoing_relations).joinedload(Relation.from_entity),
                joinedload(Entity.outgoing_relations).joinedload(Relation.to_entity),
            )
            .order_by(RelationSearchRefresh.id)
        )
        rows = result.unique().tuples().all()
        if not rows:
            return None

        return CurrentRelationGenerationSearchRefresh(
            entity=rows[0][0],
            refresh_ids=tuple(refresh_id for _, refresh_id in rows if refresh_id is not None),
        )

    async def clear_pending_search_refreshes(
        self,
        session: AsyncSession,
        refresh_ids: Sequence[int],
    ) -> None:
        """Retire only refresh work completed by the caller's successful pass."""
        # Refresh backlogs can contain tens of thousands of durable markers. Keep each
        # statement below database-driver parameter ceilings while preserving the caller's
        # transaction as the atomic retirement boundary.
        for refresh_id_batch in batched(
            refresh_ids,
            RELATION_SEARCH_REFRESH_DELETE_STATEMENT_SIZE,
        ):
            await session.execute(
                delete(RelationSearchRefresh).where(
                    RelationSearchRefresh.project_id == self.project_id,
                    RelationSearchRefresh.id.in_(refresh_id_batch),
                )
            )

    async def complete_search_refresh_for_generation(
        self,
        session: AsyncSession,
        *,
        entity_id: int,
        generation: int,
        refresh_ids: Sequence[int],
    ) -> bool:
        """Retire observed work only if the rendered generation is still current.

        Search storage is intentionally outside this repository transaction. A
        newer accepted generation can therefore win while the caller is rendering
        the older snapshot. Repeating the generation predicate in the marker
        mutation makes that race visible: stale writers leave fresh durable work
        for the winning generation instead of consuming the final repair signal.
        """
        generation_is_current = current_relation_generation_predicate(
            project_id=self.project_id,
            entity_id=entity_id,
            generation=generation,
        )
        for refresh_id_batch in batched(
            refresh_ids,
            RELATION_SEARCH_REFRESH_DELETE_STATEMENT_SIZE,
        ):
            await session.execute(
                delete(RelationSearchRefresh).where(
                    RelationSearchRefresh.project_id == self.project_id,
                    RelationSearchRefresh.id.in_(refresh_id_batch),
                    generation_is_current,
                )
            )

        is_current = bool(await session.scalar(select(generation_is_current)))
        if not is_current:
            session.add(
                RelationSearchRefresh(
                    project_id=self.project_id,
                    entity_id=entity_id,
                )
            )
        return is_current

    async def apply_resolved_targets(
        self,
        session: AsyncSession,
        writes: Sequence[ResolvedRelationWrite],
    ) -> ResolvedRelationWriteResult:
        """Backfill targets only while each source relation generation remains current."""
        if not writes:
            return ResolvedRelationWriteResult(frozenset(), ())

        ordered_writes = sorted(writes, key=lambda write: write.relation_id)

        # Publishers and resolvers both acquire the source NoteContent lock before
        # relation rows. Targets remain an identity snapshot: explicitly locking a
        # mutual target Entity here would invert with the source-Entity key-share
        # lock required by the durable search-refresh marker.
        requested_source_entity_ids = sorted({write.from_id for write in ordered_writes})
        source_result = await session.execute(
            select(NoteContent.entity_id, NoteContent.db_version)
            .where(
                NoteContent.project_id == self.project_id,
                NoteContent.entity_id.in_(requested_source_entity_ids),
            )
            .order_by(NoteContent.entity_id)
            .with_for_update()
        )
        current_generation_by_source = dict(source_result.tuples().all())

        # The statement guards below repeat the no-NoteContent condition, so a
        # concurrent bootstrap closes this compatibility path before mutation.
        legacy_source_entity_ids = {
            write.from_id
            for write in ordered_writes
            if write.generation == LEGACY_RELATION_GENERATION
            and write.from_id not in current_generation_by_source
        }
        current_generation_by_source.update(
            (source_entity_id, LEGACY_RELATION_GENERATION)
            for source_entity_id in legacy_source_entity_ids
        )

        stale_relation_ids = [
            write.relation_id
            for write in ordered_writes
            if current_generation_by_source.get(write.from_id) != write.generation
        ]
        generation_current_writes = [
            write
            for write in ordered_writes
            if current_generation_by_source.get(write.from_id) == write.generation
        ]
        if not generation_current_writes:
            return ResolvedRelationWriteResult(
                affected_entity_ids=frozenset(),
                duplicate_relation_ids=(),
                stale_relation_ids=tuple(sorted(stale_relation_ids)),
            )

        # The guarded UPDATE repeats the target external-ID predicate. PostgreSQL's
        # FK check then acquires the target key-share lock only for the accepted
        # mutation; SQLite's first mutation enters single-writer serialization.
        current_target_identities: set[tuple[int, str]] = set()
        target_ids = sorted({write.target_id for write in generation_current_writes})
        for target_id_batch in batched(
            target_ids,
            RESOLVED_RELATION_WRITE_STATEMENT_SIZE,
        ):
            target_result = await session.execute(
                select(Entity.id, Entity.external_id)
                .where(
                    Entity.project_id == self.project_id,
                    Entity.id.in_(target_id_batch),
                )
                .order_by(Entity.id)
            )
            current_target_identities.update(target_result.tuples().all())

        collision_domains = sorted(
            {(write.from_id, write.relation_type) for write in generation_current_writes}
        )
        result = await session.execute(
            select(
                Relation.id,
                Relation.from_id,
                Relation.to_id,
                Relation.to_name,
                Relation.relation_type,
                Relation.generation,
            )
            .where(
                Relation.project_id == self.project_id,
                tuple_(Relation.from_id, Relation.relation_type).in_(collision_domains),
            )
            .order_by(Relation.id)
            .with_for_update()
        )
        existing_relations = [
            ExistingRelationSnapshot(
                relation_id=relation_id,
                from_id=from_id,
                to_id=to_id,
                to_name=to_name,
                relation_type=relation_type,
                generation=generation,
            )
            for relation_id, from_id, to_id, to_name, relation_type, generation in result.tuples()
        ]
        existing_relations_by_id = {
            snapshot.relation_id: snapshot for snapshot in existing_relations
        }

        current_writes: list[ResolvedRelationWrite] = []
        for write in generation_current_writes:
            snapshot = existing_relations_by_id.get(write.relation_id)
            if (
                snapshot is None
                or (
                    snapshot.relation_id,
                    snapshot.from_id,
                    snapshot.to_id,
                    snapshot.to_name,
                    snapshot.relation_type,
                    snapshot.generation,
                )
                != write.unresolved_identity
                or write.target_identity not in current_target_identities
            ):
                stale_relation_ids.append(write.relation_id)
                continue
            current_writes.append(write)

        resolved_snapshots_by_target: dict[
            tuple[int, int, str], list[ExistingRelationSnapshot]
        ] = {}
        for snapshot in existing_relations:
            current_source_generation = current_generation_by_source[snapshot.from_id]
            if snapshot.generation > current_source_generation:
                raise RuntimeError(
                    "Relation generation cannot be newer than its source note_content: "
                    f"relation_id={snapshot.relation_id}, "
                    f"relation_generation={snapshot.generation}, "
                    f"source_generation={current_source_generation}"
                )
            if snapshot.target_key is not None:
                resolved_snapshots_by_target.setdefault(snapshot.target_key, []).append(snapshot)

        writes_by_target: dict[tuple[int, int, str], list[ResolvedRelationWrite]] = {}
        for write in current_writes:
            key = (write.from_id, write.target_id, write.relation_type)
            writes_by_target.setdefault(key, []).append(write)

        accepted_writes: list[ResolvedRelationWrite] = []
        duplicate_writes: list[ResolvedRelationWrite] = []
        duplicate_snapshot_deletes: list[tuple[ExistingRelationSnapshot, int]] = []
        superseded_snapshot_deletes: list[tuple[ExistingRelationSnapshot, int]] = []
        for target_key, target_writes in sorted(writes_by_target.items()):
            source_id = target_key[0]
            expected_generation = current_generation_by_source[source_id]
            target_snapshots = resolved_snapshots_by_target.get(target_key, [])
            current_snapshots: list[ExistingRelationSnapshot] = []
            for snapshot in target_snapshots:
                if snapshot.generation < expected_generation:
                    superseded_snapshot_deletes.append((snapshot, expected_generation))
                else:
                    current_snapshots.append(snapshot)

            winner_id = min(
                [write.relation_id for write in target_writes]
                + [snapshot.relation_id for snapshot in current_snapshots]
            )
            for write in target_writes:
                if write.relation_id == winner_id:
                    accepted_writes.append(write)
                else:
                    duplicate_writes.append(write)
            duplicate_snapshot_deletes.extend(
                (snapshot, expected_generation)
                for snapshot in current_snapshots
                if snapshot.relation_id != winner_id
            )

        deleted_snapshot_ids: set[int] = set()
        snapshot_delete_commands = [
            *superseded_snapshot_deletes,
            *duplicate_snapshot_deletes,
        ]
        for delete_batch in batched(
            snapshot_delete_commands,
            RESOLVED_RELATION_WRITE_STATEMENT_SIZE,
        ):
            delete_result = await session.execute(
                delete(Relation)
                .where(
                    or_(
                        *(
                            current_relation_snapshot_predicate(
                                snapshot,
                                project_id=self.project_id,
                                expected_generation=expected_generation,
                            )
                            for snapshot, expected_generation in delete_batch
                        )
                    )
                )
                .returning(Relation.id)
            )
            deleted_snapshot_ids.update(delete_result.scalars().all())

        duplicate_relation_ids = {
            snapshot.relation_id
            for snapshot, _ in duplicate_snapshot_deletes
            if snapshot.relation_id in deleted_snapshot_ids
        }
        deleted_duplicate_write_ids: set[int] = set()
        for write_batch in batched(
            duplicate_writes,
            RESOLVED_RELATION_WRITE_STATEMENT_SIZE,
        ):
            delete_result = await session.execute(
                delete(Relation)
                .where(
                    Relation.project_id == self.project_id,
                    or_(
                        *(
                            current_resolved_relation_write_predicate(
                                write,
                                project_id=self.project_id,
                            )
                            for write in write_batch
                        )
                    ),
                )
                .returning(Relation.id)
            )
            deleted_relation_ids = set(delete_result.scalars().all())
            deleted_duplicate_write_ids.update(deleted_relation_ids)
            duplicate_relation_ids.update(
                write.relation_id
                for write in write_batch
                if write.relation_id in deleted_relation_ids
            )
            stale_relation_ids.extend(
                write.relation_id
                for write in write_batch
                if write.relation_id not in deleted_relation_ids
            )

        updated_relation_ids: set[int] = set()
        for write_batch in batched(
            accepted_writes,
            RESOLVED_RELATION_WRITE_STATEMENT_SIZE,
        ):
            update_result = await session.execute(
                update(Relation)
                .where(
                    or_(
                        *(
                            current_resolved_relation_write_predicate(
                                write,
                                project_id=self.project_id,
                            )
                            for write in write_batch
                        )
                    )
                )
                .values(
                    to_id=case(
                        {write.relation_id: write.target_id for write in write_batch},
                        value=Relation.id,
                    )
                )
                .returning(Relation.id)
                .execution_options(synchronize_session=False)
            )
            batch_updated_relation_ids = set(update_result.scalars().all())
            updated_relation_ids.update(batch_updated_relation_ids)
            stale_relation_ids.extend(
                write.relation_id
                for write in write_batch
                if write.relation_id not in batch_updated_relation_ids
            )

        writes_by_id = {write.relation_id: write for write in current_writes}
        source_entity_ids = {
            writes_by_id[relation_id].from_id
            for relation_id in deleted_duplicate_write_ids | updated_relation_ids
        }
        deleted_snapshots_by_id = {
            snapshot.relation_id: snapshot for snapshot, _ in snapshot_delete_commands
        }
        source_entity_ids.update(
            deleted_snapshots_by_id[relation_id].from_id for relation_id in deleted_snapshot_ids
        )

        # Trigger: relation targets changed or duplicate edges were removed.
        # Why: a later storage/search failure must not consume the only evidence
        # that each source projection still needs reconciliation.
        # Outcome: the relation mutation and its retryable refresh work commit
        # atomically, while concurrent later changes receive separate markers.
        session.add_all(
            RelationSearchRefresh(
                project_id=self.project_id,
                entity_id=entity_id,
            )
            for entity_id in sorted(source_entity_ids)
        )

        return ResolvedRelationWriteResult(
            affected_entity_ids=frozenset(source_entity_ids),
            duplicate_relation_ids=tuple(sorted(duplicate_relation_ids)),
            stale_relation_ids=tuple(sorted(stale_relation_ids)),
        )

    @override
    def get_load_options(self) -> List[LoaderOption]:
        return [selectinload(Relation.from_entity), selectinload(Relation.to_entity)]
