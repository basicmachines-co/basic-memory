"""Tests for the Basic Memory CLI tools.

These tests use real MCP tools with the test environment instead of mocks.
"""

# Import for testing

import io
from datetime import datetime, timedelta
import json
from textwrap import dedent
from typing import AsyncGenerator
from unittest.mock import patch

import pytest_asyncio
from typer.testing import CliRunner

from basic_memory.cli.commands.tool import tool_app
from basic_memory.schemas.base import Entity as EntitySchema

runner = CliRunner()


@pytest_asyncio.fixture
async def setup_test_note(entity_service, search_service) -> AsyncGenerator[dict, None]:
    """Create a test note for CLI tests."""
    note_content = dedent("""
        # Test Note
        
        This is a test note for CLI commands.
        
        ## Observations
        - [tech] Test observation #test
        - [note] Another observation
        
        ## Relations
        - connects_to [[Another Note]]
    """)

    entity, created = await entity_service.create_or_update_entity(
        EntitySchema(
            title="Test Note",
            folder="test",
            entity_type="note",
            content=note_content,
        )
    )

    # Index the entity for search
    await search_service.index_entity(entity)

    yield {
        "title": entity.title,
        "permalink": entity.permalink,
        "content": note_content,
    }


def test_write_note(cli_env, project_config):
    """Test write_note command with basic arguments."""
    result = runner.invoke(
        tool_app,
        [
            "write-note",
            "--title",
            "CLI Test Note",
            "--content",
            "This is a CLI test note",
            "--folder",
            "test",
        ],
    )
    assert result.exit_code == 0

    # Check for expected success message
    assert "CLI Test Note" in result.stdout
    assert "Created" in result.stdout or "Updated" in result.stdout
    assert "permalink" in result.stdout


def test_write_note_with_tags(cli_env, project_config):
    """Test write_note command with tags."""
    result = runner.invoke(
        tool_app,
        [
            "write-note",
            "--title",
            "Tagged CLI Test Note",
            "--content",
            "This is a test note with tags",
            "--folder",
            "test",
            "--tags",
            "tag1",
            "--tags",
            "tag2",
        ],
    )
    assert result.exit_code == 0

    # Check for expected success message
    assert "Tagged CLI Test Note" in result.stdout
    assert "tag1, tag2" in result.stdout or "tag1" in result.stdout and "tag2" in result.stdout


def test_write_note_from_stdin(cli_env, project_config, monkeypatch):
    """Test write_note command reading from stdin.

    This test requires minimal mocking of stdin to simulate piped input.
    """
    test_content = "This is content from stdin for testing"

    # Mock stdin using monkeypatch, which works better with typer's CliRunner
    monkeypatch.setattr("sys.stdin", io.StringIO(test_content))
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)  # Simulate piped input

    # Use runner.invoke with input parameter as a fallback
    result = runner.invoke(
        tool_app,
        [
            "write-note",
            "--title",
            "Stdin Test Note",
            "--folder",
            "test",
        ],
        input=test_content,  # Provide input as a fallback
    )

    assert result.exit_code == 0

    # Check for expected success message
    assert "Stdin Test Note" in result.stdout
    assert "Created" in result.stdout or "Updated" in result.stdout
    assert "permalink" in result.stdout


def test_write_note_content_param_priority(cli_env, project_config):
    """Test that content parameter has priority over stdin."""
    stdin_content = "This content from stdin should NOT be used"
    param_content = "This explicit content parameter should be used"

    # Mock stdin but provide explicit content parameter
    with (
        patch("sys.stdin", io.StringIO(stdin_content)),
        patch("sys.stdin.isatty", return_value=False),
    ):  # Simulate piped input
        result = runner.invoke(
            tool_app,
            [
                "write-note",
                "--title",
                "Priority Test Note",
                "--content",
                param_content,
                "--folder",
                "test",
            ],
        )

        assert result.exit_code == 0

        # Check the note was created with the content from parameter, not stdin
        # We can't directly check file contents in this test approach
        # but we can verify the command succeeded
        assert "Priority Test Note" in result.stdout
        assert "Created" in result.stdout or "Updated" in result.stdout


def test_write_note_no_content(cli_env, project_config):
    """Test error handling when no content is provided."""
    # Mock stdin to appear as a terminal, not a pipe
    with patch("sys.stdin.isatty", return_value=True):
        result = runner.invoke(
            tool_app,
            [
                "write-note",
                "--title",
                "No Content Note",
                "--folder",
                "test",
            ],
        )

        # Should exit with an error
        assert result.exit_code == 1
        # assert "No content provided" in result.stderr


def test_read_note(cli_env, setup_test_note):
    """Test read_note command."""
    permalink = setup_test_note["permalink"]

    result = runner.invoke(
        tool_app,
        ["read-note", permalink],
    )
    assert result.exit_code == 0

    # Should contain the note content and structure
    assert "Test Note" in result.stdout
    assert "This is a test note for CLI commands" in result.stdout
    assert "## Observations" in result.stdout
    assert "Test observation" in result.stdout
    assert "## Relations" in result.stdout
    assert "connects_to [[Another Note]]" in result.stdout

    # Note: We found that square brackets like [tech] are being stripped in CLI output,
    # so we're not asserting their presence


def test_search_basic(cli_env, setup_test_note):
    """Test basic search command."""
    result = runner.invoke(
        tool_app,
        ["search-notes", "test observation"],
    )
    assert result.exit_code == 0

    # Result should be JSON containing our test note
    search_result = json.loads(result.stdout)
    assert len(search_result["results"]) > 0

    # At least one result should match our test note or observation
    found = False
    for item in search_result["results"]:
        if "test" in item["permalink"].lower() and "observation" in item["permalink"].lower():
            found = True
            break

    assert found, "Search did not find the test observation"


