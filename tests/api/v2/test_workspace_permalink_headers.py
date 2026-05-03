"""Tests for workspace permalink context headers."""

import pytest
from httpx import AsyncClient

from basic_memory.workspace_context import WORKSPACE_SLUG_HEADER, WORKSPACE_TYPE_HEADER


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers, expected_detail",
    [
        (
            {WORKSPACE_SLUG_HEADER: "team-paul"},
            "workspace_slug and workspace_type must be provided together",
        ),
        (
            {
                WORKSPACE_SLUG_HEADER: "../team-paul",
                WORKSPACE_TYPE_HEADER: "organization",
            },
            f"{WORKSPACE_SLUG_HEADER} must match [a-z0-9_-]+",
        ),
        (
            {
                WORKSPACE_SLUG_HEADER: "team-paul",
                WORKSPACE_TYPE_HEADER: "enterprise",
            },
            f"{WORKSPACE_TYPE_HEADER} must be one of: organization, personal",
        ),
    ],
)
async def test_workspace_permalink_headers_fail_fast(
    client: AsyncClient,
    headers: dict[str, str],
    expected_detail: str,
):
    response = await client.get("/v2/projects/", headers=headers)

    assert response.status_code == 400
    assert response.json()["detail"] == expected_detail
