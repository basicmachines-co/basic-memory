"""Tests for the Basic Memory CLI tools."""

from datetime import datetime, timezone
import pytest
from sqlalchemy.testing import skip_test
from typer.testing import CliRunner
from unittest.mock import patch, AsyncMock

from basic_memory.cli.commands.tools import tool_app
from basic_memory.schemas.response import EntityResponse
from basic_memory.schemas.search import SearchResponse
from basic_memory.schemas.memory import GraphContext

runner = CliRunner()


@pytest.fixture
def mock_write_note():
    with patch("basic_memory.cli.commands.tools.mcp_write_note", new_callable=AsyncMock) as mock:
        mock.return_value = "Created test/note.md (abc123)\npermalink: test/note"
        yield mock


@pytest.fixture
def mock_read_note():
    with patch("basic_memory.cli.commands.tools.mcp_read_note", new_callable=AsyncMock) as mock:
        mock.return_value = "--- memory://test/note 2025-01 abc123\nTest content"
        yield mock


@pytest.fixture
def mock_search():
    with patch("basic_memory.cli.commands.tools.mcp_search", new_callable=AsyncMock) as mock:
        mock.return_value = SearchResponse(results=[], current_page=1, page_size=10)
        yield mock


@pytest.fixture
def mock_build_context():
    with patch("basic_memory.cli.commands.tools.mcp_build_context", new_callable=AsyncMock) as mock:
        now = datetime.now(timezone.utc)
        mock.return_value = GraphContext(
            primary_results=[],
            related_results=[],
            metadata={
                "uri": "test/*",
                "depth": 1,
                "timeframe": "7d",
                "generated_at": now,
                "total_results": 0,
                "total_relations": 0,
            },
        )
        yield mock


@pytest.fixture
def mock_recent_activity():
    with patch(
        "basic_memory.cli.commands.tools.mcp_recent_activity", new_callable=AsyncMock
    ) as mock:
        now = datetime.now(timezone.utc)
        mock.return_value = GraphContext(
            primary_results=[],
            related_results=[],
            metadata={
                "uri": None,
                "types": ["entity", "observation"],
                "depth": 1,
                "timeframe": "7d",
                "generated_at": now,
                "total_results": 0,
                "total_relations": 0,
            },
        )
        yield mock


@pytest.fixture
def mock_get_entity():
    with patch("basic_memory.cli.commands.tools.mcp_get_entity", new_callable=AsyncMock) as mock:
        now = datetime.now(timezone.utc)
        mock.return_value = EntityResponse(
            permalink="test/entity",
            title="Test Entity",
            file_path="test/entity.md",
            entity_type="note",
            content_type="text/markdown",
            observations=[],
            relations=[],
            created_at=now,
            updated_at=now,
        )
        yield mock


def test_write_note(mock_write_note):
    """Test write_note command with basic arguments."""
    result = runner.invoke(
        tool_app,
        [
            "write-note",
            "--title",
            "Test Note",
            "--content",
            "Test content",
            "--folder",
            "test",
        ],
    )
    assert result.exit_code == 0
    mock_write_note.assert_awaited_once_with("Test Note", "Test content", "test", None)


def test_write_note_with_tags(mock_write_note):
    """Test write_note command with tags."""
    result = runner.invoke(
        tool_app,
        [
            "write-note",
            "--title",
            "Test Note",
            "--content",
            "Test content",
            "--folder",
            "test",
            "--tags",
            "tag1",
            "--tags",
            "tag2",
        ],
    )
    assert result.exit_code == 0
    mock_write_note.assert_awaited_once_with("Test Note", "Test content", "test", ["tag1", "tag2"])


def test_read_note(mock_read_note):
    """Test read_note command."""
    result = runner.invoke(
        tool_app,
        ["read-note", "test/note"],
    )
    assert result.exit_code == 0
    mock_read_note.assert_awaited_once_with("test/note", 1, 10)


def test_read_note_with_pagination(mock_read_note):
    """Test read_note command with pagination."""
    result = runner.invoke(
        tool_app,
        ["read-note", "test/note", "--page", "2", "--page-size", "5"],
    )
    assert result.exit_code == 0
    mock_read_note.assert_awaited_once_with("test/note", 2, 5)


def test_search_basic(mock_search):
    """Test basic search command."""
    result = runner.invoke(
        tool_app,
        ["search", "test query"],
    )
    assert result.exit_code == 0
    mock_search.assert_awaited_once()
    args = mock_search.await_args[1]
    assert args["query"].text == "test query"


def test_search_permalink(mock_search):
    """Test search with permalink flag."""
    result = runner.invoke(
        tool_app,
        ["search", "test/*", "--permalink"],
    )
    assert result.exit_code == 0
    mock_search.assert_awaited_once()
    args = mock_search.await_args[1]
    assert args["query"].permalink_match == "test/*"


def test_search_title(mock_search):
    """Test search with title flag."""
    result = runner.invoke(
        tool_app,
        ["search", "test", "--title"],
    )
    assert result.exit_code == 0
    mock_search.assert_awaited_once()
    args = mock_search.await_args[1]
    assert args["query"].title == "test"


def test_search_with_pagination(mock_search):
    """Test search with pagination."""
    result = runner.invoke(
        tool_app,
        ["search", "test", "--page", "2", "--page-size", "5"],
    )
    assert result.exit_code == 0
    mock_search.assert_awaited_once()
    args = mock_search.await_args[1]
    assert args["page"] == 2
    assert args["page_size"] == 5


def test_build_context(mock_build_context):
    """Test build_context command."""
    result = runner.invoke(
        tool_app,
        ["build-context", "memory://test/*"],
    )
    assert result.exit_code == 0
    mock_build_context.assert_awaited_once_with(
        url="memory://test/*", depth=1, timeframe="7d", page=1, page_size=10, max_related=10
    )


def test_build_context_with_options(mock_build_context):
    """Test build_context command with all options."""
    result = runner.invoke(
        tool_app,
        [
            "build-context",
            "memory://test/*",
            "--depth",
            "2",
            "--timeframe",
            "1d",
            "--page",
            "2",
            "--page-size",
            "5",
            "--max-related",
            "20",
        ],
    )
    assert result.exit_code == 0
    mock_build_context.assert_awaited_once_with(
        url="memory://test/*", depth=2, timeframe="1d", page=2, page_size=5, max_related=20
    )


def test_get_entity(mock_get_entity):
    """Test get_entity command."""
    result = runner.invoke(
        tool_app,
        ["get-entity", "test/entity"],
    )
    assert result.exit_code == 0
    mock_get_entity.assert_awaited_once_with(identifier="test/entity")



def test_recent_activity(mock_recent_activity):
    """Test recent_activity command with defaults."""
    result = runner.invoke(
        tool_app,
        ["recent-activity"],
    )
    assert result.exit_code == 0
    mock_recent_activity.assert_awaited_once_with(
        type=["entity", "observation", "relation"],
        depth=1,
        timeframe="7d",
        page=1,
        page_size=10,
        max_related=10,
    )


def test_recent_activity_with_options(mock_recent_activity):
    """Test recent_activity command with options."""
    result = runner.invoke(
        tool_app,
        [
            "recent-activity",
            "--type",
            "entity",
            "--type",
            "observation",
            "--depth",
            "2",
            "--timeframe",
            "1d",
            "--page",
            "2",
            "--page-size",
            "5",
            "--max-related",
            "20",
        ],
    )
    assert result.exit_code == 0
    mock_recent_activity.assert_awaited_once_with(
        type=["entity", "observation"], depth=2, timeframe="1d", page=2, page_size=5, max_related=20
    )
