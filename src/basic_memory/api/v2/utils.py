from collections import defaultdict
from collections.abc import Mapping
from typing import Any, Protocol, Optional, List, Sequence

import logfire
from sqlalchemy.ext.asyncio import AsyncSession
from basic_memory.models import MemoryTimeIndex
from basic_memory.repository.search_repository import SearchIndexRow
from basic_memory.schemas.memory import (
    EntitySummary,
    ObservationSummary,
    RelationSummary,
    MemoryMetadata,
    GraphContext,
    ContextResult,
)
from basic_memory.schemas.search import (
    SearchItemType,
    SearchResult,
    TemporalRangeValue,
    TemporalResultMetadata,
)
from basic_memory.services.context_service import (
    ContextResultRow,
    ContextResult as ServiceContextResult,
)
from basic_memory.temporal import TemporalRange, TemporalRangeKind


class EntityBatchLookup(Protocol):
    async def find_by_ids_for_hydration(
        self,
        session: AsyncSession,
        ids: List[int],
        *,
        include_cross_project: bool = False,
    ) -> Sequence[Any]: ...


class EntityServiceBatchLookup(Protocol):
    async def get_entities_by_id(self, ids: List[int]) -> Sequence[Any]: ...


class TemporalAssertionLookup(Protocol):
    async def find_for_sources(
        self,
        session: AsyncSession,
        sources: Sequence[tuple[str, int]],
    ) -> Sequence[MemoryTimeIndex]: ...


# One page of hits, keyed by the (search row type, search row id) pair the projection
# addresses. Empty means "no valid-time metadata was loaded", never "none exists".
type TemporalMetadataBySource = Mapping[tuple[str, int], list[TemporalResultMetadata]]


async def get_entities_by_id_lookup(
    entity_service: EntityServiceBatchLookup,
    entity_ids: Sequence[int],
) -> dict[int, Any]:
    """Fetch an entity batch once and index it by internal identity."""
    if not entity_ids:
        return {}
    entities = await entity_service.get_entities_by_id(list(entity_ids))
    return {entity.id: entity for entity in entities}


def _required_str(value: str | None, field_name: str) -> str:
    """Return a required search field or fail before producing invalid response data."""
    if value is None:
        raise ValueError(f"Search result is missing required field: {field_name}")
    return value


def _search_item_type(value: str | SearchItemType) -> SearchItemType:
    """Normalize repository row type strings into the public search enum."""
    return value if isinstance(value, SearchItemType) else SearchItemType(value)


