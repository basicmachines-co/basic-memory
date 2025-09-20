"""ChatGPT-compatible MCP tools for Basic Memory.

This module provides simplified 'search' and 'fetch' tools that adapt Basic Memory's
rich MCP interface to ChatGPT's expected naming conventions and MCP content array format.

ChatGPT's MCP integration expects:
- search(query: str) -> MCP content array with JSON-encoded results
- fetch(id: str) -> MCP content array with JSON-encoded document

These tools wrap our existing search_notes and read_note functions and format
responses according to OpenAI's MCP specification.
"""

import json
from typing import Any, Dict, List, Optional
from loguru import logger
from fastmcp import Context

from basic_memory.mcp.server import mcp
from basic_memory.mcp.tools.search import search_notes
from basic_memory.mcp.tools.read_note import read_note
from basic_memory.schemas.search import SearchResponse


def _format_search_results_for_chatgpt(results: SearchResponse) -> List[Dict[str, Any]]:
    """Format search results according to ChatGPT's expected schema.

    Returns a list of result objects with id, title, and url fields.
    """
    formatted_results = []

    for result in results.results:
        formatted_result = {
            "id": result.permalink or f"doc-{len(formatted_results)}",
            "title": result.title if result.title and result.title.strip() else "Untitled",
            "url": result.permalink or ""
        }
        formatted_results.append(formatted_result)

    return formatted_results


def _format_document_for_chatgpt(content: str, identifier: str, title: Optional[str] = None) -> Dict[str, Any]:
    """Format document content according to ChatGPT's expected schema.

    Returns a document object with id, title, text, url, and metadata fields.
    """
    # Extract title from markdown content if not provided
    if not title and isinstance(content, str):
        lines = content.split('\n')
        if lines and lines[0].startswith('# '):
            title = lines[0][2:].strip()
        else:
            title = identifier.split('/')[-1].replace('-', ' ').title()

    # Ensure title is never None
    if not title:
        title = "Untitled Document"

    # Handle error cases
    if isinstance(content, str) and content.startswith("# Note Not Found"):
        return {
            "id": identifier,
            "title": title or "Document Not Found",
            "text": content,
            "url": identifier,
            "metadata": {"error": "Document not found"}
        }

    return {
        "id": identifier,
        "title": title or "Untitled Document",
        "text": content,
        "url": identifier,
        "metadata": {"format": "markdown"}
    }


@mcp.tool(
    description="Search for content across the knowledge base"
)
async def search(
    query: str,
    context: Context | None = None,
) -> List[Dict[str, Any]]:
    """Search for content across the knowledge base.

    This tool provides a simplified search interface optimized for ChatGPT's
    MCP integration. It returns results in the required MCP content array format.

    Args:
        query: Search query string (supports boolean operators, phrases)
        context: MCP context for authentication and routing

    Returns:
        MCP content array with exactly one item containing JSON-encoded search results

    The response follows OpenAI's MCP specification:
    {
        "content": [
            {
                "type": "text",
                "text": "{\"results\":[{\"id\":\"doc-1\",\"title\":\"...\",\"url\":\"...\"}]}"
            }
        ]
    }
    """
    logger.info(f"ChatGPT search request: query='{query}'")

    try:
        # Call underlying search_notes with sensible defaults for ChatGPT
        results = await search_notes.fn(
            query=query,
            page=1,
            page_size=10,  # Reasonable default for ChatGPT consumption
            search_type="text",  # Default to full-text search
            context=context
        )

        # Handle string error responses from search_notes
        if isinstance(results, str):
            logger.warning(f"Search failed with error: {results[:100]}...")
            search_results = {
                "results": [],
                "error": "Search failed",
                "error_details": results[:500]  # Truncate long error messages
            }
        else:
            # Format successful results for ChatGPT
            formatted_results = _format_search_results_for_chatgpt(results)
            search_results = {
                "results": formatted_results,
                "total_count": len(results.results),  # Use actual count from results
                "query": query
            }
            logger.info(f"Search completed: {len(formatted_results)} results returned")

        # Return in MCP content array format as required by OpenAI
        return [
            {
                "type": "text",
                "text": json.dumps(search_results, ensure_ascii=False)
            }
        ]

    except Exception as e:
        logger.error(f"ChatGPT search failed for query '{query}': {e}")
        error_results = {
            "results": [],
            "error": "Internal search error",
            "error_message": str(e)[:200]
        }
        return [
            {
                "type": "text",
                "text": json.dumps(error_results, ensure_ascii=False)
            }
        ]


@mcp.tool(
    description="Fetch the full contents of a search result document"
)
async def fetch(
    id: str,
    context: Context | None = None,
) -> List[Dict[str, Any]]:
    """Fetch the full content of a specific document.

    This tool provides a simplified fetch interface optimized for ChatGPT's
    MCP integration. It returns document content in the required MCP content array format.

    Args:
        id: Document identifier (permalink, title, or memory:// URL)
        context: MCP context for authentication and routing

    Returns:
        MCP content array with exactly one item containing JSON-encoded document

    The response follows OpenAI's MCP specification:
    {
        "content": [
            {
                "type": "text",
                "text": "{\"id\":\"doc-1\",\"title\":\"...\",\"text\":\"full text...\",\"url\":\"...\",\"metadata\":{}}"
            }
        ]
    }
    """
    logger.info(f"ChatGPT fetch request: id='{id}'")

    try:
        # Call underlying read_note function
        content = await read_note.fn(
            identifier=id,
            page=1,
            page_size=10,  # Default pagination
            context=context
        )

        # Format the document for ChatGPT
        document = _format_document_for_chatgpt(content, id)

        logger.info(f"Fetch completed: id='{id}', content_length={len(document.get('text', ''))}")

        # Return in MCP content array format as required by OpenAI
        return [
            {
                "type": "text",
                "text": json.dumps(document, ensure_ascii=False)
            }
        ]

    except Exception as e:
        logger.error(f"ChatGPT fetch failed for id '{id}': {e}")
        error_document = {
            "id": id,
            "title": "Fetch Error",
            "text": f"Failed to fetch document: {str(e)[:200]}",
            "url": id,
            "metadata": {"error": "Fetch failed"}
        }
        return [
            {
                "type": "text",
                "text": json.dumps(error_document, ensure_ascii=False)
            }
        ]