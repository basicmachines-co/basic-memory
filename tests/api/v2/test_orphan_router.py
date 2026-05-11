"""Tests for the /knowledge/orphans API endpoint."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_get_orphan_entities_empty_project(client: AsyncClient, v2_project_url):
    """An empty project returns an empty orphans list."""
    response = await client.get(f"{v2_project_url}/knowledge/orphans")

    assert response.status_code == 200
    assert response.json() == {"entities": [], "total": 0}


@pytest.mark.asyncio
async def test_get_orphan_entities_returns_unlinked_entities(client: AsyncClient, v2_project_url):
    """Entities with no relations appear in the orphans endpoint."""
    first = await client.post(
        f"{v2_project_url}/knowledge/entities",
        json={"title": "Orphan One", "directory": "orphan", "content": "No links here"},
    )
    second = await client.post(
        f"{v2_project_url}/knowledge/entities",
        json={"title": "Orphan Two", "directory": "orphan", "content": "Also no links"},
    )
    assert first.status_code == 200
    assert second.status_code == 200

    response = await client.get(f"{v2_project_url}/knowledge/orphans")

    assert response.status_code == 200
    data = response.json()
    titles = {entity["title"] for entity in data["entities"]}
    assert titles == {"Orphan One", "Orphan Two"}
    assert data["total"] == 2


@pytest.mark.asyncio
async def test_get_orphan_entities_excludes_incoming_and_outgoing_relation_nodes(
    client: AsyncClient, v2_project_url
):
    """Entities with either side of a resolved relation are excluded from orphans."""
    target = await client.post(
        f"{v2_project_url}/knowledge/entities",
        json={
            "title": "Target Note",
            "directory": "linked",
            "content": "Referenced entity",
        },
    )
    source = await client.post(
        f"{v2_project_url}/knowledge/entities",
        json={
            "title": "Source Note",
            "directory": "linked",
            "content": "- links_to [[Target Note]]",
        },
    )
    standalone = await client.post(
        f"{v2_project_url}/knowledge/entities",
        json={"title": "Standalone Note", "directory": "linked", "content": "No links"},
    )
    assert source.status_code == 200
    assert target.status_code == 200
    assert standalone.status_code == 200

    response = await client.get(f"{v2_project_url}/knowledge/orphans")

    assert response.status_code == 200
    titles = {entity["title"] for entity in response.json()["entities"]}
    assert "Source Note" not in titles
    assert "Target Note" not in titles
    assert "Standalone Note" in titles


@pytest.mark.asyncio
async def test_get_orphan_entities_response_shape(client: AsyncClient, v2_project_url):
    """Each orphan entity in the response has the expected graph-node fields."""
    created = await client.post(
        f"{v2_project_url}/knowledge/entities",
        json={"title": "Shape Test", "directory": "shape", "content": "Testing shape"},
    )
    assert created.status_code == 200

    response = await client.get(f"{v2_project_url}/knowledge/orphans")

    assert response.status_code == 200
    data = response.json()
    entity = next(entity for entity in data["entities"] if entity["title"] == "Shape Test")
    assert set(entity) == {"external_id", "title", "note_type", "file_path"}
    assert entity["file_path"].endswith(".md")
