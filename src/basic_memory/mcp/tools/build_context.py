"""Build context tool for Basic Memory MCP server."""

from typing import Optional, cast

from loguru import logger
from fastmcp import Context

from basic_memory.mcp.async_client import get_client
from basic_memory.mcp.project_context import get_active_project
from basic_memory.mcp.server import mcp
from basic_memory.schemas.base import TimeFrame
from basic_memory.schemas.memory import (
    EntitySummary,
    GraphContext,
    MemoryUrl,
    ObservationSummary,
    memory_url_path,
)
from basic_memory.dataview.integration import create_dataview_integration


@mcp.tool(
    description="""Build context from a memory:// URI to continue conversations naturally.

    Use this to follow up on previous discussions or explore related topics.

    Memory URL Format:
    - Use paths like "folder/note" or "memory://folder/note"
    - Pattern matching: "folder/*" matches all notes in folder
    - Valid characters: letters, numbers, hyphens, underscores, forward slashes
    - Avoid: double slashes (//), angle brackets (<>), quotes, pipes (|)
    - Examples: "specs/search", "projects/basic-memory", "notes/*"

    Timeframes support natural language like:
    - "2 days ago", "last week", "today", "3 months ago"
    - Or standard formats like "7d", "24h"
    """,
)
async def build_context(
    url: MemoryUrl,
    project: Optional[str] = None,
    depth: str | int | None = 1,
    timeframe: Optional[TimeFrame] = "7d",
    page: int = 1,
    page_size: int = 10,
    max_related: int = 10,
    enable_dataview: bool = True,
    context: Context | None = None,
) -> GraphContext:
    """Get context needed to continue a discussion within a specific project.

    This tool enables natural continuation of discussions by loading relevant context
    from memory:// URIs. It uses pattern matching to find relevant content and builds
    a rich context graph of related information.

    Project Resolution:
    Server resolves projects in this order: Single Project Mode → project parameter → default project.
    If project unknown, use list_memory_projects() or recent_activity() first.

    Args:
        project: Project name to build context from. Optional - server will resolve using hierarchy.
                If unknown, use list_memory_projects() to discover available projects.
        url: memory:// URI pointing to discussion content (e.g. memory://specs/search)
        depth: How many relation hops to traverse (1-3 recommended for performance)
        timeframe: How far back to look. Supports natural language like "2 days ago", "last week"
        page: Page number of results to return (default: 1)
        page_size: Number of results to return per page (default: 10)
        max_related: Maximum number of related results to return (default: 10)
        enable_dataview: Execute Dataview queries in context notes (default: True)
        context: Optional FastMCP context for performance caching.

    Returns:
        GraphContext containing:
            - primary_results: Content matching the memory:// URI
            - related_results: Connected content via relations
            - metadata: Context building details

    Examples:
        # Continue a specific discussion
        build_context("my-project", "memory://specs/search")

        # Get deeper context about a component
        build_context("work-docs", "memory://components/memory-service", depth=2)

        # Look at recent changes to a specification
        build_context("research", "memory://specs/document-format", timeframe="today")

        # Research the history of a feature
        build_context("dev-notes", "memory://features/knowledge-graph", timeframe="3 months ago")

    Raises:
        ToolError: If project doesn't exist or depth parameter is invalid
    """
    logger.info(f"Building context from {url} in project {project}")

    # Convert string depth to integer if needed
    if isinstance(depth, str):
        try:
            depth = int(depth)
        except ValueError:
            from mcp.server.fastmcp.exceptions import ToolError

            raise ToolError(f"Invalid depth parameter: '{depth}' is not a valid integer")

    # URL is already validated and normalized by MemoryUrl type annotation

    async with get_client() as client:
        # Get the active project using the new stateless approach
        active_project = await get_active_project(client, project, context)

        # Import here to avoid circular import
        from basic_memory.mcp.clients import MemoryClient
        from basic_memory.mcp.clients.knowledge import KnowledgeClient

        # Use typed MemoryClient for API calls
        memory_client = MemoryClient(client, active_project.external_id)
        graph_context = await memory_client.build_context(
            memory_url_path(url),
            depth=depth or 1,
            timeframe=timeframe,
            page=page,
            page_size=page_size,
            max_related=max_related,
        )
        
        # Enrich with Dataview if enabled
        if enable_dataview:
            logger.info("Enriching graph context with Dataview queries")
            
            # Fetch all notes for Dataview query execution
            knowledge_client = KnowledgeClient(client, active_project.external_id)
            notes = await knowledge_client.list_entities_for_dataview()
            
            # Create integration with notes_provider
            integration = create_dataview_integration(notes_provider=lambda: notes)
            
            for context_result in graph_context.results:
                # Process primary result if it's an entity with content
                primary = context_result.primary_result
                if primary.type == "entity" and primary.content:
                    try:
                        dataview_results = integration.process_note(primary.content)
                        if dataview_results:
                            # Append Dataview results markdown to content
                            dataview_section = "\n\n---\n## Dataview Query Results\n\n"
                            for result in dataview_results:
                                if result['status'] == 'success' and result.get('result_markdown'):
                                    dataview_section += result['result_markdown'] + "\n\n"
                            if len(dataview_section) > len("\n\n---\n## Dataview Query Results\n\n"):
                                primary.content += dataview_section
                    except Exception as e:
                        logger.warning(f"Failed to process Dataview for primary result: {e}")
                
                # Process related results (only entities and observations have content)
                for related in context_result.related_results:
                    if related.type == "entity":
                        entity = cast(EntitySummary, related)
                        if entity.content:
                            try:
                                dataview_results = integration.process_note(entity.content)
                                if dataview_results:
                                    dataview_section = "\n\n---\n## Dataview Query Results\n\n"
                                    for result in dataview_results:
                                        if result['status'] == 'success' and result.get('result_markdown'):
                                            dataview_section += result['result_markdown'] + "\n\n"
                                    if len(dataview_section) > len("\n\n---\n## Dataview Query Results\n\n"):
                                        entity.content += dataview_section
                            except Exception as e:
                                logger.warning(f"Failed to process Dataview for related entity: {e}")
                    elif related.type == "observation":
                        obs = cast(ObservationSummary, related)
                        if obs.content:
                            try:
                                dataview_results = integration.process_note(obs.content)
                                if dataview_results:
                                    dataview_section = "\n\n---\n## Dataview Query Results\n\n"
                                    for result in dataview_results:
                                        if result['status'] == 'success' and result.get('result_markdown'):
                                            dataview_section += result['result_markdown'] + "\n\n"
                                    if len(dataview_section) > len("\n\n---\n## Dataview Query Results\n\n"):
                                        obs.content += dataview_section
                            except Exception as e:
                                logger.warning(f"Failed to process Dataview for related observation: {e}")
        
        return graph_context
