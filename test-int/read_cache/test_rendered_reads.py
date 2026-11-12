"""Rendered reads store the route's full answer only when it is a success."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import uuid4

import pytest
from fastapi import APIRouter, FastAPI, Request, Response
from httpx import ASGITransport, AsyncClient

from basic_memory.read_cache import ReadCacheOperation
from redis.asyncio import Redis

from basic_memory.read_cache.redis import RedisReadCache
from basic_memory.read_cache.routes import ReadCacheRoute, ReadCacheScope, cached_read, note_group


class RedisCacheHarness(Protocol):
    """Structural type for the redis_cache fixture."""

    cache: RedisReadCache
    client: Redis
    prefix: str


pytestmark = pytest.mark.redis


@dataclass(frozen=True, slots=True)
class EchoRead:
    """Stores the route's bytes and sends them back unchanged."""

    params: frozenset[str] = frozenset()

    def check(self, request: Request) -> None:
        return None

    def store(self, response: Response) -> bytes:
        return bytes(response.body)

    def render(self, stored: bytes, request: Request) -> Response:
        return Response(content=stored, media_type="text/plain")


def _app(redis_cache: RedisCacheHarness, *, status_code: int) -> FastAPI:
    router = APIRouter(route_class=ReadCacheRoute)

    @router.get(
        "/projects/{project_id}/items/{entity_id}",
        dependencies=[
            cached_read(
                ReadCacheOperation.entity, group=note_group, ttl_seconds=60, rendered=EchoRead()
            )
        ],
    )
    async def item(project_id: str, entity_id: str) -> Response:
        return Response(content=f"{entity_id}", status_code=status_code)

    async def scope(request: Request) -> ReadCacheScope:
        return ReadCacheScope(cache=redis_cache.cache, user_key="")

    app = FastAPI()
    app.state.read_cache_scope_resolver = scope
    app.include_router(router)
    return app


async def _entries(redis_cache: RedisCacheHarness) -> list[bytes]:
    return [
        key async for key in redis_cache.client.scan_iter(match=f"{redis_cache.prefix}:*:entity:*")
    ]


@pytest.mark.asyncio
async def test_a_failed_answer_is_returned_but_never_stored(redis_cache: RedisCacheHarness) -> None:
    app = _app(redis_cache, status_code=404)
    url = f"/projects/{uuid4()}/items/{uuid4()}"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.get(url)
        second = await client.get(url)

    assert (first.status_code, second.status_code) == (404, 404)
    assert "x-redis-cache" not in second.headers
    assert await _entries(redis_cache) == []


@pytest.mark.asyncio
async def test_a_successful_answer_is_stored_and_rendered(redis_cache: RedisCacheHarness) -> None:
    app = _app(redis_cache, status_code=200)
    entity_id = uuid4()
    url = f"/projects/{uuid4()}/items/{entity_id}"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.get(url)
        second = await client.get(url)

    assert (first.headers["x-redis-cache"], second.headers["x-redis-cache"]) == ("MISS", "HIT")
    assert first.text == second.text == str(entity_id)
    assert len(await _entries(redis_cache)) == 1
