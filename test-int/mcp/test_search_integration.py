"""
Integration tests for search_notes MCP tool.

Comprehensive tests covering search functionality using the complete
MCP client-server flow with real databases.
"""

import pytest
from fastmcp import Client


@pytest.mark.asyncio
async def test_search_basic_text_search(mcp_server, app, test_project):
    """Test basic text search functionality."""

    async with Client(mcp_server) as client:
        # Create test notes for searching
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Python Programming Guide",
                "folder": "docs",
                "content": "# Python Programming Guide\n\nThis guide covers Python basics and advanced topics.",
                "tags": "python,programming",
            },
        )

        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Flask Web Development",
                "folder": "docs",
                "content": "# Flask Web Development\n\nBuilding web applications with Python Flask framework.",
                "tags": "python,flask,web",
            },
        )

        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
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
                "project": test_project.name,
                "query": "Python",
            },
        )

        assert len(search_result.content) == 1
        assert search_result.content[0].type == "text"

        # Parse the response (it should be a SearchResponse)
        result_text = search_result.content[0].text
        assert "Python Programming Guide" in result_text
        assert "Flask Web Development" in result_text
        assert "JavaScript Basics" not in result_text


@pytest.mark.asyncio
async def test_search_boolean_operators(mcp_server, app, test_project):
    """Test boolean search operators (AND, OR, NOT)."""

    async with Client(mcp_server) as client:
        # Create test notes
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Python Flask Tutorial",
                "folder": "tutorials",
                "content": "# Python Flask Tutorial\n\nLearn Python web development with Flask.",
                "tags": "python,flask,tutorial",
            },
        )

        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Python Django Guide",
                "folder": "tutorials",
                "content": "# Python Django Guide\n\nBuilding web apps with Python Django framework.",
                "tags": "python,django,web",
            },
        )

        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
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
                "project": test_project.name,
                "query": "Python AND Flask",
            },
        )

        result_text = search_result.content[0].text
        assert "Python Flask Tutorial" in result_text
        assert "Python Django Guide" not in result_text
        assert "React JavaScript" not in result_text

        # Test OR operator
        search_result = await client.call_tool(
            "search_notes",
            {
                "project": test_project.name,
                "query": "Flask OR Django",
            },
        )

        result_text = search_result.content[0].text
        assert "Python Flask Tutorial" in result_text
        assert "Python Django Guide" in result_text
        assert "React JavaScript" not in result_text

        # Test NOT operator
        search_result = await client.call_tool(
            "search_notes",
            {
                "project": test_project.name,
                "query": "Python NOT Django",
            },
        )

        result_text = search_result.content[0].text
        assert "Python Flask Tutorial" in result_text
        assert "Python Django Guide" not in result_text


@pytest.mark.asyncio
async def test_search_title_only(mcp_server, app, test_project):
    """Test searching in titles only."""

    async with Client(mcp_server) as client:
        # Create test notes
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Database Design",
                "folder": "docs",
                "content": "# Database Design\n\nThis covers SQL and database concepts.",
                "tags": "database,sql",
            },
        )

        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
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
                "project": test_project.name,
                "query": "Database",
                "search_type": "title",
            },
        )

        result_text = search_result.content[0].text
        assert "Database Design" in result_text
        assert "Web Development" not in result_text  # Has "database" in content but not title


@pytest.mark.asyncio
async def test_search_permalink_exact(mcp_server, app, test_project):
    """Test exact permalink search."""

    async with Client(mcp_server) as client:
        # Create test notes
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "API Documentation",
                "folder": "api",
                "content": "# API Documentation\n\nComplete API reference guide.",
                "tags": "api,docs",
            },
        )

        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
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
                "project": test_project.name,
                "query": "api/api-documentation",
                "search_type": "permalink",
            },
        )

        result_text = search_result.content[0].text
        assert "API Documentation" in result_text
        assert "API Testing" not in result_text


@pytest.mark.asyncio
async def test_search_permalink_pattern(mcp_server, app, test_project):
    """Test permalink pattern search with wildcards."""

    async with Client(mcp_server) as client:
        # Create test notes in different folders
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Meeting Notes January",
                "folder": "meetings",
                "content": "# Meeting Notes January\n\nJanuary team meeting notes.",
                "tags": "meetings,january",
            },
        )

        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Meeting Notes February",
                "folder": "meetings",
                "content": "# Meeting Notes February\n\nFebruary team meeting notes.",
                "tags": "meetings,february",
            },
        )

        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
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
                "project": test_project.name,
                "query": "meetings/*",
                "search_type": "permalink",
            },
        )

        result_text = search_result.content[0].text
        assert "Meeting Notes January" in result_text
        assert "Meeting Notes February" in result_text
        assert "Project Notes" not in result_text


@pytest.mark.asyncio
async def test_search_entity_type_filter(mcp_server, app, test_project):
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
                "project": test_project.name,
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
                "project": test_project.name,
                "query": "development",
                "entity_types": ["entity"],
            },
        )

        result_text = search_result.content[0].text
        # Should find the main entity but filter out observations/relations
        assert "Development Process" in result_text


