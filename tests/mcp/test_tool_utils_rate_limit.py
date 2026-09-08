"""Rate-limit retry coverage for the shared MCP HTTP helpers."""

import asyncio
import random
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import datetime, timezone
from email.utils import format_datetime
from io import BytesIO
from typing import Any

import httpx
import pytest
from fastmcp.exceptions import ToolError

from basic_memory.mcp.tools import utils as utils_module
from basic_memory.mcp.tools.utils import (
    call_delete,
    call_get,
    call_patch,
    call_post,
    call_put,
    call_query,
)

type CallHelper = Callable[..., Awaitable[httpx.Response]]

_CALL_HELPERS: tuple[tuple[CallHelper, dict[str, Any]], ...] = (
    (call_get, {}),
    (call_put, {"json": {"value": "same request"}}),
    (call_patch, {"json": {"value": "same request"}}),
    (call_post, {"json": {"value": "same request"}}),
    (call_query, {"json": {"value": "same request"}}),
    (call_delete, {}),
)


@pytest.mark.asyncio
@pytest.mark.parametrize("call_helper,request_kwargs", _CALL_HELPERS)
async def test_call_helpers_retry_replayable_requests_after_rate_limit(
    monkeypatch: pytest.MonkeyPatch,
    call_helper: CallHelper,
    request_kwargs: dict[str, Any],
) -> None:
    """The Basic Memory gateway rejects 429 requests before processing them (#1378)."""
    calls = 0
    sleep_delays: list[float] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                429,
                headers={"Retry-After": "0"},
                json={"detail": "gateway rate limit"},
            )
        return httpx.Response(200, json={"ok": True})

    async def record_sleep(delay: float) -> None:
        sleep_delays.append(delay)

    monkeypatch.setattr(asyncio, "sleep", record_sleep)
    monkeypatch.setattr(random, "uniform", lambda _start, _end: 0.125)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://cloud.invalid") as client:
        response = await call_helper(client, "/v2/resource", **request_kwargs)

    assert response.json() == {"ok": True}
    assert calls == 2
    assert sleep_delays == [0.125]


@pytest.mark.asyncio
async def test_call_get_accepts_http_date_retry_after(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0
    sleep_delays: list[float] = []
    now = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)
    retry_at = datetime(2026, 9, 8, 12, 0, 2, tzinfo=timezone.utc)

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                429, headers={"Retry-After": format_datetime(retry_at, usegmt=True)}
            )
        return httpx.Response(200)

    async def record_sleep(delay: float) -> None:
        sleep_delays.append(delay)

    monkeypatch.setattr(utils_module, "_utc_now", lambda: now)
    monkeypatch.setattr(asyncio, "sleep", record_sleep)
    monkeypatch.setattr(random, "uniform", lambda _start, _end: 0.125)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://cloud.invalid") as client:
        response = await call_get(client, "/v2/resource")

    assert response.status_code == 200
    assert calls == 2
    assert sleep_delays == [2.125]


@pytest.mark.asyncio
@pytest.mark.parametrize("retry_after", [None, "later", "-1", "1.5"])
async def test_call_get_does_not_retry_without_valid_retry_after(
    monkeypatch: pytest.MonkeyPatch,
    retry_after: str | None,
) -> None:
    calls = 0
    sleep_delays: list[float] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        headers = {"Retry-After": retry_after} if retry_after is not None else {}
        return httpx.Response(429, headers=headers, json={"detail": "quota detail"})

    async def record_sleep(delay: float) -> None:
        sleep_delays.append(delay)

    monkeypatch.setattr(asyncio, "sleep", record_sleep)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://cloud.invalid") as client:
        with pytest.raises(ToolError) as exc:
            await call_get(client, "/v2/resource")

    assert calls == 1
    assert sleep_delays == []
    assert "quota detail" in str(exc.value)
    assert "Retry-After is missing or invalid" in str(exc.value)


@pytest.mark.asyncio
async def test_call_get_stops_after_three_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0
    sleep_delays: list[float] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            429,
            headers={"Retry-After": "0"},
            json={"detail": "quota detail"},
        )

    async def record_sleep(delay: float) -> None:
        sleep_delays.append(delay)

    monkeypatch.setattr(asyncio, "sleep", record_sleep)
    monkeypatch.setattr(random, "uniform", lambda _start, _end: 0.125)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://cloud.invalid") as client:
        with pytest.raises(ToolError) as exc:
            await call_get(client, "/v2/resource")

    assert calls == 3
    assert sleep_delays == [0.125, 0.125]
    assert "quota detail" in str(exc.value)
    assert "exhausted after 3 attempts" in str(exc.value)
    assert "Server Retry-After: 0" in str(exc.value)


