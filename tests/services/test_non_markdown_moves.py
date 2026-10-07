"""Regular-file moves preserve bytes and identity; Markdown notes move elsewhere."""

from hashlib import sha256
from pathlib import Path

import pytest

from basic_memory import db
from basic_memory.config import ProjectConfig
from basic_memory.models import Entity, Project
from basic_memory.schemas import Entity as EntitySchema
from basic_memory.services.entity_service import EntityService
from basic_memory.services.exceptions import EntityNotFoundError

PDF_BYTES = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n\x00\xff\n%%EOF\n"


async def add_regular_file(
    entity_service: EntityService,
    project_config: ProjectConfig,
    *,
    file_path: str,
    content_type: str,
    content: bytes,
) -> Entity:
    """Write a regular file and the entity row indexing it."""
    stored = project_config.home / file_path
    stored.parent.mkdir(parents=True, exist_ok=True)
    stored.write_bytes(content)
    async with db.scoped_session(entity_service.session_maker) as session:
        return await entity_service.repository.add(
            session,
            Entity(
                title=Path(file_path).name,
                note_type="file",
                content_type=content_type,
                file_path=file_path,
                permalink=None,
                checksum=sha256(content).hexdigest(),
            ),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("filename", "content_type", "content"),
    [
        ("Dockerfile", "text/plain", b"FROM python:3.13\r\n\n"),
        ("report.pdf", "application/pdf", PDF_BYTES),
    ],
)
async def test_regular_file_move_preserves_bytes_and_identity(
    entity_service: EntityService,
    project_config: ProjectConfig,
    test_project: Project,
    filename: str,
    content_type: str,
    content: bytes,
) -> None:
    source = f"original/{filename}"
    # The destination folder does not exist yet; the move creates it.
    destination = f"archive/nested/{filename}"
    entity = await add_regular_file(
        entity_service,
        project_config,
        file_path=source,
        content_type=content_type,
        content=content,
    )

    await entity_service.move_entity(source, destination)

    assert not (project_config.home / source).exists()
    assert (project_config.home / destination).read_bytes() == content
    async with db.scoped_session(entity_service.session_maker) as session:
        moved = await entity_service.repository.get_by_file_path(session, destination)
        assert moved is not None
        assert moved.external_id == entity.external_id
        assert moved.permalink is None
        assert moved.content_type == content_type
        assert moved.checksum == sha256(content).hexdigest()
        assert await entity_service.repository.get_by_file_path(session, source) is None


@pytest.mark.asyncio
async def test_markdown_notes_are_refused(
    entity_service: EntityService,
    test_project: Project,
) -> None:
    """A note's accepted content must move with it, which only the accepted move does."""
    entity, _ = await entity_service.create_or_update_entity(
        EntitySchema(title="Plan", directory="notes", note_type="note", content="Plan")
    )

    with pytest.raises(ValueError, match="moves through the accepted note move"):
        await entity_service.move_entity(entity.file_path, "archive/Plan.md")


@pytest.mark.asyncio
async def test_an_unknown_file_is_not_found(entity_service: EntityService) -> None:
    with pytest.raises(EntityNotFoundError):
        await entity_service.move_entity("missing/report.pdf", "archive/report.pdf")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("setup", "destination", "message"),
    [
        pytest.param("source-missing", "archive/report.pdf", "Source file not found", id="source"),
        pytest.param("destination-taken", "archive/report.pdf", "Destination already", id="dest"),
        pytest.param("none", "../outside.pdf", "", id="escapes-project"),
    ],
)
async def test_a_refused_move_leaves_the_file_in_place(
    entity_service: EntityService,
    project_config: ProjectConfig,
    test_project: Project,
    setup: str,
    destination: str,
    message: str,
) -> None:
    source = "original/report.pdf"
    await add_regular_file(
        entity_service,
        project_config,
        file_path=source,
        content_type="application/pdf",
        content=PDF_BYTES,
    )
    if setup == "source-missing":
        (project_config.home / source).unlink()
    elif setup == "destination-taken":
        (project_config.home / "archive").mkdir()
        (project_config.home / destination).write_bytes(b"someone else's file")

    with pytest.raises(ValueError, match=message):
        await entity_service.move_entity(source, destination)

    async with db.scoped_session(entity_service.session_maker) as session:
        assert await entity_service.repository.get_by_file_path(session, source) is not None


@pytest.mark.asyncio
async def test_a_failed_index_update_puts_the_file_back(
    entity_service: EntityService,
    project_config: ProjectConfig,
    test_project: Project,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = "original/report.pdf"
    await add_regular_file(
        entity_service,
        project_config,
        file_path=source,
        content_type="application/pdf",
        content=PDF_BYTES,
    )

    # Injects the failure: the database refuses the row update after the bytes moved.
    async def refuse_update(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(entity_service.repository, "update", refuse_update)

    with pytest.raises(ValueError, match="Failed to update entity"):
        await entity_service.move_entity(source, "archive/report.pdf")

    assert (project_config.home / source).read_bytes() == PDF_BYTES
    assert not (project_config.home / "archive/report.pdf").exists()