@pytest.mark.asyncio
async def test_search_pagination(mcp_server, app, test_project):
    """Test search result pagination."""

    async with Client(mcp_server) as client:
        # Create multiple notes to test pagination
        for i in range(15):
            await client.call_tool(
                "write_note",
                {
                    "project": test_project.name,
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
                "project": test_project.name,
                "query": "test",
                "page": 1,
                "page_size": 5,
            },
        )

        result_text = search_result.content[0].text
        # Should contain 5 results and pagination info
        assert '"current_page":1' in result_text
        assert '"page_size":5' in result_text

        # Search page 2
        search_result = await client.call_tool(
            "search_notes",
            {
                "project": test_project.name,
                "query": "test",
                "page": 2,
                "page_size": 5,
            },
        )

        result_text = search_result.content[0].text
        assert '"current_page":2' in result_text


@pytest.mark.asyncio
async def test_search_no_results(mcp_server, app, test_project):
    """Test search with no matching results."""

    async with Client(mcp_server) as client:
        # Create a test note
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
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
                "project": test_project.name,
                "query": "nonexistent",
            },
        )

        result_text = search_result.content[0].text
        assert '"results": []' in result_text or '"results":[]' in result_text


@pytest.mark.asyncio
async def test_search_complex_boolean_query(mcp_server, app, test_project):
    """Test complex boolean queries with grouping."""

    async with Client(mcp_server) as client:
        # Create test notes
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Python Web Development",
                "folder": "tutorials",
                "content": "# Python Web Development\n\nLearn Python for web development using Flask and Django.",
                "tags": "python,web,development",
            },
        )

        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Python Data Science",
                "folder": "tutorials",
                "content": "# Python Data Science\n\nData analysis and machine learning with Python.",
                "tags": "python,data,science",
            },
        )

        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
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
                "project": test_project.name,
                "query": "(Python OR JavaScript) AND web",
            },
        )

        result_text = search_result.content[0].text
        assert "Python Web Development" in result_text
        assert "JavaScript Web Development" in result_text
        assert "Python Data Science" not in result_text  # Has Python but not web


@pytest.mark.asyncio
async def test_search_case_insensitive(mcp_server, app, test_project):
    """Test that search is case insensitive."""

    async with Client(mcp_server) as client:
        # Create test note
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
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
                    "project": test_project.name,
                    "query": search_term,
                },
            )

            result_text = search_result.content[0].text
            assert "Machine Learning Guide" in result_text, f"Failed for search term: {search_term}"


@pytest.mark.asyncio
async def test_search_with_json_string_entity_types(mcp_server, app, test_project):
    """Test search with entity_types as JSON string (for ChatGPT compatibility)."""

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
                "project": test_project.name,
                "title": "Development Process",
                "folder": "processes",
                "content": content_with_observations,
                "tags": "development,process",
            },
        )

        # Search with entity_types as JSON string (simulating ChatGPT behavior)
        search_result = await client.call_tool(
            "search_notes",
            {
                "project": test_project.name,
                "query": "development",
                "entity_types": '["entity"]',  # JSON string format
            },
        )

        result_text = search_result.content[0].text
        # Should find the main entity
        assert "Development Process" in result_text


@pytest.mark.asyncio
async def test_search_with_json_string_types(mcp_server, app, test_project):
    """Test search with types as JSON string (for ChatGPT compatibility)."""

    async with Client(mcp_server) as client:
        # Create test notes with different types
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Python Programming",
                "folder": "docs",
                "content": """# Python Programming
---
type: entity
---

Python programming language guide.""",
                "tags": "python,programming",
            },
        )

        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "JavaScript Basics",
                "folder": "docs",
                "content": """# JavaScript Basics
---
type: note
---

JavaScript programming guide.""",
                "tags": "javascript,programming",
            },
        )

        # Search with types as JSON string
        search_result = await client.call_tool(
            "search_notes",
            {
                "project": test_project.name,
                "query": "programming",
                "types": '["entity"]',  # JSON string format
            },
        )

        result_text = search_result.content[0].text
        assert "Python Programming" in result_text
        # Note: Can't definitively assert "JavaScript Basics" not in result_text
        # without checking the type filtering logic, but the test verifies
        # that JSON string format is accepted


@pytest.mark.asyncio
async def test_search_with_json_string_observation_filter(mcp_server, app, test_project):
    """Test search with entity_types filtering for observations using JSON string."""

    async with Client(mcp_server) as client:
        # Create a note with observations
        content = """# Project Planning

## Observations
- [requirement] Must support authentication
- [decision] Using OAuth2 for auth
- [problem] Legacy systems don't support OAuth

## Content
Planning our project architecture."""

        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Project Planning",
                "folder": "planning",
                "content": content,
                "tags": "planning,project",
            },
        )

        # Search for observations using JSON string format
        search_result = await client.call_tool(
            "search_notes",
            {
                "project": test_project.name,
                "query": "auth",
                "entity_types": '["observation"]',  # JSON string format
            },
        )

        result_text = search_result.content[0].text
        # Should find observations containing "auth"
        assert "authentication" in result_text or "OAuth" in result_text


@pytest.mark.asyncio
async def test_search_with_multiple_entity_types_json_string(mcp_server, app, test_project):
    """Test search with multiple entity_types in JSON string format."""

    async with Client(mcp_server) as client:
        # Create a note with both entities and observations
        content = """# Development Tools

## Observations
- [tool] VS Code is our primary editor
- [process] We use Git for version control

Regular content about development."""

        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Development Tools",
                "folder": "tools",
                "content": content,
                "tags": "development,tools",
            },
        )

        # Search with multiple entity_types as JSON string
        search_result = await client.call_tool(
            "search_notes",
            {
                "project": test_project.name,
                "query": "development",
                "entity_types": '["entity", "observation"]',  # JSON string with multiple values
            },
        )

        result_text = search_result.content[0].text
        assert "Development Tools" in result_text
