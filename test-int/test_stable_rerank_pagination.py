"""Real API/MCP pagination over the same single-pass semantic repository."""

import json

import pytest
from fastapi import FastAPI
from fastmcp import Client, FastMCP
from httpx import AsyncClient
from sqlalchemy import func, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from basic_memory import db

from basic_memory.deps.services import get_search_service_v2_external
from basic_memory.models import Project
from basic_memory.services.search_service import SearchService
from semantic_search_helpers import (
    BackendSearchRepository,
    _FakeReranker,
    rerank_search_repository as rerank_search_repository,
    pagination_repository as pagination_repository,
)


@pytest.fixture
def session_maker(
    engine_factory: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> async_sessionmaker[AsyncSession]:
    return engine_factory[1]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["vector", "hybrid"])
async def test_api_and_mcp_keep_probe_and_page_boundaries(
    pagination_repository: BackendSearchRepository,
    search_service: SearchService,
    app: FastAPI,
    client: AsyncClient,
    test_project: Project,
    mcp_server: FastMCP,
    mode: str,
) -> None:
    v2_project_url = f"/v2/projects/{test_project.external_id}"
    repo = pagination_repository
    repo._rerank_provider = _FakeReranker({"Note 00": 0.8, "Note 01": 0.9})
    async with db.scoped_session(repo.session_maker) as session:
        await session.execute(
            update(Project).where(Project.id == test_project.id).values(last_indexed_at=func.now())
        )
        await session.commit()
    search_service.repository = repo
    app.dependency_overrides[get_search_service_v2_external] = lambda: search_service
    query = {"text": "auth session token", "retrieval_mode": mode, "min_similarity": 0.5}
    response = await client.request(
        "QUERY", f"{v2_project_url}/search/", json=query, params={"page_size": 100}
    )
    assert response.status_code == 200, response.text
    expected = response.json()["results"]
    pages = []
    async with Client(mcp_server) as mcp:
        for page in range(1, (len(expected) + 4) // 5 + 2):
            result = await mcp.call_tool(
                "search_notes",
                {
                    "project": test_project.name,
                    "query": "auth session token",
                    "search_type": mode,
                    "min_similarity": 0.5,
                    "page": page,
                    "page_size": 5,
                    "output_format": "json",
                },
            )
            payload = json.loads(result.content[0].text)
            assert payload["has_more"] == (page * 5 < len(expected))
            assert payload["total_is_exact"] is False
            pages.extend(payload["results"])
    assert [row["permalink"] for row in pages] == [row["permalink"] for row in expected]
