"""
Integration tests for search_notes MCP tool.

Comprehensive tests covering search functionality using the complete
MCP client-server flow with real databases.
"""

import pytest
from fastmcp import Client


@pytest.mark.asyncio
async def test_search_basic_text_search(mcp_server, app):
    """Test basic text search functionality."""

    async with Client(mcp_server) as client:
        # Create test notes for searching
        await client.call_tool(
            "write_note",
            {
                "title": "Python Programming Guide",
                "folder": "docs",
                "content": "# Python Programming Guide\n\nThis guide covers Python basics and advanced topics.",
                "tags": "python,programming",
            },
        )

        await client.call_tool(
            "write_note",
            {
                "title": "Flask Web Development",
                "folder": "docs",
                "content": "# Flask Web Development\n\nBuilding web applications with Python Flask framework.",
                "tags": "python,flask,web",
            },
        )

        await client.call_tool(
            "write_note",
            {
                "title": "JavaScript Basics",
                "folder": "docs",
                "content": "# JavaScript Basics\n\nIntroduction to JavaScript programming language.",
                "tags": "javascript,programming",
            },
        )

        # Search for Python-related content
        search_result = await client.call_tool(
            "search_notes",
            {
                "query": "Python",
            },
        )

        assert len(search_result) == 1
        assert search_result[0].type == "text"

        # Parse the response (it should be a SearchResponse)
        result_text = search_result[0].text
        assert "Python Programming Guide" in result_text
        assert "Flask Web Development" in result_text
        assert "JavaScript Basics" not in result_text


@pytest.mark.asyncio
async def test_search_boolean_operators(mcp_server, app):
    """Test boolean search operators (AND, OR, NOT)."""

    async with Client(mcp_server) as client:
        # Create test notes
        await client.call_tool(
            "write_note",
            {
                "title": "Python Flask Tutorial",
                "folder": "tutorials",
                "content": "# Python Flask Tutorial\n\nLearn Python web development with Flask.",
                "tags": "python,flask,tutorial",
            },
        )

        await client.call_tool(
            "write_note",
            {
                "title": "Python Django Guide",
                "folder": "tutorials",
                "content": "# Python Django Guide\n\nBuilding web apps with Python Django framework.",
                "tags": "python,django,web",
            },
        )

        await client.call_tool(
            "write_note",
            {
                "title": "React JavaScript",
                "folder": "tutorials",
                "content": "# React JavaScript\n\nBuilding frontend applications with React.",
                "tags": "javascript,react,frontend",
            },
        )

        # Test AND operator
        search_result = await client.call_tool(
            "search_notes",
            {
                "query": "Python AND Flask",
            },
        )

        result_text = search_result[0].text
        assert "Python Flask Tutorial" in result_text
        assert "Python Django Guide" not in result_text
        assert "React JavaScript" not in result_text

        # Test OR operator
        search_result = await client.call_tool(
            "search_notes",
            {
                "query": "Flask OR Django",
            },
        )

        result_text = search_result[0].text
        assert "Python Flask Tutorial" in result_text
        assert "Python Django Guide" in result_text
        assert "React JavaScript" not in result_text

        # Test NOT operator
        search_result = await client.call_tool(
            "search_notes",
            {
                "query": "Python NOT Django",
            },
        )

        result_text = search_result[0].text
        assert "Python Flask Tutorial" in result_text
        assert "Python Django Guide" not in result_text


@pytest.mark.asyncio
async def test_search_title_only(mcp_server, app):
    """Test searching in titles only."""

    async with Client(mcp_server) as client:
        # Create test notes
        await client.call_tool(
            "write_note",
            {
                "title": "Database Design",
                "folder": "docs",
                "content": "# Database Design\n\nThis covers SQL and database concepts.",
                "tags": "database,sql",
            },
        )

        await client.call_tool(
            "write_note",
            {
                "title": "Web Development",
                "folder": "docs",
                "content": "# Web Development\n\nDatabase integration in web applications.",
                "tags": "web,development",
            },
        )

        # Search for "database" in titles only
        search_result = await client.call_tool(
            "search_notes",
            {
                "query": "Database",
                "search_type": "title",
            },
        )

        result_text = search_result[0].text
        assert "Database Design" in result_text
        assert "Web Development" not in result_text  # Has "database" in content but not title


@pytest.mark.asyncio
async def test_search_permalink_exact(mcp_server, app):
    """Test exact permalink search."""

    async with Client(mcp_server) as client:
        # Create test notes
        await client.call_tool(
            "write_note",
            {
                "title": "API Documentation",
                "folder": "api",
                "content": "# API Documentation\n\nComplete API reference guide.",
                "tags": "api,docs",
            },
        )

        await client.call_tool(
            "write_note",
            {
                "title": "API Testing",
                "folder": "testing",
                "content": "# API Testing\n\nHow to test REST APIs.",
                "tags": "api,testing",
            },
        )

        # Search for exact permalink
        search_result = await client.call_tool(
            "search_notes",
            {
                "query": "api/api-documentation",
                "search_type": "permalink",
            },
        )

        result_text = search_result[0].text
        assert "API Documentation" in result_text
        assert "API Testing" not in result_text


