"""Emacs scratch files must never enter local note reconciliation."""

from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from watchfiles import Change

from basic_memory import db
from basic_memory.config import BasicMemoryConfig, ProjectConfig
from basic_memory.index.filesystem import local_relative_path_is_filtered
from basic_memory.index.local_project import LocalProjectIndexRunner
from basic_memory.index.local_runtime import LocalWatchEventIndexRuntimeFactory
from basic_memory.index.watch_service import WatchService
from basic_memory.models import Project
from basic_memory.repository import EntityRepository, ProjectRepository
from basic_memory.repository.note_content_repository import NoteContentRepository


@pytest.mark.parametrize(
    ("path", "filtered"),
    [
        ("#note.md#", True),
        ("notes/#note.markdown#", True),
        ("notes/#draft.txt#", True),
        ("notes/#note.md", False),
        ("notes/note#.md", False),
        ("notes/note.md#", False),
        ("notes/note.md.bak", False),
    ],
)
def test_emacs_autosave_path_filter(path: str, filtered: bool) -> None:
    assert local_relative_path_is_filtered(path) is filtered


@pytest.mark.parametrize("same_content", [True, False])
async def test_emacs_autosave_preserves_note_through_watch_and_full_index(
    app_config: BasicMemoryConfig,
    project_repository: ProjectRepository,
    session_maker: async_sessionmaker[AsyncSession],
    test_project: Project,
    project_config: ProjectConfig,
    entity_repository: EntityRepository,
    same_content: bool,
) -> None:
    note_content_repository = NoteContentRepository(project_id=test_project.id)
    note_path: Path = project_config.home / "notes/note.md"
    note_path.parent.mkdir(parents=True, exist_ok=True)
    note_path.write_text(
        "---\ntitle: Real note\npermalink: notes/note\n---\n"
        "# Real note\n\n- [fact] Preserve me\n- relates_to [[Other note]]\n",
        encoding="utf-8",
    )
    watcher = WatchService(
        app_config=app_config,
        project_repository=project_repository,
        session_maker=session_maker,
        event_index_runtime_factory=LocalWatchEventIndexRuntimeFactory(),
    )
    await watcher.handle_changes(test_project, {(Change.added, str(note_path))})
    original_bytes = note_path.read_bytes()
    async with db.scoped_session(session_maker) as session:
        original = await entity_repository.get_by_file_path(session, "notes/note.md")
        assert original is not None
        original_id = original.id
        original_external_id = original.external_id
        accepted = await note_content_repository.get_by_entity_id(session, original_id)
        assert accepted is not None
        accepted_bytes = accepted.markdown_content
        accepted_version = accepted.db_version

    scratch_path = note_path.with_name("#note.md#")
    scratch_path.write_bytes(original_bytes if same_content else b"Unsaved draft\n")
    for change in (Change.added, Change.modified):
        await watcher.handle_changes(test_project, {(change, str(scratch_path))})
        async with db.scoped_session(session_maker) as session:
            scratch = await entity_repository.get_by_file_path(session, "notes/#note.md#")
            assert scratch is None

    runner = LocalProjectIndexRunner(
        project_repository=project_repository,
        session_maker=session_maker,
    )
    await runner.index_project(test_project.id, force_full=True)
    async with db.scoped_session(session_maker) as session:
        assert await entity_repository.get_by_file_path(session, "notes/#note.md#") is None
    scratch_path.unlink()
    await watcher.handle_changes(test_project, {(Change.deleted, str(scratch_path))})

    async with db.scoped_session(session_maker) as session:
        note = await entity_repository.get_by_file_path(session, "notes/note.md")
        scratch = await entity_repository.get_by_file_path(session, "notes/#note.md#")
        accepted = await note_content_repository.get_by_entity_id(session, original_id)
        assert note is not None
        assert note.id == original_id
        assert note.external_id == original_external_id
        assert note.title == "Real note"
        assert note.is_markdown
        assert note.permalink == "notes/note"
        assert len(note.observations) == 1
        assert len(note.outgoing_relations) == 1
        assert accepted is not None
        assert accepted.markdown_content == accepted_bytes
        assert accepted.db_version == accepted_version
        assert scratch is None
    assert note_path.read_bytes() == original_bytes
    assert watcher.state.error_count == 0
