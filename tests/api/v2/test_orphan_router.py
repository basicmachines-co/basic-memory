"""Tests for the /knowledge/orphans API endpoint."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_get_orphan_entities_empty_project(client: AsyncClient, v2_project_url):
    """An empty project returns an empty orphans list."""
    response = await client.get(f"{v2_project_url}/knowledge/orphans")
    assert response.status_code == 200

    data = response.json()
    assert data["entities"] == []
    assert data["total"] == 0


@pytest.mark.asyncio
async def test_get_orphan_entities_returns_unlinked_entities(
    client: AsyncClient, v2_project_url
):
    """Entities with no relations appear in the orphans endpoint."""
    r1 = await client.post(
        f"{v2_project_url}/knowledge/entities",
        json={"title": "Orphan One", "directory": "orphan", "content": "No links here"},
    )
    assert r1.status_code == 200

    r2 = await client.post(
        f"{v2_project_url}/knowledge/entities",
        json={"title": "Orphan Two", "directory": "orphan", "content": "Also no links"},
    )
    assert r2.status_code == 200

    response = await client.get(f"{v2_project_url}/knowledge/orphans")
    assert response.status_code == 200

    data = response.json()
    assert "entities" in data
    assert "total" in data
    titles = {e["title"] for e in data["entities"]}
    assert "Orphan One" in titles
    assert "Orphan Two" in titles
    assert data["total"] == len(data["entities"])
    assert data["total"] == 2


@pytest.mark.asyncio
async def test_get_orphan_entities_excludes_entity_with_outgoing_relation(
    client: AsyncClient, v2_project_url
):
    """An entity with an outgoing wiki-link relation is excluded from orphans."""
    # Source entity references another via wikilink in content
    r_source = await client.post(
        f"{v2_project_url}/knowledge/entities",
        json={
            "title": "Source Note",
            "directory": "linked",
            "content": "- links_to [[Target Note]]",
        },
    )
    assert r_source.status_code == 200

    # Target entity (no outgoing links)
    await client.post(
        f"{v2_project_url}/knowledge/entities",
        json={"title": "Target Note", "directory": "linked", "content": "Referenced entity"},
    )

    # Unlinked entity - should appear in orphans
    await client.post(
        f"{v2_project_url}/knowledge/entities",
        json={"title": "Standalone Note", "directory": "linked", "content": "No links at all"},
    )

    response = await client.get(f"{v2_project_url}/knowledge/orphans")
    assert response.status_code == 200

    data = response.json()
    titles = {e["title"] for e in data["entities"]}

    # Source has outgoing relation — not an orphan
    assert "Source Note" not in titles
    # Standalone has no links — is an orphan
    assert "Standalone Note" in titles


@pytest.mark.asyncio
async def test_get_orphan_entities_response_shape(client: AsyncClient, v2_project_url):
    """Each entity in the response has the expected fields."""
    await client.post(
        f"{v2_project_url}/knowledge/entities",
        json={"title": "Shape Test", "directory": "shape", "content": "Testing response shape"},
    )

    response = await client.get(f"{v2_project_url}/knowledge/orphans")
    assert response.status_code == 200

    data = response.json()
    assert data["total"] >= 1

    entity = next(e for e in data["entities"] if e["title"] == "Shape Test")
    assert "external_id" in entity
    assert "title" in entity
    assert "file_path" in entity
    assert "note_type" in entity
    assert entity["title"] == "Shape Test"
    assert entity["file_path"].endswith(".md")
