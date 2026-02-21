"""Tests for cloud upload command routing behavior."""

from contextlib import asynccontextmanager

import httpx
from typer.testing import CliRunner

from basic_memory.cli.app import app

runner = CliRunner()


def test_cloud_upload_uses_control_plane_client(monkeypatch, tmp_path):
    """Upload command should use control-plane cloud client for WebDAV PUT operations."""
    import basic_memory.cli.commands.cloud.upload_command as upload_command

    upload_dir = tmp_path / "upload"
    upload_dir.mkdir()
    (upload_dir / "note.md").write_text("hello", encoding="utf-8")

    seen: dict[str, str] = {}

    async def fake_project_exists(_project_name: str) -> bool:
        return True

    @asynccontextmanager
    async def fake_get_client():
        async with httpx.AsyncClient(base_url="https://cloud.example.test") as client:
            yield client

    async def fake_upload_path(*args, **kwargs):
        client_cm_factory = kwargs.get("client_cm_factory")
        assert client_cm_factory is not None
        async with client_cm_factory() as client:
            seen["base_url"] = str(client.base_url).rstrip("/")
        return True

    monkeypatch.setattr(upload_command, "project_exists", fake_project_exists)
    monkeypatch.setattr(upload_command, "get_cloud_control_plane_client", fake_get_client)
    monkeypatch.setattr(upload_command, "upload_path", fake_upload_path)

    result = runner.invoke(
        app,
        [
            "cloud",
            "upload",
            str(upload_dir),
            "--project",
            "routing-test",
            "--no-sync",
        ],
    )

    assert result.exit_code == 0, result.output
    assert seen["base_url"] == "https://cloud.example.test"
