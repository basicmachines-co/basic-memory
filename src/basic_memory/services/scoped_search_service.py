"""Search over an explicit set of projects in one database.

The project route and this service run the same ``SearchReader``; only the scope
differs. Hydration stays inside the scope too: owning entities, relation endpoints,
project identities, and valid-time assertions are read only from the projects the
search was allowed to read, so a page never names something outside its scope.
"""

from collections.abc import Iterable, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import load_only

from basic_memory import db
from basic_memory.models import Entity, MemoryTimeIndex, Project
from basic_memory.repository.repository import SELECT_BY_IDS_CHUNK_SIZE
from basic_memory.repository.search_index_row import SearchIndexRow
from basic_memory.repository.search_reader import SearchReader
from basic_memory.repository.search_scope import ProjectScope
from basic_memory.schemas.search import SearchQuery
from basic_memory.services.search_service import (
    include_legacy_note_type_spellings,
    prepare_search_query,
    relaxed_fts_fallback_eligible,
)


def _chunks[T](values: Sequence[T]) -> Iterable[Sequence[T]]:
    """Split a bind list at the shared per-statement parameter bound."""
    for start in range(0, len(values), SELECT_BY_IDS_CHUNK_SIZE):
        yield values[start : start + SELECT_BY_IDS_CHUNK_SIZE]


class ScopedSearchService:
    """Run and hydrate searches over one ``ProjectScope``."""

    def __init__(
        self,
        session_maker: async_sessionmaker[AsyncSession],
        scope: ProjectScope,
        reader: SearchReader,
    ) -> None:
        self.session_maker = session_maker
        self.scope = scope
        self.reader = reader

    # --- Retrieval ---

    async def search(
        self,
        query: SearchQuery,
        *,
        limit: int,
        offset: int,
    ) -> list[SearchIndexRow]:
        """One ranking over every project in scope."""
        prepared = prepare_search_query(query)
        if prepared is None:
            return []
        prepared = await include_legacy_note_type_spellings(
            self.session_maker, self.scope, prepared
        )
        allow_relaxed = relaxed_fts_fallback_eligible(
            query, prepared.search_text, prepared.retrieval_mode
        )
        return await self.reader.search(
            prepared, limit=limit, offset=offset, allow_relaxed=allow_relaxed
        )

    async def count(self, query: SearchQuery) -> int:
        """Exact full-text match count over every project in scope."""
        prepared = prepare_search_query(query)
        if prepared is None:
            return 0
        prepared = await include_legacy_note_type_spellings(
            self.session_maker, self.scope, prepared
        )
        allow_relaxed = relaxed_fts_fallback_eligible(
            query, prepared.search_text, prepared.retrieval_mode
        )
        return await self.reader.count(prepared, allow_relaxed=allow_relaxed)

    # --- Hydration, bounded by the scope ---

    async def get_entities_by_id(self, ids: Sequence[int]) -> Sequence[Entity]:
        """The entities a page of hits refers to, read only from projects in scope.

        Only the fields result shaping reads are loaded. Ids reached through a scoped
        search already belong to the scope; the predicate keeps that true by
        construction rather than by trust.
        """
        if not ids or self.scope.is_empty:
            return []
        entities: list[Entity] = []
        async with db.scoped_session(self.session_maker) as session:
            for chunk in _chunks(list(ids)):
                result = await session.scalars(
                    select(Entity)
                    .where(
                        Entity.project_id.in_(self.scope.project_ids),
                        Entity.id.in_(chunk),
                    )
                    .options(
                        load_only(
                            Entity.id, Entity.project_id, Entity.permalink, Entity.external_id
                        )
                    )
                )
                entities.extend(result.all())
        return entities

    async def find_for_sources(
        self,
        session: AsyncSession,
        sources: Iterable[tuple[str, int]],
    ) -> Sequence[MemoryTimeIndex]:
        """The valid-time assertions behind a page of hits, read only from projects in scope.

        Mirrors ``MemoryTimeIndexRepository.find_for_sources`` for a set of projects:
        one statement per source type, chunked at the bind bound.
        """
        if self.scope.is_empty:
            return []
        ids_by_type: dict[str, list[int]] = {}
        for source_type, source_id in sources:
            ids_by_type.setdefault(source_type, []).append(source_id)

        rows: list[MemoryTimeIndex] = []
        for source_type, source_ids in ids_by_type.items():
            for chunk in _chunks(source_ids):
                result = await session.scalars(
                    select(MemoryTimeIndex)
                    .where(
                        MemoryTimeIndex.project_id.in_(self.scope.project_ids),
                        MemoryTimeIndex.source_type == source_type,
                        MemoryTimeIndex.source_id.in_(chunk),
                    )
                    .order_by(MemoryTimeIndex.source_id, MemoryTimeIndex.id)
                )
                rows.extend(result.all())
        return rows

    async def project_external_ids(
        self,
        session: AsyncSession,
        rows: Sequence[SearchIndexRow],
    ) -> dict[int, str]:
        """External ids for the projects a page of hits came from."""
        project_ids = sorted({row.project_id for row in rows})
        if not project_ids:
            return {}
        result = await session.execute(
            select(Project.id, Project.external_id).where(Project.id.in_(project_ids))
        )
        return {int(project_id): str(external_id) for project_id, external_id in result.all()}
