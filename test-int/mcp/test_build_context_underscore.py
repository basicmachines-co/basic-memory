"""Integration test for build_context with underscore in memory:// URLs."""

import pytest
from fastmcp import Client


@pytest.mark.asyncio
async def test_build_context_underscore_normalization(mcp_server, app, test_project):
    """Test that build_context normalizes underscores in relation types."""

    async with Client(mcp_server) as client:
        # Create parent note
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Parent Entity",
                "folder": "testing",
                "content": "# Parent Entity\n\nMain entity for testing underscore relations.",
                "tags": "test,parent",
            },
        )

        # Create child notes with different relation formats
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Child with Underscore",
                "folder": "testing",
                "content": """# Child with Underscore

- part_of [[Parent Entity]]
- related_to [[Parent Entity]]
                """,
                "tags": "test,child",
            },
        )

        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Child with Hyphen",
                "folder": "testing",
                "content": """# Child with Hyphen

- part-of [[Parent Entity]]
- related-to [[Parent Entity]]
                """,
                "tags": "test,child",
            },
        )

        # Test 1: Search with underscore format should return results
        result_underscore = await client.call_tool(
            "build_context",
            {
                "project": test_project.name,
                "url": "memory://testing/parent-entity/part_of/*",  # Using underscore
            },
        )

        # Parse response
        assert len(result_underscore.content) == 1
        response_text = result_underscore.content[0].text  # pyright: ignore
        assert '"results"' in response_text

        # Both children should be found since they both have part_of/part-of relations
        # The system should normalize the underscore to hyphen internally
        assert "Child with Underscore" in response_text or "child-with-underscore" in response_text
        assert "Child with Hyphen" in response_text or "child-with-hyphen" in response_text

        # Test 2: Search with hyphen format should also return results
        result_hyphen = await client.call_tool(
            "build_context",
            {
                "project": test_project.name,
                "url": "memory://testing/parent-entity/part-of/*",  # Using hyphen
            },
        )

        response_text_hyphen = result_hyphen.content[0].text  # pyright: ignore
        assert '"results"' in response_text_hyphen
        assert "Child with Underscore" in response_text_hyphen or "child-with-underscore" in response_text_hyphen
        assert "Child with Hyphen" in response_text_hyphen or "child-with-hyphen" in response_text_hyphen

        # Test 3: Test with related_to/related-to as well
        result_related = await client.call_tool(
            "build_context",
            {
                "project": test_project.name,
                "url": "memory://testing/parent-entity/related_to/*",  # Using underscore
            },
        )

        response_text_related = result_related.content[0].text  # pyright: ignore
        assert '"results"' in response_text_related

        # Test 4: Test exact path (non-wildcard) with underscore
        result_exact = await client.call_tool(
            "build_context",
            {
                "project": test_project.name,
                "url": "memory://testing/parent-entity/part_of/child-with-underscore",
            },
        )

        response_text_exact = result_exact.content[0].text  # pyright: ignore
        assert '"results"' in response_text_exact


@pytest.mark.asyncio
async def test_build_context_complex_underscore_paths(mcp_server, app, test_project):
    """Test build_context with complex paths containing underscores."""

    async with Client(mcp_server) as client:
        # Create notes with underscores in titles and relations
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "workflow_manager_agent",
                "folder": "specs",
                "content": """# Workflow Manager Agent

Specification for the workflow manager agent.
                """,
                "tags": "spec,workflow",
            },
        )

        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "task_parser",
                "folder": "components",
                "content": """# Task Parser

- part_of [[workflow_manager_agent]]
- implements_for [[workflow_manager_agent]]
                """,
                "tags": "component,parser",
            },
        )

        # Test with underscores in all parts of the path
        test_cases = [
            "memory://specs/workflow_manager_agent/part_of/*",
            "memory://specs/workflow-manager-agent/part_of/*",
            "memory://specs/workflow_manager_agent/part-of/*",
            "memory://specs/workflow-manager-agent/part-of/*",
        ]

        for url in test_cases:
            result = await client.call_tool(
                "build_context", {"project": test_project.name, "url": url}
            )

            # All variations should work and find the related content
            assert len(result.content) == 1
            response = result.content[0].text  # pyright: ignore
            assert '"results"' in response
            # The task_parser should be found in all cases
            assert "task" in response.lower() and "parser" in response.lower(), f"Failed for URL: {url}"