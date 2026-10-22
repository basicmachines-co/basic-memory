"""Logical paths never consult the host filesystem; local roots still do."""

from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import AsyncClient, Timeout

from basic_memory.mcp.async_client import _build_asgi_client, local_project_path
from basic_memory.schemas.project_info import ProjectItem
from basic_memory.services.file_service import FileService
from basic_memory.utils import validate_project_path


@pytest.mark.parametrize("path", ["link/note.md", "missing/note.md", "", "notes/file..md"])
def test_remote_validation_never_resolves_host_paths(
    path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_resolve(*args: object, **kwargs: object) -> Path:
        pytest.fail("remote validation touched the host filesystem")

    monkeypatch.setattr(Path, "resolve", no_resolve)
    storage = FileService(Path("/logical-project"), local_filesystem=False)
    assert storage.local_project_path is None
    assert validate_project_path(path, storage.local_project_path)


@pytest.mark.parametrize(
    "path", ["../secret", "x/../../secret", "/etc/passwd", r"C:\secret", r"..\secret"]
)
@pytest.mark.parametrize("local", [False, True])
def test_traversal_rejected_in_both_storage_modes(path: str, local: bool, tmp_path: Path) -> None:
    assert not validate_project_path(path, tmp_path if local else None)


async def test_selected_client_controls_filesystem_validation(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "escape").symlink_to(outside, target_is_directory=True)
    (root / "contained").symlink_to(root / "notes", target_is_directory=True)
    project = ProjectItem(id=1, external_id="project-id", name="project", path=str(root))
    async with _build_asgi_client(FastAPI(), Timeout(1)) as local:
        assert not validate_project_path("escape/note.md", local_project_path(local, project))
        assert validate_project_path("contained/note.md", local_project_path(local, project))
    # Both remote HTTP and hosted factory ASGI clients lack local ownership.
    async with AsyncClient() as remote:
        assert local_project_path(remote, project) is None
        assert validate_project_path("escape/note.md", local_project_path(remote, project))
