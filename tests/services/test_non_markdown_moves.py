"""Non-Markdown moves preserve file bytes and never invent note identity."""

from hashlib import sha256

import pytest

from basic_memory import db
from basic_memory.config import BasicMemoryConfig, ProjectConfig
from basic_memory.models import Entity, Project
from basic_memory.services.entity_service import EntityService


@pytest.mark.asyncio
@pytest.mark.parametrize("directory_move", [False, True])
@pytest.mark.parametrize("update_permalinks", [False, True])
@pytest.mark.parametrize(
    ("filename", "content_type", "content"),
    [
        ("Dockerfile", "text/plain", b"FROM python:3.13\r\n\n"),
        ("report.pdf", "application/pdf", b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n\x00\xff\n%%EOF\n"),
    ],
)
async def test_non_markdown_move_preserves_bytes_and_identity(
    entity_service: EntityService,
    project_config: ProjectConfig,
    test_project: Project,
    filename: str,
    content_type: str,
    content: bytes,
    update_permalinks: bool,
    directory_move: bool,
) -> None:
    source = f"original/{filename}"
    destination = f"archive/{filename}"
    source_path = project_config.home / source
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(content)
    async with db.scoped_session(entity_service.session_maker) as session:
        entity = await entity_service.repository.add(
            session,
            Entity(
                title=filename,
                note_type="file",
                content_type=content_type,
                file_path=source,
                permalink=None,
                checksum=sha256(content).hexdigest(),
            ),
        )
    external_id = entity.external_id
    config = BasicMemoryConfig(update_permalinks_on_move=update_permalinks)

    if directory_move:
        result = await entity_service.move_directory(
            "original",
            "archive",
            project_config,
            config,
            project_external_id=test_project.external_id,
            read_cache=None,
        )
        assert result.failed_moves == 0, result.errors
        assert result.moved_files == [destination]
    else:
        await entity_service.move_entity(source, destination, project_config, config)

    assert not source_path.exists()
    assert (project_config.home / destination).read_bytes() == content
    async with db.scoped_session(entity_service.session_maker) as session:
        moved = await entity_service.repository.get_by_file_path(session, destination)
        assert moved is not None
        assert moved.external_id == external_id
        assert moved.permalink is None
        assert moved.content_type == content_type
        assert moved.checksum == sha256(content).hexdigest()
        assert await entity_service.repository.get_by_file_path(session, source) is None
