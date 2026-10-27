"""ChatGPT-compatible MCP tools for Basic Memory.

These adapters expose Basic Memory's search/fetch functionality using the exact
tool names and response structure OpenAI's MCP clients expect: each call returns
a list containing a single `{"type": "text", "text": "{...json...}"}` item.
"""

import json
from typing import Any, Dict, List, Optional, cast

from fastmcp import Context
from loguru import logger

from basic_memory.mcp.client_info import is_openai_mcp_client
from basic_memory.config import ConfigManager
from basic_memory.mcp.project_context import (
    detect_project_from_identifier_prefix,
    resolve_project_parameter,
)
from basic_memory.mcp.tools.read import bm_read
from basic_memory.mcp.refusals import Refusal
from basic_memory.mcp.tools.ranked_search import bm_search
from basic_memory.schemas.search import SearchResponse, SearchResult


_UNSUPPORTED_CLIENT_ERROR = "Unsupported MCP client"


def _text_content(payload: dict[str, Any]) -> List[Dict[str, Any]]:
    return [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]


def _unsupported_search_client_response() -> List[Dict[str, Any]]:
    return _text_content(
        {
            "results": [],
            "error": _UNSUPPORTED_CLIENT_ERROR,
            "error_message": (
                "The search compatibility tool is only available to OpenAI MCP clients. "
                "Use bm_search instead."
            ),
        }
    )


def _unsupported_fetch_client_response(identifier: str) -> List[Dict[str, Any]]:
    return _text_content(
        {
            "id": identifier,
            "title": "Unsupported MCP Client",
            "text": (
                "The fetch compatibility tool is only available to OpenAI MCP clients. "
                "Use bm_read instead."
            ),
            "url": identifier,
            "metadata": {"error": _UNSUPPORTED_CLIENT_ERROR},
        }
    )


def _identifier_for_bm_read(identifier: str) -> str:
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

    return {
        "id": identifier,
        "title": title or "Untitled Document",
        "text": content,
        "url": identifier,
        "metadata": {"format": "markdown"},
    }


async def search(
    query: str,
    context: Context | None = None,
) -> List[Dict[str, Any]]:
    """ChatGPT/OpenAI MCP search adapter returning a single text content item.

    Args:
        query: Search query (full-text syntax supported by `bm_search`)
        context: Optional FastMCP context passed through for auth/session data

    Returns:
        List with one dict: `{ "type": "text", "text": "{...JSON...}" }`
        where the JSON body contains `results`, `total_count`, and echo of `query`.
    """
    if not await is_openai_mcp_client(context):
        logger.warning("Rejected ChatGPT search request from non-OpenAI MCP client")
        return _unsupported_search_client_response()

    logger.debug(f"ChatGPT search request: query='{query}'")

    try:
        # Keep this adapter tiny: the real search behavior lives in bm_search.
        # The connector has no project parameter; preserve its default-project scope.
        project = await resolve_project_parameter(context=context)
        results = await bm_search(
            project=project,
            query=query,
            page=1,
            page_size=10,
            output_format="json",
            context=context,
        )

        raw_results = results.get("results", []) if isinstance(results, dict) else []

        formatted_results = _format_search_results_for_chatgpt(raw_results)
        search_results = {
            "results": formatted_results,
            "total_count": len(raw_results),  # Use actual count from results
            "query": query,
        }
        logger.debug(f"Search completed: {len(formatted_results)} results returned")

        # Return in MCP content array format as required by OpenAI
        return _text_content(search_results)

    except Refusal as error:
        return _text_content(
            {
                "results": [],
                "error": "Search temporarily unavailable"
                if error.code == "UNAVAILABLE"
                else "Search failed",
                "error_message": "Search temporarily unavailable, retry shortly"
                if error.code == "UNAVAILABLE"
                else error.message,
            }
        )

    except Exception as e:
        logger.error(f"ChatGPT search failed for query '{query}': {e}")
        error_results = {
            "results": [],
            "error": "Internal search error",
            "error_message": str(e)[:200],
        }
        return _text_content(error_results)


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
    if not await is_openai_mcp_client(context):
        logger.warning("Rejected ChatGPT fetch request from non-OpenAI MCP client")
        return _unsupported_fetch_client_response(id)

    logger.debug(f"ChatGPT fetch request: id='{id}'")

    try:
        # Folder separators do not establish project scope. Only a recognized
        # project prefix overrides this connector's default-project workflow.
        detected = await detect_project_from_identifier_prefix(id, ConfigManager().config, context)
        project = (
            detected.project_id or detected.project
            if detected is not None
            else await resolve_project_parameter(context=context)
        )
        content = str(
            await bm_read(
                identifier=_identifier_for_bm_read(id),
                include_frontmatter=False,
                project=project,
                context=context,
            )
        )

        # Format the document for ChatGPT
        document = _format_document_for_chatgpt(content, id)

        logger.debug(f"Fetch completed: id='{id}', content_length={len(document.get('text', ''))}")

        # Return in MCP content array format as required by OpenAI
        return _text_content(document)

    except Refusal as error:
        return _text_content(
            {
                "id": id,
                "title": "Document Not Found" if error.code == "NOT_FOUND" else "Fetch Error",
                "text": error.message,
                "url": id,
                "metadata": {
                    "error": "Document not found" if error.code == "NOT_FOUND" else error.code
                },
            }
        )

    except Exception as e:
        logger.error(f"ChatGPT fetch failed for id '{id}': {e}")
        error_document = {
            "id": id,
            "title": "Fetch Error",
            "text": f"Failed to fetch document: {str(e)[:200]}",
            "url": id,
            "metadata": {"error": "Fetch failed"},
        }
        return _text_content(error_document)
