"""POSIX-style read-only tools for Basic Memory MCP server.

Six familiar Unix verbs — cat, grep, ls, find, tail, man — each a thin
translation over the same typed API clients the canonical tools use. They are
tagged ``POSIX_TOOLS_TAG`` and hidden by default: the composition root in
``basic_memory.mcp.server`` flips their visibility from the
``enable_posix_tools`` config flag at lifespan startup, so no tool body ever
checks config itself.
"""

from typing import Any, Optional

from fastmcp import Context
from fastmcp.exceptions import ToolError

from basic_memory.config import ConfigManager
from basic_memory.man import bundled_pages, find_page, parse_page_ref, render_index
from basic_memory.mcp.container import get_container
from basic_memory.mcp.note_reads import read_note_json_by_external_id
from basic_memory.mcp.project_context import get_project_client
from basic_memory.mcp.server import POSIX_TOOLS_TAG, mcp, set_posix_tools_visibility
from basic_memory.schemas.directory import (
    DEFAULT_DIRECTORY_PAGE_SIZE,
    MAX_DIRECTORY_PAGE_SIZE,
)
from basic_memory.schemas.search import SearchItemType, SearchQuery, SearchRetrievalMode

# The manual project holds the non-bundled manual pages as ordinary notes;
# `man` falls back to it for page reads and searches it in query mode.
_MANUAL_PROJECT = "manual"

# API bound on directory recursion (directory_router depth query: ge=1, le=10).
_MAX_FIND_DEPTH = 10

# recent_activity's page-size cap; tail's `lines` maps onto it.
_MAX_TAIL_LINES = 100


@mcp.tool(
    title="Cat",
    description="Print a note's content.",
    tags={POSIX_TOOLS_TAG, "notes"},
    annotations={
        "title": "Cat",
        "readOnlyHint": True,
        "destructiveHint": False,
        "openWorldHint": False,
    },
)
async def cat(
    identifier: str,
    start_line: Optional[int] = None,
    end_line: Optional[int] = None,
    section: Optional[str] = None,
    max_tokens: Optional[int] = None,
    include_frontmatter: bool = True,
    project: Optional[str] = None,
    project_id: Optional[str] = None,
    context: Context | None = None,
) -> dict[str, Any]:
    """Print a note's content, optionally sliced by line range, section, or token budget.

    Args:
        identifier: Note title, permalink, or memory:// URL (resolved exactly).
        start_line: First line to include (1-indexed, inclusive).
        end_line: Last line to include (inclusive). Defaults to the last line.
        section: Heading to slice to: "Decisions", path form "Auth/Decisions"
            to disambiguate by parent, or bracket form "Heading[1]" for the
            second duplicate heading. Cannot combine with start_line/end_line;
            the response's start_line/end_line support follow-up range reads.
        max_tokens: Approximate token budget. Longer content is truncated at a
            section or paragraph boundary with an explicit ellipsis marker; the
            response carries truncated/continue_line for resuming.
        include_frontmatter: Include the YAML frontmatter block in `content`.
            Ignored for section/max_tokens reads — those slices never carry a
            frontmatter block. A start_line/end_line range combined with
            max_tokens addresses the full document (frontmatter included) and
            therefore requires include_frontmatter=True.
        project: Project name. Optional - the server resolves the default.
        project_id: Project external_id (UUID); takes precedence over `project`.
        context: Optional FastMCP context.

    Returns:
        The read_note JSON payload (title, permalink, file_path, content,
        frontmatter), plus start_line/end_line/total_lines when a slice applied,
        `section` for section reads, and truncated/continue_line when max_tokens
        cut the content.
    """
    if section is not None and (start_line is not None or end_line is not None):
        raise ValueError(
            "cat: 'section' cannot be combined with start_line/end_line; use the "
            "returned start_line/end_line for follow-up range reads"
        )
    if max_tokens is not None and max_tokens < 1:
        raise ValueError(f"max_tokens must be >= 1, got {max_tokens}")
    if start_line is not None and start_line < 1:
        raise ValueError(f"start_line must be >= 1, got {start_line}")
    if end_line is not None and end_line < (start_line or 1):
        raise ValueError(f"end_line must be >= start_line, got {end_line}")
    # Trigger: a line range rides along with max_tokens while frontmatter is opted out.
    # Why: server-side line ranges are document-absolute (frontmatter included), but
    #      include_frontmatter=False range reads slice the frontmatter-stripped body —
    #      the same numbers would address different lines, and the served range could
    #      carry frontmatter text despite the explicit opt-out.
    # Outcome: the combination is rejected so every read keeps one coordinate system.
    if (
        max_tokens is not None
        and not include_frontmatter
        and (start_line is not None or end_line is not None)
    ):
        raise ValueError(
            "cat: max_tokens with start_line/end_line requires include_frontmatter=True — "
            "those ranges address the full document (frontmatter included); drop max_tokens "
            "for a body-relative range, or keep include_frontmatter=True"
        )

    # Trigger: section or max_tokens is set.
    # Why: those slices need the server-side section scan and token budgeting;
    #      plain line ranges keep their original client-side slicing untouched.
    # Outcome: the read carries the slice params (line bounds ride along as a
    #          lines= range) and the server-supplied payload returns as-is.
    server_side_slice = section is not None or max_tokens is not None
    lines_param: Optional[str] = None
    if server_side_slice and (start_line is not None or end_line is not None):
        lines_param = f"{start_line or 1}-{'' if end_line is None else end_line}"

    async with get_project_client(project, context=context, project_id=project_id) as (
        client,
        active_project,
    ):
        # Import here to avoid circular import
        from basic_memory.mcp.clients import KnowledgeClient, ResourceClient

        knowledge_client = KnowledgeClient(client, active_project.external_id)
        entity_id = await knowledge_client.resolve_entity(identifier, strict=True)
        payload: dict[str, Any] = dict(
            await read_note_json_by_external_id(
                knowledge_client=knowledge_client,
                resource_client=ResourceClient(client, active_project.external_id),
                entity_external_id=entity_id,
                include_frontmatter=include_frontmatter,
                section=section,
                lines=lines_param,
                max_tokens=max_tokens,
            )
        )

    if server_side_slice or (start_line is None and end_line is None):
        return payload

    lines = str(payload["content"]).splitlines()
    total_lines = len(lines)
    first = start_line or 1
    last = min(end_line, total_lines) if end_line is not None else total_lines
    payload["content"] = "\n".join(lines[first - 1 : last])
    payload["start_line"] = first
    payload["end_line"] = last
    payload["total_lines"] = total_lines
    return payload


