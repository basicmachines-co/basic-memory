"""Tests for discussion context MCP tool."""

from datetime import datetime, timedelta, timezone

import pytest

from mcp.server.fastmcp.exceptions import ToolError

from basic_memory.mcp.tools import recent_activity
from basic_memory.schemas.search import SearchItemType
from basic_memory.schemas.memory import (
    ActivityStats,
    ProjectActivity,
    GraphContext,
    MemoryMetadata,
    ContextResult,
    EntitySummary,
    ObservationSummary,
)

# Test data for different timeframe formats
valid_timeframes = [
    "7d",  # Standard format
    "yesterday",  # Natural language
    "0d",  # Zero duration
]

invalid_timeframes = [
    "invalid",  # Nonsense string
    # NOTE: "tomorrow" now returns 1 day ago due to timezone safety - no longer invalid
]


@pytest.mark.asyncio
async def test_recent_activity_timeframe_formats(client, test_project, test_graph):
    """Test that recent_activity accepts various timeframe formats."""
    # Test each valid timeframe with project-specific mode
    for timeframe in valid_timeframes:
        try:
            result = await recent_activity.fn(
                project=test_project.name,
                type=["entity"],
                timeframe=timeframe,
            )
            assert result is not None
            assert isinstance(result, str)
            assert "Recent Activity:" in result
            assert timeframe in result
        except Exception as e:
            pytest.fail(f"Failed with valid timeframe '{timeframe}': {str(e)}")

    # Test invalid timeframes should raise ValidationError
    for timeframe in invalid_timeframes:
        with pytest.raises(ToolError):
            await recent_activity.fn(project=test_project.name, timeframe=timeframe)


@pytest.mark.asyncio
async def test_recent_activity_type_filters(client, test_project, test_graph):
    """Test that recent_activity correctly filters by types."""

    # Test single string type
    result = await recent_activity.fn(project=test_project.name, type=SearchItemType.ENTITY)
    assert result is not None
    assert isinstance(result, str)
    assert "Recent Activity:" in result
    assert "Recent Notes & Documents" in result

    # Test single string type
    result = await recent_activity.fn(project=test_project.name, type="entity")
    assert result is not None
    assert isinstance(result, str)
    assert "Recent Activity:" in result
    assert "Recent Notes & Documents" in result

    # Test single type
    result = await recent_activity.fn(project=test_project.name, type=["entity"])
    assert result is not None
    assert isinstance(result, str)
    assert "Recent Activity:" in result
    assert "Recent Notes & Documents" in result

    # Test multiple types
    result = await recent_activity.fn(project=test_project.name, type=["entity", "observation"])
    assert result is not None
    assert isinstance(result, str)
    assert "Recent Activity:" in result
    # Should contain sections for both types
    assert "Recent Notes & Documents" in result or "Recent Observations" in result

    # Test multiple types
    result = await recent_activity.fn(
        project=test_project.name, type=[SearchItemType.ENTITY, SearchItemType.OBSERVATION]
    )
    assert result is not None
    assert isinstance(result, str)
    assert "Recent Activity:" in result
    # Should contain sections for both types
    assert "Recent Notes & Documents" in result or "Recent Observations" in result

    # Test all types
    result = await recent_activity.fn(
        project=test_project.name, type=["entity", "observation", "relation"]
    )
    assert result is not None
    assert isinstance(result, str)
    assert "Recent Activity:" in result
    assert "Activity Summary:" in result


@pytest.mark.asyncio
async def test_recent_activity_type_invalid(client, test_project, test_graph):
    """Test that recent_activity correctly filters by types."""

    # Test single invalid string type
    with pytest.raises(ValueError) as e:
        await recent_activity.fn(project=test_project.name, type="note")
    assert (
        str(e.value) == "Invalid type: note. Valid types are: ['entity', 'observation', 'relation']"
    )

    # Test invalid string array type
    with pytest.raises(ValueError) as e:
        await recent_activity.fn(project=test_project.name, type=["note"])
    assert (
        str(e.value) == "Invalid type: note. Valid types are: ['entity', 'observation', 'relation']"
    )


