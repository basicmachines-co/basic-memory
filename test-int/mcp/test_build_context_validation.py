"""Integration tests for build_context memory URL validation."""

import pytest
from fastmcp import Client


@pytest.mark.asyncio
async def test_build_context_valid_urls(mcp_server, app, test_project):
    """Test that build_context works with valid memory URLs."""

    async with Client(mcp_server) as client:
        # Create a test note to ensure we have something to find
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "URL Validation Test",
                "folder": "testing",
                "content": "# URL Validation Test\n\nThis note tests URL validation.",
                "tags": "test,validation",
            },
        )

        # Test various valid URL formats
        valid_urls = [
            "memory://testing/url-validation-test",  # Full memory URL
            "testing/url-validation-test",  # Relative path
            "testing/*",  # Pattern matching
        ]

        for url in valid_urls:
            result = await client.call_tool(
                "build_context", {"project": test_project.name, "url": url}
            )

            # Should return a valid GraphContext response
            assert len(result.content) == 1
            response = result.content[0].text  # pyright: ignore [reportAttributeAccessIssue]
            assert '"results"' in response  # Should contain results structure
            assert '"metadata"' in response  # Should contain metadata


@pytest.mark.asyncio
async def test_build_context_invalid_urls_fail_validation(mcp_server, app, test_project):
    """Test that build_context properly validates and rejects invalid memory URLs."""

    async with Client(mcp_server) as client:
        # Test cases: (invalid_url, expected_error_fragment)
        invalid_test_cases = [
            ("memory//test", "double slashes"),
            ("invalid://test", "protocol scheme"),
            ("notes<brackets>", "invalid characters"),
            ('notes"quotes"', "invalid characters"),
        ]

        for invalid_url, expected_error in invalid_test_cases:
            with pytest.raises(Exception) as exc_info:
                await client.call_tool(
                    "build_context", {"project": test_project.name, "url": invalid_url}
                )

            error_message = str(exc_info.value).lower()
            assert expected_error in error_message, (
                f"URL '{invalid_url}' should fail with '{expected_error}' error"
            )


@pytest.mark.asyncio
async def test_build_context_empty_urls_fail_validation(mcp_server, app, test_project):
    """Test that empty or whitespace-only URLs fail validation."""

    async with Client(mcp_server) as client:
        # These should fail validation
        empty_urls = [
            "",  # Empty string
            "   ",  # Whitespace only
        ]

        for empty_url in empty_urls:
            with pytest.raises(Exception) as exc_info:
                await client.call_tool(
                    "build_context", {"project": test_project.name, "url": empty_url}
                )

            error_message = str(exc_info.value)
            # Should fail with validation error
            assert (
                "cannot be empty" in error_message
                or "empty or whitespace" in error_message
                or "value_error" in error_message
                or "should be non-empty" in error_message
            )


@pytest.mark.asyncio
async def test_build_context_nonexistent_urls_return_empty_results(mcp_server, app, test_project):
    """Test that valid but nonexistent URLs return empty results (not errors)."""

    async with Client(mcp_server) as client:
        # These are valid URL formats but don't exist in the system
        nonexistent_valid_urls = [
            "memory://nonexistent/note",
            "nonexistent/note",
            "missing/*",
        ]

        for url in nonexistent_valid_urls:
            result = await client.call_tool(
                "build_context", {"project": test_project.name, "url": url}
            )

            # Should return valid response with empty results
            assert len(result.content) == 1
            response = result.content[0].text  # pyright: ignore [reportAttributeAccessIssue]
            assert '"results":[]' in response  # Empty results
            assert '"total_results":0' in response  # Zero count
            assert '"metadata"' in response  # But should have metadata


@pytest.mark.asyncio
async def test_build_context_error_messages_are_helpful(mcp_server, app, test_project):
    """Test that validation error messages provide helpful guidance."""

    async with Client(mcp_server) as client:
        # Test double slash error message
        with pytest.raises(Exception) as exc_info:
            await client.call_tool(
                "build_context", {"project": test_project.name, "url": "memory//bad"}
            )

        error_msg = str(exc_info.value).lower()
        # Should contain validation error info
        assert (
            "double slashes" in error_msg
            or "value_error" in error_msg
            or "validation error" in error_msg
        )

        # Test protocol scheme error message
        with pytest.raises(Exception) as exc_info:
            await client.call_tool(
                "build_context", {"project": test_project.name, "url": "http://example.com"}
            )

        error_msg = str(exc_info.value).lower()
        assert (
            "protocol scheme" in error_msg
            or "protocol" in error_msg
            or "value_error" in error_msg
            or "validation error" in error_msg
        )


@pytest.mark.asyncio
async def test_build_context_pattern_matching_works(mcp_server, app, test_project):
    """Test that valid pattern matching URLs work correctly."""

    async with Client(mcp_server) as client:
        # Create multiple test notes
        test_notes = [
            ("Pattern Test One", "patterns", "# Pattern Test One\n\nFirst pattern test."),
            ("Pattern Test Two", "patterns", "# Pattern Test Two\n\nSecond pattern test."),
            ("Other Note", "other", "# Other Note\n\nNot a pattern match."),
        ]

        for title, folder, content in test_notes:
            await client.call_tool(
                "write_note",
                {
                    "project": test_project.name,
                    "title": title,
                    "folder": folder,
                    "content": content,
                },
            )

        # Test pattern matching
        result = await client.call_tool(
            "build_context", {"project": test_project.name, "url": "patterns/*"}
        )

        assert len(result.content) == 1
        response = result.content[0].text  # pyright: ignore [reportAttributeAccessIssue]

        # Should find the pattern matches but not the other note
        assert '"total_results":2' in response or '"primary_count":2' in response
        assert "Pattern Test" in response
        assert "Other Note" not in response
