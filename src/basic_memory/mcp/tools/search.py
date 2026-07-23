"""Search tools for Basic Memory MCP server."""

import re
from textwrap import dedent
from typing import Annotated, List, Optional, Dict, Any, Literal, cast
from uuid import UUID

import logfire
from loguru import logger
from fastmcp import Context
from pydantic import AliasChoices, BeforeValidator, Field

from basic_memory.config import ConfigManager, has_cloud_credentials
from basic_memory.utils import (
    build_canonical_permalink,
    coerce_dict,
    parse_str_list,
    parse_tags,
    strict_search_tags,
)
from basic_memory.mcp.async_client import (
    _explicit_routing,
    _force_local_mode,
    is_factory_mode,
)
from basic_memory.mcp.container import get_container
from basic_memory.mcp.project_context import (
    detect_project_from_identifier_prefix,
    get_project_client,
    resolve_project_and_path,
)
from basic_memory.mcp.server import mcp
from basic_memory.schemas.search import (
    SearchItemType,
    SearchQuery,
    SearchResponse,
    SearchResult,
    SearchRetrievalMode,
)


def _default_search_type() -> str:
    """Pick default search mode from config, falling back to auto-detection.

    Priority: config default_search_type > auto-detect (hybrid if semantic enabled, else text).
    """
    try:
        config = get_container().config
    except RuntimeError:
        config = ConfigManager().config

    if config.default_search_type:
        return config.default_search_type

    return "hybrid" if config.semantic_search_enabled else "text"


def _format_search_error_response(
    project: str, error_message: str, query: str, search_type: str = "text"
) -> str:
    """Format helpful error responses for search failures that guide users to successful searches."""

    # Semantic config/dependency errors
    if "semantic search is disabled" in error_message.lower():
        return dedent(f"""
            # Search Failed - Semantic Search Disabled

            You requested `{search_type}` search for query '{query}', but semantic search is disabled.

            ## How to enable
            1. Set `BASIC_MEMORY_SEMANTIC_SEARCH_ENABLED=true`
            2. Restart the Basic Memory server/process

            ## Alternative now
            - Run FTS search instead:
              `search_notes("{project}", "{query}", search_type="text")`
            """).strip()

    if "pip install" in error_message.lower() and "semantic" in error_message.lower():
        return dedent(f"""
            # Search Failed - Semantic Dependencies Missing

            Semantic retrieval is enabled but required packages are not installed.

            ## Fix
            1. Install/update Basic Memory: `pip install -U basic-memory`
            2. Restart Basic Memory
            3. Retry your query:
               `search_notes("{project}", "{query}", search_type="{search_type}")`
            """).strip()

    # Corrupt/missing FastEmbed model cache (interrupted download leaves a partial
    # snapshot missing model_optimized.onnx; the ONNX runtime then raises NO_SUCHFILE).
    # Basic Memory self-heals by re-downloading on the next load, but if the user still
    # hits this, point them at the cache dir to clear manually and offer a text fallback.
    error_lower = error_message.lower()
    # "load model from" is the exact ONNX phrasing ("Load model from <path>.onnx failed").
    # The looser "load model" matched unrelated errors, so we keep only the specific phrase
    # alongside the onnxruntime / no_suchfile / model_optimized.onnx fingerprints.
    if (
        "onnxruntime" in error_lower
        or "no_suchfile" in error_lower
        or "model_optimized.onnx" in error_lower
        or "load model from" in error_lower
    ):
        # Deferred import: keeps the repository layer out of the tool's import graph
        # (matches the SearchClient deferral below) and is only needed on this error path.
        from basic_memory.repository.embedding_provider_factory import _resolve_cache_dir

        try:
            cache_dir = _resolve_cache_dir(get_container().config)
        except RuntimeError:
            cache_dir = _resolve_cache_dir(ConfigManager().config)
        return dedent(f"""
            # Search Failed - Embedding Model Missing or Corrupt

            The local FastEmbed model could not be loaded for query '{query}': {error_message}

            This usually means an earlier model download was interrupted and left an
            incomplete file in the model cache.

            ## How to fix
            1. Delete the FastEmbed model cache so it re-downloads on the next search:
               `{cache_dir}`
            2. Run your search again (the model downloads automatically on first use):
               `search_notes("{project}", "{query}", search_type="{search_type}")`

            ## Workaround right now
            - Use full-text search, which needs no embedding model:
              `search_notes("{project}", "{query}", search_type="text")`
            """).strip()

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
            - Content-specific: `tag:example`

            ## Try again with:
            ```
            search_notes("{project}","{clean_query}")
            ```

            ## Alternative search strategies:
            - Break into simpler terms: `search_notes("{project}", "{" ".join(clean_query.split()[:2])}")`
            - Try different search types: `search_notes("{project}","{clean_query}", search_type="title")`
            - Use filtering: `search_notes("{project}","{clean_query}", note_types=["note"])`
            """).strip()

    # Project not found errors (check before general "not found")
    if "project not found" in error_message.lower():
        return dedent(f"""
            # Search Failed - Project Not Found

            The current project is not accessible or doesn't exist: {error_message}

            ## How to resolve:
            1. **Check available projects**: `list_projects()`
            3. **Verify project setup**: Ensure your project is properly configured

            ## Current session info:
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
               - **Text search**: `search_notes("{project}","{query}", search_type="text")` (searches full content)
               - **Title search**: `search_notes("{project}","{query}", search_type="title")` (searches only titles)
               - **Permalink search**: `search_notes("{project}","{query}", search_type="permalink")` (searches file paths)

            4. **Try boolean operators for broader results**:
               - OR search: `search_notes("{project}","{" OR ".join(query.split()[:3])}")`
               - Remove restrictive terms: Focus on the most important keywords

            5. **Use filtering to narrow scope**:
               - By note type in frontmatter: `search_notes("{project}","{query}", note_types=["note"])`
               - By recent content: `search_notes("{project}","{query}", after_date="1 week")`
               - By entity type: `search_notes("{project}","{query}", entity_types=["observation"])`

            6. **Try advanced search patterns**:
               - Tag search: `search_notes("{project}","tag:your-tag")`
               - Observation category: `search_notes("{project}","{query}", entity_types=["observation"], categories=["requirement"])`
               - Pattern matching: `search_notes("{project}","*{query}*", search_type="permalink")`

            ## Explore what content exists:
            - **Recent activity**: `recent_activity(timeframe="7d")` - See what's been updated recently
            - **List directories**: `list_directory("{project}","/")` - Browse all content
            - **Browse by folder**: `list_directory("{project}","/notes")` or `list_directory("/docs")`
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
            - Browse files directly: `list_directory("{project}","/")`
            - Check recent activity: `recent_activity(timeframe="7d")`
            - Try a different search type: `search_notes("{project}","{query}", search_type="title")`

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
- List available projects: `list_projects()`"""

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
  - Title only: `search_notes("{project}","{query}", search_type="title")`
  - Permalink patterns: `search_notes("{project}","{query}*", search_type="permalink")`
- **With filters**: `search_notes("{project}","{query}", note_types=["note"])`
- **Recent content**: `search_notes("{project}","{query}", after_date="1 week")`
- **Boolean variations**: `search_notes("{project}","{" OR ".join(query.split()[:2])}")`

## Explore your content:
- **Browse files**: `list_directory("{project}","/")` - See all available content
- **Recent activity**: `recent_activity(timeframe="7d")` - Check what's been updated
- **All projects**: `list_projects()` 

## Search syntax reference:
- **Basic**: `keyword` or `multiple words`
- **Boolean**: `term1 AND term2`, `term1 OR term2`, `term1 NOT term2`
- **Phrases**: `"exact phrase"`
- **Grouping**: `(term1 OR term2) AND term3`
- **Tags**: `tag:example`
- **Observation categories**: `entity_types=["observation"], categories=["requirement"]`"""