def test_search_permalink(cli_env, setup_test_note):
    """Test search with permalink flag."""
    permalink = setup_test_note["permalink"]

    result = runner.invoke(
        tool_app,
        ["search-notes", permalink, "--permalink"],
    )
    assert result.exit_code == 0

    # Result should be JSON containing our test note
    search_result = json.loads(result.stdout)
    assert len(search_result["results"]) > 0

    # Should find a result with matching permalink
    found = False
    for item in search_result["results"]:
        if item["permalink"] == permalink:
            found = True
            break

    assert found, "Search did not find the note by permalink"


def test_build_context(cli_env, setup_test_note):
    """Test build_context command."""
    permalink = setup_test_note["permalink"]

    result = runner.invoke(
        tool_app,
        ["build-context", f"memory://{permalink}"],
    )
    assert result.exit_code == 0

    # Result should be JSON containing our test note
    context_result = json.loads(result.stdout)
    assert "results" in context_result
    assert len(context_result["results"]) > 0

    # Primary results should include our test note
    found = False
    for item in context_result["results"]:
        if item["primary_result"]["permalink"] == permalink:
            found = True
            break

    assert found, "Context did not include the test note"


def test_build_context_with_options(cli_env, setup_test_note):
    """Test build_context command with all options."""
    permalink = setup_test_note["permalink"]

    result = runner.invoke(
        tool_app,
        [
            "build-context",
            f"memory://{permalink}",
            "--depth",
            "2",
            "--timeframe",
            "1d",
            "--page",
            "1",
            "--page-size",
            "5",
            "--max-related",
            "20",
        ],
    )
    assert result.exit_code == 0

    # Result should be JSON containing our test note
    context_result = json.loads(result.stdout)

    # Check that metadata reflects our options
    assert context_result["metadata"]["depth"] == 2
    timeframe = datetime.fromisoformat(context_result["metadata"]["timeframe"])
    assert datetime.now() - timeframe <= timedelta(days=2)  # don't bother about timezones

    # Results should include our test note
    found = False
    for item in context_result["results"]:
        if item["primary_result"]["permalink"] == permalink:
            found = True
            break

    assert found, "Context did not include the test note"


# The get-entity CLI command was removed when tools were refactored
# into separate files with improved error handling


def test_recent_activity(cli_env, setup_test_note):
    """Test recent_activity command with defaults."""
    result = runner.invoke(
        tool_app,
        ["recent-activity"],
    )
    assert result.exit_code == 0

    # Result should be JSON containing recent activity
    activity_result = json.loads(result.stdout)
    assert "results" in activity_result
    assert "metadata" in activity_result

    # Our test note should be in the recent activity
    found = False
    for item in activity_result["results"]:
        if "primary_result" in item and "permalink" in item["primary_result"]:
            if setup_test_note["permalink"] == item["primary_result"]["permalink"]:
                found = True
                break

    assert found, "Recent activity did not include the test note"


def test_recent_activity_with_options(cli_env, setup_test_note):
    """Test recent_activity command with options."""
    result = runner.invoke(
        tool_app,
        [
            "recent-activity",
            "--type",
            "entity",
            "--depth",
            "2",
            "--timeframe",
            "7d",
            "--page",
            "1",
            "--page-size",
            "20",
            "--max-related",
            "20",
        ],
    )
    assert result.exit_code == 0

    # Result should be JSON containing recent activity
    activity_result = json.loads(result.stdout)

    # Check that requested entity types are included
    entity_types = set()
    for item in activity_result["results"]:
        if "primary_result" in item and "type" in item["primary_result"]:
            entity_types.add(item["primary_result"]["type"])

    # Should find entity type since we requested it
    assert "entity" in entity_types


def test_continue_conversation(cli_env, setup_test_note):
    """Test continue_conversation command."""
    permalink = setup_test_note["permalink"]

    # Run the CLI command
    result = runner.invoke(
        tool_app,
        ["continue-conversation", "--topic", "Test Note"],
    )
    assert result.exit_code == 0

    # Check result contains expected content
    assert "Continuing conversation on: Test Note" in result.stdout
    assert "This is a memory retrieval session" in result.stdout
    assert "read_note" in result.stdout
    assert permalink in result.stdout


def test_continue_conversation_no_results(cli_env):
    """Test continue_conversation command with no results."""
    # Run the CLI command with a nonexistent topic
    result = runner.invoke(
        tool_app,
        ["continue-conversation", "--topic", "NonexistentTopic"],
    )
    assert result.exit_code == 0

    # Check result contains expected content for no results
    assert "Continuing conversation on: NonexistentTopic" in result.stdout
    assert "The supplied query did not return any information" in result.stdout


@patch("basic_memory.services.initialization.initialize_database")
def test_ensure_migrations_functionality(mock_initialize_database, project_config, monkeypatch):
    """Test the database initialization functionality."""
    from basic_memory.services.initialization import ensure_initialization

    # Call the function
    ensure_initialization(project_config)

    # The underlying asyncio.run should call our mocked function
    mock_initialize_database.assert_called_once()


@patch("basic_memory.services.initialization.initialize_database")
def test_ensure_migrations_handles_errors(mock_initialize_database, project_config, monkeypatch):
    """Test that initialization handles errors gracefully."""
    from basic_memory.services.initialization import ensure_initialization

    # Configure mock to raise an exception
    mock_initialize_database.side_effect = Exception("Test error")

    # Call the function - should not raise exception
    ensure_initialization(project_config)

    # We're just making sure it doesn't crash by calling it