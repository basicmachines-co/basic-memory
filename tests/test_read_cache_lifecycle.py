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


@pytest.mark.asyncio
@pytest.mark.parametrize("configured", [None, " "])
async def test_open_redis_read_cache_is_disabled_without_url(configured: str | None) -> None:
    async with open_redis_read_cache(configured) as read_cache:
        assert read_cache is None