def _grep_retrieval_mode(literal: bool) -> SearchRetrievalMode:
    """Pick grep's retrieval mode: literal full-text on request, semantic when available."""
    if literal:
        return SearchRetrievalMode.FTS
    try:
        config = get_container().config
    except RuntimeError:
        # CLI paths call tools before the MCP container exists (search.py precedent).
        config = ConfigManager().config
    return SearchRetrievalMode.HYBRID if config.semantic_search_enabled else SearchRetrievalMode.FTS


@mcp.tool(
    title="Grep",
    description="Search note content for a pattern.",
    tags={POSIX_TOOLS_TAG, "search"},
    annotations={
        "title": "Grep",
        "readOnlyHint": True,
        "destructiveHint": False,
        "openWorldHint": False,
    },
)
async def grep(
    pattern: str,
    literal: bool = False,
    page: int = 1,
    page_size: int = 10,
    project: Optional[str] = None,
    project_id: Optional[str] = None,
    context: Context | None = None,
) -> dict[str, Any]:
    """Search note content, semantically by default.

    Args:
        pattern: Text to search for.
        literal: Force literal full-text matching instead of semantic search.
        page: Page number (1-indexed).
        page_size: Results per page.
        project: Project name. Optional - the server resolves the default.
        project_id: Project external_id (UUID); takes precedence over `project`.
        context: Optional FastMCP context.

    Returns:
        The search response as JSON: results, pagination, and totals.
    """
    if not pattern or not pattern.strip():
        raise ValueError("pattern must not be empty")
    if page < 1:
        raise ValueError(f"page must be >= 1, got {page}")
    if page_size < 1:
        raise ValueError(f"page_size must be >= 1, got {page_size}")

    query = SearchQuery(
        text=pattern,
        retrieval_mode=_grep_retrieval_mode(literal),
        entity_types=[SearchItemType.ENTITY],
    )
    async with get_project_client(project, context=context, project_id=project_id) as (
        client,
        active_project,
    ):
        # Import here to avoid circular import
        from basic_memory.mcp.clients import SearchClient

        search_client = SearchClient(client, active_project.external_id)
        response = await search_client.search(query.model_dump(), page=page, page_size=page_size)
        return response.model_dump(mode="json", exclude_none=True)


