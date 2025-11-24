"""V2 Prompt Router - ID-based prompt generation operations.

This router uses v2 dependencies for consistent project ID handling.
Prompt endpoints are action-based (not resource-based), so they don't
have entity IDs in URLs - they generate formatted prompts from queries.
"""

from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, status
from loguru import logger

from basic_memory.api.routers.utils import to_graph_context, to_search_results
from basic_memory.api.template_loader import template_loader
from basic_memory.schemas.base import parse_timeframe
from basic_memory.deps import (
    ContextServiceV2Dep,
    EntityRepositoryV2Dep,
    SearchServiceV2Dep,
    EntityServiceV2Dep,
    ProjectIdPathDep,
)
from basic_memory.schemas.prompt import (
    ContinueConversationRequest,
    SearchPromptRequest,
    PromptResponse,
    PromptMetadata,
)
from basic_memory.schemas.search import SearchItemType, SearchQuery

router = APIRouter(prefix="/prompt", tags=["prompt-v2"])


@router.post("/continue-conversation", response_model=PromptResponse)
async def continue_conversation(
    project_id: ProjectIdPathDep,
    search_service: SearchServiceV2Dep,
    entity_service: EntityServiceV2Dep,
    context_service: ContextServiceV2Dep,
    entity_repository: EntityRepositoryV2Dep,
    request: ContinueConversationRequest,
) -> PromptResponse:
    """Generate a prompt for continuing a conversation.

    This endpoint takes a topic and/or timeframe and generates a prompt with
    relevant context from the knowledge base.

    Args:
        project_id: Validated numeric project ID from URL path
        request: The request parameters

    Returns:
        Formatted continuation prompt with context
    """
    logger.info(
        f"V2 Generating continue conversation prompt for project {project_id}, "
        f"topic: {request.topic}, timeframe: {request.timeframe}"
    )

    since = parse_timeframe(request.timeframe) if request.timeframe else None

    # Initialize search results
    search_results = []

    # Get data needed for template
    if request.topic:
        query = SearchQuery(text=request.topic, after_date=request.timeframe)
        results = await search_service.search(query, limit=request.search_items_limit)
        search_results = await to_search_results(entity_service, results)

        # Build context from results
        all_hierarchical_results = []
        for result in search_results:
            if hasattr(result, "permalink") and result.permalink:
                # Get hierarchical context using the new dataclass-based approach
                context_result = await context_service.build_context(
                    result.permalink,
                    depth=request.depth,
                    since=since,
                    max_related=request.related_items_limit,
                    include_observations=True,  # Include observations for entities
                )

                # Process results into the schema format
                graph_context = await to_graph_context(
                    context_result, entity_repository=entity_repository
                )

                # Add results to our collection (limit to top results for each permalink)
                if graph_context.results:
                    all_hierarchical_results.extend(graph_context.results[:3])

        # Limit to a reasonable number of total results
        all_hierarchical_results = all_hierarchical_results[:10]

        template_context = {
            "topic": request.topic,
            "timeframe": request.timeframe,
            "hierarchical_results": all_hierarchical_results,
            "has_results": len(all_hierarchical_results) > 0,
        }
    else:
        # If no topic, get recent activity
        context_result = await context_service.build_context(
            types=[SearchItemType.ENTITY],
            depth=request.depth,
            since=since,
            max_related=request.related_items_limit,
            include_observations=True,
        )
        recent_context = await to_graph_context(context_result, entity_repository=entity_repository)

        hierarchical_results = recent_context.results[:5]  # Limit to top 5 recent items

        template_context = {
            "topic": f"Recent Activity from ({request.timeframe})",
            "timeframe": request.timeframe,
            "hierarchical_results": hierarchical_results,
            "has_results": len(hierarchical_results) > 0,
        }

    try:
        # Render template
        rendered_prompt = await template_loader.render(
            "prompts/continue_conversation.hbs", template_context
        )

        # Calculate metadata
        # Count items of different types
        observation_count = 0
        relation_count = 0
        entity_count = 0

        # Get the hierarchical results from the template context
        hierarchical_results_for_count = template_context.get("hierarchical_results", [])

        # For topic-based search
        if request.topic:
            for item in hierarchical_results_for_count:
                if hasattr(item, "observations"):
                    observation_count += len(item.observations) if item.observations else 0

                if hasattr(item, "related_results"):
                    for related in item.related_results or []:
                        if hasattr(related, "type"):
                            if related.type == "relation":
                                relation_count += 1
                            elif related.type == "entity":  # pragma: no cover
                                entity_count += 1  # pragma: no cover
        # For recent activity
        else:
            for item in hierarchical_results_for_count:
                if hasattr(item, "observations"):
                    observation_count += len(item.observations) if item.observations else 0

                if hasattr(item, "related_results"):
                    for related in item.related_results or []:
                        if hasattr(related, "type"):
                            if related.type == "relation":
                                relation_count += 1
                            elif related.type == "entity":  # pragma: no cover
                                entity_count += 1  # pragma: no cover

        # Build metadata
        metadata = {
            "query": request.topic,
            "timeframe": request.timeframe,
            "search_count": len(search_results)
            if request.topic
            else 0,  # Original search results count
            "context_count": len(hierarchical_results_for_count),
            "observation_count": observation_count,
            "relation_count": relation_count,
            "total_items": (
                len(hierarchical_results_for_count)
                + observation_count
                + relation_count
                + entity_count
            ),
            "search_limit": request.search_items_limit,
            "context_depth": request.depth,
            "related_limit": request.related_items_limit,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

        prompt_metadata = PromptMetadata(**metadata)

        return PromptResponse(
            prompt=rendered_prompt, context=template_context, metadata=prompt_metadata
        )
    except Exception as e:
        logger.error(f"Error rendering continue conversation template: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error rendering prompt template: {str(e)}",
        )


@router.post("/search", response_model=PromptResponse)
async def search_prompt(
    project_id: ProjectIdPathDep,
    search_service: SearchServiceV2Dep,
    entity_service: EntityServiceV2Dep,
    request: SearchPromptRequest,
    page: int = 1,
    page_size: int = 10,
) -> PromptResponse:
    """Generate a prompt for search results.

    This endpoint takes a search query and formats the results into a helpful
    prompt with context and suggestions.

    Args:
        project_id: Validated numeric project ID from URL path
        request: The search parameters
        page: The page number for pagination
        page_size: The number of results per page, defaults to 10

    Returns:
        Formatted search results prompt with context
    """
    logger.info(
        f"V2 Generating search prompt for project {project_id}, "
        f"query: {request.query}, timeframe: {request.timeframe}"
    )

    limit = page_size
    offset = (page - 1) * page_size

    query = SearchQuery(text=request.query, after_date=request.timeframe)
    results = await search_service.search(query, limit=limit, offset=offset)
    search_results = await to_search_results(entity_service, results)

    template_context = {
        "query": request.query,
        "timeframe": request.timeframe,
        "results": search_results,
        "has_results": len(search_results) > 0,
        "result_count": len(search_results),
    }

    try:
        # Render template
        rendered_prompt = await template_loader.render("prompts/search.hbs", template_context)

        # Build metadata
        metadata = {
            "query": request.query,
            "timeframe": request.timeframe,
            "search_count": len(search_results),
            "context_count": len(search_results),
            "observation_count": 0,  # Search results don't include observations
            "relation_count": 0,  # Search results don't include relations
            "total_items": len(search_results),
            "search_limit": limit,
            "context_depth": 0,  # No context depth for basic search
            "related_limit": 0,  # No related items for basic search
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

        prompt_metadata = PromptMetadata(**metadata)

        return PromptResponse(
            prompt=rendered_prompt, context=template_context, metadata=prompt_metadata
        )
    except Exception as e:
        logger.error(f"Error rendering search template: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error rendering prompt template: {str(e)}",
        )
