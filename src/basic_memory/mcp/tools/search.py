"""Search tools for Basic Memory MCP server."""

from textwrap import dedent
from typing import List, Optional

from loguru import logger
from fastmcp import Context

from basic_memory.mcp.async_client import client
from basic_memory.mcp.project_context import get_active_project
from basic_memory.mcp.server import mcp
from basic_memory.mcp.tools.utils import call_post
from basic_memory.schemas.search import SearchItemType, SearchQuery, SearchResponse


def _format_search_error_response(error_message: str, query: str, search_type: str = "text") -> str:
    """Format helpful error responses for search failures that guide users to successful searches."""

    # FTS5 syntax errors
    if "syntax error" in error_message.lower() or "fts5" in error_message.lower():
        clean_query = (
            query.replace('"', "")
            .replace("(", "")
            .replace(")", "")
            .replace("+", "")
            .replace("*", "")
        )
        return dedent(f"""
            # Search Failed - Invalid Syntax

            The search query '{query}' contains invalid syntax that the search engine cannot process.

            ## Common syntax issues:
            1. **Special characters**: Characters like `+`, `*`, `"`, `(`, `)` have special meaning in search
            2. **Unmatched quotes**: Make sure quotes are properly paired
            3. **Invalid operators**: Check AND, OR, NOT operators are used correctly

            ## How to fix:
            1. **Simplify your search**: Try using simple words instead: `{clean_query}`
            2. **Remove special characters**: Use alphanumeric characters and spaces
            3. **Use basic boolean operators**: `word1 AND word2`, `word1 OR word2`, `word1 NOT word2`

            ## Examples of valid searches:
            - Simple text: `project planning`
            - Boolean AND: `project AND planning`
            - Boolean OR: `meeting OR discussion`
            - Boolean NOT: `project NOT archived`
            - Grouped: `(project OR planning) AND notes`
            - Exact phrases: `"weekly standup meeting"`
            - Content-specific: `tag:example` or `category:observation`

            ## Try again with:
            ```
            search_notes("{clean_query}")
            ```

            ## Alternative search strategies:
            - Break into simpler terms: `search_notes("{" ".join(clean_query.split()[:2])}")`
            - Try different search types: `search_notes("{clean_query}", search_type="title")`
            - Use filtering: `search_notes("{clean_query}", types=["entity"])`
            """).strip()

    # Project not found errors (check before general "not found")
    if "project not found" in error_message.lower():
        return dedent(f"""
            # Search Failed - Project Not Found

            The current project is not accessible or doesn't exist: {error_message}

            ## How to resolve:
            1. **Check available projects**: `list_projects()`
            2. **Switch to valid project**: `switch_project("valid-project-name")`
            3. **Verify project setup**: Ensure your project is properly configured

            ## Current session info:
            - Check current project: `get_current_project()`
            - See available projects: `list_projects()`
            """).strip()

    # No results found
    if "no results" in error_message.lower() or "not found" in error_message.lower():
        simplified_query = (
            " ".join(query.split()[:2])
            if len(query.split()) > 2
            else query.split()[0]
            if query.split()
            else "notes"
        )
        return dedent(f"""
            # Search Complete - No Results Found

            No content found matching '{query}' in the current project.

            ## Search strategy suggestions:
            1. **Broaden your search**: Try fewer or more general terms
               - Instead of: `{query}`
               - Try: `{simplified_query}`

            2. **Check spelling and try variations**:
               - Verify terms are spelled correctly
               - Try synonyms or related terms

            3. **Use different search approaches**:
               - **Text search**: `search_notes("{query}", search_type="text")` (searches full content)
               - **Title search**: `search_notes("{query}", search_type="title")` (searches only titles)
               - **Permalink search**: `search_notes("{query}", search_type="permalink")` (searches file paths)

            4. **Try boolean operators for broader results**:
               - OR search: `search_notes("{" OR ".join(query.split()[:3])}")`
               - Remove restrictive terms: Focus on the most important keywords

            5. **Use filtering to narrow scope**:
               - By content type: `search_notes("{query}", types=["entity"])`
               - By recent content: `search_notes("{query}", after_date="1 week")`
               - By entity type: `search_notes("{query}", entity_types=["observation"])`

            6. **Try advanced search patterns**:
               - Tag search: `search_notes("tag:your-tag")`
               - Category search: `search_notes("category:observation")`
               - Pattern matching: `search_notes("*{query}*", search_type="permalink")`

            ## Explore what content exists:
            - **Recent activity**: `recent_activity(timeframe="7d")` - See what's been updated recently
            - **List directories**: `list_directory("/")` - Browse all content
            - **Browse by folder**: `list_directory("/notes")` or `list_directory("/docs")`
            - **Check project**: `get_current_project()` - Verify you're in the right project
            """).strip()

    # Server/API errors
    if "server error" in error_message.lower() or "internal" in error_message.lower():
        return dedent(f"""
            # Search Failed - Server Error

            The search service encountered an error while processing '{query}': {error_message}

            ## Immediate steps:
            1. **Try again**: The error might be temporary
            2. **Simplify the query**: Use simpler search terms
            3. **Check project status**: Ensure your project is properly synced

            ## Alternative approaches:
            - Browse files directly: `list_directory("/")`
            - Check recent activity: `recent_activity(timeframe="7d")`
            - Try a different search type: `search_notes("{query}", search_type="title")`

            ## If the problem persists:
            The search index might need to be rebuilt. Send a message to support@basicmachines.co or check the project sync status.
            """).strip()

    # Permission/access errors
    if (
        "permission" in error_message.lower()
        or "access" in error_message.lower()
        or "forbidden" in error_message.lower()
    ):
        return f"""# Search Failed - Access Error

You don't have permission to search in the current project: {error_message}

## How to resolve:
1. **Check your project access**: Verify you have read permissions for this project
2. **Switch projects**: Try searching in a different project you have access to
3. **Check authentication**: You might need to re-authenticate

## Alternative actions:
- List available projects: `list_projects()`
- Switch to accessible project: `switch_project("project-name")`
- Check current project: `get_current_project()`"""

    # Generic fallback
    return f"""# Search Failed

Error searching for '{query}': {error_message}

## Troubleshooting steps:
1. **Simplify your query**: Try basic words without special characters
2. **Check search syntax**: Ensure boolean operators are correctly formatted
3. **Verify project access**: Make sure you can access the current project
4. **Test with simple search**: Try `search_notes("test")` to verify search is working

## Alternative search approaches:
- **Different search types**: 
  - Title only: `search_notes("{query}", search_type="title")`
  - Permalink patterns: `search_notes("{query}*", search_type="permalink")`
- **With filters**: `search_notes("{query}", types=["entity"])`
- **Recent content**: `search_notes("{query}", after_date="1 week")`
- **Boolean variations**: `search_notes("{" OR ".join(query.split()[:2])}")`

## Explore your content:
- **Browse files**: `list_directory("/")` - See all available content
- **Recent activity**: `recent_activity(timeframe="7d")` - Check what's been updated
- **Project info**: `get_current_project()` - Verify current project
- **All projects**: `list_projects()` - Switch to different project if needed

## Search syntax reference:
- **Basic**: `keyword` or `multiple words`
- **Boolean**: `term1 AND term2`, `term1 OR term2`, `term1 NOT term2`
- **Phrases**: `"exact phrase"`
- **Grouping**: `(term1 OR term2) AND term3`
- **Patterns**: `tag:example`, `category:observation`"""