@mcp.tool(
    title="Ls",
    description="List one directory level.",
    tags={POSIX_TOOLS_TAG, "navigation"},
    annotations={
        "title": "Ls",
        "readOnlyHint": True,
        "destructiveHint": False,
        "openWorldHint": False,
    },
)
async def ls(
    path: str = "/",
    page: int = 1,
    page_size: int = DEFAULT_DIRECTORY_PAGE_SIZE,
    project: Optional[str] = None,
    project_id: Optional[str] = None,
    context: Context | None = None,
) -> dict[str, Any]:
    """List the immediate contents of one directory.

    Args:
        path: Directory path to list (default: project root).
        page: Page number (1-indexed).
        page_size: Nodes per page.
        project: Project name. Optional - the server resolves the default.
        project_id: Project external_id (UUID); takes precedence over `project`.
        context: Optional FastMCP context.

    Returns:
        The directory listing as JSON: nodes, pagination, and totals.
    """
    if page < 1:
        raise ValueError(f"page must be >= 1, got {page}")
    if page_size < 1:
        raise ValueError(f"page_size must be >= 1, got {page_size}")
    if page_size > MAX_DIRECTORY_PAGE_SIZE:
        raise ValueError(f"page_size must be <= {MAX_DIRECTORY_PAGE_SIZE}, got {page_size}")

    async with get_project_client(project, context=context, project_id=project_id) as (
        client,
        active_project,
    ):
        # Import here to avoid circular import
        from basic_memory.mcp.clients import DirectoryClient

        directory_client = DirectoryClient(client, active_project.external_id)
        listing = await directory_client.list(path, depth=1, page=page, page_size=page_size)
        return listing.model_dump(mode="json")


@mcp.tool(
    title="Find",
    description="Recursively list files matching a name glob.",
    tags={POSIX_TOOLS_TAG, "navigation"},
    annotations={
        "title": "Find",
        "readOnlyHint": True,
        "destructiveHint": False,
        "openWorldHint": False,
    },
)
async def find(
    path: str = "/",
    name: Optional[str] = None,
    depth: int = _MAX_FIND_DEPTH,
    page: int = 1,
    page_size: int = DEFAULT_DIRECTORY_PAGE_SIZE,
    project: Optional[str] = None,
    project_id: Optional[str] = None,
    context: Context | None = None,
) -> dict[str, Any]:
    """Recursively list files under a directory, optionally filtered by name glob.

    Args:
        path: Directory to start from (default: project root).
        name: File-name glob to match, e.g. "*.md". None matches everything.
        depth: How many levels to recurse (1-10, default: 10).
        page: Page number (1-indexed).
        page_size: Nodes per page.
        project: Project name. Optional - the server resolves the default.
        project_id: Project external_id (UUID); takes precedence over `project`.
        context: Optional FastMCP context.

    Returns:
        The directory listing as JSON: nodes, pagination, and totals.
    """
    if depth < 1 or depth > _MAX_FIND_DEPTH:
        raise ValueError(f"depth must be between 1 and {_MAX_FIND_DEPTH}, got {depth}")
    if page < 1:
        raise ValueError(f"page must be >= 1, got {page}")
    if page_size < 1:
        raise ValueError(f"page_size must be >= 1, got {page_size}")
    if page_size > MAX_DIRECTORY_PAGE_SIZE:
        raise ValueError(f"page_size must be <= {MAX_DIRECTORY_PAGE_SIZE}, got {page_size}")

    async with get_project_client(project, context=context, project_id=project_id) as (
        client,
        active_project,
    ):
        # Import here to avoid circular import
        from basic_memory.mcp.clients import DirectoryClient

        directory_client = DirectoryClient(client, active_project.external_id)
        listing = await directory_client.list(
            path,
            depth=depth,
            file_name_glob=name,
            page=page,
            page_size=page_size,
        )
        return listing.model_dump(mode="json")


