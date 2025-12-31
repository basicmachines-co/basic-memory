import pytest
from watchfiles.main import Change

from basic_memory.sync.watch_service import WatchService


@pytest.mark.asyncio
async def test_handle_changes_reclassifies_added_existing_files_as_modified(
    app_config,
    project_repository,
    sync_service,
    test_project,
    project_config,
):
    """Regression: don't mutate `adds` while iterating.

    Some editors perform atomic writes that can show up as "added" events for files
    that already exist and have entities in the DB. We should process these as
    modifications for *all* affected files (not skip half the batch).
    """

    async def sync_service_factory(_project):
        return sync_service

    watch_service = WatchService(
        app_config=app_config,
        project_repository=project_repository,
        quiet=True,
        sync_service_factory=sync_service_factory,
    )

    # Create two files and sync them so they exist in the DB.
    file_a = project_config.home / "atomic-a.md"
    file_b = project_config.home / "atomic-b.md"
    file_a.write_text("# A\n\n- links_to [[B]]\n", encoding="utf-8")
    file_b.write_text("# B\n", encoding="utf-8")

    await sync_service.sync(project_config.home, project_name=test_project.name)

    # Simulate a watcher batch where both existing files show up as "added".
    changes = {
        (Change.added, str(file_a)),
        (Change.added, str(file_b)),
    }

    await watch_service.handle_changes(test_project, changes)

    # Both should have been processed as "modified" (reclassified), not "new".
    actions = [e.action for e in watch_service.state.recent_events]
    assert "new" not in actions
    assert actions.count("modified") >= 2


