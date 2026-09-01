"""The valid-time version-skew guard in SearchClient (SPEC-82).

`SearchQuery` ignores unknown fields, which is normally a harmless forward-compatibility
choice. For a valid-time filter it is not: a server predating SPEC-82 accepts the request
and returns *unfiltered* results, which include exactly the undated sources the filter
was asked to exclude. The caller cannot tell the difference by looking at them.

The server therefore confirms explicitly that it ran the filter, and the client refuses a
response that does not carry that confirmation.
"""

from typing import Any
from unittest.mock import MagicMock

import pytest

from basic_memory.mcp.clients import SearchClient

# A response body from a server that knows nothing about valid time.
LEGACY_PAYLOAD: dict[str, Any] = {
    "results": [],
    "current_page": 1,
    "page_size": 10,
    "total": 0,
    "total_is_exact": True,
    "has_more": False,
}


def _stub_call_query(monkeypatch, payload: dict[str, Any]) -> None:
    mock_response = MagicMock()
    mock_response.json.return_value = payload

    async def mock_call_query(client, url, **kwargs):
        return mock_response

    monkeypatch.setattr("basic_memory.mcp.tools.utils.call_query", mock_call_query)


@pytest.mark.parametrize("field", ["valid_at", "valid_overlaps", "time_kind"])
@pytest.mark.asyncio
async def test_unconfirmed_valid_time_filter_is_refused(monkeypatch, field: str):
    """Every valid-time field triggers the check; none of them may pass unconfirmed."""
    _stub_call_query(monkeypatch, dict(LEGACY_PAYLOAD))
    client = SearchClient(MagicMock(), "proj-123")

    with pytest.raises(ValueError, match="did not apply the requested valid-time filter"):
        await client.search({"text": "cache", field: "effective"}, page=1, page_size=10)


@pytest.mark.asyncio
async def test_explicitly_false_confirmation_is_also_refused(monkeypatch):
    """A server that answers "no" is as unusable as one that answers nothing."""
    _stub_call_query(monkeypatch, dict(LEGACY_PAYLOAD, temporal_applied=False))
    client = SearchClient(MagicMock(), "proj-123")

    with pytest.raises(ValueError, match="did not apply the requested valid-time filter"):
        await client.search({"text": "cache", "valid_at": "2026-07-28"}, page=1, page_size=10)


@pytest.mark.asyncio
async def test_confirmed_valid_time_filter_is_accepted(monkeypatch):
    _stub_call_query(monkeypatch, dict(LEGACY_PAYLOAD, temporal_applied=True))
    client = SearchClient(MagicMock(), "proj-123")

    response = await client.search(
        {"text": "cache", "valid_at": "2026-07-28"}, page=1, page_size=10
    )

    assert response.temporal_applied is True


@pytest.mark.asyncio
async def test_search_without_a_valid_time_filter_is_unaffected(monkeypatch):
    """The guard must not touch ordinary searches against any server version."""
    _stub_call_query(monkeypatch, dict(LEGACY_PAYLOAD))
    client = SearchClient(MagicMock(), "proj-123")

    response = await client.search({"text": "cache"}, page=1, page_size=10)

    assert response.temporal_applied is None
    assert response.total == 0


@pytest.mark.asyncio
async def test_empty_valid_time_values_do_not_trigger_the_guard(monkeypatch):
    """Fields present but unset are not a request, so nothing needs confirming."""
    _stub_call_query(monkeypatch, dict(LEGACY_PAYLOAD))
    client = SearchClient(MagicMock(), "proj-123")

    response = await client.search(
        {"text": "cache", "valid_at": None, "valid_overlaps": None, "time_kind": None},
        page=1,
        page_size=10,
    )

    assert response.temporal_applied is None
