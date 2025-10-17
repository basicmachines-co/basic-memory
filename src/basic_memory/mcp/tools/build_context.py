"""Build context tool for Basic Memory MCP server."""

from typing import Optional

from loguru import logger
from fastmcp import Context

from basic_memory.mcp.async_client import get_client
from basic_memory.mcp.project_context import get_active_project
from basic_memory.mcp.server import mcp
from basic_memory.mcp.tools.utils import call_get
from basic_memory.schemas.base import TimeFrame
from basic_memory.schemas.memory import (
    GraphContext,
    MemoryUrl,
    memory_url_path,
)

type StringOrInt = str | int


@mcp.tool(
    description="""Navigates your knowledge graph by following relations from a starting point. Builds comprehensive context by traversing semantic connections, perfect for continuing conversations or exploring related concepts.

```yaml
node:
  topic: build_context - Graph Traversal
  goal: Navigate knowledge graph via relations
  insight: Memory URLs enable conversation continuity
  context:
    traversal: Breadth-first with depth control
    patterns: ["memory://exact", "memory://folder/*", "memory://*"]
    performance: O(n^depth) complexity
    use_case: Continue discussions with full context
```

```baml
class BuildContextInput {
  url string @pattern("memory://.*") @description("Memory URI pattern")
  project string?
  depth int @default(1) @range(1, 5) @description("Relation traversal depth")
  timeframe string @default("7d") @description("Period like '2 days ago'")
  types string[]? @description("Filter entity types")
  page int @default(1)
  page_size int @default(10)
  max_related int @default(10)
}

class ContextResult {
  primary_results Note[] @description("Direct matches")
  related_results Note[] @description("Connected via relations")
  metadata ContextMetadata
}

class ContextMetadata {
  depth int
  timeframe string
  primary_count int
  related_count int
  generated_at datetime
}

function build_context(BuildContextInput) -> ContextResult {
  @description("Traverse knowledge graph from memory:// starting points")
  @complexity("O(n^depth)")
  @async(true)
}
```

## Memory Patterns

- `memory://specs/search` - Exact note
- `memory://specs/*` - All in folder
- `memory://*` - Everything

## Traversal Examples
```python
# Continue discussion
context = build_context("memory://discussions/api-design")

# Deep exploration (2 hops)
context = build_context(
    "memory://architecture/microservices",
    depth=2,
    timeframe="30d"
)

# Filtered traversal
context = build_context(
    "memory://specs/*",
    types=["entity", "observation"]
)
```

Performance: Depth 1: 100ms, Depth 2: 500ms, Depth 3+: May be slow""",
)
async def build_context(
    url: MemoryUrl,
    project: Optional[str] = None,
    depth: Optional[StringOrInt] = 1,
    timeframe: Optional[TimeFrame] = "7d",
    page: int = 1,
    page_size: int = 10,
    max_related: int = 10,
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

        # Check migration status and wait briefly if needed
        from basic_memory.mcp.tools.utils import wait_for_migration_or_return_status

        migration_status = await wait_for_migration_or_return_status(
            timeout=5.0, project_name=active_project.name
        )
        if migration_status:  # pragma: no cover
            # Return a proper GraphContext with status message
            from basic_memory.schemas.memory import MemoryMetadata
            from datetime import datetime

            return GraphContext(
                results=[],
                metadata=MemoryMetadata(
                    depth=depth or 1,
                    timeframe=timeframe,
                    generated_at=datetime.now().astimezone(),
                    primary_count=0,
                    related_count=0,
                    uri=migration_status,  # Include status in metadata
                ),
            )
        project_url = active_project.project_url

        response = await call_get(
            client,
            f"{project_url}/memory/{memory_url_path(url)}",
            params={
                "depth": depth,
                "timeframe": timeframe,
                "page": page,
                "page_size": page_size,
                "max_related": max_related,
            },
        )
        return GraphContext.model_validate(response.json())
