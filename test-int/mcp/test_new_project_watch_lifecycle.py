"""New-project discovery through the production MCP and native watcher lifecycle."""

import asyncio
from contextlib import suppress
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from fastmcp import Client

from basic_memory.config import BasicMemoryConfig, ConfigManager
from basic_memory.index.local_schedulers import drain_background_tasks
from basic_memory.mcp.server import mcp
from basic_memory.services.initialization import _initial_index_tasks


pytestmark = [pytest.mark.asyncio, pytest.mark.real_embedder]


async def write_ignored_activity(path: Path) -> None:
    """Keep hidden-file events flowing while the new watch attaches."""
    while True:
        path.write_bytes(b"ignored activity\n")
        await asyncio.sleep(0.05)


@pytest.mark.usefixtures("engine_factory")
async def test_new_project_copied_files_and_live_changes_are_searchable(
    app_config: BasicMemoryConfig, config_manager: ConfigManager
) -> None:
    app_config.env = "dev"
    app_config.index_changes = True
    app_config.watch_project_reload_interval = 1
    app_config.index_delay = 50
    config_manager.save_config(app_config)

    # Use the actual lifespan, database initialization and native file watcher.
    # The new root must be outside the default project's root to respect nesting rules.
    with TemporaryDirectory(prefix="bm-new-project-") as directory:
        root = Path(directory)
        with pytest.MonkeyPatch.context() as environment:
            environment.setenv("BASIC_MEMORY_ENV", "dev")
            environment.delenv("PYTEST_CURRENT_TEST", raising=False)
            environment.delenv("BASIC_MEMORY_MCP_PROJECT", raising=False)
            assert not app_config.is_test_env
            assert app_config.default_project is not None
            ignored_path = (
                Path(app_config.projects[app_config.default_project].path) / ".watch-noise"
            )
            noise = asyncio.create_task(write_ignored_activity(ignored_path))
            try:
                async with Client(mcp) as client:
                    await client.call_tool(
                        "bm_project",
                        {"command": "create", "name": "copied-project", "path": str(root)},
                    )
                    await drain_background_tasks()
                    (root / "inbox").mkdir()
                    copied = root / "inbox/copied.md"
                    copied.write_bytes(b"# Copied Note\n\n- [fact] unicorn\n")

                    # Poll an observable public result under a deadline; no watcher,
                    # scheduler, indexer, database or embedder is substituted.
                    async with asyncio.timeout(45):
                        while True:
                            result = await client.call_tool(
                                "bm_search",
                                {
                                    "query": "unicorn",
                                    "mode": "text",
                                    "project": "copied-project",
                                    "output_format": "json",
                                },
                            )
                            assert isinstance(result.data, dict)
                            if any(
                                row["file_path"] == "inbox/copied.md"
                                for row in result.data["results"]
                            ):
                                break
                            await asyncio.sleep(0.1)

                    copied.write_bytes(b"# Copied Note\n\n- [fact] narwhal\n")
                    async with asyncio.timeout(45):
                        while True:
                            result = await client.call_tool(
                                "bm_search",
                                {
                                    "query": "narwhal",
                                    "mode": "text",
                                    "project": "copied-project",
                                    "output_format": "json",
                                },
                            )
                            assert isinstance(result.data, dict)
                            if any(
                                row["file_path"] == "inbox/copied.md"
                                for row in result.data["results"]
                            ):
                                break
                            await asyncio.sleep(0.1)
            finally:
                noise.cancel()
                with suppress(asyncio.CancelledError):
                    await noise
                # Startup scans have explicit task ownership outside the watch task.
                # Let them settle before fixture teardown closes the database.
                await asyncio.gather(*_initial_index_tasks)
                await drain_background_tasks()
