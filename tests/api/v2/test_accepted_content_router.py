"""Accepted note-content batch API coverage."""

import pytest
from httpx import AsyncClient


async def _create_note(client: AsyncClient, project_url: str, title: str) -> dict[str, object]:
    response = await client.post(
        f"{project_url}/knowledge/entities",
        json={
            "title": title,
            "directory": "batch-test",
            "content": f"# {title}\n\nAccepted content for {title}.\n",
        },
    )
    assert response.status_code == 202
    return response.json()


@pytest.mark.asyncio
async def test_batch_returns_accepted_content_in_request_order(
    client: AsyncClient,
    v2_project_url: str,
) -> None:
    first = await _create_note(client, v2_project_url, "First batch note")
    second = await _create_note(client, v2_project_url, "Second batch note")
    missing_id = "00000000-0000-0000-0000-000000000000"

    response = await client.request(
        "QUERY",
        f"{v2_project_url}/knowledge/entities/batch",
        json={
            "entity_ids": [
                second["external_id"],
                missing_id,
                first["external_id"],
            ]
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert [item["external_id"] for item in payload["items"]] == [
        second["external_id"],
        first["external_id"],
    ]
    assert payload["items"][0]["content"].endswith(
        "# Second batch note\n\nAccepted content for Second batch note.\n"
    )
    assert payload["items"][1]["content"].endswith(
        "# First batch note\n\nAccepted content for First batch note.\n"
    )
    assert payload["missing_entity_ids"] == [missing_id]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "entity_ids",
    [
        ["same", "same"],
        [f"note-{index}" for index in range(26)],
    ],
)
async def test_batch_rejects_duplicate_or_oversized_input(
    client: AsyncClient,
    v2_project_url: str,
    entity_ids: list[str],
) -> None:
    response = await client.request(
        "QUERY",
        f"{v2_project_url}/knowledge/entities/batch",
        json={"entity_ids": entity_ids},
    )

    assert response.status_code == 422
