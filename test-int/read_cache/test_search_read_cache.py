"""Real Redis coverage for semantic search response caching."""

from typing import Any, Protocol

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from redis.asyncio import Redis

from basic_memory.deps.read_cache import get_read_cache
from basic_memory.deps.services import get_search_service_v2_external
from basic_memory.index.local_schedulers import LocalEntityVectorSyncScheduler
from basic_memory.models import Project
from basic_memory.read_cache import ReadCacheKey, ReadCacheOperation
from basic_memory.read_cache.redis import RedisReadCache


class RedisCacheHarness(Protocol):
    """Structural type for the real Redis fixture."""

    cache: RedisReadCache
    client: Redis
    namespace: str
    prefix: str


pytestmark = pytest.mark.redis


class CountingSearchService:
    """Authoritative empty search whose calls expose cache behavior."""

    def __init__(self) -> None:
        self.search_calls = 0
        self.count_calls = 0

    async def search(self, query: Any, *, limit: int, offset: int) -> list[Any]:
        del query, limit, offset
        self.search_calls += 1
        return []

    async def count(self, query: Any) -> int:
        del query
        self.count_calls += 1
        return 0


class PartiallyFailingVectorSync:
    """Vector publisher that fails after derived state may have changed."""

    def __init__(self) -> None:
        self.synced_entity_ids: list[int] = []

    async def sync_entity_vectors(self, entity_id: int) -> None:
        self.synced_entity_ids.append(entity_id)
        raise RuntimeError("vector publication failed")


@pytest.mark.asyncio
async def test_post_and_query_share_canonical_real_redis_entry(
    app: FastAPI,
    client: AsyncClient,
    test_project: Project,
    redis_cache: RedisCacheHarness,
) -> None:
    """Method aliases and JSON map order resolve to one project-scoped search value."""
    search_service = CountingSearchService()
    app.dependency_overrides[get_read_cache] = lambda: redis_cache.cache
    app.dependency_overrides[get_search_service_v2_external] = lambda: search_service

    project_external_id = str(test_project.external_id)
    search_url = f"/v2/projects/{project_external_id}/search/"
    pagination = {"page": 1, "page_size": 10}
    post_response = await client.post(
        search_url,
        params=pagination,
        json={
            "text": "canonical cache",
            "metadata_filters": {
                "status": "open",
                "nested": {"z": 2, "a": 1},
            },
        },
    )
    query_response = await client.request(
        "QUERY",
        search_url,
        params=pagination,
        json={
            "metadata_filters": {
                "nested": {"a": 1, "z": 2},
                "status": "open",
            },
            "text": "canonical cache",
        },
    )

    assert post_response.status_code == 200
    assert query_response.status_code == 200
    assert query_response.json() == post_response.json()
    assert query_response.headers["Accept-Query"] == "application/json"
    assert search_service.search_calls == 1
    assert search_service.count_calls == 1

    search_keys = [
        key async for key in redis_cache.client.scan_iter(match=f"{redis_cache.prefix}:*:search:*")
    ]
    assert len(search_keys) == 1
    search_ttl = await redis_cache.client.ttl(search_keys[0])
    assert 0 < search_ttl <= 1800

    await redis_cache.cache.invalidate_project(project_external_id)
    refreshed_response = await client.request(
        "QUERY",
        search_url,
        params=pagination,
        json={
            "text": "canonical cache",
            "metadata_filters": {
                "status": "open",
                "nested": {"z": 2, "a": 1},
            },
        },
    )

    assert refreshed_response.status_code == 200
    assert search_service.search_calls == 2
    assert search_service.count_calls == 2


@pytest.mark.asyncio
async def test_search_pagination_uses_distinct_real_redis_entries(
    app: FastAPI,
    client: AsyncClient,
    test_project: Project,
    redis_cache: RedisCacheHarness,
) -> None:
    """Pagination changes result identity even when the query document is unchanged."""
    search_service = CountingSearchService()
    app.dependency_overrides[get_read_cache] = lambda: redis_cache.cache
    app.dependency_overrides[get_search_service_v2_external] = lambda: search_service

    search_url = f"/v2/projects/{test_project.external_id}/search/"
    first_page = await client.request(
        "QUERY",
        search_url,
        params={"page": 1, "page_size": 10},
        json={"text": "pagination"},
    )
    second_page = await client.request(
        "QUERY",
        search_url,
        params={"page": 2, "page_size": 10},
        json={"text": "pagination"},
    )

    assert first_page.status_code == 200
    assert second_page.status_code == 200
    assert search_service.search_calls == 2
    assert search_service.count_calls == 2
    search_keys = [
        key async for key in redis_cache.client.scan_iter(match=f"{redis_cache.prefix}:*:search:*")
    ]
    assert len(search_keys) == 2


@pytest.mark.asyncio
async def test_partial_vector_publication_invalidates_real_redis_generation(
    test_project: Project,
    redis_cache: RedisCacheHarness,
) -> None:
    """A failed background vector sync cannot preserve cached vector or hybrid results."""
    project_external_id = str(test_project.external_id)
    cache_key = ReadCacheKey(
        project_id=project_external_id,
        operation=ReadCacheOperation.search,
        request_digest="0" * 64,
    )
    generation_before = (await redis_cache.cache.lookup(cache_key)).generation
    vector_sync = PartiallyFailingVectorSync()
    scheduler = LocalEntityVectorSyncScheduler(
        search_service=vector_sync,
        project_external_id=project_external_id,
        read_cache=redis_cache.cache,
        test_mode=False,
    )

    with pytest.raises(RuntimeError, match="vector publication failed"):
        await scheduler._run_entity_vector_sync(entity_id=42)

    generation_after = (await redis_cache.cache.lookup(cache_key)).generation
    assert vector_sync.synced_entity_ids == [42]
    assert generation_after != generation_before
