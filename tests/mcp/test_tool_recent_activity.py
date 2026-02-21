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
async def test_recent_activity_discovery_mode(client, test_project, test_graph, config_manager):
    """Test that recent_activity discovery mode works without project parameter."""
    # Clear default_project to test discovery mode
    cfg = config_manager.load_config()
    cfg.default_project = None
    config_manager.save_config(cfg)

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
async def test_recent_activity_discovery_mode_no_activity(client, test_project, config_manager):
    """If there is no activity in any project, discovery mode should say so."""
    # Clear default_project to test discovery mode
    cfg = config_manager.load_config()
    cfg.default_project = None
    config_manager.save_config(cfg)

    result = await recent_activity.fn()
    assert "Recent Activity Summary" in result
    assert "No recent activity found in any project." in result


@pytest.mark.asyncio
async def test_recent_activity_discovery_mode_multiple_active_projects(
    app, client, test_project, tmp_path_factory, config_manager
):
    """Discovery mode should use the multi-project guidance when multiple projects have activity."""
    # Clear default_project to test discovery mode
    cfg = config_manager.load_config()
    cfg.default_project = None
    config_manager.save_config(cfg)

    from basic_memory.mcp.tools import create_memory_project, write_note

    second_root = tmp_path_factory.mktemp("second-project-home")

    result = await create_memory_project.fn(
        project_name="second-project",
        project_path=str(second_root),
        set_default=False,
    )
    assert result.startswith("✓")

    await write_note.fn(project=test_project.name, title="One", directory="notes", content="one")
    await write_note.fn(project="second-project", title="Two", directory="notes", content="two")

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
                            "external_id": "550e8400-e29b-41d4-a716-446655440001",
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
                    },
                    {
                        "primary_result": {
                            "type": "entity",
                            "external_id": "550e8400-e29b-41d4-a716-446655440002",
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
        project_name="proj", activity_data=empty, timeframe="7d", type_filter="", page=1
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
        page=1,
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
                    external_id="550e8400-e29b-41d4-a716-446655440001",
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
                    external_id="550e8400-e29b-41d4-a716-446655440002",
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


@pytest.mark.asyncio
async def test_recent_activity_entity_only_default(client, test_project, test_graph):
    """When no type is specified, recent_activity should default to entity-only.

    test_graph creates entities with observations and relations, so if all types
    were returned we'd see observation/relation rows in the JSON output.
    """
    json_result = await recent_activity.fn(project=test_project.name, output_format="json")
    assert isinstance(json_result, list)
    assert len(json_result) > 0
    # Every item should be an entity — no observations or relations
    for item in json_result:
        assert item["type"] == "entity", f"Expected entity-only default, got type={item['type']}"


@pytest.mark.asyncio
async def test_recent_activity_explicit_types_returns_requested_types(
    client, test_project, test_graph
):
    """Explicitly requesting observation/relation types should return those types."""
    # Request observations only
    obs_result = await recent_activity.fn(
        project=test_project.name, type=["observation"], output_format="json"
    )
    assert isinstance(obs_result, list)
    for item in obs_result:
        assert item["type"] == "observation", f"Expected observation type, got type={item['type']}"

    # Request relations only
    rel_result = await recent_activity.fn(
        project=test_project.name, type=["relation"], output_format="json"
    )
    assert isinstance(rel_result, list)
    for item in rel_result:
        assert item["type"] == "relation", f"Expected relation type, got type={item['type']}"

    # Request all types explicitly
    all_result = await recent_activity.fn(
        project=test_project.name,
        type=["entity", "observation", "relation"],
        output_format="json",
    )
    assert isinstance(all_result, list)
    types_found = {item["type"] for item in all_result}
    # test_graph creates entities with observations and relations,
    # so we expect at least entity and one other type
    assert "entity" in types_found


@pytest.mark.asyncio
async def test_recent_activity_pagination_params(client, test_project, test_graph):
    """Test that page and page_size params are forwarded correctly."""
    result = await recent_activity.fn(
        project=test_project.name,
        type=["entity"],
        page=1,
        page_size=2,
    )
    assert isinstance(result, str)
    assert "Recent Activity:" in result


@pytest.mark.asyncio
async def test_recent_activity_pagination_validation():
    """Invalid page/page_size values should raise clear ValueError messages."""
    with pytest.raises(ValueError, match="page must be >= 1, got 0"):
        await recent_activity.fn(page=0)

    with pytest.raises(ValueError, match="page must be >= 1, got -1"):
        await recent_activity.fn(page=-1)

    with pytest.raises(ValueError, match="page_size must be >= 1, got 0"):
        await recent_activity.fn(page_size=0)

    with pytest.raises(ValueError, match="page_size must be >= 1, got -5"):
        await recent_activity.fn(page_size=-5)

    with pytest.raises(ValueError, match="page_size must be <= 100, got 999"):
        await recent_activity.fn(page_size=999)


def test_format_project_output_has_more_pagination_guidance():
    """When has_more is True, activity summary should show pagination guidance."""
    import importlib

    recent_activity_module = importlib.import_module("basic_memory.mcp.tools.recent_activity")

    now = datetime.now(timezone.utc)
    activity = GraphContext(
        results=[
            ContextResult(
                primary_result=EntitySummary(
                    external_id="550e8400-e29b-41d4-a716-446655440001",
                    entity_id=1,
                    permalink="notes/test",
                    title="Test Note",
                    content=None,
                    file_path="notes/test.md",
                    created_at=now,
                ),
                observations=[],
                related_results=[],
            )
        ],
        metadata=MemoryMetadata(depth=1, generated_at=now),
        has_more=True,
    )

    out = recent_activity_module._format_project_output(
        project_name="proj",
        activity_data=activity,
        timeframe="7d",
        type_filter="entity",
        page=1,
    )
    assert "Use page=2 to see more" in out
    assert "Showing 1 items (page 1)" in out


def test_format_project_output_no_more_pages():
    """When has_more is False, activity summary should not show pagination guidance."""
    import importlib

    recent_activity_module = importlib.import_module("basic_memory.mcp.tools.recent_activity")

    now = datetime.now(timezone.utc)
    activity = GraphContext(
        results=[
            ContextResult(
                primary_result=EntitySummary(
                    external_id="550e8400-e29b-41d4-a716-446655440001",
                    entity_id=1,
                    permalink="notes/test",
                    title="Test Note",
                    content=None,
                    file_path="notes/test.md",
                    created_at=now,
                ),
                observations=[],
                related_results=[],
            )
        ],
        metadata=MemoryMetadata(depth=1, generated_at=now),
        has_more=False,
    )

    out = recent_activity_module._format_project_output(
        project_name="proj",
        activity_data=activity,
        timeframe="7d",
        type_filter="entity",
        page=1,
    )
    assert "1 items found." in out
    assert "Use page=" not in out
