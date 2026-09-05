"""Read note tool for Basic Memory MCP server."""

from textwrap import dedent
from typing import Any, Annotated, Optional, Literal, cast
from uuid import UUID

import logfire

from loguru import logger
from fastmcp import Context
from pydantic import AliasChoices, Field

from basic_memory.config import ConfigManager
from basic_memory.mcp.project_context import (
    detect_project_from_identifier_prefix,
    get_project_client,
    resolve_project_and_path,
)
from basic_memory.mcp.note_reads import (
    parse_opening_frontmatter,
    read_note_json_by_external_id,
)
from basic_memory.mcp.server import mcp
from basic_memory.mcp.tools.search import search_notes
from basic_memory.schemas.memory import memory_url_path
from basic_memory.utils import validate_project_path

# The title-match fallback exists to find THE note by exact title, so it scans
# fixed-size pages of title results instead of the caller's page/page_size
# (which apply only to the text-search suggestion listing).
_TITLE_LOOKUP_PAGE_SIZE = 10

# Hard safety cap on title-lookup pages. The loop normally stops as soon as an
# exact match is found or results run out (has_more=False); the cap only bounds
# pathological knowledge bases where hundreds of fuzzy titles contain the
# queried phrase. Exhausting the cap falls through to the suggestion behavior.
_TITLE_LOOKUP_MAX_PAGES = 10


def _is_exact_title_match(identifier: str, title: str) -> bool:
    """Return True when identifier exactly matches a title (case-insensitive)."""
    return identifier.strip().casefold() == title.strip().casefold()


def _parse_opening_frontmatter(content: str) -> tuple[str, dict[str, Any] | None]:
    """Retain the existing test/import surface for the shared parser."""
    return parse_opening_frontmatter(content)


def _exact_external_id(identifier: str) -> str | None:
    """Return the canonical UUID when the whole identifier is an external ID."""
    try:
        return str(UUID(identifier.strip()))
    except ValueError:
        return None


