"""Lifecycle helpers for the optional standalone Redis read cache."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from loguru import logger

from basic_memory.read_cache.contract import ReadCache


STANDALONE_CACHE_NAMESPACE = "standalone"


def normalize_redis_url(redis_url: str) -> str:
    """Accept either a complete Redis URL or a bare Redis hostname."""
    normalized = redis_url.strip()
    if "://" in normalized:
        return normalized
    return f"redis://{normalized}"


@asynccontextmanager
async def open_redis_read_cache(
    redis_url: str | None,
    *,
    max_connections: int = 20,
) -> AsyncIterator[ReadCache | None]:
    """Open one process-owned Redis cache when standalone caching is configured."""
    if max_connections <= 0:
        raise ValueError("Redis max_connections must be positive")
    if redis_url is None or not redis_url.strip():
        yield None
        return

    # Importing the adapter only when configured preserves Redis as an optional dependency.
    from basic_memory.read_cache.redis import RedisReadCache, create_redis_read_cache_client

    client = create_redis_read_cache_client(
        normalize_redis_url(redis_url),
        max_connections=max_connections,
    )
    logger.info("Standalone Redis read cache enabled")
    try:
        yield RedisReadCache(client=client, namespace=STANDALONE_CACHE_NAMESPACE)
    finally:
        await client.aclose()
