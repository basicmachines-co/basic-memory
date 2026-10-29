"""Every importer-created directory goes through the project guard (#1908)."""

import os
from pathlib import Path

import pytest

from basic_memory.importers.claude_projects_importer import ClaudeProjectsImporter
from basic_memory.importers.memory_json_importer import MemoryJsonImporter
from basic_memory.markdown.entity_parser import EntityParser
from basic_memory.markdown.markdown_processor import MarkdownProcessor
from basic_memory.services.file_service import FileService


@pytest.mark.asyncio
async def test_memory_json_entity_type_cannot_create_outside_directory(tmp_path: Path):
    root = tmp_path / "project"
    root.mkdir()
    processor = MarkdownProcessor(entity_parser=EntityParser(base_path=root))
    files = FileService(root, markdown_processor=processor)
    importer = MemoryJsonImporter(root, processor, files)
    result = await importer.import_data(
        [{"type": "entity", "name": "x", "entityType": "../outside/new", "observations": []}]
    )
    assert not (tmp_path / "outside").exists()
    assert not result.success


@pytest.mark.skipif(os.name == "nt", reason="Requires unprivileged directory symlinks")
@pytest.mark.asyncio
async def test_claude_project_directory_cannot_follow_outside_symlink(tmp_path: Path):
    root = tmp_path / "project"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "redirect").symlink_to(outside, target_is_directory=True)
    processor = MarkdownProcessor(entity_parser=EntityParser(base_path=root))
    files = FileService(root, markdown_processor=processor)
    importer = ClaudeProjectsImporter(root, processor, files)
    result = await importer.import_data([{"name": "redirect", "docs": []}], "")
    assert not (outside / "docs").exists()
    assert not result.success
