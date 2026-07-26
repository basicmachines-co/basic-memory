"""
Integration tests for move_note MCP tool.

Tests the complete move note workflow: MCP client -> MCP server -> FastAPI -> database -> file system
"""

import json
from hashlib import sha256

import pytest
from fastmcp import Client
from httpx import AsyncClient

from basic_memory import db
from basic_memory.index.local_project import (
    LocalProjectIndexRuntimeFactory,
    run_local_project_index_for_project,
)
from basic_memory.index.note_content_materialization import (
    InlineNoteFileDeleteEnqueuer,
    recover_move_vacates,
)
from basic_memory.repository.entity_repository import EntityRepository
from basic_memory.repository.note_content_repository import NoteContentRepository
from basic_memory.repository.note_file_vacate_repository import NoteFileVacateRepository
from basic_memory.runtime.cleanup import RuntimeNoteFileDeleteJobRequest
from basic_memory.services.file_service import FileService


@pytest.mark.asyncio
async def test_move_note_basic_operation(mcp_server, app, test_project):
    """Test basic move note operation to a new folder."""

    async with Client(mcp_server) as client:
        # Create a note to move
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Move Test Note",
                "directory": "source",
                "content": "# Move Test Note\n\nThis note will be moved to a new location.",
                "tags": "test,move",
            },
        )

        # Move the note to a new location
        move_result = await client.call_tool(
            "move_note",
            {
                "project": test_project.name,
                "identifier": "Move Test Note",
                "destination_path": "destination/moved-note.md",
            },
        )

        # Should return successful move message
        assert len(move_result.content) == 1
        move_text = move_result.content[0].text
        assert "✅ Note moved successfully" in move_text
        assert "Move Test Note" in move_text
        assert "destination/moved-note.md" in move_text
        assert "📊 Database and search index updated" in move_text

        # Verify the note can be read from its new location
        read_result = await client.call_tool(
            "read_note",
            {
                "project": test_project.name,
                "identifier": "destination/moved-note.md",
            },
        )

        content = read_result.content[0].text
        assert "This note will be moved to a new location" in content

        # Verify the original location no longer works
        read_original = await client.call_tool(
            "read_note",
            {
                "project": test_project.name,
                "identifier": "source/move-test-note.md",
            },
        )

        # Should return "Note Not Found" message
        assert "Note Not Found" in read_original.content[0].text


@pytest.mark.parametrize("force_full", [False, True], ids=["observed", "force-full"])
@pytest.mark.asyncio
async def test_move_note_lingering_source_is_not_reindexed(
    mcp_server,
    app,
    test_project,
    project_config,
    engine_factory,
    monkeypatch,
    force_full: bool,
):
    """A move-vacated source is skipped even across the unpublished-checksum race."""
    del app
    source_relative = "source/Move Orphan Race.md"
    destination_relative = "archive/move-orphan-race.md"
    source_path = project_config.home / source_relative
    _, session_maker = engine_factory
    entity_repository = EntityRepository(project_id=test_project.id)
    note_content_repository = NoteContentRepository(project_id=test_project.id)
    vacate_repository = NoteFileVacateRepository(project_id=test_project.id)

    async with Client(mcp_server) as client:
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Move Orphan Race",
                "directory": "source",
                "content": "# Move Orphan Race\n\nThis source object must never become a ghost.",
            },
        )

        source_checksum = sha256(source_path.read_bytes()).hexdigest()
        async with db.scoped_session(session_maker) as session:
            entity = await entity_repository.get_by_file_path(session, source_relative)
            assert entity is not None
            note_content = await note_content_repository.get_by_entity_id(session, entity.id)
            assert note_content is not None
            assert note_content.db_checksum == source_checksum
            moved_entity_id = entity.id

            # Reproduce a write that reached storage before its publisher recorded checksums.
            entity.checksum = None
            note_content.file_checksum = None

        async def leave_source_cleanup_pending(
            self: InlineNoteFileDeleteEnqueuer,
            request: RuntimeNoteFileDeleteJobRequest,
        ) -> None:
            del self, request

        # The enqueue boundary is the only failure injection; move, materialization, repositories,
        # storage, and the subsequent project index all run through their real integration paths.
        monkeypatch.setattr(
            InlineNoteFileDeleteEnqueuer,
            "enqueue_note_file_delete",
            leave_source_cleanup_pending,
        )

        move_result = await client.call_tool(
            "move_note",
            {
                "project": test_project.name,
                "identifier": "Move Orphan Race",
                "destination_path": destination_relative,
            },
        )
        assert "✅ Note moved successfully" in move_result.content[0].text
        assert source_path.exists()

        async with db.scoped_session(session_maker) as session:
            markers = await vacate_repository.load_vacate_markers(
                session,
                [source_relative],
            )
        assert markers[source_relative].file_checksum == source_checksum

        await run_local_project_index_for_project(
            test_project,
            runtime_factory=LocalProjectIndexRuntimeFactory(batch_size=10),
            force_full=force_full,
        )

        async with db.scoped_session(session_maker) as session:
            ghost = await entity_repository.get_by_file_path(session, source_relative)
            moved = await entity_repository.get_by_file_path(session, destination_relative)

        assert ghost is None
        assert moved is not None
        assert moved.id == moved_entity_id


