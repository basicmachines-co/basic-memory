"""Read note tool for Basic Memory MCP server."""

from textwrap import dedent
from typing import Optional, Literal

import yaml

from loguru import logger
from fastmcp import Context

from basic_memory.config import ConfigManager
from basic_memory.mcp.project_context import (
    detect_project_from_url_prefix,
    get_project_client,
    resolve_project_and_path,
)
from basic_memory.mcp.server import mcp
from basic_memory.mcp.tools.search import search_notes
from basic_memory.schemas.memory import memory_url_path
from basic_memory.utils import validate_project_path


def _is_exact_title_match(identifier: str, title: str) -> bool:
    """Return True when identifier exactly matches a title (case-insensitive)."""
    return identifier.strip().casefold() == title.strip().casefold()


def _parse_opening_frontmatter(content: str) -> tuple[str, dict | None]:
    """Parse opening YAML frontmatter and return (body, frontmatter).

    Mirrors CLI behavior: only parses a frontmatter block at the very top.
    If parsing fails or frontmatter is not a mapping, returns body unchanged and None.
    """
    original_content = content
    if not content.startswith("---\n"):
        return original_content, None

    lines = content.splitlines(keepends=True)
    closing_index = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            closing_index = i
            break

    if closing_index is None:
        return original_content, None

    fm_text = "".join(lines[1:closing_index])
    try:
        parsed = yaml.safe_load(fm_text)
    except yaml.YAMLError:
        return original_content, None

    if parsed is None:
        parsed = {}
    if not isinstance(parsed, dict):
        return original_content, None

    body_content = "".join(lines[closing_index + 1 :])
    return body_content, parsed