@mcp.tool(
    title="Tail",
    description="Show recently changed notes.",
    tags={POSIX_TOOLS_TAG, "navigation", "notes"},
    annotations={
        "title": "Tail",
        "readOnlyHint": True,
        "destructiveHint": False,
        "openWorldHint": False,
    },
)
async def tail(
    timeframe: str = "7d",
    lines: int = 10,
    project: Optional[str] = None,
    project_id: Optional[str] = None,
    context: Context | None = None,
) -> list[dict[str, Any]]:
    """Show the most recently changed notes in a project.

    Args:
        timeframe: Time window, e.g. "7d", "yesterday", "2 days ago".
        lines: Maximum number of rows to return (1-100).
        project: Project name. Optional - the server resolves the default.
        project_id: Project external_id (UUID); takes precedence over `project`.
        context: Optional FastMCP context.

    Returns:
        Rows of {type, title, permalink, file_path, created_at}, newest first.
    """
    if lines < 1:
        raise ValueError(f"lines must be >= 1, got {lines}")
    if lines > _MAX_TAIL_LINES:
        raise ValueError(f"lines must be <= {_MAX_TAIL_LINES}, got {lines}")

    async with get_project_client(project, context=context, project_id=project_id) as (
        client,
        active_project,
    ):
        # Import here to avoid circular import
        from basic_memory.mcp.clients import MemoryClient

        memory_client = MemoryClient(client, active_project.external_id)
        activity = await memory_client.recent(
            timeframe=timeframe,
            depth=1,
            types=[SearchItemType.ENTITY.value],
            page=1,
            page_size=lines,
        )

    rows: list[dict[str, Any]] = []
    for result in activity.results:
        primary = result.primary_result
        rows.append(
            {
                "type": primary.type,
                "title": primary.title,
                "permalink": primary.permalink,
                "file_path": primary.file_path,
                "created_at": primary.created_at.isoformat() if primary.created_at else None,
            }
        )
    return rows


@mcp.tool(
    title="Man",
    description="Look up a manual page or search the manual.",
    tags={POSIX_TOOLS_TAG, "notes"},
    annotations={
        "title": "Man",
        "readOnlyHint": True,
        "destructiveHint": False,
        "openWorldHint": False,
    },
)
async def man(
    page: Optional[str] = None,
    query: Optional[str] = None,
    project: Optional[str] = None,
    project_id: Optional[str] = None,
    context: Context | None = None,
) -> str | dict[str, Any]:
    """Look up one manual page, search the manual, or render the index.

    Modes:
    - No arguments: the manual index (apropos view), as markdown.
    - page: one page — a bundled page by reference (e.g. "search-notes(3)"),
      else a note read from the manual project.
    - query: search notes of type "manpage" in the manual project.

    Args:
        page: Page reference, e.g. "search-notes(3)". Any common spelling works.
        query: Apropos search text over manpage notes. Mutually exclusive with `page`.
        project: Manual project name (default: "manual"). Bundled pages need no project.
        project_id: Project external_id (UUID); takes precedence over `project`.
        context: Optional FastMCP context.

    Returns:
        Markdown (index or one page) or a search response as JSON.
    """
    if page is not None and query is not None:
        raise ValueError("man: pass either 'page' or 'query', not both")

    if page is None and query is None:
        # Mirror the memory://man resource: mark pages whose tool this server
        # does not register so an agent does not call a tool that is not there.
        tools = await mcp.list_tools(run_middleware=False)
        return render_index(bundled_pages(), frozenset(tool.name for tool in tools))

    manual_project = project or _MANUAL_PROJECT

    if page is not None:
        try:
            page_ref = parse_page_ref(page)
        except ValueError:
            bundled = None
        else:
            bundled = find_page(page_ref)
        if bundled is not None:
            return bundled.read()

        # Not a bundled page — the reference may name a manual note (the
        # non-bundled sections live as notes in the manual project), mirroring
        # the memory://man resource fallback.
        async with get_project_client(manual_project, context=context, project_id=project_id) as (
            client,
            active_project,
        ):
            # Import here to avoid circular import
            from basic_memory.mcp.clients import KnowledgeClient, ResourceClient

            knowledge_client = KnowledgeClient(client, active_project.external_id)
            resource_client = ResourceClient(client, active_project.external_id)
            try:
                entity_id = await knowledge_client.resolve_entity(page, strict=True)
            except ToolError as error:
                # Neither a bundled page nor a manual note — the manual's hint
                # is the useful error, not the raw resolution failure. Only the
                # resolve miss means "no such entry"; a failed read of a note
                # that DID resolve is an operational error and propagates as-is.
                raise ToolError(f"No manual entry for {page}") from error
            response = await resource_client.read(entity_id)
            return response.text

    search_query = SearchQuery(
        text=query,
        note_types=["manpage"],
        entity_types=[SearchItemType.ENTITY],
    )
    async with get_project_client(manual_project, context=context, project_id=project_id) as (
        client,
        active_project,
    ):
        # Import here to avoid circular import
        from basic_memory.mcp.clients import SearchClient

        search_client = SearchClient(client, active_project.external_id)
        response = await search_client.search(search_query.model_dump(), page=1, page_size=10)
        return response.model_dump(mode="json", exclude_none=True)


# Default-hidden until the composition root reads config in lifespan. Keeps the
# tool listing identical to today for any consumer that lists before startup.
set_posix_tools_visibility(mcp, False)
