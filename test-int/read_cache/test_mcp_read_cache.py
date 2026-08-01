"""Real Redis coverage for the standalone MCP cache lifecycle."""

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
    from basic_memory import config as config_module

    # Fixtures load config before the test body can set its process-start environment.
    # Reset the process cache so the MCP startup observes the same state as a fresh `bm mcp`.
    config_module._CONFIG_CACHE = None
    config_module._CONFIG_MTIME = None
    config_module._CONFIG_SIZE = None

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