async def to_graph_context(
    context_result: ServiceContextResult,
    entity_repository: EntityBatchLookup,
    session: AsyncSession,
    page: Optional[int] = None,
    page_size: Optional[int] = None,
) -> GraphContext:
    with logfire.span(
        "memory.hydrate_context",
        domain="memory",
        action="build_context",
        phase="hydrate_context",
        page=page,
        page_size=page_size,
        result_count=len(context_result.results),
    ):
        # First pass: collect all entity IDs needed for external_id lookup
        # This includes: entity primary results, observation parent entities, relation from/to entities
        entity_ids_needed: set[int] = set()
        for context_item in context_result.results:
            for item in (
                [context_item.primary_result]
                + context_item.observations
                + context_item.related_results
            ):
                item_type = _search_item_type(item.type)
                if item_type == SearchItemType.ENTITY:
                    # Entity's own ID for its external_id
                    entity_ids_needed.add(item.id)
                elif item_type == SearchItemType.OBSERVATION:
                    # Parent entity ID for entity_external_id
                    if item.entity_id:
                        entity_ids_needed.add(item.entity_id)
                elif item_type == SearchItemType.RELATION:
                    # Source and target entity IDs for external_ids
                    if item.from_id:
                        entity_ids_needed.add(item.from_id)
                    if item.to_id:
                        entity_ids_needed.add(item.to_id)

        # Batch fetch just the entity fields needed to shape the response.
        entity_title_lookup: dict[int, str] = {}
        entity_external_id_lookup: dict[int, str] = {}
        if entity_ids_needed:
            with logfire.span(
                "memory.hydrate_context.lookup_entities",
                domain="memory",
                action="build_context",
                phase="lookup_entities",
                result_count=len(entity_ids_needed),
            ):
                entities = await entity_repository.find_by_ids_for_hydration(
                    session, list(entity_ids_needed), include_cross_project=True
                )
            for e in entities:
                entity_title_lookup[e.id] = e.title
                entity_external_id_lookup[e.id] = e.external_id

        # Helper function to convert items to summaries
        def to_summary(
            item: SearchIndexRow | ContextResultRow,
        ) -> EntitySummary | ObservationSummary | RelationSummary:
            item_type = _search_item_type(item.type)
            match item_type:
                case SearchItemType.ENTITY:
                    return EntitySummary(
                        external_id=entity_external_id_lookup.get(item.id, ""),
                        entity_id=item.id,
                        title=_required_str(item.title, "title"),
                        permalink=item.permalink,
                        content=item.content,
                        file_path=_required_str(item.file_path, "file_path"),
                        created_at=item.created_at,
                    )
                case SearchItemType.OBSERVATION:
                    entity_ext_id = None
                    entity_title = None
                    if item.entity_id:
                        entity_ext_id = entity_external_id_lookup.get(item.entity_id)
                        entity_title = entity_title_lookup.get(item.entity_id)
                    return ObservationSummary(
                        observation_id=item.id,
                        entity_id=item.entity_id,
                        entity_external_id=entity_ext_id,
                        title=entity_title,
                        file_path=_required_str(item.file_path, "file_path"),
                        category=_required_str(item.category, "category"),
                        content=_required_str(item.content, "content"),
                        permalink=_required_str(item.permalink, "permalink"),
                        created_at=item.created_at,
                    )
                case SearchItemType.RELATION:
                    from_title = entity_title_lookup.get(item.from_id) if item.from_id else None
                    to_title = entity_title_lookup.get(item.to_id) if item.to_id else None
                    from_ext_id = (
                        entity_external_id_lookup.get(item.from_id) if item.from_id else None
                    )
                    to_ext_id = entity_external_id_lookup.get(item.to_id) if item.to_id else None
                    return RelationSummary(
                        relation_id=item.id,
                        entity_id=item.entity_id,
                        title=_required_str(item.title, "title"),
                        file_path=_required_str(item.file_path, "file_path"),
                        permalink=_required_str(item.permalink, "permalink"),
                        relation_type=_required_str(item.relation_type, "relation_type"),
                        from_entity=from_title,
                        from_entity_id=item.from_id,
                        from_entity_external_id=from_ext_id,
                        to_entity=to_title,
                        to_name=item.to_name,
                        to_entity_id=item.to_id,
                        to_entity_external_id=to_ext_id,
                        created_at=item.created_at,
                    )

        with logfire.span(
            "memory.hydrate_context.shape_results",
            domain="memory",
            action="build_context",
            phase="shape_results",
            result_count=len(context_result.results),
        ):
            hierarchical_results = []
            for context_item in context_result.results:
                primary_result = to_summary(context_item.primary_result)
                observations = [
                    summary
                    for summary in (to_summary(obs) for obs in context_item.observations)
                    if isinstance(summary, ObservationSummary)
                ]
                related = [to_summary(rel) for rel in context_item.related_results]
                hierarchical_results.append(
                    ContextResult(
                        primary_result=primary_result,
                        observations=observations,
                        related_results=related,
                    )
                )

        metadata = MemoryMetadata(
            uri=context_result.metadata.uri,
            types=context_result.metadata.types,
            depth=context_result.metadata.depth,
            timeframe=context_result.metadata.timeframe,
            generated_at=context_result.metadata.generated_at,
            primary_count=context_result.metadata.primary_count,
            related_count=context_result.metadata.related_count,
            total_results=context_result.metadata.primary_count
            + context_result.metadata.related_count,
            total_relations=context_result.metadata.total_relations,
            total_observations=context_result.metadata.total_observations,
        )

        return GraphContext(
            results=hierarchical_results,
            metadata=metadata,
            page=page,
            page_size=page_size,
            has_more=context_result.metadata.has_more,
        )


def _temporal_result_metadata(row: MemoryTimeIndex) -> TemporalResultMetadata:
    """Shape one projected assertion into the value a caller sees.

    Rebuilding the domain range from the stored scalars re-runs its invariants, so a
    row that somehow violated them surfaces here instead of being rendered as a
    plausible-looking interval.
    """
    valid_during = TemporalRange(
        kind=TemporalRangeKind(row.range_kind),
        lower=row.lower_value,
        upper=row.upper_value,
        lower_inclusive=row.lower_inclusive,
        upper_inclusive=row.upper_inclusive,
        is_empty=row.is_empty,
    )
    return TemporalResultMetadata(
        role=row.time_role,
        valid_during=TemporalRangeValue(
            kind=valid_during.kind.value,
            literal=str(valid_during),
            lower=valid_during.lower,
            upper=valid_during.upper,
            lower_inclusive=valid_during.lower_inclusive,
            upper_inclusive=valid_during.upper_inclusive,
            is_empty=valid_during.is_empty,
        ),
        source_text=row.source_text,
    )


