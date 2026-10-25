"""A project created through MCP accepts files copied before watcher reload (#1817)."""

import asyncio

import pytest

from basic_memory.index import watch_service as watch_module
from basic_memory.index.watch_service import WatchService
from basic_memory.mcp.tools import bm_project, bm_search


@pytest.mark.asyncio
async def test_files_copied_after_project_creation_are_searchable_after_watch_attach(
    client,
    app_config,
    project_repository,
    session_maker,
    test_project,
    tmp_path_factory,
    monkeypatch,
):
    watcher = WatchService(
        app_config.model_copy(deep=True),
        project_repository,
        session_maker,
        initial_projects=[test_project],
    )
    root = tmp_path_factory.mktemp("mcp-created-project")
    await bm_project(command="create", name="copied-project", path=str(root))
    (root / "inbox").mkdir()
    (root / "inbox" / "copied.md").write_text(
        "# Copied Note\n\n- [fact] unicorn\n", encoding="utf-8"
    )

    async def quiet_watch(*_paths, stop_event, **_kwargs):
        yield set()
        stop_event.set()

    monkeypatch.setattr(watch_module, "awatch", quiet_watch)
    projects = await watcher._select_projects_to_watch()
    assert "copied-project" in {project.name for project in projects}
    await watcher._watch_projects_cycle(projects, asyncio.Event())

    results = await bm_search(
        query="unicorn", mode="text", project="copied-project", output_format="json"
    )
    assert isinstance(results, dict)
    assert "inbox/copied.md" in {row["file_path"] for row in results["results"]}