@pytest.mark.parametrize(
    "move_back_before_recovery",
    [False, True],
    ids=["cleanup-current-source", "cancel-stale-snapshot"],
)
@pytest.mark.asyncio
async def test_startup_recovery_retries_lost_move_source_cleanup(
    mcp_server,
    app,
    test_project,
    project_config,
    engine_factory,
    monkeypatch,
    move_back_before_recovery: bool,
):
    """A synchronized move converges after its in-memory cleanup enqueue is lost."""
    del app
    source_relative = "source/Lost Cleanup.md"
    destination_relative = "archive/lost-cleanup.md"
    source_path = project_config.home / source_relative
    destination_path = project_config.home / destination_relative
    _, session_maker = engine_factory
    entity_repository = EntityRepository(project_id=test_project.id)
    note_content_repository = NoteContentRepository(project_id=test_project.id)
    vacate_repository = NoteFileVacateRepository(project_id=test_project.id)
    original_cleanup = InlineNoteFileDeleteEnqueuer.enqueue_note_file_delete

    async def lose_source_cleanup(
        self: InlineNoteFileDeleteEnqueuer,
        request: RuntimeNoteFileDeleteJobRequest,
    ) -> None:
        del self, request

    monkeypatch.setattr(
        InlineNoteFileDeleteEnqueuer,
        "enqueue_note_file_delete",
        lose_source_cleanup,
    )

    async with Client(mcp_server) as client:
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Lost Cleanup",
                "directory": "source",
                "content": "# Lost Cleanup\n\nStartup must retry this source deletion.",
            },
        )
        move_result = await client.call_tool(
            "move_note",
            {
                "project": test_project.name,
                "identifier": "Lost Cleanup",
                "destination_path": destination_relative,
            },
        )

    assert "✅ Note moved successfully" in move_result.content[0].text
    assert source_path.exists()
    assert destination_path.exists()
    async with db.scoped_session(session_maker) as session:
        assert source_relative in await vacate_repository.load_vacate_markers(
            session,
            [source_relative],
        )

    if move_back_before_recovery:
        original_lock = NoteFileVacateRepository.lock_recoverable_vacate
        moved_back = False

        async def move_back_before_recovery_lock(
            self: NoteFileVacateRepository,
            session,
            *,
            file_path: str,
            file_checksum: str,
        ):
            nonlocal moved_back
            if not moved_back:
                # This second committed session models another local server accepting
                # B -> A after recovery took its initial candidate snapshot.
                async with db.scoped_session(session_maker) as concurrent_session:
                    entity = await entity_repository.get_by_file_path(
                        concurrent_session,
                        destination_relative,
                    )
                    assert entity is not None
                    note_content = await note_content_repository.get_by_entity_id(
                        concurrent_session,
                        entity.id,
                    )
                    assert note_content is not None
                    entity.file_path = source_relative
                    note_content.file_path = source_relative
                    await vacate_repository.clear_vacate_path(
                        concurrent_session,
                        file_path=source_relative,
                    )
                moved_back = True
            return await original_lock(
                self,
                session,
                file_path=file_path,
                file_checksum=file_checksum,
            )

        monkeypatch.setattr(
            NoteFileVacateRepository,
            "lock_recoverable_vacate",
            move_back_before_recovery_lock,
        )

    # Re-enable the real cleanup adapter, matching a fresh process startup.
    monkeypatch.setattr(
        InlineNoteFileDeleteEnqueuer,
        "enqueue_note_file_delete",
        original_cleanup,
    )
    recovered = await recover_move_vacates(
        session_maker=session_maker,
        file_service=FileService(project_config.home),
        project_id=test_project.id,
    )

    assert recovered == (0 if move_back_before_recovery else 1)
    assert source_path.exists() is move_back_before_recovery
    assert destination_path.exists()
    async with db.scoped_session(session_maker) as session:
        if move_back_before_recovery:
            assert await entity_repository.get_by_file_path(session, source_relative) is not None
        assert await vacate_repository.load_vacate_markers(session, [source_relative]) == {}


@pytest.mark.parametrize(
    "source_publication_state",
    ["unpublished", "synced"],
)
@pytest.mark.asyncio
async def test_move_preserves_recreated_source_when_source_is_absent(
    mcp_server,
    app,
    test_project,
    project_config,
    engine_factory,
    source_publication_state: str,
):
    """An absent source never authorizes delayed deletion of a later identical file."""
    del app
    source_relative = "source/Absent Before Move.md"
    destination_relative = "archive/absent-before-move.md"
    source_path = project_config.home / source_relative
    destination_path = project_config.home / destination_relative
    _, session_maker = engine_factory
    entity_repository = EntityRepository(project_id=test_project.id)
    note_content_repository = NoteContentRepository(project_id=test_project.id)
    vacate_repository = NoteFileVacateRepository(project_id=test_project.id)

    async with Client(mcp_server) as client:
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Absent Before Move",
                "directory": "source",
                "content": "# Absent Before Move\n\nCleanup should retire its marker.",
            },
        )

        source_bytes = source_path.read_bytes()
        source_checksum = sha256(source_bytes).hexdigest()
        async with db.scoped_session(session_maker) as session:
            entity = await entity_repository.get_by_file_path(session, source_relative)
            assert entity is not None
            note_content = await note_content_repository.get_by_entity_id(session, entity.id)
            assert note_content is not None
            assert note_content.db_checksum == source_checksum
            if source_publication_state == "unpublished":
                entity.checksum = None
                note_content.file_checksum = None

        # Reproduce the source disappearing before the move observes it. Absence must remain
        # absence rather than borrowing either a pending or synchronized DB checksum as authority
        # to delete this path later.
        source_path.unlink()
        move_result = await client.call_tool(
            "move_note",
            {
                "project": test_project.name,
                "identifier": "Absent Before Move",
                "destination_path": destination_relative,
            },
        )

    assert "✅ Note moved successfully" in move_result.content[0].text
    assert not source_path.exists()
    assert destination_path.exists()

    # A user may legitimately reuse the vacated path after the move. Even byte-identical content
    # must survive because no source object existed when the move claimed its cleanup work.
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(source_bytes)
    recovered = await recover_move_vacates(
        session_maker=session_maker,
        file_service=FileService(project_config.home),
        project_id=test_project.id,
    )

    assert recovered == 0
    assert source_path.read_bytes() == source_bytes
    async with db.scoped_session(session_maker) as session:
        markers = await vacate_repository.load_vacate_markers(session, [source_relative])
    assert markers == {}


