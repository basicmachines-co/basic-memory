"""Legacy upload paths cannot redirect an authenticated PUT (#1905)."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI, Request, Response

from basic_memory.cli.commands.cloud.upload import upload_path


@pytest.mark.skipif(os.name == "nt", reason="Backslashes are literal filename characters on POSIX")
@pytest.mark.asyncio
@pytest.mark.parametrize("single_file", [False, True])
async def test_upload_refuses_traversing_backslash_name(tmp_path: Path, single_file: bool):
    source = tmp_path / "safe\\..\\..\\other-project\\leak.md"
    source.write_bytes(b"private note")
    received: list[str] = []
    receiver = FastAPI()

    @receiver.put("/{path:path}")
    async def receive(path: str, request: Request) -> Response:
        received.append(path)
        await request.body()
        return Response(status_code=201)

    @asynccontextmanager
    async def client_factory() -> AsyncIterator[httpx.AsyncClient]:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=receiver), base_url="http://upload.test"
        ) as client:
            yield client

    success = await upload_path(
        source if single_file else tmp_path,
        "authorized-project",
        use_gitignore=False,
        client_cm_factory=client_factory,
    )
    assert received == []
    assert not success