@mcp.tool(
    description="Search across all content in the knowledge base with advanced syntax support.",
)
async def search_notes(
    query: str,
    page: int = 1,
    page_size: int = 10,
    search_type: str = "text",
    types: Optional[List[str]] = None,
    entity_types: Optional[List[str]] = None,
    after_date: Optional[str] = None,
    project: Optional[str] = None,
    context: Context | None = None,
) -> SearchResponse | str:
    """Search across all content in the knowledge base with comprehensive syntax support.

    This tool searches the knowledge base using full-text search, pattern matching,
    or exact permalink lookup. It supports filtering by content type, entity type,
    and date, with advanced boolean and phrase search capabilities.

    ## Search Syntax Examples

    ### Basic Searches
    - `search_notes("keyword")` - Find any content containing "keyword"
    - `search_notes("exact phrase")` - Search for exact phrase match

    ### Advanced Boolean Searches
    - `search_notes("term1 term2")` - Find content with both terms (implicit AND)
    - `search_notes("term1 AND term2")` - Explicit AND search (both terms required)
    - `search_notes("term1 OR term2")` - Either term can be present
    - `search_notes("term1 NOT term2")` - Include term1 but exclude term2
    - `search_notes("(project OR planning) AND notes")` - Grouped boolean logic

    ### Content-Specific Searches
    - `search_notes("tag:example")` - Search within specific tags (if supported by content)
    - `search_notes("category:observation")` - Filter by observation categories
    - `search_notes("author:username")` - Find content by author (if metadata available)

    ### Search Type Examples
    - `search_notes("Meeting", search_type="title")` - Search only in titles
    - `search_notes("docs/meeting-*", search_type="permalink")` - Pattern match permalinks
    - `search_notes("keyword", search_type="text")` - Full-text search (default)

    ### Filtering Options
    - `search_notes("query", types=["entity"])` - Search only entities
    - `search_notes("query", types=["note", "person"])` - Multiple content types
    - `search_notes("query", entity_types=["observation"])` - Filter by entity type
    - `search_notes("query", after_date="2024-01-01")` - Recent content only
    - `search_notes("query", after_date="1 week")` - Relative date filtering

    ### Advanced Pattern Examples
    - `search_notes("project AND (meeting OR discussion)")` - Complex boolean logic
    - `search_notes("\"exact phrase\" AND keyword")` - Combine phrase and keyword search
    - `search_notes("bug NOT fixed")` - Exclude resolved issues
    - `search_notes("docs/2024-*", search_type="permalink")` - Year-based permalink search

    Args:
        query: The search query string (supports boolean operators, phrases, patterns)
        page: The page number of results to return (default 1)
        page_size: The number of results to return per page (default 10)
        search_type: Type of search to perform, one of: "text", "title", "permalink" (default: "text")
        types: Optional list of note types to search (e.g., ["note", "person"])
        entity_types: Optional list of entity types to filter by (e.g., ["entity", "observation"])
        after_date: Optional date filter for recent content (e.g., "1 week", "2d", "2024-01-01")
        project: Optional project name to search in. If not provided, uses current active project.

    Returns:
        SearchResponse with results and pagination info, or helpful error guidance if search fails

    Examples:
        # Basic text search
        results = await search_notes("project planning")

        # Boolean AND search (both terms must be present)
        results = await search_notes("project AND planning")

        # Boolean OR search (either term can be present)
        results = await search_notes("project OR meeting")

        # Boolean NOT search (exclude terms)
        results = await search_notes("project NOT meeting")

        # Boolean search with grouping
        results = await search_notes("(project OR planning) AND notes")

        # Exact phrase search
        results = await search_notes("\"weekly standup meeting\"")

        # Search with type filter
        results = await search_notes(
            query="meeting notes",
            types=["entity"],
        )

        # Search with entity type filter
        results = await search_notes(
            query="meeting notes",
            entity_types=["observation"],
        )

        # Search for recent content
        results = await search_notes(
            query="bug report",
            after_date="1 week"
        )

        # Pattern matching on permalinks
        results = await search_notes(
            query="docs/meeting-*",
            search_type="permalink"
        )

        # Search in specific project
        results = await search_notes("meeting notes", project="work-project")

        # Complex search with multiple filters
        results = await search_notes(
            query="(bug OR issue) AND NOT resolved",
            types=["entity"],
            after_date="2024-01-01"
        )
    """
    # Create a SearchQuery object based on the parameters
    search_query = SearchQuery()

    # Set the appropriate search field based on search_type
    if search_type == "text":
        search_query.text = query
    elif search_type == "title":
        search_query.title = query
    elif search_type == "permalink" and "*" in query:
        search_query.permalink_match = query
    elif search_type == "permalink":
        search_query.permalink = query
    else:
        search_query.text = query  # Default to text search

    # Add optional filters if provided
    if entity_types:
        search_query.entity_types = [SearchItemType(t) for t in entity_types]
    if types:
        search_query.types = types
    if after_date:
        search_query.after_date = after_date

    active_project = await get_active_project(client, context=context, project_override=project)
    project_url = active_project.project_url

    logger.info(f"Searching for {search_query}")

    try:
        response = await call_post(
            client,
            f"{project_url}/search/",
            json=search_query.model_dump(),
            params={"page": page, "page_size": page_size},
        )
        result = SearchResponse.model_validate(response.json())

        # Check if we got no results and provide helpful guidance
        if not result.results:
            logger.info(f"Search returned no results for query: {query}")
            # Don't treat this as an error, but the user might want guidance
            # We return the empty result as normal - the user can decide if they need help

        return result

    except Exception as e:
        logger.error(f"Search failed for query '{query}': {e}")
        # Return formatted error message as string for better user experience
        return _format_search_error_response(str(e), query, search_type)