@pytest.mark.parametrize(
    "retirement_trigger",
    ["cleanup-worker", "index-observation"],
)
@pytest.mark.asyncio
async def test_move_retires_vacate_marker_after_source_replacement(
    mcp_server,
    app,
    test_project,
    project_config,
    engine_factory,
    monkeypatch,
    retirement_trigger: str,
):
    """Cleanup or replacement observation retires stale move evidence before path reuse."""
    del app
    source_relative = "source/Move Replacement Lifecycle.md"
    destination_relative = "archive/move-replacement-lifecycle.md"
    source_path = project_config.home / source_relative
    _, session_maker = engine_factory
    entity_repository = EntityRepository(project_id=test_project.id)
    vacate_repository = NoteFileVacateRepository(project_id=test_project.id)
    original_cleanup = InlineNoteFileDeleteEnqueuer.enqueue_note_file_delete
    pending_cleanup: (
        tuple[
            InlineNoteFileDeleteEnqueuer,
            RuntimeNoteFileDeleteJobRequest,
        ]
        | None
    ) = None

    async def capture_source_cleanup(
        self: InlineNoteFileDeleteEnqueuer,
        request: RuntimeNoteFileDeleteJobRequest,
    ) -> None:
        nonlocal pending_cleanup
        pending_cleanup = (self, request)

    monkeypatch.setattr(
        InlineNoteFileDeleteEnqueuer,
        "enqueue_note_file_delete",
        capture_source_cleanup,
    )

    async with Client(mcp_server) as client:
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Move Replacement Lifecycle",
                "directory": "source",
                "content": (
                    "# Move Replacement Lifecycle\n\n"
                    "This content may later be copied back legitimately."
                ),
            },
        )
        original_source = source_path.read_bytes()
        source_checksum = sha256(original_source).hexdigest()

        move_result = await client.call_tool(
            "move_note",
            {
                "project": test_project.name,
                "identifier": "Move Replacement Lifecycle",
                "destination_path": destination_relative,
            },
        )
        assert "✅ Note moved successfully" in move_result.content[0].text
        assert pending_cleanup is not None

        cleanup_enqueuer, cleanup_request = pending_cleanup
        source_path.write_text(
            "# Source Replacement\n\nThis independent file replaced the moved source.",
            encoding="utf-8",
        )
        if retirement_trigger == "cleanup-worker":
            await original_cleanup(cleanup_enqueuer, cleanup_request)

        # Restore normal inline cleanup before deleting the indexed replacement below.
        monkeypatch.setattr(
            InlineNoteFileDeleteEnqueuer,
            "enqueue_note_file_delete",
            original_cleanup,
        )

        await run_local_project_index_for_project(
            test_project,
            runtime_factory=LocalProjectIndexRuntimeFactory(batch_size=10),
            force_full=True,
        )
        async with db.scoped_session(session_maker) as session:
            replacement = await entity_repository.get_by_file_path(session, source_relative)
            moved = await entity_repository.get_by_file_path(session, destination_relative)
            markers = await vacate_repository.load_vacate_markers(session, [source_relative])
        assert replacement is not None
        assert moved is not None
        assert replacement.id != moved.id
        assert markers == {}
        replacement_external_id = replacement.external_id
        moved_entity_id = moved.id

        delete_result = await client.call_tool(
            "delete_note",
            {
                "project": test_project.name,
                "identifier": source_relative,
            },
        )
        assert "true" in delete_result.content[0].text.lower()
        assert not source_path.exists()

        source_path.write_bytes(original_source)
        assert sha256(source_path.read_bytes()).hexdigest() == source_checksum
        await run_local_project_index_for_project(
            test_project,
            runtime_factory=LocalProjectIndexRuntimeFactory(batch_size=10),
            force_full=True,
        )

    async with db.scoped_session(session_maker) as session:
        legitimate_copy = await entity_repository.get_by_file_path(session, source_relative)
        markers = await vacate_repository.load_vacate_markers(session, [source_relative])

    assert legitimate_copy is not None
    assert legitimate_copy.id != moved_entity_id
    assert legitimate_copy.external_id != replacement_external_id
    assert markers == {}


@pytest.mark.parametrize(
    "publication_state",
    [
        "synced",
        "pending-before-write",
        "failed-before-write",
        "published-checksum-stale",
        "external-change-restored-published",
    ],
)
@pytest.mark.parametrize("force_full", [False, True], ids=["observed", "force-full"])
@pytest.mark.asyncio
async def test_put_rename_lingering_source_is_not_reindexed(
    client: AsyncClient,
    app,
    test_project,
    project_config,
    engine_factory,
    monkeypatch,
    force_full: bool,
    publication_state: str,
):
    """A PUT rename records current accepted bytes despite stale publication fields."""
    del app
    _, session_maker = engine_factory
    entity_repository = EntityRepository(project_id=test_project.id)
    note_content_repository = NoteContentRepository(project_id=test_project.id)
    vacate_repository = NoteFileVacateRepository(project_id=test_project.id)
    project_url = f"/v2/projects/{test_project.external_id}"

    create_response = await client.post(
        f"{project_url}/knowledge/entities",
        json={
            "title": "PUT Rename Race",
            "directory": "source",
            "content": "This source object must remain represented only by its renamed entity.",
        },
    )
    assert create_response.status_code == 202
    created = create_response.json()
    source_relative = created["file_path"]
    source_path = project_config.home / source_relative
    materialized_checksum = sha256(source_path.read_bytes()).hexdigest()
    source_checksum = materialized_checksum

    if publication_state in {
        "pending-before-write",
        "failed-before-write",
        "published-checksum-stale",
        "external-change-restored-published",
    }:
        pending_markdown = (
            source_path.read_text(encoding="utf-8").rstrip()
            + "\n\nThese accepted bytes differ from the last published bytes.\n"
        )
        accepted_checksum = sha256(pending_markdown.encode()).hexdigest()
        if publication_state == "published-checksum-stale":
            source_path.write_bytes(pending_markdown.encode())
            source_checksum = sha256(source_path.read_bytes()).hexdigest()
        async with db.scoped_session(session_maker) as session:
            entity = await entity_repository.get_by_file_path(session, source_relative)
            assert entity is not None
            note_content = await note_content_repository.get_by_entity_id(session, entity.id)
            assert note_content is not None
            note_content.markdown_content = pending_markdown
            note_content.db_version += 1
            note_content.db_checksum = accepted_checksum
            note_content.file_write_status = {
                "pending-before-write": "pending",
                "failed-before-write": "failed",
                "published-checksum-stale": "writing",
                "external-change-restored-published": "external_change_detected",
            }[publication_state]
            assert note_content.file_version is not None
            assert note_content.file_version < note_content.db_version
            assert note_content.file_checksum == materialized_checksum

    async def leave_source_cleanup_pending(
        self: InlineNoteFileDeleteEnqueuer,
        request: RuntimeNoteFileDeleteJobRequest,
    ) -> None:
        del self, request

    monkeypatch.setattr(
        InlineNoteFileDeleteEnqueuer,
        "enqueue_note_file_delete",
        leave_source_cleanup_pending,
    )

    update_response = await client.put(
        f"{project_url}/knowledge/entities/{created['external_id']}",
        json={
            "title": "PUT Rename Race",
            "directory": "archive",
            "content": "The renamed destination also carries updated content.",
        },
    )
    assert update_response.status_code == 202
    updated = update_response.json()
    destination_relative = updated["file_path"]
    assert destination_relative != source_relative
    assert source_path.exists()

    async with db.scoped_session(session_maker) as session:
        markers = await vacate_repository.load_vacate_markers(session, [source_relative])
    assert markers[source_relative].entity_id == created["id"]
    assert markers[source_relative].file_checksum == source_checksum

    await run_local_project_index_for_project(
        test_project,
        runtime_factory=LocalProjectIndexRuntimeFactory(batch_size=10),
        force_full=force_full,
    )

    async with db.scoped_session(session_maker) as session:
        ghost = await entity_repository.get_by_file_path(session, source_relative)
        renamed = await entity_repository.get_by_file_path(session, destination_relative)

    assert ghost is None
    assert renamed is not None
    assert renamed.id == created["id"]


