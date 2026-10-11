"""The standalone read cache across processes: a CLI writer and an MCP server.

A local MCP server keeps one Redis cache. Other processes (the CLI) change the
same database, so their writes must retire that cache too, and a restarted
server must not reuse entries that a process without a cache left stale.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import Timeout

from basic_memory.api.container import has_installed_container
from basic_memory.config import BasicMemoryConfig, ConfigManager
from basic_memory.mcp.async_client import _asgi_client
from basic_memory.mcp.server import _invalidate_persisted_read_cache
from basic_memory.read_cache import PROJECT
from basic_memory.read_cache.lifecycle import STANDALONE_CACHE_NAMESPACE, open_redis_read_cache
from basic_memory.read_cache.redis import RedisReadCache


async def _project_version(cache: RedisReadCache, project_id: str) -> str:
    return await cache.versions(project_id, PROJECT)


@pytest.mark.asyncio
async def test_cli_project_delete_retires_the_standalone_cache(
    app: FastAPI,
    app_config: BasicMemoryConfig,
    config_manager: ConfigManager,
    redis_url: str,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """A CLI process opens the configured cache, so its project delete reaches it.

    Cache hits are served before project validation, so without this a running
    MCP server keeps answering reads for a project the CLI deleted.
    """
    # A real CLI process has no MCP or API host container; the autouse
    # clean_api_container fixture keeps one leaked by an earlier test from
    # standing in for it.
    assert not has_installed_container()
    app_config.redis_url = redis_url
    config_manager.save_config(app_config)
    # A sibling of the test project's directory: projects cannot nest.
    project_path = tmp_path_factory.mktemp("doomed")

    async with open_redis_read_cache(redis_url) as server_cache:
        assert server_cache is not None
        async with _asgi_client(Timeout(30.0)) as client:
            assert has_installed_container()
            created = await client.post(
                "/v2/projects/",
                json={"name": "doomed", "path": str(project_path), "set_default": False},
            )
            assert created.status_code == 201, created.text
            project_id = created.json()["new_project"]["external_id"]
            before = await _project_version(server_cache, project_id)

            deleted = await client.delete(f"/v2/projects/{project_id}")
            assert deleted.status_code == 200, deleted.text

        assert await _project_version(server_cache, project_id) != before
    assert not has_installed_container()


@pytest.mark.asyncio
async def test_mcp_startup_retires_entries_of_deleted_projects(redis_url: str) -> None:
    """Startup advances the global epoch, which every key embeds.

    A project deleted while no cache was open is no longer active, so retiring
    only active projects would leave its entries live.
    """
    async with open_redis_read_cache(redis_url) as cache:
        assert cache is not None
        assert cache.namespace == STANDALONE_CACHE_NAMESPACE
        deleted_project_id = "6f1c2c1e-3a51-4f0e-9d1b-6b0f6f3f9a10"
        before = await _project_version(cache, deleted_project_id)

        assert await _invalidate_persisted_read_cache(cache) is cache

        assert await _project_version(cache, deleted_project_id) != before