async def load_temporal_metadata(
    temporal_repository: TemporalAssertionLookup,
    session: AsyncSession,
    results: Sequence[SearchIndexRow],
) -> dict[tuple[str, int], list[TemporalResultMetadata]]:
    """Load the authored valid-time assertions behind one page of search hits.

    Keyed on the search row's own ``(type, id)`` pair, which is exactly the address
    the projection stores -- so an observation hit resolves to the assertions written
    on that observation, not to its note's other assertions.
    """
    sources = [(result.type, result.id) for result in results]
    if not sources:
        return {}

    with logfire.span(
        "search.hydrate_results.fetch_temporal",
        domain="search",
        action="search",
        phase="fetch_temporal",
        result_count=len(sources),
    ):
        rows = await temporal_repository.find_for_sources(session, sources)

    by_source: defaultdict[tuple[str, int], list[TemporalResultMetadata]] = defaultdict(list)
    for row in rows:
        by_source[(row.source_type, row.source_id)].append(_temporal_result_metadata(row))
    return dict(by_source)


async def to_search_results(
    entity_service: EntityServiceBatchLookup,
    results: List[SearchIndexRow],
    *,
    temporal_by_source: TemporalMetadataBySource | None = None,
) -> list[SearchResult]:
    with logfire.span(
        "search.hydrate_results",
        domain="search",
        action="search",
        phase="hydrate_results",
        result_count=len(results),
    ):
        # Collect all unique entity IDs across all results in a single pass
        # This avoids N+1 queries — one batch fetch instead of one per result
        all_entity_ids: set[int] = set()
        for result in results:
            for eid in (result.entity_id, result.from_id, result.to_id):
                if eid is not None:
                    all_entity_ids.add(eid)

        # Single batch fetch for all entities
        entities_by_id: dict[int, Any] = {}
        with logfire.span(
            "search.hydrate_results.fetch_entities",
            domain="search",
            action="search",
            phase="fetch_entities",
            result_count=len(all_entity_ids),
        ):
            entities_by_id = await get_entities_by_id_lookup(
                entity_service,
                list(all_entity_ids),
            )

        search_results = []
        with logfire.span(
            "search.hydrate_results.shape_results",
            domain="search",
            action="search",
            phase="shape_results",
            result_count=len(results),
        ):
            for result in results:
                entity_id = None
                observation_id = None
                relation_id = None

                if result.type == SearchItemType.ENTITY:
                    entity_id = result.id
                elif result.type == SearchItemType.OBSERVATION:
                    observation_id = result.id
                    entity_id = result.entity_id
                elif result.type == SearchItemType.RELATION:
                    relation_id = result.id
                    entity_id = result.entity_id

                # Look up entities by their specific IDs
                parent_entity = entities_by_id.get(result.entity_id) if result.entity_id else None
                from_entity = entities_by_id.get(result.from_id) if result.from_id else None
                to_entity = entities_by_id.get(result.to_id) if result.to_id else None

                search_results.append(
                    SearchResult(
                        title=_required_str(result.title, "title"),
                        type=_search_item_type(result.type),
                        permalink=result.permalink,
                        score=result.score if result.score is not None else 0.0,
                        entity=parent_entity.permalink if parent_entity else None,
                        # Parent entity UUID, so hosted MCP can deep-link the note this hit
                        # belongs to. Available for entity, observation, and relation results
                        # because each row carries its owning entity's id (#1423).
                        external_id=parent_entity.external_id if parent_entity else None,
                        content=result.content,
                        matched_chunk=result.matched_chunk_text,
                        file_path=_required_str(result.file_path, "file_path"),
                        updated_at=result.updated_at,
                        metadata=result.metadata,
                        entity_id=entity_id,
                        observation_id=observation_id,
                        relation_id=relation_id,
                        category=result.category,
                        from_entity=from_entity.permalink if from_entity else None,
                        to_entity=to_entity.permalink if to_entity else None,
                        relation_type=result.relation_type,
                        temporal=(
                            temporal_by_source.get((result.type, result.id))
                            if temporal_by_source
                            else None
                        ),
                    )
                )
        return search_results