@pytest.mark.parametrize("force_full", [False, True], ids=["observed", "force-full"])
@pytest.mark.asyncio
async def test_deleted_move_lingering_source_is_not_resurrected(
    mcp_server,
    app,
    test_project,
    project_config,
    engine_factory,
    monkeypatch,
    force_full: bool,
):
    """A move marker survives destination deletion until the lingering source is cleaned."""
    del app
    source_relative = "source/Delete After Move.md"
    destination_relative = "archive/delete-after-move.md"
    source_path = project_config.home / source_relative
    destination_path = project_config.home / destination_relative
    _, session_maker = engine_factory
    entity_repository = EntityRepository(project_id=test_project.id)
    vacate_repository = NoteFileVacateRepository(project_id=test_project.id)
    original_cleanup = InlineNoteFileDeleteEnqueuer.enqueue_note_file_delete

    async def leave_only_move_source_cleanup_pending(
        self: InlineNoteFileDeleteEnqueuer,
        request: RuntimeNoteFileDeleteJobRequest,
    ) -> None:
        if request.file_path == source_relative:
            return
        await original_cleanup(self, request)

    monkeypatch.setattr(
        InlineNoteFileDeleteEnqueuer,
        "enqueue_note_file_delete",
        leave_only_move_source_cleanup_pending,
    )

    async with Client(mcp_server) as client:
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Delete After Move",
                "directory": "source",
                "content": "# Delete After Move\n\nDo not resurrect this deleted note.",
            },
        )
        source_checksum = sha256(source_path.read_bytes()).hexdigest()

        move_result = await client.call_tool(
            "move_note",
            {
                "project": test_project.name,
                "identifier": "Delete After Move",
                "destination_path": destination_relative,
            },
        )
        assert "✅ Note moved successfully" in move_result.content[0].text
        assert source_path.exists()
        assert destination_path.exists()

        delete_result = await client.call_tool(
            "delete_note",
            {
                "project": test_project.name,
                "identifier": destination_relative,
            },
        )
        assert "true" in delete_result.content[0].text.lower()
        assert source_path.exists()
        assert not destination_path.exists()

    async with db.scoped_session(session_maker) as session:
        markers = await vacate_repository.load_vacate_markers(session, [source_relative])
    # SQLite backends can expose either a null or dangling destination ID after deletion. The
    # durable checksum is the portable invariant that keeps these exact source bytes suppressed.
    assert markers[source_relative].file_checksum == source_checksum

    await run_local_project_index_for_project(
        test_project,
        runtime_factory=LocalProjectIndexRuntimeFactory(batch_size=10),
        force_full=force_full,
    )

    async with db.scoped_session(session_maker) as session:
        resurrected = await entity_repository.get_by_file_path(session, source_relative)

    assert resurrected is None

    recovered = await recover_move_vacates(
        session_maker=session_maker,
        file_service=FileService(project_config.home),
        project_id=test_project.id,
    )
    assert recovered == 1
    assert not source_path.exists()
    async with db.scoped_session(session_maker) as session:
        assert await vacate_repository.load_vacate_markers(session, [source_relative]) == {}


@pytest.mark.asyncio
async def test_move_back_retires_old_destination_marker_before_path_reuse(
    mcp_server,
    app,
    test_project,
    project_config,
    engine_factory,
    monkeypatch,
):
    """Publishing a moved-back note supersedes older move evidence for that destination."""
    del app
    source_relative = "source/Move Back Lifecycle.md"
    destination_relative = "archive/move-back-lifecycle.md"
    source_path = project_config.home / source_relative
    destination_path = project_config.home / destination_relative
    _, session_maker = engine_factory
    entity_repository = EntityRepository(project_id=test_project.id)
    vacate_repository = NoteFileVacateRepository(project_id=test_project.id)
    original_cleanup = InlineNoteFileDeleteEnqueuer.enqueue_note_file_delete

    async def leave_move_source_cleanup_pending(
        self: InlineNoteFileDeleteEnqueuer,
        request: RuntimeNoteFileDeleteJobRequest,
    ) -> None:
        if request.live_file_path is not None:
            return
        await original_cleanup(self, request)

    monkeypatch.setattr(
        InlineNoteFileDeleteEnqueuer,
        "enqueue_note_file_delete",
        leave_move_source_cleanup_pending,
    )

    async with Client(mcp_server) as client:
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Move Back Lifecycle",
                "directory": "source",
                "content": "# Move Back Lifecycle\n\nThese original bytes will be reused later.",
            },
        )
        original_source = source_path.read_bytes()

        move_away = await client.call_tool(
            "move_note",
            {
                "project": test_project.name,
                "identifier": "Move Back Lifecycle",
                "destination_path": destination_relative,
            },
        )
        assert "✅ Note moved successfully" in move_away.content[0].text
        assert source_path.exists()
        assert destination_path.exists()

        edit_result = await client.call_tool(
            "edit_note",
            {
                "project": test_project.name,
                "identifier": destination_relative,
                "operation": "append",
                "content": "\n\nThe moved-back note now has different bytes.",
            },
        )
        assert "# Edited note (append)" in edit_result.content[0].text
        assert destination_path.read_bytes() != original_source

        # Simulate the first move's physical cleanup completing without its durable marker being
        # retired. The stale marker must not outlive the next successful publication at this path.
        source_path.unlink()
        move_back = await client.call_tool(
            "move_note",
            {
                "project": test_project.name,
                "identifier": destination_relative,
                "destination_path": source_relative,
            },
        )
        assert "✅ Note moved successfully" in move_back.content[0].text

        async with db.scoped_session(session_maker) as session:
            moved_back = await entity_repository.get_by_file_path(session, source_relative)
            markers = await vacate_repository.load_vacate_markers(
                session,
                [source_relative, destination_relative],
            )
        assert moved_back is not None
        moved_external_id = moved_back.external_id
        assert source_relative not in markers
        assert destination_relative in markers

        delete_result = await client.call_tool(
            "delete_note",
            {
                "project": test_project.name,
                "identifier": source_relative,
            },
        )
        assert "true" in delete_result.content[0].text.lower()
        assert not source_path.exists()

    source_path.write_bytes(original_source)
    await run_local_project_index_for_project(
        test_project,
        runtime_factory=LocalProjectIndexRuntimeFactory(batch_size=10),
        force_full=True,
    )

    async with db.scoped_session(session_maker) as session:
        legitimate_reuse = await entity_repository.get_by_file_path(session, source_relative)
        markers = await vacate_repository.load_vacate_markers(session, [source_relative])

    assert legitimate_reuse is not None
    assert legitimate_reuse.external_id != moved_external_id
    assert markers == {}


