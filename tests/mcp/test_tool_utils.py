"""Tests for MCP tool utilities."""

from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient, HTTPStatusError
from mcp.server.fastmcp.exceptions import ToolError

from basic_memory.mcp.tools.utils import call_get, call_post, call_put, call_delete


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


@pytest.mark.asyncio
async def test_call_get_success(mock_response):
    """Test successful GET request."""
    client = AsyncClient()
    client.get = lambda *args, **kwargs: AsyncMock(return_value=mock_response())()

    response = await call_get(client, "http://test.com")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_call_get_error(mock_response):
    """Test GET request with error."""
    client = AsyncClient()
    client.get = lambda *args, **kwargs: AsyncMock(return_value=mock_response(404))()

    with pytest.raises(ToolError) as exc:
        await call_get(client, "http://test.com")
    assert "Resource not found" in str(exc.value)


@pytest.mark.asyncio
async def test_call_post_success(mock_response):
    """Test successful POST request."""
    client = AsyncClient()
    response = mock_response()
    response.json = lambda: {"test": "data"}
    client.post = lambda *args, **kwargs: AsyncMock(return_value=response)()

    response = await call_post(client, "http://test.com", json={"test": "data"})
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_call_post_error(mock_response):
    """Test POST request with error."""
    client = AsyncClient()
    response = mock_response(500)
    response.json = lambda: {"test": "error"}

    client.post = lambda *args, **kwargs: AsyncMock(return_value=response)()

    with pytest.raises(ToolError) as exc:
        await call_post(client, "http://test.com", json={"test": "data"})
    assert "Internal server error" in str(exc.value)


@pytest.mark.asyncio
async def test_call_put_success(mock_response):
    """Test successful PUT request."""
    client = AsyncClient()
    client.put = lambda *args, **kwargs: AsyncMock(return_value=mock_response())()

    response = await call_put(client, "http://test.com", json={"test": "data"})
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_call_put_error(mock_response):
    """Test PUT request with error."""
    client = AsyncClient()
    client.put = lambda *args, **kwargs: AsyncMock(return_value=mock_response(400))()

    with pytest.raises(ToolError) as exc:
        await call_put(client, "http://test.com", json={"test": "data"})
    assert "Invalid request" in str(exc.value)


@pytest.mark.asyncio
async def test_call_delete_success(mock_response):
    """Test successful DELETE request."""
    client = AsyncClient()
    client.delete = lambda *args, **kwargs: AsyncMock(return_value=mock_response())()

    response = await call_delete(client, "http://test.com")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_call_delete_error(mock_response):
    """Test DELETE request with error."""
    client = AsyncClient()
    client.delete = lambda *args, **kwargs: AsyncMock(return_value=mock_response(403))()

    with pytest.raises(ToolError) as exc:
        await call_delete(client, "http://test.com")
    assert "Access denied" in str(exc.value)


@pytest.mark.asyncio
async def test_call_get_with_params(mock_response):
    """Test GET request with query parameters."""
    client = AsyncClient()
    mock_get = AsyncMock(return_value=mock_response())
    client.get = mock_get

    params = {"key": "value", "test": "data"}
    await call_get(client, "http://test.com", params=params)

    mock_get.assert_called_once()
    call_kwargs = mock_get.call_args[1]
    assert call_kwargs["params"] == params


@pytest.mark.asyncio
async def test_get_error_message():
    """Test the get_error_message function."""
    from basic_memory.mcp.tools.utils import get_error_message

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
    client = AsyncClient()
    response = mock_response()
    response.json = lambda: {"test": "data"}

    mock_post = AsyncMock(return_value=response)
    client.post = mock_post

    json_data = {"key": "value", "nested": {"test": "data"}}
    await call_post(client, "http://test.com", json=json_data)

    mock_post.assert_called_once()
    call_kwargs = mock_post.call_args[1]
    assert call_kwargs["json"] == json_data
