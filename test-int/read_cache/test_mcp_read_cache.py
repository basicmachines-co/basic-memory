"""Real Redis coverage for the standalone MCP cache lifecycle."""

from pathlib import Path

import pytest
from fastmcp import Client
from redis.asyncio import Redis

from basic_memory.api.container import resolve_container
from basic_memory.mcp.container import get_container as get_mcp_container
from basic_memory.read_cache.keys import (
    DEFAULT_READ_CACHE_PREFIX,
    redis_read_cache_generation_key,
)
from basic_memory.read_cache.lifecycle import STANDALONE_CACHE_NAMESPACE


def _reset_config_cache() -> None:
    from basic_memory import config as config_module

    config_module._CONFIG_CACHE = None
    config_module._CONFIG_MTIME = None
    config_module._CONFIG_SIZE = None


@pytest.mark.asyncio
async def test_mcp_uses_configured_redis_for_requests_and_invalidation(
    redis_url,
    mcp_server,
    app,
    test_project,
    monkeypatch,
) -> None:
    """The MCP lifespan shares one real cache with FastAPI and its watcher."""
    monkeypatch.setenv("BASIC_MEMORY_REDIS_URL", redis_url)
    # Fixtures load config before the test body can set its process-start environment.
    # Reset the process cache so the MCP startup observes the same state as a fresh `bm mcp`.
    _reset_config_cache()

    redis = Redis.from_url(redis_url, decode_responses=False)
    generation_key = redis_read_cache_generation_key(
        prefix=DEFAULT_READ_CACHE_PREFIX,
        namespace=STANDALONE_CACHE_NAMESPACE,
        project_id=str(test_project.external_id),
    )
    project_key_pattern = f"{generation_key.removesuffix(':generation')}:*"

    try:
        existing_keys = [key async for key in redis.scan_iter(match=project_key_pattern)]
        if existing_keys:
            await redis.delete(*existing_keys)

        async with Client(mcp_server) as client:
            mcp_container = get_mcp_container()
            assert mcp_container.read_cache is not None
            assert resolve_container().read_cache is mcp_container.read_cache

            created = await client.call_tool(
                "write_note",
                {
                    "project": test_project.name,
                    "title": "Standalone Redis Cache",
                    "directory": "cache",
                    "content": "# Standalone Redis Cache\n\nA cached MCP read.",
                },
            )
            assert created.is_error is False

            for _ in range(2):
                result = await client.call_tool(
                    "read_note",
                    {
                        "project": test_project.name,
                        "identifier": "cache/standalone-redis-cache",
                        "output_format": "json",
                    },
                )
                assert result.is_error is False

            cache_keys = [key async for key in redis.scan_iter(match=project_key_pattern)]
            assert generation_key.encode() in cache_keys
            assert len(cache_keys) > 1
    finally:
        cache_keys = [key async for key in redis.scan_iter(match=project_key_pattern)]
        if cache_keys:
            await redis.delete(*cache_keys)
        await redis.aclose()


@pytest.mark.asyncio
async def test_mcp_restart_invalidates_persisted_project_generation(
    redis_url,
    mcp_server,
    test_project,
    monkeypatch,
) -> None:
    """A fresh MCP lifespan cannot reuse cache entries created before offline edits."""
    monkeypatch.setenv("BASIC_MEMORY_REDIS_URL", redis_url)
    _reset_config_cache()

    redis = Redis.from_url(redis_url, decode_responses=False)
    generation_key = redis_read_cache_generation_key(
        prefix=DEFAULT_READ_CACHE_PREFIX,
        namespace=STANDALONE_CACHE_NAMESPACE,
        project_id=str(test_project.external_id),
    )
    project_key_pattern = f"{generation_key.removesuffix(':generation')}:*"

    try:
        existing_keys = [key async for key in redis.scan_iter(match=project_key_pattern)]
        if existing_keys:
            await redis.delete(*existing_keys)

        async with Client(mcp_server) as client:
            created = await client.call_tool(
                "write_note",
                {
                    "project": test_project.name,
                    "title": "Offline Restart",
                    "directory": "cache",
                    "content": "# Offline Restart\n\nBefore restart.",
                },
            )
            assert created.is_error is False
            read = await client.call_tool(
                "read_note",
                {
                    "project": test_project.name,
                    "identifier": "cache/offline-restart",
                    "output_format": "json",
                },
            )
            assert read.is_error is False
            generation_before_restart = await redis.get(generation_key)
            assert generation_before_restart is not None

        note_path = Path(test_project.path) / "cache" / "Offline Restart.md"
        assert note_path.is_file()
        note_path.write_text(
            "# Offline Restart\n\nEdited while MCP was stopped.\n", encoding="utf-8"
        )

        async with Client(mcp_server):
            generation_after_restart = await redis.get(generation_key)

        assert generation_after_restart is not None
        assert generation_after_restart != generation_before_restart
    finally:
        cache_keys = [key async for key in redis.scan_iter(match=project_key_pattern)]
        if cache_keys:
            await redis.delete(*cache_keys)
        await redis.aclose()
