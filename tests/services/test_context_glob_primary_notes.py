"""Glob context selects notes before paging, including legacy workspace fallback."""

import pytest

from basic_memory.services.context_service import ContextService
from basic_memory.workspace_context import workspace_permalink_context

GRAPH_FILE_PATHS = {
    "test/Connected Entity 1.md",
    "test/Connected Entity 2.md",
    "test/Deep Entity.md",
    "test/Deeper Entity.md",
    "test/Root.md",
}


@pytest.mark.asyncio
@pytest.mark.parametrize("workspace", [None, "team-paul"])
async def test_glob_primary_rows_are_notes_in_both_permalink_forms(
    search_repository,
    entity_repository,
    observation_repository,
    session_maker,
    test_graph,
    workspace,
):
    service = ContextService(
        search_repository, entity_repository, observation_repository, session_maker=session_maker
    )
    prefix = f"{workspace}/" if workspace else ""

    with workspace_permalink_context(
        workspace_slug=workspace, workspace_type="organization" if workspace else None
    ):
        result = await service.build_context(f"memory://{prefix}test-project/test/*", limit=100)

    assert all(item.primary_result.type == "entity" for item in result.results)
    assert {item.primary_result.file_path for item in result.results} == GRAPH_FILE_PATHS
    assert result.metadata.primary_count == len(result.results)
    assert result.metadata.has_more is False


@pytest.mark.asyncio
async def test_note_pattern_later_page_does_not_fall_back_to_graph_rows(
    search_repository,
    entity_repository,
    observation_repository,
    session_maker,
    test_graph,
):
    service = ContextService(
        search_repository, entity_repository, observation_repository, session_maker=session_maker
    )

    with workspace_permalink_context(workspace_slug=None, workspace_type=None):
        result = await service.build_context("memory://test-project/test/*", limit=2, offset=6)

    assert result.results == []
    assert result.metadata.primary_count == 0


@pytest.mark.asyncio
async def test_graph_row_pattern_still_reaches_observation_and_relation_rows(
    search_repository,
    entity_repository,
    observation_repository,
    session_maker,
    test_graph,
):
    service = ContextService(
        search_repository, entity_repository, observation_repository, session_maker=session_maker
    )

    with workspace_permalink_context(workspace_slug=None, workspace_type=None):
        relation_result = await service.build_context(
            "memory://test-project/test/*/connects-to/*", limit=100
        )
        observation_result = await service.build_context(
            "memory://test-project/test/*/observations/*", limit=100
        )

    assert relation_result.results
    assert all(item.primary_result.type == "relation" for item in relation_result.results)
    assert any(
        "connects-to" in (item.primary_result.permalink or "") for item in relation_result.results
    )
    assert observation_result.results
    assert all(item.primary_result.type == "observation" for item in observation_result.results)