@pytest.mark.asyncio
async def test_recent_activity_discovery_mode(client, test_project, test_graph):
    """Test that recent_activity discovery mode works without project parameter."""
    # Test discovery mode (no project parameter)
    result = await recent_activity.fn()
    assert result is not None
    assert isinstance(result, str)

    # Check that we get a formatted summary
    assert "Recent Activity Summary" in result
    assert "Most Active Project:" in result or "Other Active Projects:" in result
    assert "Summary:" in result
    assert "active projects" in result

    # Should contain project discovery guidance
    assert "Suggested project:" in result or "Multiple active projects" in result
    assert "Session reminder:" in result


@pytest.mark.asyncio
async def test_recent_activity_discovery_mode_no_activity(client, test_project):
    """If there is no activity in any project, discovery mode should say so."""
    result = await recent_activity.fn()
    assert "Recent Activity Summary" in result
    assert "No recent activity found in any project." in result


@pytest.mark.asyncio
async def test_recent_activity_discovery_mode_multiple_active_projects(
    app, client, test_project, tmp_path_factory
):
    """Discovery mode should use the multi-project guidance when multiple projects have activity."""
    from basic_memory.mcp.tools import create_memory_project, write_note

    second_root = tmp_path_factory.mktemp("second-project-home")

    result = await create_memory_project.fn(
        project_name="second-project",
        project_path=str(second_root),
        set_default=False,
    )
    assert result.startswith("✓")

    await write_note.fn(project=test_project.name, title="One", folder="notes", content="one")
    await write_note.fn(project="second-project", title="Two", folder="notes", content="two")

    out = await recent_activity.fn()
    assert "Recent Activity Summary" in out
    assert "or would you prefer a different project" in out


def test_recent_activity_format_relative_time_and_truncate_helpers():
    """Unit-test helper formatting to keep MCP output stable."""
    import importlib

    recent_activity_module = importlib.import_module("basic_memory.mcp.tools.recent_activity")

    # _format_relative_time: naive datetime should be treated as UTC.
    naive_dt = datetime.now() - timedelta(days=1)
    assert recent_activity_module._format_relative_time(naive_dt) in {"yesterday", "recently"}

    # ISO string parsing path
    iso_dt = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    assert "hour" in recent_activity_module._format_relative_time(iso_dt)

    now = datetime.now(timezone.utc)
    assert "year" in recent_activity_module._format_relative_time(now - timedelta(days=800))
    assert "month" in recent_activity_module._format_relative_time(now - timedelta(days=40))
    assert "week" in recent_activity_module._format_relative_time(now - timedelta(days=14))
    assert "days ago" in recent_activity_module._format_relative_time(now - timedelta(days=3))
    assert "minute" in recent_activity_module._format_relative_time(now - timedelta(minutes=5))
    assert recent_activity_module._format_relative_time(now) in {"just now", "recently"}

    # Exception fallback
    assert recent_activity_module._format_relative_time(object()) == "recently"

    # _truncate_at_word: both branches
    assert recent_activity_module._truncate_at_word("short", 80) == "short"
    assert recent_activity_module._truncate_at_word("word " * 40, 80).endswith("...")
    assert recent_activity_module._truncate_at_word("x" * 200, 80).endswith("...")


@pytest.mark.asyncio
async def test_recent_activity_get_project_activity_timezone_normalization(monkeypatch):
    """_get_project_activity should handle naive datetimes and extract active folders."""
    import importlib

    recent_activity_module = importlib.import_module("basic_memory.mcp.tools.recent_activity")

    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    async def fake_call_get(client, url, params=None):
        assert "/memory/recent" in str(url)
        t1 = datetime.now() - timedelta(minutes=2)
        t2 = datetime.now() - timedelta(minutes=1)
        return FakeResponse(
            {
                "results": [
                    {
                        "primary_result": {
                            "type": "entity",
                            "entity_id": 1,
                            "permalink": "notes/x",
                            "title": "X",
                            "content": None,
                            "file_path": "folder/x.md",
                            # Naive datetime (no timezone) on purpose.
                            "created_at": t1.isoformat(),
                        },
                        "observations": [],
                        "related_results": [],
                    }
                    ,
                    {
                        "primary_result": {
                            "type": "entity",
                            "entity_id": 2,
                            "permalink": "notes/y",
                            "title": "Y",
                            "content": None,
                            "file_path": "folder/y.md",
                            "created_at": t2.isoformat(),
                        },
                        "observations": [],
                        "related_results": [],
                    },
                ],
                "metadata": {"depth": 1, "generated_at": datetime.now(timezone.utc).isoformat()},
            }
        )

    monkeypatch.setattr(recent_activity_module, "call_get", fake_call_get)

    class P:
        id = 1
        external_id = "test-external-id"
        name = "p"
        path = "/tmp/p"

    proj_activity = await recent_activity_module._get_project_activity(
        client=None, project_info=P(), params={}, depth=1
    )
    assert proj_activity.item_count == 2
    assert "folder" in proj_activity.active_folders
    assert proj_activity.last_activity is not None