@pytest.mark.asyncio
async def test_move_note_using_permalink(mcp_server, app, test_project):
    """Test moving a note using its permalink as identifier."""

    async with Client(mcp_server) as client:
        # Create a note to move
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Permalink Move Test",
                "directory": "test",
                "content": "# Permalink Move Test\n\nMoving by permalink.",
                "tags": "test,permalink",
            },
        )

        # Move using permalink
        move_result = await client.call_tool(
            "move_note",
            {
                "project": test_project.name,
                "identifier": "test/permalink-move-test",
                "destination_path": "archive/permalink-moved.md",
            },
        )

        # Should successfully move
        assert len(move_result.content) == 1
        move_text = move_result.content[0].text
        assert "✅ Note moved successfully" in move_text
        assert "test/permalink-move-test" in move_text
        assert "archive/permalink-moved.md" in move_text

        # Verify accessibility at new location
        read_result = await client.call_tool(
            "read_note",
            {
                "project": test_project.name,
                "identifier": "archive/permalink-moved.md",
            },
        )

        assert "Moving by permalink" in read_result.content[0].text


@pytest.mark.asyncio
async def test_move_note_with_observations_and_relations(mcp_server, app, test_project):
    """Test moving a note that contains observations and relations."""

    async with Client(mcp_server) as client:
        # Create complex note with observations and relations
        complex_content = """# Complex Note

This note has various structured content.

## Observations
- [feature] Has structured observations
- [tech] Uses markdown format
- [status] Ready for move testing

## Relations
- implements [[Auth System]]
- documented_in [[Move Guide]]
- depends_on [[File System]]

## Content
This note demonstrates moving complex content."""

        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Complex Note",
                "directory": "complex",
                "content": complex_content,
                "tags": "test,complex,move",
            },
        )

        # Move the complex note
        move_result = await client.call_tool(
            "move_note",
            {
                "project": test_project.name,
                "identifier": "Complex Note",
                "destination_path": "moved/complex-note.md",
            },
        )

        # Should successfully move
        assert len(move_result.content) == 1
        move_text = move_result.content[0].text
        assert "✅ Note moved successfully" in move_text
        assert "Complex Note" in move_text
        assert "moved/complex-note.md" in move_text

        # Verify content preservation including structured data
        read_result = await client.call_tool(
            "read_note",
            {
                "project": test_project.name,
                "identifier": "moved/complex-note.md",
            },
        )

        content = read_result.content[0].text
        assert "Has structured observations" in content
        assert "implements [[Auth System]]" in content
        assert "## Observations" in content
        assert "[feature]" in content  # Should show original markdown observations
        assert "## Relations" in content


@pytest.mark.asyncio
async def test_move_note_to_nested_directory(mcp_server, app, test_project):
    """Test moving a note to a deeply nested directory structure."""

    async with Client(mcp_server) as client:
        # Create a note
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Nested Move Test",
                "directory": "root",
                "content": "# Nested Move Test\n\nThis will be moved deep.",
                "tags": "test,nested",
            },
        )

        # Move to a deep nested structure
        move_result = await client.call_tool(
            "move_note",
            {
                "project": test_project.name,
                "identifier": "Nested Move Test",
                "destination_path": "projects/2025/q2/work/nested-note.md",
            },
        )

        # Should successfully create directory structure and move
        assert len(move_result.content) == 1
        move_text = move_result.content[0].text
        assert "✅ Note moved successfully" in move_text
        assert "Nested Move Test" in move_text
        assert "projects/2025/q2/work/nested-note.md" in move_text

        # Verify accessibility
        read_result = await client.call_tool(
            "read_note",
            {
                "project": test_project.name,
                "identifier": "projects/2025/q2/work/nested-note.md",
            },
        )

        assert "This will be moved deep" in read_result.content[0].text


@pytest.mark.asyncio
async def test_move_note_with_special_characters(mcp_server, app, test_project):
    """Test moving notes with special characters in titles and paths."""

    async with Client(mcp_server) as client:
        # Create note with special characters
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Special (Chars) & Symbols",
                "directory": "special",
                "content": "# Special (Chars) & Symbols\n\nTesting special characters in move.",
                "tags": "test,special",
            },
        )

        # Move to path with special characters
        move_result = await client.call_tool(
            "move_note",
            {
                "project": test_project.name,
                "identifier": "Special (Chars) & Symbols",
                "destination_path": "archive/special-chars-note.md",
            },
        )

        # Should handle special characters properly
        assert len(move_result.content) == 1
        move_text = move_result.content[0].text
        assert "✅ Note moved successfully" in move_text
        assert "archive/special-chars-note.md" in move_text

        # Verify content preservation
        read_result = await client.call_tool(
            "read_note",
            {
                "project": test_project.name,
                "identifier": "archive/special-chars-note.md",
            },
        )

        assert "Testing special characters in move" in read_result.content[0].text


