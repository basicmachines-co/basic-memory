from typing import Optional, List

from basic_memory import telemetry
from basic_memory.models import Entity as EntityModel
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
    with telemetry.scope(
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
                if item.type == SearchItemType.ENTITY:
                    # Entity's own ID for its external_id
                    entity_ids_needed.add(item.id)
                elif item.type == SearchItemType.OBSERVATION:
                    # Parent entity ID for entity_external_id
                    if item.entity_id:  # pyright: ignore
                        entity_ids_needed.add(item.entity_id)  # pyright: ignore
                elif item.type == SearchItemType.RELATION:
                    # Source and target entity IDs for external_ids
                    if item.from_id:  # pyright: ignore
                        entity_ids_needed.add(item.from_id)  # pyright: ignore
                    if item.to_id:
                        entity_ids_needed.add(item.to_id)

        # Batch fetch all entities at once - get both title and external_id
        entity_title_lookup: dict[int, str] = {}
        entity_external_id_lookup: dict[int, str] = {}
        if entity_ids_needed:
            with telemetry.scope(
                "memory.hydrate_context.lookup_entities",
                domain="memory",
                action="build_context",
                phase="lookup_entities",
                result_count=len(entity_ids_needed),
            ):
                entities = await entity_repository.find_by_ids(list(entity_ids_needed))
            for e in entities:
                entity_title_lookup[e.id] = e.title
                entity_external_id_lookup[e.id] = e.external_id

        # Helper function to convert items to summaries
        def to_summary(item: SearchIndexRow | ContextResultRow):
            match item.type:
                case SearchItemType.ENTITY:
                    return EntitySummary(
                        external_id=entity_external_id_lookup.get(item.id, ""),
                        entity_id=item.id,
                        title=item.title,  # pyright: ignore
                        permalink=item.permalink,
                        content=item.content,
                        file_path=item.file_path,
                        created_at=item.created_at,
                    )
                case SearchItemType.OBSERVATION:
                    entity_ext_id = None
                    if item.entity_id:  # pyright: ignore
                        entity_ext_id = entity_external_id_lookup.get(item.entity_id)  # pyright: ignore
                    return ObservationSummary(
                        observation_id=item.id,
                        entity_id=item.entity_id,  # pyright: ignore
                        entity_external_id=entity_ext_id,
                        title=entity_title_lookup.get(item.entity_id),  # pyright: ignore
                        file_path=item.file_path,
                        category=item.category,  # pyright: ignore
                        content=item.content,  # pyright: ignore
                        permalink=item.permalink,  # pyright: ignore
                        created_at=item.created_at,
                    )
                case SearchItemType.RELATION:
                    from_title = entity_title_lookup.get(item.from_id) if item.from_id else None  # pyright: ignore
                    to_title = entity_title_lookup.get(item.to_id) if item.to_id else None
                    from_ext_id = (
                        entity_external_id_lookup.get(item.from_id) if item.from_id else None
                    )  # pyright: ignore
                    to_ext_id = entity_external_id_lookup.get(item.to_id) if item.to_id else None
                    return RelationSummary(
                        relation_id=item.id,
                        entity_id=item.entity_id,  # pyright: ignore
                        title=item.title,  # pyright: ignore
                        file_path=item.file_path,
                        permalink=item.permalink,  # pyright: ignore
                        relation_type=item.relation_type,  # pyright: ignore
                        from_entity=from_title,
                        from_entity_id=item.from_id,  # pyright: ignore
                        from_entity_external_id=from_ext_id,
                        to_entity=to_title,
                        to_entity_id=item.to_id,
                        to_entity_external_id=to_ext_id,
                        created_at=item.created_at,
                    )
                case _:  # pragma: no cover
                    raise ValueError(f"Unexpected type: {item.type}")

        with telemetry.scope(
            "memory.hydrate_context.shape_results",
            domain="memory",
            action="build_context",
            phase="shape_results",
            result_count=len(context_result.results),
        ):
            hierarchical_results = []
            for context_item in context_result.results:
                primary_result = to_summary(context_item.primary_result)
                observations = [to_summary(obs) for obs in context_item.observations]
                related = [to_summary(rel) for rel in context_item.related_results]
                hierarchical_results.append(
                    ContextResult(
                        primary_result=primary_result,
                        observations=observations,  # pyright: ignore[reportArgumentType]
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


async def to_search_results(entity_service: EntityService, results: List[SearchIndexRow]):
    with telemetry.scope(
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
        entities_by_id: dict[int, EntityModel] = {}
        with telemetry.scope(
            "search.hydrate_results.fetch_entities",
            domain="search",
            action="search",
            phase="fetch_entities",
            result_count=len(all_entity_ids),
        ):
            if all_entity_ids:
                entities = await entity_service.get_entities_by_id(list(all_entity_ids))
                entities_by_id = {e.id: e for e in entities}

        search_results = []
        with telemetry.scope(
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
                parent_entity = entities_by_id.get(result.entity_id) if result.entity_id else None  # pyright: ignore
                from_entity = entities_by_id.get(result.from_id) if result.from_id else None  # pyright: ignore
                to_entity = entities_by_id.get(result.to_id) if result.to_id else None

                search_results.append(
                    SearchResult(
                        title=result.title,  # pyright: ignore
                        type=result.type,  # pyright: ignore
                        permalink=result.permalink,
                        score=result.score,  # pyright: ignore
                        entity=parent_entity.permalink if parent_entity else None,
                        content=result.content,
                        matched_chunk=result.matched_chunk_text,
                        file_path=result.file_path,
                        metadata=result.metadata,
                        entity_id=entity_id,
                        observation_id=observation_id,
                        relation_id=relation_id,
                        category=result.category,
                        from_entity=from_entity.permalink if from_entity else None,
                        to_entity=to_entity.permalink if to_entity else None,
                        relation_type=result.relation_type,
                    )
                )
        return search_results
