"""A cached_read route only serves the cache when its router uses ReadCacheRoute."""

import pytest
from fastapi import APIRouter, FastAPI, Request
from httpx import ASGITransport, AsyncClient

from basic_memory.read_cache import ReadCacheOperation
from basic_memory.read_cache.routes import (
    ReadCacheRoute,
    ReadCacheScope,
    cached_read,
    content_group,
)


async def no_cache(request: Request) -> ReadCacheScope | None:
    """A host with no cache configured: every read goes to the endpoint."""
    del request
    return None


def _app(router: APIRouter) -> FastAPI:
    @router.get(
        "/projects/{project_id}/tree",
        dependencies=[
            cached_read(ReadCacheOperation.directory_tree, group=content_group, ttl_seconds=60)
        ],
    )
    async def tree(project_id: str) -> dict[str, str]:
        return {"project_id": project_id}

    app = FastAPI()
    app.state.read_cache_scope_resolver = no_cache
    app.include_router(router)
    return app


@pytest.mark.asyncio
async def test_cached_read_runs_the_endpoint_through_read_cache_route() -> None:
    app = _app(APIRouter(route_class=ReadCacheRoute))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/projects/p1/tree")

    assert response.status_code == 200
    assert response.json() == {"project_id": "p1"}


@pytest.mark.asyncio
async def test_cached_read_without_read_cache_route_fails_fast() -> None:
    """Without the route class nothing serves the cache, so the route refuses to run."""
    app = _app(APIRouter())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        with pytest.raises(TypeError, match="route_class=ReadCacheRoute"):
            await client.get("/projects/p1/tree")