@pytest.mark.asyncio
async def test_search_permalink_pattern(mcp_server, app):
    """Test permalink pattern search with wildcards."""

    async with Client(mcp_server) as client:
        # Create test notes in different folders
        await client.call_tool(
            "write_note",
            {
                "title": "Meeting Notes January",
                "folder": "meetings",
                "content": "# Meeting Notes January\n\nJanuary team meeting notes.",
                "tags": "meetings,january",
            },
        )

        await client.call_tool(
            "write_note",
            {
                "title": "Meeting Notes February",
                "folder": "meetings",
                "content": "# Meeting Notes February\n\nFebruary team meeting notes.",
                "tags": "meetings,february",
            },
        )

        await client.call_tool(
            "write_note",
            {
                "title": "Project Notes",
                "folder": "projects",
                "content": "# Project Notes\n\nGeneral project documentation.",
                "tags": "projects,notes",
            },
        )

        # Search for all meeting notes using pattern
        search_result = await client.call_tool(
            "search_notes",
            {
                "query": "meetings/*",
                "search_type": "permalink",
            },
        )

        result_text = search_result[0].text
        assert "Meeting Notes January" in result_text
        assert "Meeting Notes February" in result_text
        assert "Project Notes" not in result_text


@pytest.mark.asyncio
async def test_search_entity_type_filter(mcp_server, app):
    """Test filtering search results by entity type."""

    async with Client(mcp_server) as client:
        # Create a note with observations and relations
        content_with_observations = """# Development Process

This describes our development workflow.

## Observations
- [process] We use Git for version control
- [tool] We use VS Code as our editor

## Relations
- uses [[Git]]
- part_of [[Development Workflow]]

Regular content about development practices."""

        await client.call_tool(
            "write_note",
            {
                "title": "Development Process",
                "folder": "processes",
                "content": content_with_observations,
                "tags": "development,process",
            },
        )

        # Search for "development" in entities only
        search_result = await client.call_tool(
            "search_notes",
            {
                "query": "development",
                "entity_types": ["entity"],
            },
        )

        result_text = search_result[0].text
        # Should find the main entity but filter out observations/relations
        assert "Development Process" in result_text


@pytest.mark.asyncio
async def test_search_pagination(mcp_server, app):
    """Test search result pagination."""

    async with Client(mcp_server) as client:
        # Create multiple notes to test pagination
        for i in range(15):
            await client.call_tool(
                "write_note",
                {
                    "title": f"Test Note {i + 1:02d}",
                    "folder": "test",
                    "content": f"# Test Note {i + 1:02d}\n\nThis is test content for pagination testing.",
                    "tags": "test,pagination",
                },
            )

        # Search with pagination (page 1, page_size 5)
        search_result = await client.call_tool(
            "search_notes",
            {
                "query": "test",
                "page": 1,
                "page_size": 5,
            },
        )

        result_text = search_result[0].text
        # Should contain 5 results and pagination info
        assert '"current_page": 1' in result_text
        assert '"page_size": 5' in result_text

        # Search page 2
        search_result = await client.call_tool(
            "search_notes",
            {
                "query": "test",
                "page": 2,
                "page_size": 5,
            },
        )

        result_text = search_result[0].text
        assert '"current_page": 2' in result_text


@pytest.mark.asyncio
async def test_search_no_results(mcp_server, app):
    """Test search with no matching results."""

    async with Client(mcp_server) as client:
        # Create a test note
        await client.call_tool(
            "write_note",
            {
                "title": "Sample Note",
                "folder": "test",
                "content": "# Sample Note\n\nThis is a sample note for testing.",
                "tags": "sample,test",
            },
        )

        # Search for something that doesn't exist
        search_result = await client.call_tool(
            "search_notes",
            {
                "query": "nonexistent",
            },
        )

        result_text = search_result[0].text
        assert '"results": []' in result_text or '"results":[]' in result_text


@pytest.mark.asyncio
async def test_search_complex_boolean_query(mcp_server, app):
    """Test complex boolean queries with grouping."""

    async with Client(mcp_server) as client:
        # Create test notes
        await client.call_tool(
            "write_note",
            {
                "title": "Python Web Development",
                "folder": "tutorials",
                "content": "# Python Web Development\n\nLearn Python for web development using Flask and Django.",
                "tags": "python,web,development",
            },
        )

        await client.call_tool(
            "write_note",
            {
                "title": "Python Data Science",
                "folder": "tutorials",
                "content": "# Python Data Science\n\nData analysis and machine learning with Python.",
                "tags": "python,data,science",
            },
        )

        await client.call_tool(
            "write_note",
            {
                "title": "JavaScript Web Development",
                "folder": "tutorials",
                "content": "# JavaScript Web Development\n\nBuilding web applications with JavaScript and React.",
                "tags": "javascript,web,development",
            },
        )

        # Complex boolean query: (Python OR JavaScript) AND web
        search_result = await client.call_tool(
            "search_notes",
            {
                "query": "(Python OR JavaScript) AND web",
            },
        )

        result_text = search_result[0].text
        assert "Python Web Development" in result_text
        assert "JavaScript Web Development" in result_text
        assert "Python Data Science" not in result_text  # Has Python but not web


@pytest.mark.asyncio
async def test_search_case_insensitive(mcp_server, app):
    """Test that search is case insensitive."""

    async with Client(mcp_server) as client:
        # Create test note
        await client.call_tool(
            "write_note",
            {
                "title": "Machine Learning Guide",
                "folder": "guides",
                "content": "# Machine Learning Guide\n\nIntroduction to MACHINE LEARNING concepts.",
                "tags": "ML,AI",
            },
        )

        # Search with different cases
        search_cases = ["machine", "MACHINE", "Machine", "learning", "LEARNING"]

        for search_term in search_cases:
            search_result = await client.call_tool(
                "search_notes",
                {
                    "query": search_term,
                },
            )

            result_text = search_result[0].text
            assert "Machine Learning Guide" in result_text, f"Failed for search term: {search_term}"
