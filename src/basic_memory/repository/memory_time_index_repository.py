"""Repository for managing MemoryTimeIndex rows."""

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from basic_memory.models import MemoryTimeIndex
from basic_memory.repository.relation_repository import current_relation_generation_statement
from basic_memory.repository.repository import SELECT_BY_IDS_CHUNK_SIZE, Repository
from basic_memory.temporal import TemporalAssertion


@dataclass(frozen=True, slots=True)
class AcceptedTemporalAssertion:
    """One authored assertion paired with the persisted row that carried it.

    The parser cannot supply `source_type`/`source_id`: it reads markdown, where the
    projection's row identities do not exist yet. Publication mints them and pairs
    them here.
    """

    source_type: str
    source_id: int
    assertion: TemporalAssertion


@dataclass(frozen=True, slots=True)
class TemporalGenerationWriteResult:
    """Whether a guarded temporal replacement still owned its source generation."""

    generation_is_current: bool


def _projection_row(
    accepted: AcceptedTemporalAssertion,
    *,
    project_id: int,
    entity_id: int,
) -> MemoryTimeIndex:
    """Flatten one assertion into the portable scalar columns the table stores."""
    valid_during = accepted.assertion.valid_during
    return MemoryTimeIndex(
        project_id=project_id,
        entity_id=entity_id,
        source_type=accepted.source_type,
        source_id=accepted.source_id,
        time_role=accepted.assertion.time_role.value,
        range_kind=valid_during.kind.value,
        lower_value=valid_during.lower,
        upper_value=valid_during.upper,
        lower_inclusive=valid_during.lower_inclusive,
        upper_inclusive=valid_during.upper_inclusive,
        is_empty=valid_during.is_empty,
        extractor=accepted.assertion.extractor,
        source_text=accepted.assertion.source_text,
        assertion_metadata=accepted.assertion.metadata,
    )


class MemoryTimeIndexRepository(Repository[MemoryTimeIndex]):
    """Repository for the temporal projection of accepted note content."""

    project_id: int

    def __init__(self, project_id: int):
        """Initialize with project_id filter.

        Args:
            project_id: Project ID to filter all operations by
        """
        super().__init__(MemoryTimeIndex, project_id=project_id)

    async def find_by_entity(
        self, session: AsyncSession, entity_id: int
    ) -> Sequence[MemoryTimeIndex]:
        """Find every temporal assertion projected from one entity."""
        query = (
            self.select()
            .filter(MemoryTimeIndex.entity_id == entity_id)
            .order_by(MemoryTimeIndex.source_id, MemoryTimeIndex.id)
        )
        result = await self.execute_query(session, query)
        return result.scalars().all()

    async def find_for_sources(
        self,
        session: AsyncSession,
        sources: Iterable[tuple[str, int]],
    ) -> Sequence[MemoryTimeIndex]:
        """Find every assertion carried by the given ``(source_type, source_id)`` rows.

        Used to explain search hits, so it batches: search returns a page of rows and
        this loads their assertions in one pass per source type rather than one query
        per hit. Ids are chunked because SQLite caps bound parameters per statement.
        """
        ids_by_type: defaultdict[str, list[int]] = defaultdict(list)
        for source_type, source_id in sources:
            ids_by_type[source_type].append(source_id)
        if not ids_by_type:
            return []

        rows: list[MemoryTimeIndex] = []
        for source_type, source_ids in ids_by_type.items():
            for start in range(0, len(source_ids), SELECT_BY_IDS_CHUNK_SIZE):
                chunk = source_ids[start : start + SELECT_BY_IDS_CHUNK_SIZE]
                query = (
                    self.select()
                    .filter(MemoryTimeIndex.source_type == source_type)
                    .filter(MemoryTimeIndex.source_id.in_(chunk))
                    .order_by(MemoryTimeIndex.source_id, MemoryTimeIndex.id)
                )
                result = await self.execute_query(session, query)
                rows.extend(result.scalars().all())
        return rows

    async def replace_assertions_for_generation(
        self,
        session: AsyncSession,
        *,
        entity_id: int,
        generation: int,
        assertions: Sequence[AcceptedTemporalAssertion],
    ) -> TemporalGenerationWriteResult:
        """Replace temporal rows only while the accepted content generation is current."""
        # This helper is a shared note_content fence despite its historical relation name.
        current_generation = await session.scalar(
            current_relation_generation_statement(
                project_id=self.project_id,
                entity_id=entity_id,
                generation=generation,
            )
        )
        # Trigger: a newer accepted note generation won before this transaction acquired the row.
        # Why: replacing here would publish valid time the current markdown no longer asserts.
        # Outcome: leave every existing row untouched and let the current writer publish.
        if current_generation is None:
            return TemporalGenerationWriteResult(generation_is_current=False)

        await self.delete_by_fields(session, entity_id=entity_id)
        rows = [
            _projection_row(accepted, project_id=self.project_id, entity_id=entity_id)
            for accepted in assertions
        ]
        await self.add_all_no_return(session, rows)
        return TemporalGenerationWriteResult(generation_is_current=True)
