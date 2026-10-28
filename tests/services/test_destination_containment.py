"""Chosen project boundaries hold at import/materialization/move sinks (#1908)."""

import os
from pathlib import Path

import pytest

from basic_memory.importers.chatgpt_importer import ChatGPTImporter
from basic_memory.index.note_content_materialization import LocalNoteContentStorage
from basic_memory.markdown.entity_parser import EntityParser
from basic_memory.markdown.markdown_processor import MarkdownProcessor
from basic_memory.markdown.schemas import EntityFrontmatter, EntityMarkdown
from basic_memory.services.exceptions import FileOperationError
from basic_memory.services.file_service import FileService


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", ["import-write", "import-directory", "materialize", "move"])
@pytest.mark.parametrize("spelling", ["traversal", "absolute", "symlink"])
async def test_destinations_cannot_escape_project(tmp_path: Path, surface: str, spelling: str):
    if spelling == "symlink" and os.name == "nt":
        pytest.skip("Requires unprivileged directory symlinks")
    root = tmp_path / "project"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    processor = MarkdownProcessor(entity_parser=EntityParser(base_path=root))
    files = FileService(root, markdown_processor=processor)
    importer = ChatGPTImporter(root, processor, files)
    if spelling == "symlink":
        (root / "redirect").symlink_to(outside, target_is_directory=True)
        destination = "redirect/new/target.md"
    elif spelling == "absolute":
        destination = str(outside / "new" / "target.md")
    else:
        destination = "../outside/new/target.md"
    original = root / "source.bin"
    original.write_bytes(b"original")

    with pytest.raises((ValueError, FileOperationError), match="outside.*project"):
        if surface == "import-directory":
            await importer.ensure_folder_exists(str(Path(destination).parent))
        elif surface == "import-write":
            await importer.write_entity(
                EntityMarkdown(
                    frontmatter=EntityFrontmatter(metadata={"title": "Import", "type": "note"}),
                    content="imported private content",
                ),
                destination,
            )
        elif surface == "materialize":
            await LocalNoteContentStorage(files).write_file(destination, "accepted private content")
        else:
            await files.move_file("source.bin", destination)

    assert not (outside / "new").exists()
    assert original.read_bytes() == b"original"


def test_object_storage_paths_are_relative_keys_not_host_paths(tmp_path: Path):
    files = FileService(tmp_path, local_filesystem=False)
    files.require_project_path("notes/valid.md")
    for path in ("../outside.md", "/absolute.md", "C:\\outside.md"):
        with pytest.raises(FileOperationError, match="outside.*project"):
            files.require_project_path(path)


def test_absolute_paths_inside_local_root_remain_supported(tmp_path: Path):
    files = FileService(tmp_path)
    files.require_project_path(tmp_path / "folder" / "note.md")


@pytest.mark.asyncio
async def test_guarded_move_preserves_literal_tildes(tmp_path: Path):
    source = tmp_path / "~source.pdf"
    source.write_bytes(b"file content")
    await FileService(tmp_path).move_file("~source.pdf", "notes/~draft.pdf")
    assert not source.exists()
    assert (tmp_path / "notes/~draft.pdf").read_bytes() == b"file content"