@pytest.mark.asyncio
async def test_call_get_reports_the_final_rate_limit_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                429,
                headers={"Retry-After": "0"},
                json={"detail": "first boundary"},
            )
        return httpx.Response(429, json={"detail": "final boundary"})

    async def no_wait(_delay: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", no_wait)
    monkeypatch.setattr(random, "uniform", lambda _start, _end: 0.125)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://cloud.invalid") as client:
        with pytest.raises(ToolError) as exc:
            await call_get(client, "/v2/resource")

    assert calls == 2
    assert "final boundary" in str(exc.value)
    assert "first boundary" not in str(exc.value)
    assert "Retry-After is missing or invalid" in str(exc.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("retry_after", ["31", "9" * 400, "9" * 5000])
async def test_call_get_does_not_truncate_retry_after_to_wait_budget(
    monkeypatch: pytest.MonkeyPatch,
    retry_after: str,
) -> None:
    calls = 0
    sleep_delays: list[float] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            429,
            headers={"Retry-After": retry_after},
            json={"detail": "quota detail"},
        )

    async def record_sleep(delay: float) -> None:
        sleep_delays.append(delay)

    monkeypatch.setattr(asyncio, "sleep", record_sleep)
    monkeypatch.setattr(random, "uniform", lambda _start, _end: 0.125)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://cloud.invalid") as client:
        with pytest.raises(ToolError) as exc:
            await call_get(client, "/v2/resource")

    assert calls == 1
    assert sleep_delays == []
    assert "quota detail" in str(exc.value)
    assert "maximum cumulative wait 30s" in str(exc.value)
    assert f"Server Retry-After: {retry_after}" in str(exc.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("retry_after", "expected_delay"),
    [("0" * 5000, 0.125), ("0" * 5000 + "1", 1.125)],
)
async def test_call_get_accepts_long_zero_padded_retry_after(
    monkeypatch: pytest.MonkeyPatch,
    retry_after: str,
    expected_delay: float,
) -> None:
    calls = 0
    sleep_delays: list[float] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": retry_after})
        return httpx.Response(200)

    async def record_sleep(delay: float) -> None:
        sleep_delays.append(delay)

    monkeypatch.setattr(asyncio, "sleep", record_sleep)
    monkeypatch.setattr(random, "uniform", lambda _start, _end: 0.125)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://cloud.invalid") as client:
        response = await call_get(client, "/v2/resource")

    assert response.status_code == 200
    assert calls == 2
    assert sleep_delays == [expected_delay]


@pytest.mark.asyncio
async def test_call_get_limits_cumulative_retry_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0
    sleep_delays: list[float] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            429,
            headers={"Retry-After": "15"},
            json={"detail": "quota detail"},
        )

    async def record_sleep(delay: float) -> None:
        sleep_delays.append(delay)

    monkeypatch.setattr(asyncio, "sleep", record_sleep)
    monkeypatch.setattr(random, "uniform", lambda _start, _end: 0.125)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://cloud.invalid") as client:
        with pytest.raises(ToolError) as exc:
            await call_get(client, "/v2/resource")

    assert calls == 2
    assert sleep_delays == [15.125]
    assert "maximum cumulative wait 30s" in str(exc.value)
    assert "Server Retry-After: 15" in str(exc.value)


@pytest.mark.asyncio
async def test_call_get_does_not_retry_transport_failure_after_rate_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    sleep_delays: list[float] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        raise httpx.ConnectError("connection refused", request=request)

    async def record_sleep(delay: float) -> None:
        sleep_delays.append(delay)

    monkeypatch.setattr(asyncio, "sleep", record_sleep)
    monkeypatch.setattr(random, "uniform", lambda _start, _end: 0.125)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://cloud.invalid") as client:
        with pytest.raises(ToolError, match="Connection failed.*connection refused"):
            await call_get(client, "/v2/resource")

    assert calls == 2
    assert sleep_delays == [0.125]


@pytest.mark.asyncio
async def test_call_post_does_not_retry_async_request_content() -> None:
    calls = 0

    async def one_shot_content() -> AsyncIterator[bytes]:
        yield b"payload"

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert await request.aread() == b"payload"
        return httpx.Response(
            429,
            headers={"Retry-After": "1"},
            json={"detail": "quota detail"},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://cloud.invalid") as client:
        with pytest.raises(ToolError) as exc:
            await call_post(client, "/v2/resource", content=one_shot_content())

    assert calls == 1
    assert "quota detail" in str(exc.value)
    assert "request body cannot be safely replayed" in str(exc.value)
    assert "Server Retry-After: 1" in str(exc.value)


@pytest.mark.asyncio
async def test_call_post_does_not_retry_file_uploads() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert b"payload" in await request.aread()
        return httpx.Response(
            429,
            headers={"Retry-After": "1"},
            json={"detail": "quota detail"},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://cloud.invalid") as client:
        with pytest.raises(ToolError) as exc:
            await call_post(
                client,
                "/v2/resource",
                files={"file": ("payload.txt", BytesIO(b"payload"))},
            )

    assert calls == 1
    assert "quota detail" in str(exc.value)
    assert "request body cannot be safely replayed" in str(exc.value)
    assert "Server Retry-After: 1" in str(exc.value)