@mcp.tool(
    title="Read Note",
    description="Read a markdown note by title or permalink.",
    tags={"notes"},
    # TODO: re-enable once MCP client rendering is working
    # meta={"ui/resourceUri": "ui://basic-memory/note-preview"},
    annotations={
        "title": "Read Note",
        "readOnlyHint": True,
        "destructiveHint": False,
        "openWorldHint": False,
    },
)
async def read_note(
    identifier: str,
    project: Optional[str] = None,
    project_id: Optional[str] = None,
    # Accept common pagination aliases models reach for from training data
    # (page_number/limit/per_page), matching the sibling navigation tools
    # (search_notes, build_context, recent_activity). The schema advertises
    # only the canonical names; aliases are silently mapped at validation time.
    # `offset` is intentionally NOT aliased: offset is item-indexed (skip N
    # items) while page is a 1-indexed page-number, so direct aliasing would
    # return the wrong slice.
    page: Annotated[
        int,
        Field(default=1, validation_alias=AliasChoices("page", "page_number")),
    ] = 1,
    page_size: Annotated[
        int,
        Field(default=10, validation_alias=AliasChoices("page_size", "limit", "per_page")),
    ] = 10,
    output_format: Literal["text", "json"] = "text",
    include_frontmatter: bool = False,
    context: Context | None = None,
) -> str | dict[str, Any]:
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
        project_id: Project external_id (UUID). Prefer this over `project` when known —
                it routes to the exact project regardless of name collisions across cloud
                workspaces. Takes precedence over `project`. Get from list_memory_projects().
        identifier: The title or permalink of the note to read.
                   Can be a full memory:// URL, a permalink, a title, or search text.
                   From the CLI this is a positional argument, not a flag.
        page: Page of fallback-search results to use when the identifier does not
            resolve to a note directly (default: 1). A direct or exact-title match
            always returns the full note content — page/page_size never chunk the
            note itself, and the title-match lookup pages through fixed-size pages
            of title results until an exact match is found or results are
            exhausted, regardless of page or page_size. Aliases: page_number.
        page_size: Number of fallback-search results per page (default: 10). When no
            match is found, this caps how many related-note suggestions are listed.
            Aliases: limit, per_page.
        output_format: "text" returns markdown content or guidance text.
            "json" returns a structured object with title/permalink/file_path/content/frontmatter.
        include_frontmatter: When output_format="json", whether content should include the
            opening YAML frontmatter block; the parsed frontmatter object is returned either
            way. The CLI flag is --frontmatter (--include-frontmatter is a deprecated alias).
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

        # Read recent meeting notes
        read_note("team-docs", "Weekly Standup")

        # Page through fallback-search suggestions when nothing matches directly
        read_note("unknown topic", page=2, page_size=5)

    Raises:
        HTTPError: If project doesn't exist or is inaccessible
        SecurityError: If identifier attempts path traversal

    Note:
        If the exact note isn't found, this tool provides helpful suggestions
        including related notes, search commands, and note creation templates.
    """
    # Trigger: page < 1 or page_size < 1 (e.g. page_size=0 or negative).
    # Why: both flow into the fallback search's server-side slicing, where
    #      non-positive values produce empty result pages with unreachable
    #      pagination. Fail fast, matching search_notes/build_context.
    if page < 1:
        raise ValueError(f"page must be >= 1, got {page}")
    if page_size < 1:
        raise ValueError(f"page_size must be >= 1, got {page_size}")

    # Detect project from a memory URL or permalink prefix before routing.
    # project_id routes by external UUID, so it bypasses URL discovery entirely.
    if project is None and project_id is None:
        detected = await detect_project_from_identifier_prefix(
            identifier,
            ConfigManager().config,
            context=context,
        )
        if detected is not None:
            # The id rides along so the name is never re-resolved against a
            # different accessible workspace holding the same permalink (#1432).
            project, project_id = detected.project, detected.project_id

    with logfire.span(
        "mcp.tool.read_note",
        entrypoint="mcp",
        tool_name="read_note",
        requested_project=project,
        requested_project_id=project_id,
        page=page,
        page_size=page_size,
        output_format=output_format,
        include_frontmatter=include_frontmatter,
    ):
        async with get_project_client(project, context=context, project_id=project_id) as (
            client,
            active_project,
        ):
            # Resolve identifier with project-prefix awareness for memory:// URLs.
            # Pass active_project.name (the canonical resolved name) rather than the
            # original `project` arg so the inner get_active_project cache hits even
            # when project_id was used or `project` was wrong/ambiguous.
            _, entity_path, _ = await resolve_project_and_path(
                client, identifier, active_project.name, context
            )

            # Validate identifier to prevent path traversal attacks
            # For memory:// URLs, validate the extracted path (not the raw URL which
            # has a scheme prefix that confuses path validation)
            raw_path = (
                memory_url_path(identifier) if identifier.startswith("memory://") else identifier
            )
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

            def _empty_json_payload() -> dict[str, Any]:
                return {
                    "title": None,
                    "permalink": None,
                    "file_path": None,
                    "content": None,
                    "frontmatter": None,
                }

            def _search_results(payload: object) -> list[dict[str, object]]:
                if not isinstance(payload, dict):
                    return []
                payload_dict = cast(dict[str, object], payload)
                results = payload_dict.get("results")
                if not isinstance(results, list):
                    return []
                return [
                    cast(dict[str, object], result)
                    for result in results
                    if isinstance(result, dict)
                ]

            async def _search_candidates(
                identifier_text: str, *, title_only: bool, lookup_page: int = 1
            ) -> dict[str, object]:
                # Trigger: direct entity resolution failed for the caller's identifier.
                # Why: search_notes applies the same memory:// normalization and tool-level
                #      query handling as the rest of MCP routing, which raw client calls skip.
                # Outcome: unresolved memory URLs still fall back through normalized search.
                # Pass project_id (external_id UUID) so the workspace selection from the
                # outer get_project_client() is preserved across the inner re-resolution.
                # Without this, project names that collide across workspaces could re-resolve
                # to a different tenant via the default-workspace fallback (CLI/context=None).
                search_type = "title" if title_only else "text"
                # Trigger: title_only — the title search exists to find THE note by
                #          exact title, not to page through suggestions.
                # Why: paginating it by the caller's page would skip an exact match
                #      sitting on page 1 (read_note("Exact Title", page=2)), and a
                #      small caller page_size could let a higher-ranked fuzzy title
                #      displace the exact match out of the lookup window
                #      (read_note("Foo Bar", page_size=1) when "Foo Bar Foo Bar"
                #      ranks first) — both returning suggestions instead of the note.
                # Outcome: title lookup uses its own lookup_page with a fixed lookup
                #          size, walked by the caller below; caller page/page_size
                #          apply only to the text-search suggestion listing.
                response = await search_notes(
                    project=active_project.name,
                    project_id=active_project.external_id,
                    query=identifier_text,
                    search_type=search_type,
                    page=lookup_page if title_only else page,
                    page_size=_TITLE_LOOKUP_PAGE_SIZE if title_only else page_size,
                    output_format="json",
                    context=context,
                )
                return cast(dict[str, object], response) if isinstance(response, dict) else {}

            def _result_title(item: dict[str, object]) -> str:
                return str(item.get("title") or "")

            def _result_permalink(item: dict[str, object]) -> Optional[str]:
                value = item.get("permalink")
                return str(value) if value else None

            def _result_file_path(item: dict[str, object]) -> Optional[str]:
                value = item.get("file_path")
                return str(value) if value else None

            def _result_external_id(item: dict[str, object]) -> str | None:
                value = item.get("external_id")
                return value if isinstance(value, str) and value else None

            if output_format == "json":
                exact_external_id = _exact_external_id(entity_path)
                if exact_external_id is not None:
                    return dict(
                        await read_note_json_by_external_id(
                            knowledge_client=knowledge_client,
                            resource_client=resource_client,
                            entity_external_id=exact_external_id,
                            include_frontmatter=include_frontmatter,
                        )
                    )

                try:
                    entity_id = await knowledge_client.resolve_entity(entity_path, strict=True)
                except Exception as error:  # pragma: no cover
                    logger.info(f"Direct lookup failed for '{entity_path}': {error}")
                else:
                    logger.info(
                        "Returning JSON read_note result from entity: {path}",
                        path=entity_path,
                    )
                    return dict(
                        await read_note_json_by_external_id(
                            knowledge_client=knowledge_client,
                            resource_client=resource_client,
                            entity_external_id=entity_id,
                            include_frontmatter=include_frontmatter,
                        )
                    )
            else:
                # Text mode intentionally retains the resolve -> resource behavior.
                try:
                    entity_id = await knowledge_client.resolve_entity(entity_path, strict=True)
                    response = await resource_client.read(entity_id)
                    if response.status_code == 200:
                        logger.info(
                            "Returning read_note result from resource: {path}",
                            path=entity_path,
                        )
                        return response.text
                except Exception as error:  # pragma: no cover
                    logger.info(f"Direct lookup failed for '{entity_path}': {error}")

            # Fallback 1: Try title search via API, walking fixed-size pages of
            # title results until an exact match is found or results run out.
            # A single page is not enough: when more than _TITLE_LOOKUP_PAGE_SIZE
            # higher-ranked fuzzy titles contain the queried phrase, the exact
            # title lands on a later page and a one-page lookup would miss it.
            logger.info(f"Search title for: {identifier}")
            result: dict[str, object] | None = None
            for lookup_page in range(1, _TITLE_LOOKUP_MAX_PAGES + 1):
                title_results = await _search_candidates(
                    identifier, title_only=True, lookup_page=lookup_page
                )
                title_candidates = _search_results(title_results)
                if not title_candidates:
                    logger.info(
                        f"No results in title search for: {identifier} "
                        f"in project {active_project.name}"
                    )
                    break
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
                if result is not None:
                    break
                # Trigger: this page held only fuzzy titles and the server reports
                #          no further pages (has_more is False or absent).
                # Why: continuing past the last page would issue empty lookups.
                # Outcome: give up on the title fallback and try text search below.
                if title_results.get("has_more") is not True:
                    logger.info(f"No exact title match found for: {identifier}")
                    break

            if result is not None and output_format == "json":
                try:
                    entity_id = _result_external_id(result)
                    if entity_id is None and _result_permalink(result) is not None:
                        entity_id = await knowledge_client.resolve_entity(
                            _result_permalink(result) or "", strict=True
                        )
                    if entity_id is not None:
                        logger.info(
                            f"Found note by exact title search: {_result_permalink(result)}"
                        )
                        return dict(
                            await read_note_json_by_external_id(
                                knowledge_client=knowledge_client,
                                resource_client=resource_client,
                                entity_external_id=entity_id,
                                include_frontmatter=include_frontmatter,
                            )
                        )
                except Exception as error:  # pragma: no cover
                    logger.info(
                        "Failed to fetch content for found title match "
                        f"{_result_permalink(result)}: {error}"
                    )
            elif result is not None and _result_permalink(result):
                try:
                    entity_id = await knowledge_client.resolve_entity(
                        _result_permalink(result) or "", strict=True
                    )
                    response = await resource_client.read(entity_id)
                    if response.status_code == 200:
                        logger.info(
                            f"Found note by exact title search: {_result_permalink(result)}"
                        )
                        return response.text
                except Exception as error:  # pragma: no cover
                    logger.info(
                        "Failed to fetch content for found title match "
                        f"{_result_permalink(result)}: {error}"
                    )

            # Fallback 2: Text search as a last resort
            logger.info(f"Title search failed, trying text search for: {identifier}")
            text_results = await _search_candidates(identifier, title_only=False)

            # We didn't find a direct match, construct a helpful error message
            text_candidates = _search_results(text_results)
            if not text_candidates:
                if output_format == "json":
                    return _empty_json_payload()
                return format_not_found_message(active_project.name, identifier)
            # The fallback search is paginated server-side to page_size, so list
            # the whole returned page instead of a hardcoded cap — otherwise the
            # caller's page_size would be silently ignored past the cap.
            if output_format == "json":
                payload = _empty_json_payload()
                payload["related_results"] = [
                    {
                        "title": _result_title(result),
                        "permalink": _result_permalink(result),
                        "file_path": _result_file_path(result),
                    }
                    for result in text_candidates
                ]
                return payload
            return format_related_results(active_project.name, identifier, text_candidates)


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
