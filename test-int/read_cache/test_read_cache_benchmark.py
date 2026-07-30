"""Small end-to-end benchmark for warmed entity reads."""

from __future__ import annotations

from statistics import median
from time import perf_counter
from typing import Protocol

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from redis.asyncio import Redis
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from basic_memory.deps import get_read_cache
from basic_memory.models import Project
from basic_memory.read_cache.redis import RedisReadCache


class RedisCacheHarness(Protocol):
    """Structural type for the real Redis fixture."""

    cache: RedisReadCache
    client: Redis
    namespace: str
    prefix: str


pytestmark = [pytest.mark.redis, pytest.mark.benchmark]


async def _entity_read_latencies(
    client: AsyncClient,
    url: str,
    *,
    samples: int,
) -> list[float]:
    latencies: list[float] = []
    for _ in range(samples):
        started = perf_counter()
        response = await client.get(url)
        latencies.append(perf_counter() - started)
        assert response.status_code == 200
    return latencies


@pytest.mark.asyncio
async def test_benchmark_warmed_entity_reads_reduce_authoritative_queries(
    app: FastAPI,
    client: AsyncClient,
    test_project: Project,
    redis_cache: RedisCacheHarness,
    engine_factory: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    """Report HTTP latency and prove cache hits remove authoritative SQL work."""
    project_external_id = str(test_project.external_id)
    project_url = f"/v2/projects/{project_external_id}"
    created_response = await client.post(
        f"{project_url}/knowledge/entities",
        json={
            "title": "Redis Benchmark Note",
            "directory": "cache",
            "content": "# Redis Benchmark Note\n\nRepeated read benchmark.",
        },
    )
    assert created_response.status_code == 202
    entity_url = f"{project_url}/knowledge/entities/{created_response.json()['external_id']}"

    query_count = 0

    def count_query(*_: object) -> None:
        nonlocal query_count
        query_count += 1

    engine, _ = engine_factory
    event.listen(engine.sync_engine, "before_cursor_execute", count_query)
    try:
        app.dependency_overrides[get_read_cache] = lambda: None
        await client.get(entity_url)
        query_count = 0
        authoritative_latencies = await _entity_read_latencies(
            client,
            entity_url,
            samples=20,
        )
        authoritative_queries = query_count

        app.dependency_overrides[get_read_cache] = lambda: redis_cache.cache
        await client.get(entity_url)
        query_count = 0
        cached_latencies = await _entity_read_latencies(
            client,
            entity_url,
            samples=20,
        )
        cached_queries = query_count
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", count_query)

    assert cached_queries < authoritative_queries
    print(
        "\nRedis entity-read benchmark: "
        f"authoritative_median_ms={median(authoritative_latencies) * 1_000:.3f}, "
        f"cached_median_ms={median(cached_latencies) * 1_000:.3f}, "
        f"authoritative_sql={authoritative_queries}, cached_sql={cached_queries}"
    )
