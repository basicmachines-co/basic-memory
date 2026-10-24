"""Cache identities include inputs declared by nested FastAPI dependencies."""

from typing import Annotated

import pytest
from fastapi import Depends, FastAPI, Query, Request
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel

from basic_memory.read_cache.routes import _request_identity


class SearchBody(BaseModel):
    text: str
    limit: int = 10


@pytest.mark.asyncio
async def test_nested_query_parameters_and_aliases_partition_cache_entries() -> None:
    app = FastAPI()

    def pagination(page: Annotated[int, Query(alias="page-number")] = 1) -> int:
        return page

    def search_scope(page: Annotated[int, Depends(pagination)]) -> int:
        return page

    @app.get("/identity")
    async def identity(request: Request, page: Annotated[int, Depends(search_scope)]) -> str:
        return await _request_identity(request)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.get("/identity?page-number=1&nonce=a")
        ignored = await client.get("/identity?page-number=1&nonce=b")
        second = await client.get("/identity?page-number=2&nonce=a")

    assert first.status_code == ignored.status_code == second.status_code == 200
    assert first.json() == ignored.json()
    assert first.json() != second.json()


@pytest.mark.asyncio
async def test_nested_body_model_normalizes_cache_identity() -> None:
    app = FastAPI()

    def search_body(body: SearchBody) -> SearchBody:
        return body

    @app.api_route("/identity", methods=["QUERY"])
    async def identity(request: Request, body: Annotated[SearchBody, Depends(search_body)]) -> str:
        return await _request_identity(request)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.request("QUERY", "/identity", json={"text": "needle"})
        explicit = await client.request(
            "QUERY", "/identity", json={"text": "needle", "limit": 10, "nonce": "ignored"}
        )
        changed = await client.request("QUERY", "/identity", json={"text": "other"})
        invalid = await client.request("QUERY", "/identity", json={"limit": 10})

    assert first.status_code == explicit.status_code == changed.status_code == 200
    assert first.json() == explicit.json()
    assert first.json() != changed.json()
    assert invalid.status_code == 422
