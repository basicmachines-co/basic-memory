from contextlib import asynccontextmanager

import httpx
import pytest

from basic_memory.cli.commands.cloud.upload import upload_path


@pytest.mark.asyncio
async def test_upload_path_dry_run_respects_gitignore_and_bmignore(config_home, tmp_path, capsys):
    root = tmp_path / "proj"
    root.mkdir()

    # Create a .gitignore that ignores one file
    (root / ".gitignore").write_text("ignored.md\n", encoding="utf-8")

    # Create files
    (root / "keep.md").write_text("keep", encoding="utf-8")
    (root / "ignored.md").write_text("ignored", encoding="utf-8")

    ok = await upload_path(root, "proj", verbose=True, use_gitignore=True, dry_run=True)
    assert ok is True

    out = capsys.readouterr().out
    # Verbose mode prints ignored files in the scan phase, but they must not appear
    # in the final "would be uploaded" list.
    assert "[INCLUDE] keep.md" in out or "keep.md" in out
    assert "[IGNORED] ignored.md" in out
    assert "Files that would be uploaded:" in out
    assert "  keep.md (" in out
    assert "  ignored.md (" not in out


@pytest.mark.asyncio
async def test_upload_path_non_dry_puts_files_and_skips_archives(config_home, tmp_path):
    root = tmp_path / "proj"
    root.mkdir()

    (root / "keep.md").write_text("keep", encoding="utf-8")
    (root / "archive.zip").write_bytes(b"zipbytes")

    seen = {"puts": []}

    async def handler(request: httpx.Request) -> httpx.Response:
        # Expect PUT to the webdav path
        assert request.method == "PUT"
        seen["puts"].append(request.url.path)
        # Must have mtime header
        assert request.headers.get("x-oc-mtime")
        return httpx.Response(201, text="Created")

    transport = httpx.MockTransport(handler)

    @asynccontextmanager
    async def client_cm_factory():
        async with httpx.AsyncClient(
            transport=transport, base_url="https://cloud.example.test"
        ) as client:
            yield client

    ok = await upload_path(
        root,
        "proj",
        verbose=False,
        use_gitignore=False,
        dry_run=False,
        client_cm_factory=client_cm_factory,
    )
    assert ok is True

    # Only keep.md uploaded; archive skipped
    assert "/webdav/proj/keep.md" in seen["puts"]
    assert all("archive.zip" not in p for p in seen["puts"])
