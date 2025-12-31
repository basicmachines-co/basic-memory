from datetime import UTC, datetime

import pytest

from basic_memory.mcp.prompts.recent_activity import recent_activity_prompt
from basic_memory.schemas.memory import (
    ActivityStats,
    ContextResult,
    GraphContext,
    MemoryMetadata,
    ProjectActivity,
    ProjectActivitySummary,
    EntitySummary,
)
from basic_memory.schemas.search import SearchItemType


def _entity(title: str, entity_id: int = 1) -> EntitySummary:
    return EntitySummary(
        entity_id=entity_id,
        permalink=title.lower().replace(" ", "-"),
        title=title,
        content=None,
        file_path=f"{title}.md",
        created_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_recent_activity_prompt_discovery_mode(monkeypatch):
    recent = ProjectActivitySummary(
        projects={
            "p1": ProjectActivity(
                project_name="p1",
                project_path="/tmp/p1",
                activity=GraphContext(
                    results=[ContextResult(primary_result=_entity("A"), observations=[], related_results=[])],
                    metadata=MemoryMetadata(
                        uri=None,
                        types=[SearchItemType.ENTITY],
                        depth=1,
                        timeframe="7d",
                        generated_at=datetime.now(UTC),
                    ),
                ),
                item_count=1,
            ),
            "p2": ProjectActivity(
                project_name="p2",
                project_path="/tmp/p2",
                activity=GraphContext(
                    results=[ContextResult(primary_result=_entity("B", 2), observations=[], related_results=[])],
                    metadata=MemoryMetadata(
                        uri=None,
                        types=[SearchItemType.ENTITY],
                        depth=1,
                        timeframe="7d",
                        generated_at=datetime.now(UTC),
                    ),
                ),
                item_count=1,
            ),
        },
        summary=ActivityStats(total_projects=2, active_projects=2, most_active_project="p1", total_items=2),
        timeframe="7d",
        generated_at=datetime.now(UTC),
    )

    async def fake_fn(**_kwargs):
        return recent

    monkeypatch.setattr("basic_memory.mcp.prompts.recent_activity.recent_activity.fn", fake_fn)

    out = await recent_activity_prompt.fn(timeframe="7d", project=None)  # pyright: ignore[reportGeneralTypeIssues]
    assert "Recent Activity Across All Projects" in out
    assert "Cross-Project Activity Discovery" in out


@pytest.mark.asyncio
async def test_recent_activity_prompt_project_mode(monkeypatch):
    recent = GraphContext(
        results=[ContextResult(primary_result=_entity("Only"), observations=[], related_results=[])],
        metadata=MemoryMetadata(
            uri=None,
            types=[SearchItemType.ENTITY],
            depth=1,
            timeframe="1d",
            generated_at=datetime.now(UTC),
        ),
    )

    async def fake_fn(**_kwargs):
        return recent

    monkeypatch.setattr("basic_memory.mcp.prompts.recent_activity.recent_activity.fn", fake_fn)

    out = await recent_activity_prompt.fn(timeframe="1d", project="proj")  # pyright: ignore[reportGeneralTypeIssues]
    assert "Recent Activity in proj" in out
    assert "Opportunity to Capture Activity Summary" in out