@pytest.mark.asyncio
async def test_move_note_error_handling_note_not_found(mcp_server, app, test_project):
    """Test error handling when trying to move a non-existent note."""

    async with Client(mcp_server) as client:
        # Try to move a note that doesn't exist - should return error message
        move_result = await client.call_tool(
            "move_note",
            {
                "project": test_project.name,
                "identifier": "Non-existent Note",
                "destination_path": "new/location.md",
            },
        )

        # Should contain error message about the failed operation
        assert len(move_result.content) == 1
        error_message = move_result.content[0].text
        assert "# Move Failed" in error_message
        assert "Non-existent Note" in error_message


@pytest.mark.asyncio
async def test_move_note_error_handling_invalid_destination(mcp_server, app, test_project):
    """Test error handling for invalid destination paths."""

    async with Client(mcp_server) as client:
        # Create a note to attempt moving
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Invalid Dest Test",
                "directory": "test",
                "content": "# Invalid Dest Test\n\nThis move should fail.",
                "tags": "test,error",
            },
        )

        # Try to move to absolute path (should fail) - should return error message
        move_result = await client.call_tool(
            "move_note",
            {
                "project": test_project.name,
                "identifier": "Invalid Dest Test",
                "destination_path": "/absolute/path/note.md",
            },
        )

        # Should contain error message about the failed operation
        assert len(move_result.content) == 1
        error_message = move_result.content[0].text
        assert "# Move Failed" in error_message
        assert "/absolute/path/note.md" in error_message


@pytest.mark.asyncio
async def test_move_note_error_handling_destination_exists(mcp_server, app, test_project):
    """Test error handling when destination file already exists."""

    async with Client(mcp_server) as client:
        # Create source note
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Source Note",
                "directory": "source",
                "content": "# Source Note\n\nThis is the source.",
                "tags": "test,source",
            },
        )

        # Create destination note that already exists at the exact path we'll try to move to
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Existing Note",
                "directory": "destination",
                "content": "# Existing Note\n\nThis already exists.",
                "tags": "test,existing",
            },
        )

        # Try to move source to existing destination (should fail) - should return error message
        move_result = await client.call_tool(
            "move_note",
            {
                "project": test_project.name,
                "identifier": "Source Note",
                "destination_path": "destination/Existing Note.md",  # Use exact existing file name
            },
        )

        # Should contain error message about the failed operation
        assert len(move_result.content) == 1
        error_message = move_result.content[0].text
        assert "# Move Failed" in error_message
        assert "already exists" in error_message


@pytest.mark.asyncio
async def test_move_note_preserves_search_functionality(mcp_server, app, test_project):
    """Test that moved notes remain searchable after move operation."""

    async with Client(mcp_server) as client:
        # Create a note with searchable content
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Searchable Note",
                "directory": "original",
                "content": """# Searchable Note

This note contains unique search terms:
- quantum mechanics
- artificial intelligence
- machine learning algorithms

## Features
- [technology] Advanced AI features
- [research] Quantum computing research

## Relations
- relates_to [[AI Research]]""",
                "tags": "search,test,move",
            },
        )

        # Verify note is searchable before move
        search_before = await client.call_tool(
            "search_notes",
            {
                "project": test_project.name,
                "query": "quantum mechanics",
            },
        )

        assert len(search_before.content) > 0
        assert "Searchable Note" in search_before.content[0].text

        # Move the note
        move_result = await client.call_tool(
            "move_note",
            {
                "project": test_project.name,
                "identifier": "Searchable Note",
                "destination_path": "research/quantum-ai-note.md",
            },
        )

        assert len(move_result.content) == 1
        move_text = move_result.content[0].text
        assert "✅ Note moved successfully" in move_text

        # Verify note is still searchable after move
        search_after = await client.call_tool(
            "search_notes",
            {
                "project": test_project.name,
                "query": "quantum mechanics",
            },
        )

        assert len(search_after.content) > 0
        search_text = search_after.content[0].text
        # Search results include observations/relations — check the note is found by file path
        assert "quantum-ai-note" in search_text

        # Verify search by new location works
        search_by_path = await client.call_tool(
            "search_notes",
            {
                "project": test_project.name,
                "query": "research/quantum",
            },
        )

        assert len(search_by_path.content) > 0


@pytest.mark.asyncio
async def test_move_note_using_different_identifier_formats(mcp_server, app, test_project):
    """Test moving notes using different identifier formats (title, permalink, folder/title)."""

    async with Client(mcp_server) as client:
        # Create notes for different identifier tests
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Title ID Note",
                "directory": "test",
                "content": "# Title ID Note\n\nMove by title.",
                "tags": "test,identifier",
            },
        )

        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Permalink ID Note",
                "directory": "test",
                "content": "# Permalink ID Note\n\nMove by permalink.",
                "tags": "test,identifier",
            },
        )

        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Folder Title Note",
                "directory": "test",
                "content": "# Folder Title Note\n\nMove by folder/title.",
                "tags": "test,identifier",
            },
        )

        # Test moving by title
        move1 = await client.call_tool(
            "move_note",
            {
                "project": test_project.name,
                "identifier": "Title ID Note",  # by title
                "destination_path": "moved/title-moved.md",
            },
        )
        assert len(move1.content) == 1
        assert "✅ Note moved successfully" in move1.content[0].text

        # Test moving by permalink
        move2 = await client.call_tool(
            "move_note",
            {
                "project": test_project.name,
                "identifier": "test/permalink-id-note",  # by permalink
                "destination_path": "moved/permalink-moved.md",
            },
        )
        assert len(move2.content) == 1
        assert "✅ Note moved successfully" in move2.content[0].text

        # Test moving by folder/title format
        move3 = await client.call_tool(
            "move_note",
            {
                "project": test_project.name,
                "identifier": "test/Folder Title Note",  # by folder/title
                "destination_path": "moved/folder-title-moved.md",
            },
        )
        assert len(move3.content) == 1
        assert "✅ Note moved successfully" in move3.content[0].text

        # Verify all notes can be accessed at their new locations
        read1 = await client.call_tool(
            "read_note", {"project": test_project.name, "identifier": "moved/title-moved.md"}
        )
        assert "Move by title" in read1.content[0].text

        read2 = await client.call_tool(
            "read_note", {"project": test_project.name, "identifier": "moved/permalink-moved.md"}
        )
        assert "Move by permalink" in read2.content[0].text

        read3 = await client.call_tool(
            "read_note", {"project": test_project.name, "identifier": "moved/folder-title-moved.md"}
        )
        assert "Move by folder/title" in read3.content[0].text