def _format_search_markdown(
    result: SearchResponse, project: str, query: str | None, project_id: str | None = None
) -> str:
    """Format SearchResponse as compact markdown text.

    Produces a human-readable markdown representation suitable for LLM
    consumption when structured data isn't needed.
    """
    if not result.results:
        # Empty search is usually "no match for this query," not "empty knowledge base," so we
        # do not repeat the first-note offer here (that would nag established users). Point at
        # recent_activity, which owns the getting-started guidance when the base is truly empty.
        # Route the orientation call the same way the search was routed. all-projects search
        # is not a real project (discovery mode → bare call); otherwise prefer the external id,
        # since names collide across cloud workspaces, and fall back to the name locally.
        if project == "all projects":
            orient = "recent_activity()"
        elif project_id:
            orient = f'recent_activity(project_id="{project_id}")'
        else:
            orient = f'recent_activity(project="{project}")'
        return (
            f"No results found for '{query or ''}' in project '{project}'. "
            f"Try broader or different terms, or call {orient} to orient — if the knowledge "
            "base is empty it will guide creating a first note."
        )

    parts = []

    # --- Header ---
    if query:
        parts.append(f"# Search Results: {query}")
    else:
        parts.append("# Search Results")
    parts.append(f"*project: {project}*")
    parts.append("")

    # --- Result blocks ---
    for r in result.results:
        parts.append(f"### {r.title}")
        parts.append(f"- permalink: {r.permalink}")
        # external_id is the note's stable identifier. Emitting it lets the hosted MCP layer
        # deep-link each hit to the web app from the final (post-merge) result the caller sees,
        # which matters for all-projects search where the displayed page is decided after the
        # per-project API calls the gateway would otherwise record (#1423).
        if r.external_id:
            parts.append(f"- external_id: {r.external_id}")
        parts.append(f"- score: {r.score:.4f}")
        if r.matched_chunk:
            parts.append(f"- match: {r.matched_chunk[:200]}")
        parts.append("")

    # --- Footer with pagination ---
    parts.append("---")
    count = len(result.results)
    parts.append(
        f"*{count} result{'s' if count != 1 else ''}"
        f" | page {result.current_page}, page_size {result.page_size}"
        f"{' | more available' if result.has_more else ''}*"
    )

    return "\n".join(parts)