@mcp.tool(
    description="Read a markdown note by title or permalink.",
    # TODO: re-enable once MCP client rendering is working
    # meta={"ui/resourceUri": "ui://basic-memory/note-preview"},
    annotations={"readOnlyHint": True, "openWorldHint": False},
)
async def read_note(
    identifier: str,
    project: Optional[str] = None,
    workspace: Optional[str] = None,
    page: int = 1,
    page_size: int = 10,
    output_format: Literal["text", "json"] = "text",
    include_frontmatter: bool = False,
    context: Context | None = None,
) -> str | dict:
    """Return the raw markdown for a note, or guidance text if no match is found.

    Finds and retrieves a note by its title, permalink, or content search,
    returning the raw markdown content including observations, relations, and metadata.

    Project Resolution:
    Server resolves projects using a unified priority chain (same in local and cloud modes):
    Single Project Mode → project parameter → default project.
    Uses default project automatically. Specify `project` parameter to target a different project.

    This tool will try multiple lookup strategies to find the most relevant note:
    1. Direct permalink lookup
    2. Title search fallback
    3. Text search as last resort

    Args:
        project: Project name to read from. Optional - server will resolve using the
                hierarchy above. If unknown, use list_memory_projects() to discover
                available projects.
        identifier: The title or permalink of the note to read
                   Can be a full memory:// URL, a permalink, a title, or search text
        page: Page number for paginated results (default: 1)
        page_size: Number of items per page (default: 10)
        output_format: "text" returns markdown content or guidance text.
            "json" returns a structured object with title/permalink/file_path/content/frontmatter.
        include_frontmatter: When output_format="json", whether content should include the
            opening YAML frontmatter block.
        context: Optional FastMCP context for performance caching.

    Returns:
        The full markdown content of the note if found, or helpful guidance if not found.
        Content includes frontmatter, observations, relations, and all markdown formatting.

    Examples:
        # Read by permalink
        read_note("my-research", "specs/search-spec")

        # Read by title
        read_note("work-project", "Search Specification")

        # Read with memory URL
        read_note("my-research", "memory://specs/search-spec")

        # Read with pagination
        read_note("work-project", "Project Updates", page=2, page_size=5)

        # Read recent meeting notes
        read_note("team-docs", "Weekly Standup")

    Raises:
        HTTPError: If project doesn't exist or is inaccessible
        SecurityError: If identifier attempts path traversal

    Note:
        If the exact note isn't found, this tool provides helpful suggestions
        including related notes, search commands, and note creation templates.
    """
    # Detect project from memory URL prefix before routing
    if project is None:
        detected = detect_project_from_url_prefix(identifier, ConfigManager().config)
        if detected:
            project = detected

    async with get_project_client(project, workspace, context) as (client, active_project):
        # Resolve identifier with project-prefix awareness for memory:// URLs
        _, entity_path, _ = await resolve_project_and_path(client, identifier, project, context)

        # Validate identifier to prevent path traversal attacks
        # For memory:// URLs, validate the extracted path (not the raw URL which
        # has a scheme prefix that confuses path validation)
        raw_path = memory_url_path(identifier) if identifier.startswith("memory://") else identifier
        processed_path = entity_path
        project_path = active_project.home

        if not validate_project_path(raw_path, project_path) or not validate_project_path(
            processed_path, project_path
        ):
            logger.warning(
                "Attempted path traversal attack blocked",
                identifier=identifier,
                processed_path=processed_path,
                project=active_project.name,
            )
            if output_format == "json":
                return {
                    "title": None,
                    "permalink": None,
                    "file_path": None,
                    "content": None,
                    "frontmatter": None,
                    "error": "SECURITY_VALIDATION_ERROR",
                }
            return f"# Error\n\nIdentifier '{identifier}' is not allowed - paths must stay within project boundaries"

        # Get the file via REST API - first try direct identifier resolution
        logger.info(
            f"Attempting to read note from Project: {active_project.name} identifier: {entity_path}"
        )

        # Import here to avoid circular import
        from basic_memory.mcp.clients import KnowledgeClient, ResourceClient

        # Use typed clients for API calls
        knowledge_client = KnowledgeClient(client, active_project.external_id)
        resource_client = ResourceClient(client, active_project.external_id)

        async def _read_json_payload(entity_id: str) -> dict:
            entity = await knowledge_client.get_entity(entity_id)
            response = await resource_client.read(entity_id, page=page, page_size=page_size)
            content_text = response.text
            body_content, parsed_frontmatter = _parse_opening_frontmatter(content_text)
            return {
                "title": entity.title,
                "permalink": entity.permalink,
                "file_path": entity.file_path,
                "content": content_text if include_frontmatter else body_content,
                "frontmatter": parsed_frontmatter,
            }

        def _empty_json_payload() -> dict:
            return {
                "title": None,
                "permalink": None,
                "file_path": None,
                "content": None,
                "frontmatter": None,
            }

        def _search_results(payload: object) -> list[dict]:
            if not isinstance(payload, dict):
                return []
            results = payload.get("results")
            return results if isinstance(results, list) else []

        def _result_title(item: dict) -> str:
            return str(item.get("title") or "")

        def _result_permalink(item: dict) -> Optional[str]:
            value = item.get("permalink")
            return str(value) if value else None

        def _result_file_path(item: dict) -> Optional[str]:
            value = item.get("file_path")
            return str(value) if value else None

        try:
            # Try to resolve identifier to entity ID
            entity_id = await knowledge_client.resolve_entity(entity_path, strict=True)

            # Fetch content using entity ID
            response = await resource_client.read(entity_id, page=page, page_size=page_size)

            # If successful, return the content
            if response.status_code == 200:
                logger.info("Returning read_note result from resource: {path}", path=entity_path)
                if output_format == "json":
                    return await _read_json_payload(entity_id)
                return response.text
        except Exception as e:  # pragma: no cover
            logger.info(f"Direct lookup failed for '{entity_path}': {e}")
            # Continue to fallback methods

        # Fallback 1: Try title search via API
        logger.info(f"Search title for: {identifier}")
        title_results = await search_notes(
            query=identifier,
            search_type="title",
            project=active_project.name,
            workspace=workspace,
            output_format="json",
            context=context,
        )

        title_candidates = _search_results(title_results)
        if title_candidates:
            # Trigger: direct resolution failed and title search returned candidates.
            # Why: avoid returning unrelated notes when search yields only fuzzy matches.
            # Outcome: fetch content only when a true exact title match exists.
            result = next(
                (
                    candidate
                    for candidate in title_candidates
                    if _is_exact_title_match(identifier, _result_title(candidate))
                ),
                None,
            )
            if not result:
                logger.info(f"No exact title match found for: {identifier}")
            elif _result_permalink(result):
                try:
                    # Resolve the permalink to entity ID
                    entity_id = await knowledge_client.resolve_entity(
                        _result_permalink(result) or "", strict=True
                    )

                    # Fetch content using the entity ID
                    response = await resource_client.read(entity_id, page=page, page_size=page_size)

                    if response.status_code == 200:
                        logger.info(
                            f"Found note by exact title search: {_result_permalink(result)}"
                        )
                        if output_format == "json":
                            return await _read_json_payload(entity_id)
                        return response.text
                except Exception as e:  # pragma: no cover
                    logger.info(
                        f"Failed to fetch content for found title match {_result_permalink(result)}: {e}"
                    )
        else:
            logger.info(
                f"No results in title search for: {identifier} in project {active_project.name}"
            )

        # Fallback 2: Text search as a last resort
        logger.info(f"Title search failed, trying text search for: {identifier}")
        text_results = await search_notes(
            query=identifier,
            search_type="text",
            project=active_project.name,
            workspace=workspace,
            output_format="json",
            context=context,
        )

        # We didn't find a direct match, construct a helpful error message
        text_candidates = _search_results(text_results)
        if not text_candidates:
            if output_format == "json":
                return _empty_json_payload()
            return format_not_found_message(active_project.name, identifier)
        if output_format == "json":
            payload = _empty_json_payload()
            payload["related_results"] = [
                {
                    "title": _result_title(result),
                    "permalink": _result_permalink(result),
                    "file_path": _result_file_path(result),
                }
                for result in text_candidates[:5]
            ]
            return payload
        return format_related_results(active_project.name, identifier, text_candidates[:5])