def test_recent_activity_format_project_output_no_results():
    import importlib

    recent_activity_module = importlib.import_module("basic_memory.mcp.tools.recent_activity")

    empty = GraphContext(
        results=[],
        metadata=MemoryMetadata(depth=1, generated_at=datetime.now(timezone.utc)),
    )

    out = recent_activity_module._format_project_output(
        project_name="proj", activity_data=empty, timeframe="7d", type_filter=""
    )
    assert "No recent activity found" in out


def test_recent_activity_format_project_output_includes_observation_truncation():
    import importlib

    recent_activity_module = importlib.import_module("basic_memory.mcp.tools.recent_activity")

    long_content = "This is a very long observation " * 10

    activity = GraphContext(
        results=[
            ContextResult(
                primary_result=ObservationSummary(
                    observation_id=1,
                    entity_id=1,
                    title="Obs",
                    file_path="notes/obs.md",
                    permalink="notes/obs",
                    category="test",
                    content=long_content,
                    created_at=datetime.now(timezone.utc),
                ),
                observations=[],
                related_results=[],
            )
        ],
        metadata=MemoryMetadata(depth=1, generated_at=datetime.now(timezone.utc)),
    )

    out = recent_activity_module._format_project_output(
        project_name="proj",
        activity_data=activity,
        timeframe="7d",
        type_filter="observation",
    )
    assert "Recent Observations" in out
    assert "..." in out  # truncated


def test_recent_activity_format_discovery_output_includes_other_active_projects_and_key_developments():
    import importlib

    recent_activity_module = importlib.import_module("basic_memory.mcp.tools.recent_activity")

    now = datetime.now(timezone.utc)
    activity_one = GraphContext(
        results=[
            ContextResult(
                primary_result=EntitySummary(
                    entity_id=1,
                    permalink="docs/complete-feature",
                    title="Complete Feature Spec",
                    content=None,
                    file_path="docs/complete-feature.md",
                    created_at=now,
                ),
                observations=[],
                related_results=[],
            )
        ],
        metadata=MemoryMetadata(depth=1, generated_at=now),
    )
    activity_two = GraphContext(
        results=[
            ContextResult(
                primary_result=EntitySummary(
                    entity_id=2,
                    permalink="docs/other",
                    title="Other Note",
                    content=None,
                    file_path="docs/other.md",
                    created_at=now - timedelta(hours=1),
                ),
                observations=[],
                related_results=[],
            )
        ],
        metadata=MemoryMetadata(depth=1, generated_at=now),
    )

    projects_activity = {
        "A": ProjectActivity(
            project_name="A",
            project_path="/a",
            activity=activity_one,
            item_count=2,
            last_activity=now,
            active_folders=["docs"],
        ),
        "B": ProjectActivity(
            project_name="B",
            project_path="/b",
            activity=activity_two,
            item_count=1,
            last_activity=now - timedelta(hours=1),
            active_folders=[],
        ),
    }
    summary = ActivityStats(
        total_projects=2,
        active_projects=2,
        most_active_project="A",
        total_items=3,
        total_entities=3,
        total_relations=0,
        total_observations=0,
    )

    out = recent_activity_module._format_discovery_output(
        projects_activity=projects_activity,
        summary=summary,
        timeframe="7d",
        guidance="Session reminder: Remember their project choice throughout this conversation.",
    )
    assert "Most Active Project:" in out
    assert "Other Active Projects:" in out
    assert "Key Developments:" in out
