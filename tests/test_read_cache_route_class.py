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


#: Operations keyed apart from the key they had before a change to their answers.
#: Entries stored under the earlier key are never read back.
_REKEYED_OPERATIONS = {
    # File checksums were added to tree entries.
    ReadCacheOperation.directory_tree: "directory_tree",
    # The resolver began preferring a stored file path over a slug-colliding sibling.
    ReadCacheOperation.link_resolve: "link_resolve",
    ReadCacheOperation.resolve: "resolve",
}


@pytest.mark.parametrize(
    ("operation", "earlier_key"), list(_REKEYED_OPERATIONS.items()), ids=lambda v: str(v)
)
def test_rekeyed_operation_is_keyed_apart_from_its_earlier_answers(
    operation: ReadCacheOperation, earlier_key: str
) -> None:
    assert operation.value != earlier_key
    assert operation.value.startswith(earlier_key)


def test_operations_not_rekeyed_keep_their_original_key() -> None:
    # Every other operation's cached answers are unaffected and stay readable.
    assert {op.value for op in ReadCacheOperation if op not in _REKEYED_OPERATIONS} == {
        "directory_list",
        "directory_structure",
        "entity",
        "project_resolve",
        "recent_activity",
        "resource",
        "search",
    }
