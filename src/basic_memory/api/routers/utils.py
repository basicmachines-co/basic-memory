from typing import Optional, List

from basic_memory.repository import EntityRepository
from basic_memory.repository.search_repository import SearchIndexRow
from basic_memory.schemas.memory import (
    EntitySummary,
    ObservationSummary,
    RelationSummary,
    MemoryMetadata,
    GraphContext,
    ContextResult,
)
from basic_memory.schemas.search import SearchItemType, SearchResult
from basic_memory.services import EntityService
from basic_memory.services.context_service import (
    ContextResultRow,
    ContextResult as ServiceContextResult,
)


async def to_graph_context(
    context_result: ServiceContextResult,
    entity_repository: EntityRepository,
    page: Optional[int] = None,
    page_size: Optional[int] = None,
):
    # First pass: collect all entity IDs needed for relations
    entity_ids_needed: set[int] = set()
    for context_item in context_result.results:
        for item in (
            [context_item.primary_result] + context_item.observations + context_item.related_results
        ):
            if item.type == SearchItemType.RELATION:
                if item.from_id:  # pyright: ignore
                    entity_ids_needed.add(item.from_id)  # pyright: ignore
                if item.to_id:
                    entity_ids_needed.add(item.to_id)

    # Batch fetch all entities at once
    entity_lookup: dict[int, str] = {}
    if entity_ids_needed:
        entities = await entity_repository.find_by_ids(list(entity_ids_needed))
        entity_lookup = {e.id: e.title for e in entities}

    # Helper function to convert items to summaries
    def to_summary(item: SearchIndexRow | ContextResultRow):
        match item.type:
            case SearchItemType.ENTITY:
                return EntitySummary(
                    entity_id=item.id,
                    title=item.title,  # pyright: ignore
                    permalink=item.permalink,
                    content=item.content,
                    file_path=item.file_path,
                    created_at=item.created_at,
                )
            case SearchItemType.OBSERVATION:
                return ObservationSummary(
                    observation_id=item.id,
                    entity_id=item.entity_id,  # pyright: ignore
                    title=item.title,  # pyright: ignore
                    file_path=item.file_path,
                    category=item.category,  # pyright: ignore
                    content=item.content,  # pyright: ignore
                    permalink=item.permalink,  # pyright: ignore
                    created_at=item.created_at,
                )
            case SearchItemType.RELATION:
                from_title = entity_lookup.get(item.from_id) if item.from_id else None  # pyright: ignore
                to_title = entity_lookup.get(item.to_id) if item.to_id else None
                return RelationSummary(
                    relation_id=item.id,
                    entity_id=item.entity_id,  # pyright: ignore
                    title=item.title,  # pyright: ignore
                    file_path=item.file_path,
                    permalink=item.permalink,  # pyright: ignore
                    relation_type=item.relation_type,  # pyright: ignore
                    from_entity=from_title,
                    from_entity_id=item.from_id,  # pyright: ignore
                    to_entity=to_title,
                    to_entity_id=item.to_id,
                    created_at=item.created_at,
                )
            case _:  # pragma: no cover
                raise ValueError(f"Unexpected type: {item.type}")

    # Process the hierarchical results
    hierarchical_results = []
    for context_item in context_result.results:
        # Process primary result
        primary_result = to_summary(context_item.primary_result)

        # Process observations (always ObservationSummary, validated by context_service)
        observations = [to_summary(obs) for obs in context_item.observations]

        # Process related results
        related = [to_summary(rel) for rel in context_item.related_results]

        # Add to hierarchical results
        hierarchical_results.append(
            ContextResult(
                primary_result=primary_result,
                observations=observations,  # pyright: ignore[reportArgumentType]
                related_results=related,
            )
        )

    # Create schema metadata from service metadata
    metadata = MemoryMetadata(
        uri=context_result.metadata.uri,
        types=context_result.metadata.types,
        depth=context_result.metadata.depth,
        timeframe=context_result.metadata.timeframe,
        generated_at=context_result.metadata.generated_at,
        primary_count=context_result.metadata.primary_count,
        related_count=context_result.metadata.related_count,
        total_results=context_result.metadata.primary_count + context_result.metadata.related_count,
        total_relations=context_result.metadata.total_relations,
        total_observations=context_result.metadata.total_observations,
    )

    # Return new GraphContext with just hierarchical results
    return GraphContext(
        results=hierarchical_results,
        metadata=metadata,
        page=page,
        page_size=page_size,
    )


async def to_search_results(entity_service: EntityService, results: List[SearchIndexRow]):
    search_results = []
    for r in results:
        entities = await entity_service.get_entities_by_id([r.entity_id, r.from_id, r.to_id])  # pyright: ignore

        # Determine which IDs to set based on type
        entity_id = None
        observation_id = None
        relation_id = None

        if r.type == SearchItemType.ENTITY:
            entity_id = r.id
        elif r.type == SearchItemType.OBSERVATION:
            observation_id = r.id
            entity_id = r.entity_id  # Parent entity
        elif r.type == SearchItemType.RELATION:
            relation_id = r.id
            entity_id = r.entity_id  # Parent entity

        search_results.append(
            SearchResult(
                title=r.title,  # pyright: ignore
                type=r.type,  # pyright: ignore
                permalink=r.permalink,
                score=r.score,  # pyright: ignore
                entity=entities[0].permalink if entities else None,
                content=r.content,
                file_path=r.file_path,
                metadata=r.metadata,
                entity_id=entity_id,
                observation_id=observation_id,
                relation_id=relation_id,
                category=r.category,
                from_entity=entities[0].permalink if entities else None,
                to_entity=entities[1].permalink if len(entities) > 1 else None,
                relation_type=r.relation_type,
            )
        )
    return search_results
