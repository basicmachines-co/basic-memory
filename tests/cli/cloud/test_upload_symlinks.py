"""Directory uploads must not disclose file symlink targets (#1898)."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI, Request, Response

from basic_memory.cli.commands.cloud.upload import upload_path


@pytest.mark.skipif(os.name == "nt", reason="Requires unprivileged symlink creation")
@pytest.mark.asyncio
@pytest.mark.parametrize("verbose", [False, True])
async def test_directory_upload_skips_symlinks(tmp_path: Path, verbose: bool):
    root = tmp_path / "project"
    root.mkdir()
    (root / "note.md").write_bytes(b"public note")
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.md"
    secret.write_bytes(b"private outside project")
    (root / "leaked.md").symlink_to(secret)
    (root / "alias.md").symlink_to(root / "note.md")
    (root / "linked-directory").symlink_to(outside, target_is_directory=True)

    # A real in-process HTTP application receives the uploader's requests.
    # Filesystem discovery, file reads, and HTTP serialization stay real.
    receiver = FastAPI()
    received: dict[str, bytes] = {}

    @receiver.put("/webdav/{project}/{path:path}")
    async def receive(project: str, path: str, request: Request) -> Response:
        assert project == "test"
        received[path] = await request.body()
        return Response(status_code=201)

    @asynccontextmanager
    async def client_factory() -> AsyncIterator[httpx.AsyncClient]:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=receiver), base_url="http://upload.test"
        ) as client:
            yield client

    assert await upload_path(
        root, "test", verbose=verbose, use_gitignore=False, client_cm_factory=client_factory
    )
    assert received == {"note.md": b"public note"}


@pytest.mark.skipif(os.name == "nt", reason="Requires unprivileged symlink creation")
@pytest.mark.asyncio
async def test_directory_upload_skips_broken_symlink(tmp_path: Path):
    (tmp_path / "note.md").write_bytes(b"note")
    (tmp_path / "broken.md").symlink_to(tmp_path / "missing.md")

    assert await upload_path(tmp_path, "test", dry_run=True)
