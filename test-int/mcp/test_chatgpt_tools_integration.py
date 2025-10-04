"""
Integration tests for ChatGPT-compatible MCP tools.

Tests the complete flow of search and fetch tools designed for ChatGPT integration,
ensuring they properly wrap Basic Memory's MCP tools and return OpenAI-compatible
MCP content array format.
"""

import json
import pytest
from fastmcp import Client


def extract_mcp_json_content(mcp_result):
    """
    Helper to extract JSON content from MCP CallToolResult.

    FastMCP auto-serializes our List[Dict[str, Any]] return values, so we need to:
    1. Get the content list from the CallToolResult
    2. Parse the JSON string in the text field (which is our serialized list)
    3. Extract the actual JSON from the MCP content array structure
    """
    content_list = mcp_result.content
    mcp_content_list = json.loads(content_list[0].text)
    return json.loads(mcp_content_list[0]["text"])


@pytest.mark.asyncio
async def test_chatgpt_search_basic(mcp_server, app, test_project):
    """Test basic ChatGPT search functionality with MCP content array format."""

    async with Client(mcp_server) as client:
        # Create test notes for searching
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Machine Learning Fundamentals",
                "folder": "ai",
                "content": (
                    "# Machine Learning Fundamentals\n\nIntroduction to ML concepts and algorithms."
                ),
                "tags": "ml,ai,fundamentals",
            },
        )

        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Deep Learning with PyTorch",
                "folder": "ai",
                "content": (
                    "# Deep Learning with PyTorch\n\n"
                    "Building neural networks using PyTorch framework."
                ),
                "tags": "pytorch,deep-learning,ai",
            },
        )

        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Data Visualization Guide",
                "folder": "data",
                "content": (
                    "# Data Visualization Guide\n\nCreating charts and graphs for data analysis."
                ),
                "tags": "visualization,data,charts",
            },
        )

        # Test ChatGPT search tool
        search_result = await client.call_tool(
            "search",
            {
                "query": "Machine Learning",
            },
        )

        # Extract JSON content from MCP result
        results_json = extract_mcp_json_content(search_result)
        assert "results" in results_json
        assert len(results_json["results"]) > 0

        # Check result structure
        first_result = results_json["results"][0]
        assert "id" in first_result
        assert "title" in first_result
        assert "url" in first_result

        # Verify correct content found
        titles = [r["title"] for r in results_json["results"]]
        assert "Machine Learning Fundamentals" in titles
        assert "Data Visualization Guide" not in titles


@pytest.mark.asyncio
async def test_chatgpt_search_empty_results(mcp_server, app, test_project):
    """Test ChatGPT search with no matching results."""

    async with Client(mcp_server) as client:
        # Search for non-existent content
        search_result = await client.call_tool(
            "search",
            {
                "query": "NonExistentTopic12345",
            },
        )

        # Extract JSON content from MCP result
        results_json = extract_mcp_json_content(search_result)
        assert "results" in results_json
        assert len(results_json["results"]) == 0
        assert results_json["query"] == "NonExistentTopic12345"


@pytest.mark.asyncio
async def test_chatgpt_search_with_boolean_operators(mcp_server, app, test_project):
    """Test ChatGPT search with boolean operators."""

    async with Client(mcp_server) as client:
        # Create test notes
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Python Web Frameworks",
                "folder": "dev",
                "content": (
                    "# Python Web Frameworks\n\nComparing Django and Flask for web development."
                ),
                "tags": "python,web,frameworks",
            },
        )

        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "JavaScript Frameworks",
                "folder": "dev",
                "content": "# JavaScript Frameworks\n\nReact, Vue, and Angular comparison.",
                "tags": "javascript,web,frameworks",
            },
        )

        # Test with AND operator
        search_result = await client.call_tool(
            "search",
            {
                "query": "Python AND frameworks",
            },
        )

        results_json = extract_mcp_json_content(search_result)
        titles = [r["title"] for r in results_json["results"]]
        assert "Python Web Frameworks" in titles
        assert "JavaScript Frameworks" not in titles


@pytest.mark.asyncio
async def test_chatgpt_fetch_document(mcp_server, app, test_project):
    """Test ChatGPT fetch tool for retrieving full document content."""

    async with Client(mcp_server) as client:
        # Create a test note
        note_content = """# Advanced Python Techniques

## Overview
This document covers advanced Python programming techniques.

## Topics Covered
- Decorators
- Context Managers
- Metaclasses
- Async/Await patterns

## Code Examples
```python
def my_decorator(func):
    def wrapper(*args, **kwargs):
        return func(*args, **kwargs)
    return wrapper
```
"""

        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Advanced Python Techniques",
                "folder": "programming",
                "content": note_content,
                "tags": "python,advanced,programming",
            },
        )

        # Fetch the document using its title
        fetch_result = await client.call_tool(
            "fetch",
            {
                "id": "Advanced Python Techniques",
            },
        )

        # Extract JSON content from MCP result
        document_json = extract_mcp_json_content(fetch_result)
        assert "id" in document_json
        assert "title" in document_json
        assert "text" in document_json
        assert "url" in document_json
        assert "metadata" in document_json

        # Verify content
        assert document_json["title"] == "Advanced Python Techniques"
        assert "Decorators" in document_json["text"]
        assert "Context Managers" in document_json["text"]
        assert "def my_decorator" in document_json["text"]


