"""Cloud auth error translation tests for MCP tool HTTP helpers."""

import pytest
from httpx import HTTPStatusError
from mcp.server.fastmcp.exceptions import ToolError

from basic_memory.mcp.tools.utils import call_post


class _MockResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload
        self.is_success = status_code < 400

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise HTTPStatusError(
                message=f"HTTP Error {self.status_code}",
                request=None,
                response=self,
            )


class _PostClient:
    def __init__(self, response: _MockResponse):
        self._response = response

    async def post(self, *args, **kwargs):
        return self._response


@pytest.mark.asyncio
async def test_call_post_401_with_cloud_key_shows_actionable_remediation(config_manager):
    """401 auth failures with configured cloud_api_key should provide clear remediation."""
    config = config_manager.load_config()
    config.cloud_api_key = "bmc_invalid_test_key"
    config_manager.save_config(config)

    client = _PostClient(
        _MockResponse(
            401,
            {"detail": "Invalid JWT token. Authentication required."},
        )
    )

    with pytest.raises(ToolError) as exc:
        await call_post(client, "/v2/projects/", json={"name": "test"})

    message = str(exc.value)
    assert "configured cloud API key was rejected" in message
    assert "bm cloud api-key save <valid-key>" in message
    assert "cloud_api_key" in message
