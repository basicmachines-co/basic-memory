"""Repository for managing NoteSection rows."""

import hashlib
from dataclasses import dataclass
from typing import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from basic_memory.models import NoteSection
from basic_memory.repository.relation_repository import current_relation_generation_statement
from basic_memory.repository.repository import Repository


def heading_path_digest(heading_path: str) -> str:
    """Digest keyed by the section lookup index in place of the raw path.

    Arbitrary heading text can exceed PostgreSQL's 2704-byte btree index-row
    limit (same constraint as Observation.permalink), so the lookup index keys
    on this fixed-width digest while the full path stays as un-indexed text.

    Known ambiguity: the path joins heading segments with "/", so a heading
    whose text literally contains "/" collides with the equivalent nested path
    ("# A/B" and "# A" > "## B" both digest as "A/B"). The section read path is
    immune (it matches in-memory path tuples), but a future consumer of this
    table must not assume the key distinguishes those shapes without escaping
    the separator first.
    """
    return hashlib.sha256(heading_path.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class AcceptedSectionWrite:
    """One heading-bounded body span parsed from accepted markdown, ready to persist.

    Mirrors the markdown ``MarkdownSection`` fields with the path already joined,
    so the accepted-write path can persist the section index without constructing
    ORM rows in the storage-neutral runner (matching AcceptedObservationWrite).
    """

    heading: str
    level: int
    heading_path: str
    duplicate_index: int
    start_line: int
    end_line: int
    start_offset: int
    end_offset: int


@dataclass(frozen=True, slots=True)
class SectionGenerationWriteResult:
    """Whether a guarded section replacement still owned its source generation."""

    generation_is_current: bool


class NoteSectionRepository(Repository[NoteSection]):
    """Repository for the NoteSection projection of accepted note bodies."""

    project_id: int

    def __init__(self, project_id: int):
        """Initialize with project_id filter.

        Args:
            project_id: Project ID to filter all operations by
        """
        super().__init__(NoteSection, project_id=project_id)

    async def find_by_entity(self, session: AsyncSession, entity_id: int) -> Sequence[NoteSection]:
        """Find all sections for one entity in document order."""
        query = (
            self.select()
            .filter(NoteSection.entity_id == entity_id)
            .order_by(NoteSection.start_line)
        )
        result = await self.execute_query(session, query)
        return result.scalars().all()

    async def replace_sections_for_generation(
        self,
        session: AsyncSession,
        *,
        entity_id: int,
        generation: int,
        sections: Sequence[AcceptedSectionWrite],
    ) -> SectionGenerationWriteResult:
        """Replace sections only while the accepted content generation is current."""
        # This helper is a shared note_content fence despite its historical relation name.
        current_generation = await session.scalar(
            current_relation_generation_statement(
                project_id=self.project_id,
                entity_id=entity_id,
                generation=generation,
            )
        )
        # Trigger: a newer accepted note generation won before this transaction acquired the row.
        # Why: replacing here would publish stale coordinates over the newer note body.
        # Outcome: leave every existing section untouched and let the current writer publish.
        if current_generation is None:
            return SectionGenerationWriteResult(generation_is_current=False)

        await self.delete_by_fields(session, entity_id=entity_id)
        rows = [
            NoteSection(
                project_id=self.project_id,
                entity_id=entity_id,
                heading=section.heading,
                level=section.level,
                heading_path=section.heading_path,
                heading_path_digest=heading_path_digest(section.heading_path),
                duplicate_index=section.duplicate_index,
                start_line=section.start_line,
                end_line=section.end_line,
                start_offset=section.start_offset,
                end_offset=section.end_offset,
            )
            for section in sections
        ]
        await self.add_all_no_return(session, rows)
        return SectionGenerationWriteResult(generation_is_current=True)
