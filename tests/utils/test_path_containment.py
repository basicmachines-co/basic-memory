"""Shared resolved-root checks for project creation, moves, and file sinks."""

import os
from pathlib import Path

import pytest

from basic_memory.utils import resolve_path_within_root, valid_project_path_value
from basic_memory.services.file_service import FileService


def test_file_paths_treat_tildes_as_literal_characters(tmp_path: Path):
    FileService(tmp_path).require_project_path("draft~.md")
    FileService(tmp_path).require_project_path("~/note.md")


@pytest.mark.parametrize("absolute", [False, True])
def test_resolved_descendant_preserves_filename(tmp_path: Path, absolute: bool):
    root = tmp_path / "project"
    root.mkdir()
    path = Path("folder/ note.md ")
    result = resolve_path_within_root(root / path if absolute else path, root)
    assert result == root / path


def test_shared_root_requires_explicit_permission(tmp_path: Path):
    assert resolve_path_within_root(tmp_path, tmp_path) == tmp_path
    with pytest.raises(ValueError, match="outside"):
        resolve_path_within_root(tmp_path, tmp_path, allow_root=False)


@pytest.mark.parametrize("path", ["../project-sibling/note.md", "../outside/note.md"])
def test_rejects_outside_and_sibling_prefix(tmp_path: Path, path: str):
    with pytest.raises(ValueError, match="outside"):
        resolve_path_within_root(path, tmp_path / "project")


@pytest.mark.skipif(os.name == "nt", reason="Requires unprivileged symlink creation")
def test_shared_check_resolves_symlink_ancestors(tmp_path: Path):
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "project-sibling"
    outside.mkdir()
    (root / "redirect").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="outside"):
        resolve_path_within_root("redirect/note.md", root)


@pytest.mark.parametrize("path", [" note.md ", "cafe\u0301.md", "nested/note.md"])
def test_portable_validation_does_not_rewrite_filename(path: str):
    assert valid_project_path_value(path)


@pytest.mark.parametrize("path", ["\n", " \r ", "note\n.md", "\x00"])
def test_portable_validation_rejects_control_characters(path: str):
    assert not valid_project_path_value(path)
