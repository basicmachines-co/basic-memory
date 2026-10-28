"""Configured project roots constrain moves as well as creation (#1896)."""

import os

import pytest

from basic_memory import config as config_module
from basic_memory.config import ConfigManager
from basic_memory.services.project_service import ProjectService


@pytest.mark.asyncio
@pytest.mark.parametrize("destination", ["sibling", "outside", "root", "symlink"])
async def test_project_move_rejects_root_escape(
    project_service: ProjectService,
    config_manager: ConfigManager,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
    destination: str,
):
    if destination == "symlink" and os.name == "nt":
        pytest.skip("Requires unprivileged symlink creation")
    home = tmp_path_factory.mktemp("move-root")
    root = home / "data"
    root.mkdir()
    outside = home / ("data-sibling" if destination == "sibling" else "outside")
    outside.mkdir()
    target = root if destination == "root" else outside
    if destination == "symlink":
        target = root / "redirect"
        target.symlink_to(outside, target_is_directory=True)
    monkeypatch.setenv("BASIC_MEMORY_PROJECT_ROOT", str(root))
    monkeypatch.setattr(config_module, "_CONFIG_CACHE", None)
    await project_service.add_project("moving-project", "ignored")
    before = config_manager.config_file.read_bytes()
    original = await project_service.get_project("moving-project")
    assert original is not None

    with pytest.raises(ValueError, match="BASIC_MEMORY_PROJECT_ROOT"):
        await project_service.move_project("moving-project", str(target))

    assert config_manager.config_file.read_bytes() == before
    after = await project_service.get_project("moving-project")
    assert after is not None
    assert after.path == original.path


@pytest.mark.asyncio
async def test_project_move_accepts_root_descendant(
    project_service: ProjectService,
    config_manager: ConfigManager,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
):
    root = tmp_path_factory.mktemp("move-root")
    monkeypatch.setenv("BASIC_MEMORY_PROJECT_ROOT", str(root))
    monkeypatch.setattr(config_module, "_CONFIG_CACHE", None)
    await project_service.add_project("moving-project", "ignored")
    destination = root / "relocated"

    await project_service.move_project("moving-project", str(destination))

    after = await project_service.get_project("moving-project")
    assert after is not None
    assert after.path == destination.resolve().as_posix()
    assert project_service.projects["moving-project"] == after.path
