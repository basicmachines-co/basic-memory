"""Tests for MCP tool utilities."""

import pytest
from httpx import HTTPStatusError
from mcp.server.fastmcp.exceptions import ToolError

from basic_memory.mcp.tools.utils import (
    call_get,
    call_post,
    call_put,
    call_delete,
    get_error_message,
)


@pytest.fixture
def mock_response(monkeypatch):
    """Create a mock response."""

    class MockResponse:
        def __init__(self, status_code=200):
            self.status_code = status_code
            self.is_success = status_code < 400
            self.json = lambda: {}

        def raise_for_status(self):
            if self.status_code >= 400:
                raise HTTPStatusError(
                    message=f"HTTP Error {self.status_code}", request=None, response=self
                )

    return MockResponse


class _Client:
    def __init__(self):
        self.calls: list[tuple[str, tuple, dict]] = []
        self._responses: dict[str, object] = {}

    def set_response(self, method: str, response):
        self._responses[method.lower()] = response

    async def get(self, *args, **kwargs):
        self.calls.append(("get", args, kwargs))
        return self._responses["get"]

    async def post(self, *args, **kwargs):
        self.calls.append(("post", args, kwargs))
        return self._responses["post"]

    async def put(self, *args, **kwargs):
        self.calls.append(("put", args, kwargs))
        return self._responses["put"]

    async def delete(self, *args, **kwargs):
        self.calls.append(("delete", args, kwargs))
        return self._responses["delete"]


@pytest.mark.asyncio
async def test_call_get_success(mock_response):
    """Test successful GET request."""
    client = _Client()
    client.set_response("get", mock_response())

    response = await call_get(client, "http://test.com")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_call_get_error(mock_response):
    """Test GET request with error."""
    client = _Client()
    client.set_response("get", mock_response(404))

    with pytest.raises(ToolError) as exc:
        await call_get(client, "http://test.com")
    assert "Resource not found" in str(exc.value)


@pytest.mark.asyncio
async def test_call_post_success(mock_response):
    """Test successful POST request."""
    client = _Client()
    response = mock_response()
    response.json = lambda: {"test": "data"}
    client.set_response("post", response)

    response = await call_post(client, "http://test.com", json={"test": "data"})
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_call_post_error(mock_response):
    """Test POST request with error."""
    client = _Client()
    response = mock_response(500)
    response.json = lambda: {"test": "error"}

    client.set_response("post", response)

    with pytest.raises(ToolError) as exc:
        await call_post(client, "http://test.com", json={"test": "data"})
    assert "Internal server error" in str(exc.value)


@pytest.mark.asyncio
async def test_call_put_success(mock_response):
    """Test successful PUT request."""
    client = _Client()
    client.set_response("put", mock_response())

    response = await call_put(client, "http://test.com", json={"test": "data"})
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_call_put_error(mock_response):
    """Test PUT request with error."""
    client = _Client()
    client.set_response("put", mock_response(400))

    with pytest.raises(ToolError) as exc:
        await call_put(client, "http://test.com", json={"test": "data"})
    assert "Invalid request" in str(exc.value)


@pytest.mark.asyncio
async def test_call_delete_success(mock_response):
    """Test successful DELETE request."""
    client = _Client()
    client.set_response("delete", mock_response())

    response = await call_delete(client, "http://test.com")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_call_delete_error(mock_response):
    """Test DELETE request with error."""
    client = _Client()
    client.set_response("delete", mock_response(403))

    with pytest.raises(ToolError) as exc:
        await call_delete(client, "http://test.com")
    assert "Access denied" in str(exc.value)


@pytest.mark.asyncio
async def test_call_get_with_params(mock_response):
    """Test GET request with query parameters."""
    client = _Client()
    client.set_response("get", mock_response())

    params = {"key": "value", "test": "data"}
    await call_get(client, "http://test.com", params=params)

    assert len(client.calls) == 1
    method, _args, kwargs = client.calls[0]
    assert method == "get"
    assert kwargs["params"] == params


@pytest.mark.asyncio
async def test_get_error_message():
    """Test the get_error_message function."""

    # Test 400 status code
    message = get_error_message(400, "http://test.com/resource", "GET")
    assert "Invalid request" in message
    assert "resource" in message

    # Test 404 status code
    message = get_error_message(404, "http://test.com/missing", "GET")
    assert "Resource not found" in message
    assert "missing" in message

    # Test 500 status code
    message = get_error_message(500, "http://test.com/server", "POST")
    assert "Internal server error" in message
    assert "server" in message

    # Test URL object handling
    from httpx import URL

    url = URL("http://test.com/complex/path")
    message = get_error_message(403, url, "DELETE")
    assert "Access denied" in message
    assert "path" in message


@pytest.mark.asyncio
async def test_call_post_with_json(mock_response):
    """Test POST request with JSON payload."""
    client = _Client()
    response = mock_response()
    response.json = lambda: {"test": "data"}

    client.set_response("post", response)

    json_data = {"key": "value", "nested": {"test": "data"}}
    await call_post(client, "http://test.com", json=json_data)

    assert len(client.calls) == 1
    method, _args, kwargs = client.calls[0]
    assert method == "post"
    assert kwargs["json"] == json_data