def _valid_project_id(value: object) -> str | None:
    """Return a UUID project id string when one is present."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return str(UUID(value))
    except ValueError:
        return None


def _matches_constrained_project(project: dict[str, Any], constrained_project: object) -> bool:
    """Return True when a project list row satisfies BASIC_MEMORY_MCP_PROJECT."""
    if not isinstance(constrained_project, str) or not constrained_project.strip():
        return True

    candidates = {
        value
        for value in (
            project.get("name"),
            project.get("qualified_name"),
            project.get("external_id"),
        )
        if isinstance(value, str)
    }
    return constrained_project in candidates


def _search_project_refs(projects_payload: object) -> list[dict[str, str | None]]:
    """Extract project routing refs for optional account-scoped search."""
    if not isinstance(projects_payload, dict):
        return []

    payload = cast(dict[str, Any], projects_payload)
    projects = payload.get("projects")
    if not isinstance(projects, list):
        return []

    refs: list[dict[str, str | None]] = []
    seen: set[tuple[str | None, str | None]] = set()
    constrained_project = payload.get("constrained_project")
    for item in projects:
        if not isinstance(item, dict) or not _matches_constrained_project(
            item, constrained_project
        ):
            continue

        project = item.get("qualified_name") or item.get("name")
        project_name = project if isinstance(project, str) and project.strip() else None
        project_id = _valid_project_id(item.get("external_id"))
        if project_name is None and project_id is None:
            continue

        key = (project_name, project_id)
        if key in seen:
            continue
        seen.add(key)
        refs.append({"project": project_name, "project_id": project_id})
    return refs


async def _load_search_project_refs(context: Context | None = None) -> list[dict[str, str | None]]:
    """Load accessible projects for search_all_projects without coupling the wrapper tool."""
    from basic_memory.mcp.tools.project_management import list_memory_projects

    return _search_project_refs(await list_memory_projects(output_format="json", context=context))


def _raw_results_from_search_payload(
    results: SearchResponse | list[SearchResult | dict[str, Any]] | dict[str, Any],
) -> list[SearchResult | dict[str, Any]]:
    """Return the result list from any search_notes JSON-compatible payload."""
    if isinstance(results, SearchResponse):
        return list(results.results)
    if isinstance(results, dict):
        nested_results = results.get("results")
        return (
            cast(list[SearchResult | dict[str, Any]], nested_results)
            if isinstance(nested_results, list)
            else []
        )
    return list(results)


def _result_score(result: SearchResult | dict[str, Any]) -> float:
    """Return a comparable search score for merged project results."""
    if isinstance(result, SearchResult):
        return result.score
    score = result.get("score")
    return float(score) if isinstance(score, int | float) else 0.0


def _qualify_permalink_for_project(permalink: object, project: str | None) -> object:
    """Return a workspace-qualified permalink when the project ref supplies one."""
    if not isinstance(permalink, str) or not permalink.strip():
        return permalink
    if not isinstance(project, str) or "/" not in project.strip("/"):
        return permalink

    normalized_permalink = permalink.strip("/")
    qualified_project = project.strip("/")
    if normalized_permalink == qualified_project or normalized_permalink.startswith(
        f"{qualified_project}/"
    ):
        return normalized_permalink

    workspace_slug, project_permalink = qualified_project.split("/", 1)
    return build_canonical_permalink(
        project_permalink,
        normalized_permalink,
        include_project=True,
        workspace_permalink=workspace_slug,
    )


def _qualify_results_for_project(
    results: list[SearchResult | dict[str, Any]],
    project_ref: dict[str, str | None],
) -> list[dict[str, Any]]:
    """Attach the searched workspace/project prefix to each result permalink."""
    qualified: list[dict[str, Any]] = []
    for result in results:
        if isinstance(result, SearchResult):
            result_data = result.model_dump()
        else:
            result_data = dict(result)
        result_data["permalink"] = _qualify_permalink_for_project(
            result_data.get("permalink"),
            project_ref.get("project"),
        )
        qualified.append(result_data)
    return qualified


def _result_total(results: dict[str, Any], raw_results: list[SearchResult | dict[str, Any]]) -> int:
    """Return the best available total for a per-project search payload."""
    total = results.get("total")
    if isinstance(total, int) and total > 0:
        return total
    return len(raw_results) + (1 if results.get("has_more") is True else 0)


def _result_total_is_exact(results: dict[str, Any]) -> bool:
    """Return whether a per-project payload explicitly guarantees an exact total."""
    return results.get("total_is_exact") is True


def _project_ref_label(project_ref: dict[str, str | None]) -> str:
    """Return a stable log label for a project search ref."""
    return project_ref.get("project") or project_ref.get("project_id") or "<unknown project>"


async def _search_all_projects(
    *,
    query: str | None,
    page: int,
    page_size: int,
    search_type: str | None,
    output_format: Literal["text", "json"],
    note_types: list[str],
    entity_types: list[str],
    categories: list[str],
    after_date: str | None,
    metadata_filters: dict[str, Any] | None,
    tags: list[str] | None,
    status: str | None,
    min_similarity: float | None,
    context: Context | None,
) -> dict | str:
    """Search every accessible project when the caller explicitly opts in."""
    requested_page = max(page, 1)
    requested_page_size = max(page_size, 1)
    project_refs = await _load_search_project_refs(context=context)
    if not project_refs:
        response = SearchResponse(
            results=[],
            current_page=requested_page,
            page_size=requested_page_size,
            total=0,
            total_is_exact=True,
            has_more=False,
        )
        if output_format == "json":
            return response.model_dump(mode="json", exclude_none=True)
        return _format_search_markdown(response, "all projects", query)

    per_project_page_size = requested_page * requested_page_size
    merged_results: list[dict[str, Any]] = []
    total = 0
    total_is_exact = True
    any_project_has_more = False

    # Trigger: caller asked for an account-wide search.
    # Why: project_id (external UUID) routes through the cloud v2 API path,
    #      which 401s on local installs because there's no JWT to present.
    #      Project names route through the local-ASGI path and work for both
    #      backends — cloud disambiguates names via the workspace/project
    #      qualified_name already baked into project_ref["project"].
    # Outcome: forward project_id only when the same signals get_project_client
    #          uses to pick a cloud route are present. Mirrors the cloud_available
    #          composite in project_context.get_project_client (single source of
    #          truth for "can we route to cloud?").
    config = ConfigManager().config
    use_cloud_routing = (
        is_factory_mode()
        or (_explicit_routing() and not _force_local_mode())
        or has_cloud_credentials(config)
    )

    for project_ref in project_refs:
        recursive_project_id = project_ref["project_id"] if use_cloud_routing else None
        try:
            results = await search_notes(
                query=query,
                project=project_ref["project"],
                project_id=recursive_project_id,
                page=1,
                page_size=per_project_page_size,
                search_type=search_type,
                output_format="json",
                note_types=note_types or None,
                entity_types=entity_types or None,
                categories=categories or None,
                after_date=after_date,
                metadata_filters=metadata_filters,
                tags=tags,
                status=status,
                min_similarity=min_similarity,
                search_all_projects=False,
                context=context,
            )
        except Exception as exc:
            logger.warning(
                f"Multi-project search failed for project {_project_ref_label(project_ref)}: {exc}"
            )
            total_is_exact = False
            continue

        if isinstance(results, str):
            if not results.startswith("# Search Failed"):
                return results
            logger.warning(
                "Multi-project search failed for project "
                f"{_project_ref_label(project_ref)}: {results}"
            )
            total_is_exact = False
            continue

        raw_results = _raw_results_from_search_payload(results)
        total += _result_total(results, raw_results)
        total_is_exact = total_is_exact and _result_total_is_exact(results)
        any_project_has_more = any_project_has_more or results.get("has_more") is True
        merged_results.extend(_qualify_results_for_project(raw_results, project_ref))

    sorted_results = sorted(merged_results, key=_result_score, reverse=True)
    start = (requested_page - 1) * requested_page_size
    end = start + requested_page_size
    paged_results = sorted_results[start:end]
    response = SearchResponse.model_validate(
        {
            "results": paged_results,
            "current_page": requested_page,
            "page_size": requested_page_size,
            "total": total,
            "total_is_exact": total_is_exact,
            "has_more": any_project_has_more or total > end or len(sorted_results) > end,
        }
    )

    if output_format == "json":
        return response.model_dump(mode="json", exclude_none=True)
    return _format_search_markdown(response, "all projects", query)


@mcp.tool(
    title="Search Notes",
    description="Search across all content in the knowledge base with advanced syntax support.",
    tags={"search"},
    # TODO: re-enable once MCP client rendering is working
    # meta={"ui/resourceUri": "ui://basic-memory/search-results"},
    annotations={
        "title": "Search Notes",
        "readOnlyHint": True,
        "destructiveHint": False,
        "openWorldHint": False,
    },
)
async def search_notes(
    # Accept common search-query aliases models reach for from training data.
    # `q` is the universal HTTP convention; `search`/`text` are common in NL APIs.
    query: Annotated[
        Optional[str],
        Field(default=None, validation_alias=AliasChoices("query", "q", "search", "text")),
    ] = None,
    project: Optional[str] = None,
    project_id: Optional[str] = None,
    search_all_projects: Annotated[
        bool,
        Field(
            default=False,
            validation_alias=AliasChoices("search_all_projects", "all_projects"),
        ),
    ] = False,
    # `offset` is intentionally NOT aliased to `page`: offset is item-indexed
    # (skip N items) while page is 1-indexed page-number. Direct aliasing would
    # silently return the wrong slice.
    page: Annotated[
        int,
        Field(default=1, validation_alias=AliasChoices("page", "page_number")),
    ] = 1,
    page_size: Annotated[
        int,
        Field(default=10, validation_alias=AliasChoices("page_size", "limit", "per_page")),
    ] = 10,
    search_type: str | None = None,
    output_format: Literal["text", "json"] = "text",
    # Plural-vs-singular trips models constantly. Accept the singular too.
    note_types: Annotated[
        List[str] | None,
        # parse_str_list, not coerce_list: "note,task" must split into ["note", "task"]
        # consistent with how tags are handled (#910/#930). coerce_list wraps the whole
        # comma string as the single literal type ["note,task"], which matches nothing.
        BeforeValidator(parse_str_list),
        Field(default=None, validation_alias=AliasChoices("note_types", "note_type", "types")),
        "Filter by the 'type' field in note frontmatter (e.g. 'note', 'chapter', 'person'). "
        "Accepts a list, a comma-separated string (e.g. 'note,task'), or a JSON-array string. "
        "Case-insensitive.",
    ] = None,
    entity_types: Annotated[
        List[str] | None,
        BeforeValidator(parse_str_list),
        Field(default=None, validation_alias=AliasChoices("entity_types", "entity_type")),
        "Filter by knowledge graph item type: 'entity' (whole notes), 'observation', or "
        "'relation'. Defaults to 'entity'. Do NOT pass schema/frontmatter types like "
        "'Chapter' here — use note_types instead. "
        "Accepts a list, a comma-separated string (e.g. 'entity,observation'), or a JSON-array string.",
    ] = None,
    categories: Annotated[
        List[str] | None,
        BeforeValidator(parse_str_list),
        Field(default=None, validation_alias=AliasChoices("categories", "category")),
        "Filter observation results to these exact categories (e.g. ['requirement']). "
        "Accepts a list, a comma-separated string (e.g. 'requirement,decision'), or a JSON-array string. "
        "Pair with entity_types=['observation'] to return only observations whose "
        "category matches exactly — not every row mentioning the word.",
    ] = None,
    # Time-filter naming varies wildly across APIs.
    after_date: Annotated[
        Optional[str],
        Field(
            default=None,
            validation_alias=AliasChoices("after_date", "since", "after", "from_date"),
        ),
    ] = None,
    metadata_filters: Annotated[
        Dict[str, Any] | None,
        BeforeValidator(coerce_dict),
    ] = None,
    # strict_search_tags, not coerce_list: tags="a,b" must split into ["a", "b"] to
    # match the tag: query shorthand below and write_note's documented tags convention
    # (#910). coerce_list would wrap the comma string as the single literal tag
    # ["a,b"], which matches nothing. Unlike bare parse_tags, the strict wrapper only
    # splits str/list/None and lets Pydantic reject other types (42, {"a": 1}) with a
    # clear validation error instead of stringifying them into junk tags.
    tags: Annotated[
        List[str] | None,
        BeforeValidator(strict_search_tags),
    ] = None,
    status: Optional[str] = None,
    min_similarity: Annotated[
        Optional[float],
        Field(
            default=None,
            validation_alias=AliasChoices("min_similarity", "threshold", "similarity_threshold"),
        ),
    ] = None,
    context: Context | None = None,
) -> dict | str:
    """Search across all content in the knowledge base with comprehensive syntax support.

    This tool searches the knowledge base using full-text search, pattern matching,
    or exact permalink lookup. It supports filtering by content type, entity type,
    and date, with advanced boolean and phrase search capabilities.

    Project Resolution:
    Server resolves projects in this order: Single Project Mode → project parameter → default project.
    If project unknown, use list_memory_projects() or recent_activity() first.
    Set search_all_projects=True to search every accessible project; this is opt-in because it
    performs one search per project.

    ## Search Syntax Examples

    ### Basic Searches
    - `search_notes("my-project", "keyword")` - Find any content containing "keyword"
    - `search_notes("work-docs", "'exact phrase'")` - Search for exact phrase match

    ### Advanced Boolean Searches
    - `search_notes("my-project", "term1 term2")` - Strict implicit-AND first; retries with
      relaxed OR terms only if strict search returns no results
    - `search_notes("my-project", "term1 AND term2")` - Explicit AND search (both terms required)
    - `search_notes("my-project", "term1 OR term2")` - Either term can be present
    - `search_notes("my-project", "term1 NOT term2")` - Include term1 but exclude term2
    - `search_notes("my-project", "(project OR planning) AND notes")` - Grouped boolean logic

    ### Content-Specific Searches
    - `search_notes("research", "tag:example")` - Search within specific tags (if supported by content)
    - `search_notes("work-project", "req", entity_types=["observation"], categories=["requirement"])`
      - Return only observations whose category is exactly "requirement"
    - `search_notes("team-docs", "author:username")` - Find content by author (if metadata available)

    **Note:** `tag:` shorthand is automatically converted to a `tags` filter, so it works
    with any search type (text, hybrid, vector). You can also use the `tags` parameter
    directly: `search_notes("project", "query", tags=["my-tag"])`

    ### Search Type Examples
    - `search_notes("my-project", "Meeting", search_type="title")` - Search only in titles
    - `search_notes("work-docs", "docs/meeting-*", search_type="permalink")` - Pattern match permalinks
      Note: Permalink patterns match the full path (e.g., "project/folder/chapter-13*", not just "chapter-13*").
    - `search_notes("research", "keyword")` - Default search (hybrid when semantic is enabled,
      text when disabled)

    ### Filtering Options
    - `search_notes("my-project", "query", note_types=["note"])` - Search only notes
    - `search_notes("work-docs", "query", note_types=["note", "person"])` - Multiple note types
    - `search_notes("research", "query", entity_types=["observation"])` - Filter by entity type
    - `search_notes("research", "query", entity_types=["observation"], categories=["requirement"])`
      - Filter observations to an exact category
    - `search_notes("team-docs", "query", after_date="2024-01-01")` - Recent content only
    - `search_notes("my-project", "query", after_date="1 week")` - Relative date filtering
    - `search_notes("my-project", "query", tags=["security"])` - Filter by frontmatter tags
    - `search_notes("my-project", "query", status="in-progress")` - Filter by frontmatter status
    - `search_notes("my-project", "query", metadata_filters={"priority": {"$in": ["high"]}})`

    ### Structured Metadata Filters
    Filters are exact matches on frontmatter metadata. Supported forms:
    - Equality: `{"status": "in-progress"}`
    - Array contains (all): `{"tags": ["security", "oauth"]}`
    - Operators:
      - `$in`: `{"priority": {"$in": ["high", "critical"]}}`
      - `$gt`, `$gte`, `$lt`, `$lte`: `{"schema.confidence": {"$gt": 0.7}}`
      - `$between`: `{"schema.confidence": {"$between": [0.3, 0.6]}}`
    - Nested keys use dot notation (e.g., `"schema.confidence"`).

    ### Filter-only Searches
    Omit `query` (or pass None) when only using structured filters:
    - `search_notes(metadata_filters={"type": "spec"}, project="my-project")`
    - `search_notes(tags=["security"], project="my-project")`
    - `search_notes(status="draft", project="my-project")`

    ### Convenience Filters
    `tags` and `status` are shorthand for metadata_filters. If the same key exists in
    metadata_filters, that value wins.

    ### Advanced Pattern Examples
    - `search_notes("work-project", "project AND (meeting OR discussion)")` - Complex boolean logic
    - `search_notes("research", "\"exact phrase\" AND keyword")` - Combine phrase and keyword search
    - `search_notes("dev-notes", "bug NOT fixed")` - Exclude resolved issues
    - `search_notes("archive", "docs/2024-*", search_type="permalink")` - Year-based permalink search

    Args:
        query: Optional search query string (supports boolean operators, phrases, patterns).
              Omit or pass None for filter-only searches using metadata_filters, tags, or status.
        project: Project name to search in. Optional - server will resolve using hierarchy.
                If unknown, use list_memory_projects() to discover available projects.
        project_id: Project external_id (UUID). Prefer this over `project` when known —
                it routes to the exact project regardless of name collisions across cloud
                workspaces. Takes precedence over `project`. Get from list_memory_projects().
        search_all_projects: Optional opt-in to search every accessible project. Ignored when
                `project` or `project_id` is supplied.
        page: The page number of results to return (default 1)
        page_size: The number of results to return per page (default 10)
        search_type: Type of search to perform, one of:
                    "text", "title", "permalink", "vector", "semantic", "hybrid".
                    Default is dynamic: "hybrid" when semantic search is enabled, otherwise "text".
        output_format: "text" preserves existing structured search response behavior.
            "json" returns a machine-readable dictionary payload.
        note_types: Optional list of note types to search (e.g., ["note", "person"])
        entity_types: Optional list of entity types to filter by (e.g., ["entity", "observation"])
        categories: Optional list of observation categories for exact matching (e.g.,
                   ["requirement"]). Pair with entity_types=["observation"] to return only
                   observations whose category matches exactly.
        after_date: Optional date filter for recent content (e.g., "1 week", "2d", "2024-01-01")
        metadata_filters: Optional structured frontmatter filters (e.g., {"status": "in-progress"})
        tags: Optional tag filter (frontmatter tags); shorthand for metadata_filters["tags"].
              Accepts a list (["a", "b"]) or a comma-separated string ("a,b"), matching the
              write_note tags convention and the tag: query shorthand.
        status: Optional status filter (frontmatter status); shorthand for metadata_filters["status"]
        min_similarity: Optional float to override the global semantic_min_similarity threshold
                       for this query. E.g., 0.0 to see all vector results, or 0.8 for high precision.
                       Only applies to vector and hybrid search types.
        context: Optional FastMCP context for performance caching.

    Returns:
        Formatted markdown text (output_format="text"), dict (output_format="json"),
        or helpful error guidance string if search fails

        Pagination note: use `total` as a count only when `total_is_exact` is true.
        Vector and hybrid searches skip the count query (it would cost a second
        semantic retrieval pass), report `total: 0` with `total_is_exact: false`,
        and use `has_more` for pagination.

    Examples:
        # Basic text search
        results = await search_notes("project planning")
        # Plain multi-term text uses strict matching first, then relaxed OR fallback if needed

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

        # Search with note type filter - type property in frontmatter
        results = await search_notes(
            "meeting notes",
            note_types=["note"],
        )

        # Search with entity type filter
        results = await search_notes(
            "meeting notes",
            entity_types=["observation"],
        )

        # Search for recent content
        results = await search_notes(
            "bug report",
            after_date="1 week"
        )

        # Pattern matching on permalinks
        results = await search_notes(
            "docs/meeting-*",
            search_type="permalink"
        )

        # Title-only search
        results = await search_notes(
            "Machine Learning",
            search_type="title"
        )

        # Complex search with multiple filters
        results = await search_notes(
            "(bug OR issue) AND NOT resolved",
            note_types=["note"],
            after_date="2024-01-01"
        )

        # Explicit project specification
        results = await search_notes("project planning", project="my-project")
    """
    # Validate pagination arguments before they reach the API/repository layer.
    # Trigger: page < 1 or page_size < 1 (e.g. page_size=0 or a negative slice).
    # Why: a non-positive page_size yields zero rows yet the router computes
    #      has_more = offset + len(results) < total, returning a misleading
    #      has_more=True with no reachable page; a negative page_size becomes an
    #      uncapped SQLite LIMIT. Mirrors recent_activity's guard so all navigation
    #      tools reject invalid pagination consistently.
    # Outcome: caller gets an explicit ValueError instead of a silent bad payload.
    if page < 1:
        raise ValueError(f"page must be >= 1, got {page}")
    if page_size < 1:
        raise ValueError(f"page_size must be >= 1, got {page_size}")

    # Trigger: list params arrived via a direct function call instead of the MCP layer.
    # Why: the BeforeValidator annotations only run through MCP/Pydantic validation; direct
    #      callers (e.g. `bm tool search-notes --type note,task` in cli/commands/tool.py,
    #      which Typer collects as the one-element list ["note,task"]) would otherwise
    #      forward the comma string as one literal type that matches nothing (#930).
    # Outcome: comma-split/list normalization applies on every path; parse_str_list is
    #          idempotent, so MCP-validated input passes through unchanged.
    note_types = parse_str_list(note_types) if note_types is not None else []
    entity_types = parse_str_list(entity_types) if entity_types is not None else []
    categories = parse_str_list(categories) if categories is not None else []

    # Avoid mutable-default-argument footguns. Treat None as "no filter".
    # Lowercase note_types so "Chapter" matches the stored "chapter".
    note_types = [t.lower() for t in note_types] if note_types else []
    entity_types = entity_types or []
    # Categories are matched exactly against the indexed observation category,
    # so preserve their original casing (unlike the lowercased note_types).
    categories = categories or []

    # Trigger: tags arrived via a direct function call instead of the MCP layer.
    # Why: the BeforeValidator above only runs through MCP/Pydantic validation; direct
    #      callers (e.g. `bm tool search-notes --tag a,b` in cli/commands/tool.py, which
    #      Typer collects as the one-element list ["a,b"]) would otherwise forward the
    #      comma string as one literal tag that matches nothing (#910).
    # Outcome: comma-split/list normalization applies on every path; parse_tags is
    #          idempotent, so MCP-validated input passes through unchanged.
    tags = parse_tags(tags) or None

    # Parse tag:<value> shorthand at tool level so it works with all search modes.
    # Handles "tag:security", "tag:coffee tag:brewing", "tag:coffee AND tag:brewing".
    # Without this, hybrid/vector modes fail because they require non-empty text,
    # but the service-layer tag: parser clears the text after the mode is set.
    if query and "tag:" in query.lower():
        # Extract tag values, splitting comma-separated lists (e.g. "tag:coffee,brewing")
        raw_values = re.findall(r"tag:(\S+)", query, flags=re.IGNORECASE)
        tag_values = [v for raw in raw_values for v in raw.split(",") if v]
        if tag_values:
            # Merge with any explicitly provided tags
            tags = list(set((tags or []) + tag_values))
            # Remove tag: tokens and boolean connectors, keep remaining text as query
            remainder = re.sub(r"tag:\S+", "", query, flags=re.IGNORECASE)
            remainder = re.sub(r"\b(AND|OR|NOT)\b", "", remainder).strip()
            query = remainder or None

    # Detect project from a memory URL or permalink prefix before routing.
    # project_id routes by external UUID, so it bypasses URL discovery entirely.
    if project is None and project_id is None and query is not None:
        detected = await detect_project_from_identifier_prefix(
            query,
            ConfigManager().config,
            context=context,
        )
        if detected:
            project = detected

    # Trigger: caller explicitly requests account/workspace-wide search and did not
    # already provide a concrete project route.
    # Why: multi-project fan-out can be slow, so default search remains project-scoped.
    # Outcome: run one normal search per accessible project and merge ranked results.
    if search_all_projects and project is None and project_id is None:
        all_projects_result = await _search_all_projects(
            query=query,
            page=page,
            page_size=page_size,
            search_type=search_type,
            output_format=output_format,
            note_types=note_types,
            entity_types=entity_types,
            categories=categories,
            after_date=after_date,
            metadata_filters=metadata_filters,
            tags=tags,
            status=status,
            min_similarity=min_similarity,
            context=context,
        )
        return all_projects_result

    with logfire.span(
        "mcp.tool.search_notes",
        entrypoint="mcp",
        tool_name="search_notes",
        requested_project=project,
        requested_project_id=project_id,
        search_all_projects=search_all_projects,
        search_type=search_type or "default",
        output_format=output_format,
        page=page,
        page_size=page_size,
        has_query=bool(query and query.strip()),
        note_type_filter_count=len(note_types),
        entity_type_filter_count=len(entity_types),
        category_filter_count=len(categories),
        has_filters=bool(
            metadata_filters
            or tags
            or status
            or note_types
            or entity_types
            or categories
            or after_date
        ),
        has_tags_filter=bool(tags),
        has_status_filter=bool(status),
    ):
        async with get_project_client(project, context=context, project_id=project_id) as (
            client,
            active_project,
        ):
            # Handle memory:// URLs by resolving to permalink search.
            # Use active_project.name so resolution hits the cached active project
            # when project_id was used or `project` was wrong/ambiguous.
            is_memory_url = False
            if query is not None:
                _, resolved_query, is_memory_url = await resolve_project_and_path(
                    client, query, active_project.name, context
                )
                if is_memory_url:
                    query = resolved_query
            effective_search_type = search_type or _default_search_type()
            if is_memory_url:
                effective_search_type = "permalink"

            try:
                # Create a SearchQuery object based on the parameters
                search_query = SearchQuery()

                # Only map search_type to query fields when there is an actual query string.
                # When query is None/empty, skip the search mode block — filters-only path.
                effective_query = (query or "").strip()
                if effective_query:
                    valid_search_types = {
                        "text",
                        "title",
                        "permalink",
                        "vector",
                        "semantic",
                        "hybrid",
                    }
                    if effective_search_type == "text":
                        search_query.text = effective_query
                        search_query.retrieval_mode = SearchRetrievalMode.FTS
                    elif effective_search_type in ("vector", "semantic"):
                        search_query.text = effective_query
                        search_query.retrieval_mode = SearchRetrievalMode.VECTOR
                    elif effective_search_type == "hybrid":
                        search_query.text = effective_query
                        search_query.retrieval_mode = SearchRetrievalMode.HYBRID
                    elif effective_search_type == "title":
                        search_query.title = effective_query
                    elif effective_search_type == "permalink" and "*" in effective_query:
                        search_query.permalink_match = effective_query
                    elif effective_search_type == "permalink":
                        search_query.permalink = effective_query
                    else:
                        raise ValueError(
                            f"Invalid search_type '{effective_search_type}'. "
                            f"Valid options: {', '.join(sorted(valid_search_types))}"
                        )

                # Add optional filters if provided (empty lists are treated as no filter)
                if entity_types:
                    search_query.entity_types = [SearchItemType(t) for t in entity_types]
                if categories:
                    search_query.categories = categories
                if note_types:
                    search_query.note_types = note_types
                if after_date:
                    search_query.after_date = after_date
                if metadata_filters:
                    # Alias common column/model names to their frontmatter key equivalents.
                    # Users often pass "note_type" (the entity model column) when the
                    # frontmatter field is actually "type".
                    _METADATA_KEY_ALIASES = {"note_type": "type"}
                    metadata_filters = {
                        _METADATA_KEY_ALIASES.get(k, k): v for k, v in metadata_filters.items()
                    }
                    search_query.metadata_filters = metadata_filters
                if tags:
                    search_query.tags = tags
                if status:
                    search_query.status = status
                if min_similarity is not None:
                    search_query.min_similarity = min_similarity

                # Reject searches with no criteria at all
                if search_query.no_criteria():
                    return (
                        "# No Search Criteria\n\n"
                        "Please provide at least one of: `query`, `metadata_filters`, "
                        "`tags`, `status`, `note_types`, `entity_types`, `categories`, "
                        "or `after_date`."
                    )

                # Default to entity-level results to avoid returning individual
                # observations/relations as separate search results (see issue #31).
                # Applied after no_criteria() so that the implicit default doesn't
                # mask a truly empty search request.
                if not search_query.entity_types:
                    # Trigger: a category filter was supplied without an explicit
                    #          entity_types.
                    # Why: categories only exist on observations — defaulting to "entity"
                    #      (whose rows have NULL category) would AND a category filter against
                    #      entity rows and return nothing, defeating a category-only search.
                    # Outcome: scope the implicit default to observations so
                    #          search_notes(categories=[...]) returns the matching bullets.
                    if search_query.categories:
                        search_query.entity_types = [SearchItemType("observation")]
                    else:
                        search_query.entity_types = [SearchItemType("entity")]

                logger.debug(
                    f"Search request: project={active_project.name} "
                    f"search_type={effective_search_type} "
                    f"query={effective_query or '<filters-only>'} "
                    f"note_types={len(note_types)} entity_types={len(search_query.entity_types or [])} "
                    f"page={page} page_size={page_size}"
                )
                # Import here to avoid circular import (tools → clients → utils → tools)
                from basic_memory.mcp.clients import SearchClient

                # Use typed SearchClient for API calls
                search_client = SearchClient(client, active_project.external_id)
                result = await search_client.search(
                    search_query.model_dump(),
                    page=page,
                    page_size=page_size,
                )
                logger.debug(
                    f"Search response: project={active_project.name} "
                    f"results={len(result.results)} has_more={str(result.has_more).lower()} "
                    f"page={result.current_page} page_size={result.page_size}"
                )

                # Check if we got no results and provide helpful guidance
                if not result.results:
                    logger.debug(
                        f"Search returned no results for query: {query} in project {active_project.name}"
                    )
                    # Don't treat this as an error, but the user might want guidance
                    # We return the empty result as normal - the user can decide if they need help

                if output_format == "json":
                    return result.model_dump(mode="json", exclude_none=True)

                return _format_search_markdown(
                    result, active_project.name, query, project_id=active_project.external_id
                )

            except Exception as e:
                logger.error(
                    f"Search failed for query '{query or ''}': {e}, project: {active_project.name}"
                )
                # Return formatted error message as string for better user experience
                return _format_search_error_response(
                    active_project.name, str(e), query or "", effective_search_type
                )