@pytest.mark.asyncio
async def test_move_note_cross_project_detection(mcp_server, app, test_project):
    """Test cross-project move detection and helpful error messages."""

    async with Client(mcp_server) as client:
        # Create a test project to simulate cross-project scenario
        await client.call_tool(
            "create_memory_project",
            {
                "project_name": "test-project-b",
                "project_path": "/tmp/test-project-b",
                "set_default": False,
            },
        )

        # Create a note in the default project
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Cross Project Test Note",
                "directory": "source",
                "content": "# Cross Project Test Note\n\nThis note is in the default project.",
                "tags": "test,cross-project",
            },
        )

        # Try to move to a path that contains the other project name
        move_result = await client.call_tool(
            "move_note",
            {
                "project": test_project.name,
                "identifier": "Cross Project Test Note",
                "destination_path": "test-project-b/moved-note.md",
            },
        )

        # Should detect cross-project attempt and provide helpful guidance
        assert len(move_result.content) == 1
        error_message = move_result.content[0].text
        assert "Cross-Project Move Not Supported" in error_message
        assert "test-project-b" in error_message
        assert "read_note" in error_message
        assert "write_note" in error_message


@pytest.mark.asyncio
async def test_move_note_normal_moves_still_work(mcp_server, app, test_project):
    """Test that normal within-project moves still work after cross-project detection."""

    async with Client(mcp_server) as client:
        # Create a note
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Normal Move Note",
                "directory": "source",
                "content": "# Normal Move Note\n\nThis should move normally.",
                "tags": "test,normal-move",
            },
        )

        # Try a normal move that should work
        move_result = await client.call_tool(
            "move_note",
            {
                "project": test_project.name,
                "identifier": "Normal Move Note",
                "destination_path": "destination/normal-moved.md",
            },
        )

        # Should work normally
        assert len(move_result.content) == 1
        move_text = move_result.content[0].text
        assert "✅ Note moved successfully" in move_text
        assert "Normal Move Note" in move_text
        assert "destination/normal-moved.md" in move_text

        # Verify the note can be read from its new location
        read_result = await client.call_tool(
            "read_note",
            {
                "project": test_project.name,
                "identifier": "destination/normal-moved.md",
            },
        )

        content = read_result.content[0].text
        assert "This should move normally" in content


@pytest.mark.asyncio
async def test_move_note_with_destination_folder(mcp_server, app, test_project):
    """Test moving a note using destination_folder to preserve the original filename."""

    async with Client(mcp_server) as client:
        # Create a note to move
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Folder Move Integration",
                "directory": "source",
                "content": "# Folder Move Integration\n\nTesting destination_folder parameter.",
                "tags": "test,folder-move",
            },
        )

        # Move using destination_folder (filename preserved automatically)
        move_result = await client.call_tool(
            "move_note",
            {
                "project": test_project.name,
                "identifier": "Folder Move Integration",
                "destination_folder": "archive/2025",
            },
        )

        # Should return successful move message
        assert len(move_result.content) == 1
        move_text = move_result.content[0].text
        assert "✅ Note moved successfully" in move_text
        assert "Folder Move Integration" in move_text

        # Verify the note can be read from its new location (original filename preserved)
        read_result = await client.call_tool(
            "read_note",
            {
                "project": test_project.name,
                "identifier": "archive/2025/folder-move-integration",
            },
        )

        content = read_result.content[0].text
        assert "Testing destination_folder parameter" in content

        # Verify the original location no longer works
        read_original = await client.call_tool(
            "read_note",
            {
                "project": test_project.name,
                "identifier": "source/folder-move-integration",
            },
        )
        assert "Note Not Found" in read_original.content[0].text


@pytest.mark.asyncio
async def test_move_note_destination_folder_mutually_exclusive(mcp_server, app, test_project):
    """Test that providing both destination_path and destination_folder returns an error."""

    async with Client(mcp_server) as client:
        move_result = await client.call_tool(
            "move_note",
            {
                "project": test_project.name,
                "identifier": "some-note",
                "destination_path": "target/note.md",
                "destination_folder": "target",
            },
        )

        assert len(move_result.content) == 1
        error_text = move_result.content[0].text
        assert "# Move Failed - Invalid Parameters" in error_text
        assert "Cannot specify both" in error_text


@pytest.mark.asyncio
async def test_move_note_strict_resolution_rejects_fuzzy_match(mcp_server, app, test_project):
    """move_note must not fuzzy-match a nonexistent identifier to an existing note (#649)."""

    async with Client(mcp_server) as client:
        # Create two notes that could be fuzzy-matched
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Move Strict Test A",
                "directory": "test",
                "content": "# Move Strict Test A\n\nContent A.",
            },
        )
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Move Strict Test B",
                "directory": "test",
                "content": "# Move Strict Test B\n\nContent B.",
            },
        )

        # Attempt to move a nonexistent note — should error, not move A or B
        move_result = await client.call_tool(
            "move_note",
            {
                "project": test_project.name,
                "identifier": "Move Strict Test NONEXISTENT",
                "destination_path": "archive/Moved.md",
            },
        )

        assert len(move_result.content) == 1
        error_text = move_result.content[0].text
        assert "# Move Failed" in error_text

        # Verify neither A nor B was moved
        read_a = await client.call_tool(
            "read_note",
            {"project": test_project.name, "identifier": "Move Strict Test A"},
        )
        assert "Content A" in read_a.content[0].text

        read_b = await client.call_tool(
            "read_note",
            {"project": test_project.name, "identifier": "Move Strict Test B"},
        )
        assert "Content B" in read_b.content[0].text


