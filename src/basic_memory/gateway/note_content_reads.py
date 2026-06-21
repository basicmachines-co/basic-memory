"""Shared note-content read service facade."""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.indexing import (
    NoteContentReadRepairFileReader,
    NoteContentReadRepairTarget,
    NoteContentReadView,
    load_note_content_read_view_with_default_repositories,
    note_content_resource_from_read_view,
    note_content_response_payload_from_read_view,
    prepare_note_content_read_repair_with_default_repositories,
    run_note_content_read_repair_with_default_reconciler,
)
from basic_memory.models import Entity, NoteContent, Project
from basic_memory.runtime import (
    RuntimeNoteContentResource,
    RuntimeNoteContentResponsePayload,
)

type NoteContentReadRepairFileReaderFactory = Callable[
    [NoteContentReadRepairTarget[Project, Entity]],
    NoteContentReadRepairFileReader[Project, Entity],
]


async def load_note_content_query_view(
    *,
    session_maker: async_sessionmaker[AsyncSession],
    project_external_id: str,
    entity_external_id: str,
) -> NoteContentReadView[Entity, NoteContent] | None:
    """Load one project-scoped note view from current DB state."""
    async with db.scoped_session(session_maker) as session:
        return await load_note_content_read_view_with_default_repositories(
            session,
            project_external_id=project_external_id,
            entity_external_id=entity_external_id,
        )


class NoteContentQueryService:
    """Load note-content rows and shape route-friendly read payloads."""

    def __init__(
        self,
        *,
        session_maker: async_sessionmaker[AsyncSession],
        read_repair_file_reader_factory: NoteContentReadRepairFileReaderFactory | None = None,
    ) -> None:
        self.session_maker = session_maker
        self.read_repair_file_reader_factory = read_repair_file_reader_factory

    async def get_note_entity_payload(
        self,
        *,
        project_external_id: str,
        entity_external_id: str,
    ) -> RuntimeNoteContentResponsePayload | None:
        """Return the entity payload, enriching markdown notes from note_content."""
        note_view = await load_note_content_query_view(
            session_maker=self.session_maker,
            project_external_id=project_external_id,
            entity_external_id=entity_external_id,
        )
        return note_content_response_payload_from_read_view(note_view)

    async def get_note_resource(
        self,
        *,
        project_external_id: str,
        entity_external_id: str,
    ) -> RuntimeNoteContentResource | None:
        """Return full markdown content from note_content when available."""
        note_view = await load_note_content_query_view(
            session_maker=self.session_maker,
            project_external_id=project_external_id,
            entity_external_id=entity_external_id,
        )
        if note_view is None:
            return None

        return note_content_resource_from_read_view(note_view)

    async def reconcile_note_content_from_file(
        self,
        *,
        project_external_id: str,
        entity_external_id: str,
        source: str,
    ) -> bool:
        """Repair a missing note_content row from the runtime's canonical file source."""
        async with db.scoped_session(self.session_maker) as session:
            repair_preflight = await prepare_note_content_read_repair_with_default_repositories(
                session,
                project_external_id=project_external_id,
                entity_external_id=entity_external_id,
            )
            if not repair_preflight.should_read_file:
                return repair_preflight.repaired

            repair_target = repair_preflight.require_target()

        if self.read_repair_file_reader_factory is None:
            raise RuntimeError("note-content read repair requires a file reader factory")

        repair_run = await run_note_content_read_repair_with_default_reconciler(
            repair_preflight,
            session_maker=self.session_maker,
            file_reader=self.read_repair_file_reader_factory(repair_target),
            source=source,
        )
        return repair_run.repaired