def format_not_found_message(project: str | None, identifier: str) -> str:
    """Format a helpful message when no note was found."""
    return dedent(f"""
        # Note Not Found in {project}: "{identifier}"

        I couldn't find any notes matching "{identifier}". Here are some suggestions:

        ## Check Identifier Type
        - If you provided a title, try using the exact permalink instead
        - If you provided a permalink, check for typos or try a broader search

        ## Search Instead
        Try searching for related content:
        ```
        search_notes(project="{project}", query="{identifier}")
        ```

        ## Recent Activity
        Check recently modified notes:
        ```
        recent_activity(timeframe="7d")
        ```

        ## Create New Note
        This might be a good opportunity to create a new note on this topic:
        ```
        write_note(
            project="{project}",
            title="{identifier.capitalize()}",
            content='''
            # {identifier.capitalize()}

            ## Overview
            [Your content here]

            ## Observations
            - [category] [Observation about {identifier}]

            ## Relations
            - relates_to [[Related Topic]]
            ''',
            folder="notes"
        )
        ```
    """)


def format_related_results(project: str | None, identifier: str, results) -> str:
    """Format a helpful message with related results when an exact match wasn't found."""
    message = dedent(f"""
        # Note Not Found in {project}: "{identifier}"

        I couldn't find an exact match for "{identifier}", but I found some related notes:

        """)

    for i, result in enumerate(results):
        title = result.get("title") if isinstance(result, dict) else getattr(result, "title", None)
        permalink = (
            result.get("permalink")
            if isinstance(result, dict)
            else getattr(result, "permalink", None)
        )
        result_type = (
            result.get("type") if isinstance(result, dict) else getattr(result, "type", None)
        )
        normalized_type = (
            result_type
            if isinstance(result_type, str)
            else str(getattr(result_type, "value", result_type))
            if result_type is not None
            else None
        )

        message += dedent(f"""
            ## {i + 1}. {title or "Untitled"}
            - **Type**: {normalized_type or "entity"}
            - **Permalink**: {permalink or "unknown"}

            You can read this note with:
            ```
            read_note(project="{project}", identifier="{permalink or ""}")
            ```

            """)

    message += dedent(f"""
        ## Try More Specific Lookup
        For exact matches, try using the full permalink from one of the results above.

        ## Search For More Results
        To see more related content:
        ```
        search_notes(project="{project}", query="{identifier}")
        ```

        ## Create New Note
        If none of these match what you're looking for, consider creating a new note:
        ```
        write_note(
            project="{project}",
            title="[Your title]",
            content="[Your content]",
            folder="notes"
        )
        ```
    """)

    return message