@pytest.mark.asyncio
async def test_chatgpt_fetch_by_permalink(mcp_server, app, test_project):
    """Test ChatGPT fetch using permalink identifier."""

    async with Client(mcp_server) as client:
        # Create a note with known content
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Test Document",
                "folder": "test",
                "content": "# Test Document\n\nThis is test content for permalink fetching.",
                "tags": "test",
            },
        )

        # First search to get the permalink
        search_result = await client.call_tool(
            "search",
            {
                "query": "Test Document",
            },
        )

        results_json = extract_mcp_json_content(search_result)
        assert len(results_json["results"]) > 0
        permalink = results_json["results"][0]["id"]

        # Fetch using the permalink
        fetch_result = await client.call_tool(
            "fetch",
            {
                "id": permalink,
            },
        )

        # Verify the fetched document
        document_json = extract_mcp_json_content(fetch_result)
        assert document_json["id"] == permalink
        assert "Test Document" in document_json["title"]
        assert "test content for permalink fetching" in document_json["text"]


@pytest.mark.asyncio
async def test_chatgpt_fetch_nonexistent_document(mcp_server, app, test_project):
    """Test ChatGPT fetch with non-existent document ID."""

    async with Client(mcp_server) as client:
        # Try to fetch a non-existent document
        fetch_result = await client.call_tool(
            "fetch",
            {
                "id": "NonExistentDocument12345",
            },
        )

        # Extract JSON content from MCP result
        document_json = extract_mcp_json_content(fetch_result)

        # Should have document structure even for errors
        assert "id" in document_json
        assert "title" in document_json
        assert "text" in document_json

        # Check for error indication
        assert document_json["id"] == "NonExistentDocument12345"
        assert "Not Found" in document_json["text"] or "not found" in document_json["text"]


@pytest.mark.asyncio
async def test_chatgpt_fetch_with_empty_title(mcp_server, app, test_project):
    """Test ChatGPT fetch handles documents with empty or missing titles."""

    async with Client(mcp_server) as client:
        # Create a note without a title in the content
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "untitled-note",
                "folder": "misc",
                "content": "This is content without a markdown header.\n\nJust plain text.",
                "tags": "misc",
            },
        )

        # Fetch the document
        fetch_result = await client.call_tool(
            "fetch",
            {
                "id": "untitled-note",
            },
        )

        # Parse JSON response
        document_json = extract_mcp_json_content(fetch_result)

        # Should have a title even if content doesn't have one
        assert "title" in document_json
        assert document_json["title"] != ""
        assert document_json["title"] is not None
        assert "content without a markdown header" in document_json["text"]


@pytest.mark.asyncio
async def test_chatgpt_search_pagination_default(mcp_server, app, test_project):
    """Test that ChatGPT search uses reasonable pagination defaults."""

    async with Client(mcp_server) as client:
        # Create more than 10 notes to test pagination
        for i in range(15):
            await client.call_tool(
                "write_note",
                {
                    "project": test_project.name,
                    "title": f"Test Note {i}",
                    "folder": "bulk",
                    "content": f"# Test Note {i}\n\nThis is test content number {i}.",
                    "tags": "test,bulk",
                },
            )

        # Search should return max 10 results by default
        search_result = await client.call_tool(
            "search",
            {
                "query": "Test Note",
            },
        )

        results_json = extract_mcp_json_content(search_result)

        # Should have at most 10 results (the default page_size)
        assert len(results_json["results"]) <= 10
        assert results_json["total_count"] <= 10


@pytest.mark.asyncio
async def test_chatgpt_tools_error_handling(mcp_server, app, test_project):
    """Test error handling in ChatGPT tools returns proper MCP format."""

    async with Client(mcp_server) as client:
        # Test search with invalid query (if validation exists)
        # Using empty query to potentially trigger an error
        search_result = await client.call_tool(
            "search",
            {
                "query": "",  # Empty query might cause an error
            },
        )

        # Should still return MCP content array format
        assert hasattr(search_result, "content")
        content_list = search_result.content
        assert isinstance(content_list, list)
        assert len(content_list) == 1
        assert content_list[0].type == "text"

        # Should be valid JSON even on error
        results_json = extract_mcp_json_content(search_result)
        assert "results" in results_json  # Should have results key even if empty


@pytest.mark.asyncio
async def test_chatgpt_integration_workflow(mcp_server, app, test_project):
    """Test complete workflow: search then fetch, as ChatGPT would use it."""

    async with Client(mcp_server) as client:
        # Step 1: Create multiple documents
        docs = [
            {
                "title": "API Design Best Practices",
                "content": (
                    "# API Design Best Practices\n\nRESTful API design principles and patterns."
                ),
                "tags": "api,rest,design",
            },
            {
                "title": "GraphQL vs REST",
                "content": "# GraphQL vs REST\n\nComparing GraphQL and REST API architectures.",
                "tags": "api,graphql,rest",
            },
            {
                "title": "Database Design Patterns",
                "content": (
                    "# Database Design Patterns\n\n"
                    "Common database design patterns and anti-patterns."
                ),
                "tags": "database,design,patterns",
            },
        ]

        for doc in docs:
            await client.call_tool(
                "write_note",
                {
                    "project": test_project.name,
                    "title": doc["title"],
                    "folder": "architecture",
                    "content": doc["content"],
                    "tags": doc["tags"],
                },
            )

        # Step 2: Search for API-related content (as ChatGPT would)
        search_result = await client.call_tool(
            "search",
            {
                "query": "API",
            },
        )

        results_json = extract_mcp_json_content(search_result)
        assert len(results_json["results"]) >= 2

        # Step 3: Fetch one of the search results (as ChatGPT would)
        first_result_id = results_json["results"][0]["id"]
        fetch_result = await client.call_tool(
            "fetch",
            {
                "id": first_result_id,
            },
        )

        document_json = extract_mcp_json_content(fetch_result)

        # Verify the fetched document matches search result
        assert document_json["id"] == first_result_id
        assert "API" in document_json["text"] or "api" in document_json["text"].lower()

        # Verify document has expected structure
        assert document_json["metadata"]["format"] == "markdown"