@pytest.mark.asyncio
async def test_move_note_unknown_workspace_shaped_path_allowed(mcp_server, app, test_project):
    """A "<seg>/projects/<seg>/..." destination whose leading segment is NOT a known
    project is a legitimate same-project nested move and must succeed.

    The old "projects"-segment structural heuristic (#904) wrongly rejected this shape
    even though it is structurally identical to a normal nested folder. Detection now
    relies only on the leading segment matching a known project name (Detection 1) plus
    the post-move outcome backstop, so a bare "other-workspace/projects/x/..." that
    matches no known project is treated as a normal nested move.
    """

    async with Client(mcp_server) as client:
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Workspace Shape Note",
                "directory": "source",
                "content": "# Workspace Shape Note\n\nNested move into a projects folder.",
            },
        )

        move_result = await client.call_tool(
            "move_note",
            {
                "project": test_project.name,
                "identifier": "Workspace Shape Note",
                "destination_path": "other-workspace/projects/x/moved-note.md",
            },
        )

        move_text = move_result.content[0].text
        assert "Cross-Project Move Not Supported" not in move_text
        assert "✅ Note moved successfully" in move_text
        assert "other-workspace/projects/x/moved-note.md" in move_text

        # The note landed at the requested nested location.
        read_moved = await client.call_tool(
            "read_note",
            {
                "project": test_project.name,
                "identifier": "other-workspace/projects/x/moved-note.md",
            },
        )
        assert "Nested move into a projects folder" in read_moved.content[0].text


@pytest.mark.asyncio
async def test_move_note_destination_folder_boundary_rejected(mcp_server, app, test_project):
    """The destination_folder bypass (#881 Gap 3) must now be detected honestly.

    Previously the cross-boundary guard ran before destination_folder was resolved into
    destination_path, so a boundary-shaped folder slipped through and reported success.
    """

    async with Client(mcp_server) as client:
        await client.call_tool(
            "create_memory_project",
            {
                "project_name": "boundary-target-project",
                "project_path": "/tmp/boundary-target-project",
                "set_default": False,
            },
        )

        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Folder Boundary Note",
                "directory": "source",
                "content": "# Folder Boundary Note\n\nFolder bypass should be caught.",
            },
        )

        # destination_folder names another project — resolved path routes cross-project.
        move_result = await client.call_tool(
            "move_note",
            {
                "project": test_project.name,
                "identifier": "Folder Boundary Note",
                "destination_folder": "boundary-target-project",
            },
        )

        error_message = move_result.content[0].text
        assert "Cross-Project Move Not Supported" in error_message
        assert "boundary-target-project" in error_message

        # Note stays put.
        read_original = await client.call_tool(
            "read_note",
            {"project": test_project.name, "identifier": "Folder Boundary Note"},
        )
        assert "Folder bypass should be caught" in read_original.content[0].text


@pytest.mark.asyncio
async def test_move_note_unknown_workspace_shaped_path_allowed_json(mcp_server, app, test_project):
    """JSON output for an unknown-workspace-shaped destination reports moved=True.

    With the ambiguous "projects"-segment heuristic removed (#904), a
    "<seg>/projects/<seg>/..." path whose leading segment is not a known project is a
    legitimate same-project nested move, not a cross-project rejection.
    """

    async with Client(mcp_server) as client:
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Workspace Shape JSON Note",
                "directory": "source",
                "content": "# Workspace Shape JSON Note\n\nJSON path.",
            },
        )

        move_result = await client.call_tool(
            "move_note",
            {
                "project": test_project.name,
                "identifier": "Workspace Shape JSON Note",
                "destination_path": "team-space/projects/alpha/moved.md",
                "output_format": "json",
            },
        )

        data = json.loads(move_result.content[0].text)
        assert data["moved"] is True
        # Success JSON does not include an error key.
        assert "error" not in data
        assert data["file_path"] == "team-space/projects/alpha/moved.md"


@pytest.mark.asyncio
async def test_move_note_new_nested_folder_still_succeeds(mcp_server, app, test_project):
    """A legitimate same-project move into a brand-new nested folder must still succeed.

    Guards against false positives from the broadened cross-boundary detection. Note the
    top-level "projects/" folder is a valid same-project location and must NOT be flagged.
    """

    async with Client(mcp_server) as client:
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Legit Nested Note",
                "directory": "source",
                "content": "# Legit Nested Note\n\nValid same-project nested move.",
            },
        )

        move_result = await client.call_tool(
            "move_note",
            {
                "project": test_project.name,
                "identifier": "Legit Nested Note",
                "destination_path": "projects/2025/q2/legit-nested-note.md",
            },
        )

        move_text = move_result.content[0].text
        assert "✅ Note moved successfully" in move_text
        assert "projects/2025/q2/legit-nested-note.md" in move_text

        read_result = await client.call_tool(
            "read_note",
            {
                "project": test_project.name,
                "identifier": "projects/2025/q2/legit-nested-note.md",
            },
        )
        assert "Valid same-project nested move" in read_result.content[0].text


@pytest.mark.asyncio
async def test_move_note_interior_projects_segment_still_succeeds(mcp_server, app, test_project):
    """A path containing an interior 'projects' segment must not trip cross-boundary detection.

    Regression for the false positive where any path containing a 'projects' segment
    (e.g. "notes/projects/my-project/note.md") was rejected. The ambiguous structural
    heuristic has been removed (#904): a 'projects' folder anywhere in the path is just a
    normal nested folder, so this is a valid same-project move and must succeed.
    """

    async with Client(mcp_server) as client:
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Interior Projects Note",
                "directory": "source",
                "content": "# Interior Projects Note\n\nInterior projects segment is fine.",
            },
        )

        move_result = await client.call_tool(
            "move_note",
            {
                "project": test_project.name,
                "identifier": "Interior Projects Note",
                "destination_path": "team/2026/projects/alpha/interior-projects-note.md",
            },
        )

        move_text = move_result.content[0].text
        assert "✅ Note moved successfully" in move_text
        assert "team/2026/projects/alpha/interior-projects-note.md" in move_text

        read_result = await client.call_tool(
            "read_note",
            {
                "project": test_project.name,
                "identifier": "team/2026/projects/alpha/interior-projects-note.md",
            },
        )
        assert "Interior projects segment is fine" in read_result.content[0].text
