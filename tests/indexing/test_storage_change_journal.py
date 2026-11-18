"""Storage-side note changes reach the project change journal.

A note created, edited, renamed or deleted straight in storage (a local editor,
WebDAV, an rclone bisync) reaches the database through the project index, not
through the API. Each such change must still leave one row in the project change
journal (accepted_project_note_change), written already materialized. Otherwise
journal consumers such as the wiki projector and the project changes feed never
hear about it, or wait on it forever.

These tests drive the real local project indexer against files in a temporary
project directory, with no API calls.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.config import BasicMemoryConfig, ConfigManager
from basic_memory.index.local_project import LocalProjectIndexRunner
from basic_memory.index.local_wiki_projection import (
    LocalWikiState,
    apply_local_wiki_projection,
    inspect_local_wiki_projection,
)
from basic_memory.okf.export import recorded_history
from basic_memory.okf.render import ExportSnapshot, render_bundle
from basic_memory.runtime.project_partition import ACCEPTED_STORAGE_CHANGE_SOURCE
from basic_memory.models import AcceptedProjectNoteChange, NoteContent, Project
from basic_memory.repository.entity_repository import EntityRepository
from basic_memory.repository.note_content_repository import NoteContentRepository
from basic_memory.repository.project_repository import ProjectRepository

NOTE_PATH = "notes/storage-note.md"
RENAMED_PATH = "archive/storage-note.md"


def note_markdown(relative_path: str, body: str) -> str:
    # Complete frontmatter, so indexing never rewrites the file and every journal
    # row below comes from the storage change the test made.
    permalink = relative_path.removesuffix(".md")
    return (
        "---\n"
        f"title: {permalink.rsplit('/', 1)[-1]}\n"
        "type: note\n"
        f"permalink: {permalink}\n"
        "---\n"
        "\n"
        f"# Note\n\n{body}\n"
    )


def write_note(project_root: Path, relative_path: str, body: str) -> None:
    path = project_root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    # Bytes, so the checksum is the same on every platform.
    path.write_bytes(note_markdown(relative_path, body).encode("utf-8"))


async def index_project(
    project_repository: ProjectRepository,
    session_maker: async_sessionmaker[AsyncSession],
    project: Project,
) -> None:
    runner = LocalProjectIndexRunner(
        project_repository=project_repository,
        session_maker=session_maker,
    )
    await runner.index_project(project.id, force_full=True)


async def journal_after(
    session_maker: async_sessionmaker[AsyncSession],
    project: Project,
    position: int,
) -> list[AcceptedProjectNoteChange]:
    async with db.scoped_session(session_maker) as session:
        return list(
            await ProjectRepository().list_accepted_note_changes(
                session, project.id, after_position=position
            )
        )


async def current_note_content(
    session_maker: async_sessionmaker[AsyncSession],
    project: Project,
    file_path: str,
) -> NoteContent:
    async with db.scoped_session(session_maker) as session:
        entity = await EntityRepository(project_id=project.id).get_by_file_path(session, file_path)
        assert entity is not None
        note_content = await NoteContentRepository(project_id=project.id).get_by_entity_id(
            session, entity.id
        )
    assert note_content is not None
    return note_content


@pytest.mark.asyncio
async def test_storage_create_edit_and_delete_each_leave_one_journal_row(
    config_home: Path,
    project_repository: ProjectRepository,
    session_maker: async_sessionmaker[AsyncSession],
    test_project: Project,
) -> None:
    write_note(config_home, NOTE_PATH, "First version.")
    await index_project(project_repository, session_maker, test_project)
    created_content = await current_note_content(session_maker, test_project, NOTE_PATH)

    write_note(config_home, NOTE_PATH, "Edited in storage.")
    await index_project(project_repository, session_maker, test_project)
    updated_content = await current_note_content(session_maker, test_project, NOTE_PATH)

    (config_home / NOTE_PATH).unlink()
    await index_project(project_repository, session_maker, test_project)

    rows = await journal_after(session_maker, test_project, 0)
    assert [row.operation for row in rows] == ["created", "updated", "deleted"]
    assert [row.partition_position for row in rows] == sorted(
        {row.partition_position for row in rows}
    )
    assert len({row.note_external_id for row in rows}) == 1
    assert all(row.source == ACCEPTED_STORAGE_CHANGE_SOURCE for row in rows)
    assert all(row.file_path == NOTE_PATH for row in rows)
    assert all(row.materialized_at is not None for row in rows)
    created, updated, _deleted = rows
    assert (created.db_version, created.db_checksum) == (
        created_content.db_version,
        created_content.db_checksum,
    )
    assert (updated.db_version, updated.db_checksum) == (
        updated_content.db_version,
        updated_content.db_checksum,
    )
    assert (created.db_version, updated.db_version) == (1, 2)


@pytest.mark.asyncio
async def test_storage_rename_leaves_one_moved_row(
    app_config: BasicMemoryConfig,
    config_manager: ConfigManager,
    config_home: Path,
    project_repository: ProjectRepository,
    session_maker: async_sessionmaker[AsyncSession],
    test_project: Project,
) -> None:
    # Keep the file's bytes exactly as the rename left them. With
    # update_permalinks_on_move on, indexing also rewrites the moved note's
    # permalink frontmatter, so the rename is no longer content-neutral: on
    # Windows the rewrite is stored with CRLF line endings, and the same index
    # run promotes those bytes as a new version. The rewrite path is covered in
    # test_project_index_maintenance.py.
    app_config.update_permalinks_on_move = False
    config_manager.save_config(app_config)

    write_note(config_home, NOTE_PATH, "Renamed without edits.")
    await index_project(project_repository, session_maker, test_project)
    [created] = await journal_after(session_maker, test_project, 0)

    (config_home / RENAMED_PATH).parent.mkdir(parents=True)
    (config_home / NOTE_PATH).rename(config_home / RENAMED_PATH)
    await index_project(project_repository, session_maker, test_project)

    [moved] = await journal_after(session_maker, test_project, created.partition_position)
    assert (
        moved.operation,
        moved.note_external_id,
        moved.file_path,
        moved.previous_file_path,
        moved.source,
    ) == (
        "moved",
        created.note_external_id,
        RENAMED_PATH,
        NOTE_PATH,
        ACCEPTED_STORAGE_CHANGE_SOURCE,
    )
    assert (moved.db_version, moved.db_checksum) == (created.db_version, created.db_checksum)
    assert moved.materialized_at is not None


@pytest.mark.asyncio
async def test_wiki_projects_after_storage_only_changes(
    config_home: Path,
    project_repository: ProjectRepository,
    session_maker: async_sessionmaker[AsyncSession],
    test_project: Project,
) -> None:
    """Storage rows are settled when written, so they never defer the wiki projector."""
    write_note(config_home, NOTE_PATH, "First version.")
    write_note(config_home, "notes/gone.md", "Deleted later.")
    await index_project(project_repository, session_maker, test_project)
    write_note(config_home, NOTE_PATH, "Edited in storage.")
    (config_home / "notes/gone.md").unlink()
    await index_project(project_repository, session_maker, test_project)
    (config_home / RENAMED_PATH).parent.mkdir(parents=True)
    (config_home / NOTE_PATH).rename(config_home / RENAMED_PATH)
    await index_project(project_repository, session_maker, test_project)

    rows = await journal_after(session_maker, test_project, 0)
    assert {row.operation for row in rows} == {"created", "updated", "deleted", "moved"}
    assert {row.source for row in rows} == {ACCEPTED_STORAGE_CHANGE_SOURCE}

    inspection = await inspect_local_wiki_projection(test_project, session_maker=session_maker)

    assert inspection.plan.result.pending_materialization == ()
    assert inspection.state is not LocalWikiState.partial
    assert inspection.plan.result.source_watermark == max(row.partition_position for row in rows)

    # A local rebuild writes the generated files, then indexes them like any other
    # storage change. Their journal rows are the projector's own output, so the
    # projection must stay current instead of chasing its own writes.
    await apply_local_wiki_projection(inspection, session_maker=session_maker)
    await index_project(project_repository, session_maker, test_project)
    generated_rows = await journal_after(
        session_maker, test_project, max(row.partition_position for row in rows)
    )
    assert generated_rows
    assert {Path(row.file_path).name for row in generated_rows} <= {"index.md", "log.md"}

    after_rebuild = await inspect_local_wiki_projection(test_project, session_maker=session_maker)

    assert after_rebuild.state is LocalWikiState.current
    assert after_rebuild.plan.writes == ()


@pytest.mark.asyncio
async def test_okf_history_keeps_storage_edits_and_omits_generated_wiki_pages(
    app_config: BasicMemoryConfig,
    config_home: Path,
    project_repository: ProjectRepository,
    session_maker: async_sessionmaker[AsyncSession],
    test_project: Project,
) -> None:
    """The exported log lists notes edited in storage, never the pages a local rebuild wrote.

    A local rebuild writes index.md and log.md to disk, and indexing journals them as
    storage rows. The OKF export must treat them as projector output, as the wiki
    planner does, or its log presents generated pages as note history.
    """
    write_note(config_home, NOTE_PATH, "First version.")
    await index_project(project_repository, session_maker, test_project)
    write_note(config_home, NOTE_PATH, "Edited in storage.")
    await index_project(project_repository, session_maker, test_project)
    inspection = await inspect_local_wiki_projection(test_project, session_maker=session_maker)
    await apply_local_wiki_projection(inspection, session_maker=session_maker)
    await index_project(project_repository, session_maker, test_project)

    rows = await journal_after(session_maker, test_project, 0)
    generated = [row for row in rows if Path(row.file_path).name in {"index.md", "log.md"}]
    assert generated
    assert {row.source for row in generated} == {ACCEPTED_STORAGE_CHANGE_SOURCE}

    history = await recorded_history(app_config, test_project.name, config_home.resolve())

    assert [(change.operation, change.path) for change in history] == [
        ("created", NOTE_PATH),
        ("updated", NOTE_PATH),
    ]
    [log] = [
        file
        for file in render_bundle(ExportSnapshot(test_project.name, (), history))
        if file.path == "log.md"
    ]
    entries = [line for line in log.content.decode("utf-8").splitlines() if line.startswith("- ")]
    assert entries == [
        f"- updated: [{NOTE_PATH}](/{NOTE_PATH})",
        f"- created: [{NOTE_PATH}](/{NOTE_PATH})",
    ]
