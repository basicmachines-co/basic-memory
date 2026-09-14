"""CJK misses remain exact, but expose an actionable recovery path (#1533)."""

from typing import Literal

import pytest
from sqlalchemy import text
from httpx import AsyncClient

from basic_memory import db


@pytest.mark.asyncio
async def test_unspaced_compound_miss_has_actionable_hint(
    client: AsyncClient, v2_project_url: str, entity_repository, search_service
):
    async with db.scoped_session(search_service.session_maker) as session:
        entity = await entity_repository.create(
            session,
            {
                "title": "Deployment diary",
                "note_type": "note",
                "content_type": "text/markdown",
                "file_path": "notes/rime.md",
                "permalink": "notes/rime",
            },
        )
    await search_service.index_entity_data(entity, content="09-07 雾凇词库部署时实测")

    # This pins the actual backend behavior, rather than assuming the issue's
    # older unicode61-only index is still the current implementation.
    for query in ("雾凇", "词库", "雾凇词库部署"):
        response = await client.post(f"{v2_project_url}/search/", json={"text": query})
        assert response.status_code == 200
        assert len(response.json()["results"]) == 1
        assert response.json().get("query_hint") is None

    response = await client.post(f"{v2_project_url}/search/", json={"text": "雾凇拼音"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["results"] == []
    assert payload["total"] == 0
    assert payload["total_is_exact"] is True
    assert payload["has_more"] is False
    assert "shorter word" in payload["query_hint"]
    assert "spaces" in payload["query_hint"]

    later_page = await client.post(
        f"{v2_project_url}/search/", json={"text": "雾凇拼音"}, params={"page": 2}
    )
    assert later_page.json().get("query_hint") is None

    # The typed MCP client and formatter must preserve the API's guidance in
    # both output formats, including compact discovery responses.
    from basic_memory.mcp.tools.search import search_notes

    async with db.scoped_session(search_service.session_maker) as session:
        await session.execute(
            text("UPDATE project SET last_indexed_at = CURRENT_TIMESTAMP WHERE id = :id"),
            {"id": entity.project_id},
        )
    formats: tuple[Literal["text", "json"], ...] = ("text", "json")
    for output_format in formats:
        result = await search_notes(
            query="雾凇拼音", search_type="text", output_format=output_format, compact=True
        )
        if isinstance(result, str):
            assert "shorter word" in result
        else:
            assert "shorter word" in result["query_hint"]


@pytest.mark.asyncio
@pytest.mark.parametrize("output_format", ["text", "json"])
async def test_unindexed_cjk_miss_keeps_index_guidance(
    client, test_project, session_maker, config_home, output_format
):
    from basic_memory.mcp.tools.search import search_notes

    notes_dir = config_home / "notes"
    notes_dir.mkdir(exist_ok=True)
    (notes_dir / "unindexed.md").write_text("# Diary\n\n雾凇词库部署\n", encoding="utf-8")
    async with db.scoped_session(session_maker) as session:
        await session.execute(
            text("UPDATE project SET last_indexed_at = NULL WHERE id = :id"),
            {"id": test_project.id},
        )
    result = await search_notes(
        project=test_project.name, query="雾凇拼音", search_type="text", output_format=output_format
    )
    assert isinstance(result, str)
    assert result.startswith("# Project Index Required")
    assert "shorter word" not in result


@pytest.mark.asyncio
async def test_supplementary_han_miss_has_guidance(client: AsyncClient, v2_project_url: str):
    response = await client.post(f"{v2_project_url}/search/", json={"text": "𠮷野家情報"})
    assert response.status_code == 200
    assert response.json()["results"] == []
    assert "shorter word" in response.json()["query_hint"]
