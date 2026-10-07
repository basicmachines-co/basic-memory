"""A DB-first note's storage echo is current when the object holds the accepted content."""

from hashlib import sha256
from pathlib import Path
from typing import Literal

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.index.local_runtime import LocalStorageFileMetadataSource
from basic_memory.indexing.file_index_checking import (
    FileIndexChecker,
    RepositoryIndexedFileChecksumSource,
)
from basic_memory.indexing.file_index_planning import FileIndexDecisionStatus, FileIndexTarget
from basic_memory.markdown import EntityParser
from basic_memory.markdown.markdown_processor import MarkdownProcessor
from basic_memory.models import Entity, NoteContent, Project, RelationSearchRefresh
from basic_memory.repository import EntityRepository
from basic_memory.services import FileService

type EchoState = Literal["accepted", "first-write", "publication-pending", "other-bytes"]


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        pytest.param("accepted", FileIndexDecisionStatus.current, id="accepted-content"),
        # A brand-new note has no storage checksum until its first materialization settles.
        pytest.param("first-write", FileIndexDecisionStatus.current, id="first-write-echo"),
        pytest.param("publication-pending", FileIndexDecisionStatus.read, id="repair-still-reads"),
        pytest.param("other-bytes", FileIndexDecisionStatus.read, id="external-change-reads"),
    ],
)
async def test_storage_echo_before_the_storage_checksum_is_recorded(
    client: AsyncClient,
    test_project: Project,
    engine_factory: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
    state: EchoState,
    expected: FileIndexDecisionStatus,
) -> None:
    """The window between a materializer's file write and its recording the new checksum."""
    _, session_maker = engine_factory
    created = await client.post(
        f"/v2/projects/{test_project.external_id}/knowledge/write",
        json={"note": {"title": "Echo", "directory": "notes", "content": "Revision one"}},
    )
    assert created.json()["kind"] == "created", created.text
    file_path = "notes/Echo.md"
    home = Path(test_project.path)

    # Revision two is accepted and its bytes are in storage, but the entity still records
    # revision one's checksum: the materializer has written the file and not yet settled.
    async with db.scoped_session(session_maker) as session:
        note_content = await session.scalar(
            select(NoteContent).where(NoteContent.file_path == file_path)
        )
        assert note_content is not None
        revision_two = f"{note_content.markdown_content}\nRevision two\n"
        await session.execute(
            update(NoteContent)
            .where(NoteContent.entity_id == note_content.entity_id)
            .values(
                markdown_content=revision_two,
                db_checksum=sha256(revision_two.encode()).hexdigest(),
                db_version=note_content.db_version + 1,
            )
        )
        if state == "publication-pending":
            session.add(
                RelationSearchRefresh(
                    project_id=test_project.id,
                    entity_id=note_content.entity_id,
                    publication_generation=note_content.db_version + 1,
                )
            )
        if state == "first-write":
            await session.execute(
                update(Entity).where(Entity.id == note_content.entity_id).values(checksum=None)
            )
        entity_checksum = await session.scalar(
            select(Entity.checksum).where(Entity.id == note_content.entity_id)
        )
    stored = revision_two if state != "other-bytes" else "Someone else's edit\n"
    (home / file_path).write_bytes(stored.encode())
    assert entity_checksum != sha256(stored.encode()).hexdigest()

    entity_repository = EntityRepository(project_id=test_project.id)
    file_service = FileService(home, MarkdownProcessor(EntityParser(home)))
    checker = FileIndexChecker(
        indexed_checksum_source=RepositoryIndexedFileChecksumSource(
            session_maker=session_maker,
            entity_repository=entity_repository,
        ),
        current_checksum_source=LocalStorageFileMetadataSource(file_service),
    )
    plan = await checker.detect(
        (
            FileIndexTarget(
                path=file_path,
                observed_checksum=sha256(stored.encode()).hexdigest(),
            ),
        )
    )

    status = (
        FileIndexDecisionStatus.read
        if file_path in plan.paths_to_read
        else next(decision.status for decision in plan.decisions if decision.path == file_path)
    )
    assert status is expected
