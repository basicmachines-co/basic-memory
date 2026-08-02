"""Tests for standalone Redis read-cache configuration."""

import pytest

from basic_memory.config import BasicMemoryConfig
from basic_memory.read_cache.lifecycle import normalize_redis_url, open_redis_read_cache


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        ("redis", "redis://redis"),
        (" redis://localhost:6379/2 ", "redis://localhost:6379/2"),
        ("rediss://cache.example.com", "rediss://cache.example.com"),
    ],
)
def test_normalize_redis_url(configured: str, expected: str) -> None:
    assert normalize_redis_url(configured) == expected


def test_redis_url_uses_basic_memory_env_prefix(monkeypatch) -> None:
    monkeypatch.setenv("BASIC_MEMORY_REDIS_URL", "redis")

    assert BasicMemoryConfig().redis_url == "redis"


def test_redis_max_connections_uses_basic_memory_env_prefix(monkeypatch) -> None:
    monkeypatch.setenv("BASIC_MEMORY_REDIS_MAX_CONNECTIONS", "64")

    assert BasicMemoryConfig().redis_max_connections == 64


@pytest.mark.asyncio
@pytest.mark.parametrize("configured", [None, " "])
async def test_open_redis_read_cache_is_disabled_without_url(configured: str | None) -> None:
    async with open_redis_read_cache(configured) as read_cache:
        assert read_cache is None


@pytest.mark.asyncio
async def test_open_redis_read_cache_configures_pool_size(monkeypatch) -> None:
    from basic_memory.read_cache import redis as redis_adapter

    configured: dict[str, object] = {}

    class Client:
        closed = False

        async def aclose(self) -> None:
            self.closed = True

    client = Client()

    def create_client(url: str, *, max_connections: int):
        configured.update(url=url, max_connections=max_connections)
        return client

    monkeypatch.setattr(redis_adapter, "create_redis_read_cache_client", create_client)

    async with open_redis_read_cache("redis", max_connections=64) as read_cache:
        assert read_cache is not None

    assert configured == {"url": "redis://redis", "max_connections": 64}
    assert client.closed


@pytest.mark.asyncio
async def test_open_redis_read_cache_rejects_non_positive_pool_size() -> None:
    with pytest.raises(ValueError, match="max_connections must be positive"):
        async with open_redis_read_cache(None, max_connections=0):
            pass
