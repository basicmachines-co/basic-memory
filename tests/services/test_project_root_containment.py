"""Resolved project roots must use path ancestry, not text prefixes (#1891)."""

import os
from pathlib import Path

import pytest

from basic_memory import config as config_module
from basic_memory.config import ConfigManager
from basic_memory.services.project_service import ProjectService


@pytest.mark.skipif(os.name == "nt", reason="Requires unprivileged directory symlinks")
@pytest.mark.asyncio
@pytest.mark.parametrize("destination", ["data-sibling", "outside", "data"])
async def test_add_project_rejects_symlink_outside_own_directory(
    project_service: ProjectService,
    config_manager: ConfigManager,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
    destination: str,
):
    tmp_path = tmp_path_factory.mktemp("project-roots")
    root = tmp_path / "data"
    root.mkdir()
    target = tmp_path / destination
    target.mkdir(exist_ok=True)
    (root / "linked-project").symlink_to(target, target_is_directory=True)
    monkeypatch.setenv("BASIC_MEMORY_PROJECT_ROOT", str(root))
    monkeypatch.setattr(config_module, "_CONFIG_CACHE", None)
    before = config_manager.config_file.read_bytes()

    with pytest.raises(ValueError, match="All projects must be created under this directory"):
        await project_service.add_project("linked-project", "ignored")

    assert config_manager.config_file.read_bytes() == before
    assert await project_service.get_project("linked-project") is None


@pytest.mark.skipif(os.name == "nt", reason="Requires unprivileged directory symlinks")
@pytest.mark.asyncio
@pytest.mark.parametrize("linked", [False, True])
async def test_add_project_accepts_resolved_descendant(
    project_service: ProjectService,
    config_manager: ConfigManager,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
    linked: bool,
):
    tmp_path = tmp_path_factory.mktemp("project-roots")
    root = tmp_path / "data"
    root.mkdir()
    target = root / "actual-project"
    target.mkdir()
    if linked:
        (root / "new-project").symlink_to(target, target_is_directory=True)
    monkeypatch.setenv("BASIC_MEMORY_PROJECT_ROOT", str(root))
    monkeypatch.setattr(config_module, "_CONFIG_CACHE", None)

    await project_service.add_project("new-project", "ignored")

    project = await project_service.get_project("new-project")
    assert project is not None
    expected = target if linked else root / "new-project"
    assert Path(project.path) == expected.resolve()
    assert project_service.projects["new-project"] == expected.resolve().as_posix()
