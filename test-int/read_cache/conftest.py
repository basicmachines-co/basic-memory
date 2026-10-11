"""Real Redis fixtures for read-cache integration tests."""

from __future__ import annotations

import os
import shutil
import time
from collections.abc import AsyncGenerator, Generator
from dataclasses import dataclass
from uuid import uuid4

import pytest
import pytest_asyncio
from redis import Redis as SyncRedis
from redis.asyncio import Redis
from redis.exceptions import RedisError
from testcontainers.core.container import DockerContainer

from fastapi import FastAPI, Request

from basic_memory.deps.read_cache import get_read_cache
from basic_memory.read_cache.redis import RedisReadCache, create_redis_read_cache_client
from basic_memory.read_cache.routes import ReadCacheScope


def _wait_for_redis(url: str) -> None:
    client = SyncRedis.from_url(
        url,
        socket_connect_timeout=0.1,
        socket_timeout=0.1,
    )
    deadline = time.monotonic() + 10
    last_error: RedisError | None = None
    try:
        while time.monotonic() < deadline:
            try:
                client.ping()
                return
            except RedisError as error:
                last_error = error
                time.sleep(0.05)
    finally:
        client.close()

    raise RuntimeError("Redis test server did not become ready") from last_error


@pytest.fixture(scope="session")
def redis_url() -> Generator[str]:
    """Use a configured Redis server or start a real Redis 8 testcontainer."""
    configured_url = os.environ.get("BASIC_MEMORY_TEST_REDIS_URL")
    if configured_url:
        _wait_for_redis(configured_url)
        yield configured_url
        return

    # GitHub's Windows runners cannot start the Linux Redis image through testcontainers.
    # A configured URL still runs the same real-server suite on Windows when one is available.
    if os.name == "nt":
        pytest.skip("Set BASIC_MEMORY_TEST_REDIS_URL to run real Redis tests on Windows")

    if shutil.which("docker") is None:
        pytest.skip("Docker is required for real Redis integration tests")

    image = os.environ.get("BASIC_MEMORY_TEST_REDIS_IMAGE", "redis:8.8-alpine")
    with DockerContainer(image).with_exposed_ports(6379) as container:
        host = container.get_container_host_ip()
        port = container.get_exposed_port(6379)
        url = f"redis://{host}:{port}/0"
        _wait_for_redis(url)
        yield url


@dataclass(frozen=True, slots=True)
class RedisCacheHarness:
    """One isolated Redis prefix and the client that owns it."""

    cache: RedisReadCache
    client: Redis
    namespace: str
    prefix: str


@pytest_asyncio.fixture
async def redis_cache(redis_url: str) -> AsyncGenerator[RedisCacheHarness]:
    """Yield an isolated cache over the suite's real Redis server."""
    prefix = f"bm:test:read:{uuid4().hex}"
    namespace = f"tenant-{uuid4().hex}"
    # Container scheduling can make the first async handshake exceed the
    # production cache's intentionally tight timeout. Behavioral timeout tests
    # construct their own 50ms clients, so keep this shared harness stable.
    client = create_redis_read_cache_client(redis_url, socket_timeout=1.0)
    cache = RedisReadCache(
        client=client,
        namespace=namespace,
        prefix=prefix,
    )
    try:
        await client.ping()
        yield RedisCacheHarness(
            cache=cache,
            client=client,
            namespace=namespace,
            prefix=prefix,
        )
    finally:
        keys = [key async for key in client.scan_iter(match=f"{prefix}:*")]
        if keys:
            await client.delete(*keys)
        await client.aclose()


@dataclass(frozen=True, slots=True)
class CachedApp:
    """The integration app serving reads from, and invalidating, one real Redis cache."""

    app: FastAPI
    cache: RedisReadCache


# Test-only header naming the caller, so per-user isolation is observable. A managed
# host derives the user from authentication instead.
TEST_USER_HEADER = "x-test-user"


@pytest.fixture
def cached_app(app: FastAPI, redis_cache: RedisCacheHarness) -> Generator[CachedApp]:
    """Serve cached reads from the real Redis harness and invalidate it on writes."""

    async def resolve_scope(request: Request) -> ReadCacheScope:
        return ReadCacheScope(
            cache=redis_cache.cache,
            user_key=request.headers.get(TEST_USER_HEADER, ""),
        )

    previous_resolver = getattr(app.state, "read_cache_scope_resolver", None)
    app.state.read_cache_scope_resolver = resolve_scope
    app.dependency_overrides[get_read_cache] = lambda: redis_cache.cache
    try:
        yield CachedApp(app=app, cache=redis_cache.cache)
    finally:
        app.state.read_cache_scope_resolver = previous_resolver
        app.dependency_overrides.pop(get_read_cache, None)
