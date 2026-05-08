"""ChatGPT-compatible MCP tools for Basic Memory.

These adapters expose Basic Memory's search/fetch functionality using the exact
tool names and response structure OpenAI's MCP clients expect: each call returns
a list containing a single `{"type": "text", "text": "{...json...}"}` item.
"""

import json
from typing import Any, Dict, List, Optional, cast
from uuid import UUID

from fastmcp import Context
from loguru import logger

from basic_memory.mcp.server import mcp
from basic_memory.mcp.tools.project_management import list_memory_projects
from basic_memory.mcp.tools.read_note import read_note
from basic_memory.mcp.tools.search import search_notes
from basic_memory.schemas.search import SearchResponse, SearchResult


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
    """Extract project routing refs for account-scoped ChatGPT search."""
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
    if normalized_permalink == project_permalink or normalized_permalink.startswith(
        f"{project_permalink}/"
    ):
        return f"{workspace_slug}/{normalized_permalink}"
    return f"{qualified_project}/{normalized_permalink}"


def _qualify_results_for_project(
    results: list[SearchResult | dict[str, Any]],
    project_ref: dict[str, str | None],
) -> list[dict[str, Any]]:
    """Attach the searched workspace/project prefix to each ChatGPT result id."""
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


def _result_score(result: SearchResult | dict[str, Any]) -> float:
    """Return a comparable search score for merged project results."""
    if isinstance(result, SearchResult):
        return result.score
    score = result.get("score")
    return float(score) if isinstance(score, int | float) else 0.0


def _identifier_for_read_note(identifier: str) -> str:
    """Convert ChatGPT result ids into routable Basic Memory identifiers."""
    stripped = identifier.strip()
    if stripped.startswith("memory://") or "/" not in stripped:
        return identifier
    return f"memory://{stripped}"


def _format_search_results_for_chatgpt(
    results: SearchResponse | list[SearchResult | dict[str, Any]] | dict[str, Any],
) -> List[Dict[str, Any]]:
    """Format search results according to ChatGPT's expected schema.

    Returns a list of result objects with id, title, and url fields.
    """
    if isinstance(results, SearchResponse):
        raw_results: list[SearchResult | dict[str, Any]] = list(results.results)
    elif isinstance(results, dict):
        nested_results = results.get("results")
        raw_results = (
            cast(list[SearchResult | dict[str, Any]], nested_results)
            if isinstance(nested_results, list)
            else []
        )
    else:
        raw_results = results

    formatted_results = []

    for result in raw_results:
        if isinstance(result, SearchResult):
            title = result.title
            permalink = result.permalink
        elif isinstance(result, dict):
            title = result.get("title")
            permalink = result.get("permalink")
        else:
            raise TypeError(f"Unexpected result type: {type(result).__name__}")

        formatted_result = {
            "id": permalink or f"doc-{len(formatted_results)}",
            "title": title if isinstance(title, str) and title.strip() else "Untitled",
            "url": permalink or "",
        }
        formatted_results.append(formatted_result)

    return formatted_results


def _format_document_for_chatgpt(
    content: str, identifier: str, title: Optional[str] = None
) -> Dict[str, Any]:
    """Format document content according to ChatGPT's expected schema.

    Returns a document object with id, title, text, url, and metadata fields.
    """
    # Extract title from markdown content if not provided
    if not title and isinstance(content, str):
        lines = content.split("\n")
        if lines and lines[0].startswith("# "):
            title = lines[0][2:].strip()
        else:
            title = identifier.split("/")[-1].replace("-", " ").title()

    # Ensure title is never None
    if not title:
        title = "Untitled Document"

    # Handle error cases
    if isinstance(content, str) and content.lstrip().startswith("# Note Not Found"):
        return {
            "id": identifier,
            "title": title or "Document Not Found",
            "text": content,
            "url": identifier,
            "metadata": {"error": "Document not found"},
        }

    return {
        "id": identifier,
        "title": title or "Untitled Document",
        "text": content,
        "url": identifier,
        "metadata": {"format": "markdown"},
    }


