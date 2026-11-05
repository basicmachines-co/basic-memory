"""Permalink alternatives retain their identity across schema, tracing and caching."""

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient

from basic_memory.read_cache.routes import _request_identity
from basic_memory.schemas.search import SearchQuery
from basic_memory.services.search_service import describe_search_criteria, prepare_search_query


@pytest.mark.parametrize("field", ["permalink", "permalink_match"])
def test_permalink_alternatives_survive_preparation_and_trace(field):
    """Executed criteria describe each accepted alternative without stringifying the list."""
    query = SearchQuery.model_validate({field: ["notes/shared", "project/notes/shared"]})
    prepared = prepare_search_query(query)
    assert prepared is not None
    assert getattr(prepared, field) == ["notes/shared", "project/notes/shared"]
    assert describe_search_criteria(prepared) == f'{field}=["notes/shared", "project/notes/shared"]'


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["permalink", "permalink_match"])
async def test_permalink_alternatives_partition_query_cache(field):
    """Both alternatives enter the validated request key, including their list shape."""
    app = FastAPI()

    @app.api_route("/identity", methods=["QUERY"])
    async def identity(request: Request, body: SearchQuery) -> str:
        return await _request_identity(request)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        bodies = [
            {field: "notes/shared"},
            {field: ["notes/shared", "project/notes/shared"]},
            {field: ["notes/shared", "other/notes/shared"]},
            {field: ["notes/shared", "project/notes/shared"], "retrieval_mode": "fts"},
        ]
        responses = [await client.request("QUERY", "/identity", json=body) for body in bodies]
    assert all(response.status_code == 200 for response in responses)
    keys = [response.json() for response in responses]
    assert len(set(keys[:3])) == 3
    assert keys[1] == keys[3]