@mcp.tool(
    description="Search for content across the knowledge base",
    annotations={"readOnlyHint": True, "openWorldHint": False},
)
async def search(
    query: str,
    context: Context | None = None,
) -> List[Dict[str, Any]]:
    """ChatGPT/OpenAI MCP search adapter returning a single text content item.

    Args:
        query: Search query (full-text syntax supported by `search_notes`)
        context: Optional FastMCP context passed through for auth/session data

    Returns:
        List with one dict: `{ "type": "text", "text": "{...JSON...}" }`
        where the JSON body contains `results`, `total_count`, and echo of `query`.
    """
    logger.info(f"ChatGPT search request: query='{query}'")

    try:
        project_refs = _search_project_refs(
            await list_memory_projects(output_format="json", context=context)
        )

        raw_results: list[SearchResult | dict[str, Any]] = []
        if project_refs:
            # Trigger: ChatGPT only has a strict search(query) -> fetch(id) contract.
            # Why: default-project search strands notes in other workspaces/projects.
            # Outcome: query every discovered project, then merge the top matches.
            for project_ref in project_refs:
                results = await search_notes(
                    query=query,
                    project=project_ref["project"],
                    project_id=project_ref["project_id"],
                    page=1,
                    page_size=10,
                    output_format="json",
                    context=context,
                )

                if isinstance(results, str):
                    logger.warning(f"Search failed with error: {results[:100]}...")
                    search_results = {
                        "results": [],
                        "error": "Search failed",
                        "error_details": results[:500],
                    }
                    return [
                        {
                            "type": "text",
                            "text": json.dumps(search_results, ensure_ascii=False),
                        }
                    ]

                raw_results.extend(
                    _qualify_results_for_project(
                        _raw_results_from_search_payload(results),
                        project_ref,
                    )
                )
            raw_results = sorted(raw_results, key=_result_score, reverse=True)[:10]
        else:
            # Trigger: project discovery returned no structured rows.
            # Why: preserve the legacy single-project behavior when discovery is unavailable.
            # Outcome: let search_notes resolve the default project as before.
            results = await search_notes(
                query=query,
                page=1,
                page_size=10,
                output_format="json",
                context=context,
            )

            if isinstance(results, str):
                logger.warning(f"Search failed with error: {results[:100]}...")
                search_results = {
                    "results": [],
                    "error": "Search failed",
                    "error_details": results[:500],  # Truncate long error messages
                }
                return [{"type": "text", "text": json.dumps(search_results, ensure_ascii=False)}]

            raw_results = _raw_results_from_search_payload(results)

        formatted_results = _format_search_results_for_chatgpt(raw_results)
        search_results = {
            "results": formatted_results,
            "total_count": len(raw_results),  # Use actual count from results
            "query": query,
        }
        logger.info(f"Search completed: {len(formatted_results)} results returned")

        # Return in MCP content array format as required by OpenAI
        return [{"type": "text", "text": json.dumps(search_results, ensure_ascii=False)}]

    except Exception as e:
        logger.error(f"ChatGPT search failed for query '{query}': {e}")
        error_results = {
            "results": [],
            "error": "Internal search error",
            "error_message": str(e)[:200],
        }
        return [{"type": "text", "text": json.dumps(error_results, ensure_ascii=False)}]


@mcp.tool(
    description="Fetch the full contents of a search result document",
    annotations={"readOnlyHint": True, "openWorldHint": False},
)
async def fetch(
    id: str,
    context: Context | None = None,
) -> List[Dict[str, Any]]:
    """ChatGPT/OpenAI MCP fetch adapter returning a single text content item.

    Args:
        id: Document identifier (permalink, title, or memory URL)
        context: Optional FastMCP context passed through for auth/session data

    Returns:
        List with one dict: `{ "type": "text", "text": "{...JSON...}" }`
        where the JSON body includes `id`, `title`, `text`, `url`, and metadata.
    """
    logger.info(f"ChatGPT fetch request: id='{id}'")

    try:
        # Let read_note resolve the default project via get_project_client(),
        # which works in both local mode (ConfigManager) and cloud mode (database).
        content = str(
            await read_note(
                identifier=_identifier_for_read_note(id),
                context=context,
            )
        )

        # Format the document for ChatGPT
        document = _format_document_for_chatgpt(content, id)

        logger.info(f"Fetch completed: id='{id}', content_length={len(document.get('text', ''))}")

        # Return in MCP content array format as required by OpenAI
        return [{"type": "text", "text": json.dumps(document, ensure_ascii=False)}]

    except Exception as e:
        logger.error(f"ChatGPT fetch failed for id '{id}': {e}")
        error_document = {
            "id": id,
            "title": "Fetch Error",
            "text": f"Failed to fetch document: {str(e)[:200]}",
            "url": id,
            "metadata": {"error": "Fetch failed"},
        }
        return [{"type": "text", "text": json.dumps(error_document, ensure_ascii=False)}]
