"""Tests for repository-backed note-content read and read-repair handoffs."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db, file_utils
from basic_memory.indexing.note_content_read_repair_runner import (
    ENTITY_METADATA_PAYLOAD_EXCLUDE,
    NoteContentReadRepairFile,
    NoteContentReadRepairPreflight,
    NoteContentReadRepairTarget,
    NoteContentReadView,
    load_note_content_read_view_with_default_repositories,
    note_content_resource_from_read_view,
    note_content_response_payload_from_read_view,
    prepare_note_content_read_repair_with_default_repositories,
    run_note_content_read_repair_with_default_reconciler,
)
from basic_memory.models import Entity, NoteContent, Project
from basic_memory.repository import EntityRepository, NoteContentRepository
from basic_memory.runtime.note_content import (
    RuntimeAcceptedNoteResponse,
    RuntimeNoteContentReadRepairStatus,
    RuntimeNoteContentResource,
)
from basic_memory.schemas.v2.entity import EntityResponseV2


async def create_note_content_row(
    session_maker: async_sessionmaker[AsyncSession],
    project: Project,
    entity: Entity,
    markdown_content: str = "# Read\n",
) -> None:
    """Persist an accepted note_content row for one markdown entity."""
    checksum = await file_utils.compute_checksum(markdown_content)
    now = datetime.now(tz=UTC)
    repository = NoteContentRepository(project_id=project.id)
    async with db.scoped_session(session_maker) as session:
        await repository.create(
            session,
            {
                "entity_id": entity.id,
                "project_id": entity.project_id,
                "external_id": entity.external_id,
                "file_path": entity.file_path,
                "markdown_content": markdown_content,
                "db_version": 4,
                "db_checksum": checksum,
                "file_version": 3,
                "file_checksum": checksum,
                "file_write_status": "synced",
                "last_source": "api",
                "updated_at": now,
                "file_updated_at": now,
                "last_materialization_error": None,
                "last_materialization_attempt_at": None,
            },
        )


async def create_image_entity(
    session_maker: async_sessionmaker[AsyncSession],
    project: Project,
) -> Entity:
    """Persist a non-markdown entity that has no note_content row."""
    now = datetime.now(tz=UTC)
    entity_repository = EntityRepository(project_id=project.id)
    async with db.scoped_session(session_maker) as session:
        return await entity_repository.create(
            session,
            {
                "project_id": project.id,
                "title": "diagram.png",
                "note_type": "file",
                "permalink": None,
                "file_path": "images/diagram.png",
                "content_type": "image/png",
                "created_at": now,
                "updated_at": now,
            },
        )


async def load_read_view(
    session_maker: async_sessionmaker[AsyncSession],
    project: Project,
    entity: Entity,
) -> NoteContentReadView[Entity, NoteContent] | None:
    async with db.scoped_session(session_maker) as session:
        return await load_note_content_read_view_with_default_repositories(
            session,
            project_external_id=project.external_id,
            entity_external_id=entity.external_id,
        )


@dataclass(slots=True)
class StubReadRepairFileReader:
    """Return a fixed canonical file for the repair target under test."""

    repair_file: NoteContentReadRepairFile | None
    targets: list[NoteContentReadRepairTarget[Project, Entity]] = field(default_factory=list)

    async def read_note_content_repair_file(
        self,
        target: NoteContentReadRepairTarget[Project, Entity],
    ) -> NoteContentReadRepairFile | None:
        self.targets.append(target)
        return self.repair_file


# --- Hot note-content reads ---


@pytest.mark.asyncio
async def test_load_note_content_read_view_returns_markdown_entity_with_content(
    session_maker: async_sessionmaker[AsyncSession],
    test_project: Project,
    sample_entity: Entity,
) -> None:
    await create_note_content_row(session_maker, test_project, sample_entity)

    view = await load_read_view(session_maker, test_project, sample_entity)

    assert view is not None
    assert view.entity.id == sample_entity.id
    assert view.note_content is not None
    assert view.note_content.markdown_content == "# Read\n"


@pytest.mark.asyncio
async def test_load_note_content_read_view_returns_none_when_project_is_missing(
    session_maker: async_sessionmaker[AsyncSession],
    sample_entity: Entity,
) -> None:
    async with db.scoped_session(session_maker) as session:
        view = await load_note_content_read_view_with_default_repositories(
            session,
            project_external_id="missing-project",
            entity_external_id=sample_entity.external_id,
        )

    assert view is None


@pytest.mark.asyncio
async def test_load_note_content_read_view_returns_none_when_entity_is_missing(
    session_maker: async_sessionmaker[AsyncSession],
    test_project: Project,
) -> None:
    async with db.scoped_session(session_maker) as session:
        view = await load_note_content_read_view_with_default_repositories(
            session,
            project_external_id=test_project.external_id,
            entity_external_id="missing-note",
        )

    assert view is None


@pytest.mark.asyncio
async def test_load_note_content_read_view_skips_note_lookup_for_non_markdown(
    session_maker: async_sessionmaker[AsyncSession],
    test_project: Project,
) -> None:
    image_entity = await create_image_entity(session_maker, test_project)

    view = await load_read_view(session_maker, test_project, image_entity)

    assert view is not None
    assert view.entity.id == image_entity.id
    assert view.note_content is None


@pytest.mark.asyncio
async def test_note_content_response_payload_returns_accepted_note_response(
    session_maker: async_sessionmaker[AsyncSession],
    test_project: Project,
    sample_entity: Entity,
) -> None:
    await create_note_content_row(session_maker, test_project, sample_entity)
    view = await load_read_view(session_maker, test_project, sample_entity)

    payload = note_content_response_payload_from_read_view(view)

    assert isinstance(payload, RuntimeAcceptedNoteResponse)
    assert payload.external_id == sample_entity.external_id
    assert payload.markdown_content == "# Read\n"
    assert payload.db_version == 4
    assert payload.file_write_status == "synced"


@pytest.mark.asyncio
async def test_note_content_response_payload_returns_entity_payload_for_non_markdown(
    session_maker: async_sessionmaker[AsyncSession],
    test_project: Project,
) -> None:
    image_entity = await create_image_entity(session_maker, test_project)
    view = await load_read_view(session_maker, test_project, image_entity)

    payload = note_content_response_payload_from_read_view(view)

    assert payload is not None
    assert not isinstance(payload, RuntimeAcceptedNoteResponse)
    payload_dict = dict(payload)
    assert payload_dict["external_id"] == image_entity.external_id
    assert payload_dict["title"] == "diagram.png"
    assert payload_dict["note_type"] == "file"
    assert payload_dict["content_type"] == "image/png"
    assert payload_dict["file_path"] == "images/diagram.png"
    # The payload is the full EntityResponseV2 dump minus exactly the excluded
    # note_content bookkeeping fields.
    assert set(payload_dict) == set(EntityResponseV2.model_fields) - ENTITY_METADATA_PAYLOAD_EXCLUDE


def test_entity_metadata_payload_exclusions_name_real_response_fields() -> None:
    """Every excluded name must be a real EntityResponseV2 field, or exclusion drifts."""
    assert ENTITY_METADATA_PAYLOAD_EXCLUDE <= EntityResponseV2.model_fields.keys()


@pytest.mark.asyncio
async def test_note_content_resource_returns_accepted_markdown_resource(
    session_maker: async_sessionmaker[AsyncSession],
    test_project: Project,
    sample_entity: Entity,
) -> None:
    await create_note_content_row(session_maker, test_project, sample_entity)
    view = await load_read_view(session_maker, test_project, sample_entity)

    resource = note_content_resource_from_read_view(view)

    assert isinstance(resource, RuntimeNoteContentResource)
    assert resource.content == "# Read\n"
    assert resource.content_type == "text/markdown"


@pytest.mark.asyncio
async def test_note_content_read_payload_helpers_return_none_for_missing_view_or_content(
    session_maker: async_sessionmaker[AsyncSession],
    test_project: Project,
    sample_entity: Entity,
) -> None:
    markdown_without_content = await load_read_view(session_maker, test_project, sample_entity)
    assert markdown_without_content is not None
    assert markdown_without_content.note_content is None

    assert note_content_response_payload_from_read_view(None) is None
    assert note_content_response_payload_from_read_view(markdown_without_content) is None
    assert note_content_resource_from_read_view(None) is None
    assert note_content_resource_from_read_view(markdown_without_content) is None


# --- Read repair for missing note_content rows ---


async def prepare_read_repair(
    session_maker: async_sessionmaker[AsyncSession],
    *,
    project_external_id: str,
    entity_external_id: str,
) -> NoteContentReadRepairPreflight[Project, Entity]:
    async with db.scoped_session(session_maker) as session:
        return await prepare_note_content_read_repair_with_default_repositories(
            session,
            project_external_id=project_external_id,
            entity_external_id=entity_external_id,
        )


@pytest.mark.asyncio
async def test_prepare_note_content_read_repair_returns_storage_target_for_missing_row(
    session_maker: async_sessionmaker[AsyncSession],
    test_project: Project,
    sample_entity: Entity,
) -> None:
    preflight = await prepare_read_repair(
        session_maker,
        project_external_id=test_project.external_id,
        entity_external_id=sample_entity.external_id,
    )

    assert preflight.status is RuntimeNoteContentReadRepairStatus.read_file
    assert preflight.should_read_file
    target = preflight.require_target()
    assert target.project.id == test_project.id
    assert target.entity.id == sample_entity.id


@pytest.mark.asyncio
async def test_prepare_note_content_read_repair_reports_existing_row_as_repaired(
    session_maker: async_sessionmaker[AsyncSession],
    test_project: Project,
    sample_entity: Entity,
) -> None:
    await create_note_content_row(session_maker, test_project, sample_entity)

    preflight = await prepare_read_repair(
        session_maker,
        project_external_id=test_project.external_id,
        entity_external_id=sample_entity.external_id,
    )

    assert preflight.status is RuntimeNoteContentReadRepairStatus.already_present
    assert preflight.repaired
    assert not preflight.should_read_file
    with pytest.raises(RuntimeError, match="does not contain a target"):
        preflight.require_target()


@pytest.mark.asyncio
async def test_prepare_note_content_read_repair_skips_non_markdown_entities(
    session_maker: async_sessionmaker[AsyncSession],
    test_project: Project,
) -> None:
    image_entity = await create_image_entity(session_maker, test_project)

    preflight = await prepare_read_repair(
        session_maker,
        project_external_id=test_project.external_id,
        entity_external_id=image_entity.external_id,
    )

    assert preflight.status is RuntimeNoteContentReadRepairStatus.entity_missing
    assert not preflight.repaired
    assert not preflight.should_read_file


@pytest.mark.asyncio
async def test_prepare_note_content_read_repair_reports_missing_project(
    session_maker: async_sessionmaker[AsyncSession],
    sample_entity: Entity,
) -> None:
    preflight = await prepare_read_repair(
        session_maker,
        project_external_id="missing-project",
        entity_external_id=sample_entity.external_id,
    )

    assert preflight.status is RuntimeNoteContentReadRepairStatus.project_missing
    assert not preflight.should_read_file


@pytest.mark.asyncio
async def test_run_note_content_read_repair_returns_preflight_status_without_file_read(
    session_maker: async_sessionmaker[AsyncSession],
    test_project: Project,
    sample_entity: Entity,
) -> None:
    await create_note_content_row(session_maker, test_project, sample_entity)
    preflight = await prepare_read_repair(
        session_maker,
        project_external_id=test_project.external_id,
        entity_external_id=sample_entity.external_id,
    )

    run = await run_note_content_read_repair_with_default_reconciler(
        preflight,
        session_maker=session_maker,
        file_reader=None,
        source="read_repair",
    )

    assert run.status is RuntimeNoteContentReadRepairStatus.already_present
    assert run.repaired


@pytest.mark.asyncio
async def test_run_note_content_read_repair_requires_file_reader(
    session_maker: async_sessionmaker[AsyncSession],
    test_project: Project,
    sample_entity: Entity,
) -> None:
    preflight = NoteContentReadRepairPreflight(
        status=RuntimeNoteContentReadRepairStatus.read_file,
        target=NoteContentReadRepairTarget(project=test_project, entity=sample_entity),
    )

    with pytest.raises(RuntimeError, match="requires a file reader"):
        await run_note_content_read_repair_with_default_reconciler(
            preflight,
            session_maker=session_maker,
            file_reader=None,
            source="read_repair",
        )


@pytest.mark.asyncio
async def test_run_note_content_read_repair_reports_missing_file(
    session_maker: async_sessionmaker[AsyncSession],
    test_project: Project,
    sample_entity: Entity,
) -> None:
    target = NoteContentReadRepairTarget(project=test_project, entity=sample_entity)
    file_reader = StubReadRepairFileReader(None)

    run = await run_note_content_read_repair_with_default_reconciler(
        NoteContentReadRepairPreflight(
            status=RuntimeNoteContentReadRepairStatus.read_file,
            target=target,
        ),
        session_maker=session_maker,
        file_reader=file_reader,
        source="read_repair",
    )

    assert run.status is RuntimeNoteContentReadRepairStatus.file_missing
    assert not run.repaired
    assert file_reader.targets == [target]


@pytest.mark.asyncio
async def test_run_note_content_read_repair_reports_empty_file(
    session_maker: async_sessionmaker[AsyncSession],
    test_project: Project,
    sample_entity: Entity,
) -> None:
    run = await run_note_content_read_repair_with_default_reconciler(
        NoteContentReadRepairPreflight(
            status=RuntimeNoteContentReadRepairStatus.read_file,
            target=NoteContentReadRepairTarget(project=test_project, entity=sample_entity),
        ),
        session_maker=session_maker,
        file_reader=StubReadRepairFileReader(NoteContentReadRepairFile(None, observed_at=None)),
        source="read_repair",
    )

    assert run.status is RuntimeNoteContentReadRepairStatus.empty_file
    assert not run.repaired


@pytest.mark.asyncio
async def test_run_note_content_read_repair_applies_observed_markdown(
    session_maker: async_sessionmaker[AsyncSession],
    test_project: Project,
    sample_entity: Entity,
) -> None:
    """A successful repair reconciles the observed markdown into note_content."""
    markdown_content = "# Repaired\n"
    observed_at = datetime(2026, 4, 13, 15, 0, tzinfo=UTC)
    preflight = await prepare_read_repair(
        session_maker,
        project_external_id=test_project.external_id,
        entity_external_id=sample_entity.external_id,
    )
    assert preflight.should_read_file

    run = await run_note_content_read_repair_with_default_reconciler(
        preflight,
        session_maker=session_maker,
        file_reader=StubReadRepairFileReader(
            NoteContentReadRepairFile(markdown_content, observed_at=observed_at)
        ),
        source="read_repair",
    )

    assert run.status is RuntimeNoteContentReadRepairStatus.repaired
    assert run.repaired
    repository = NoteContentRepository(project_id=test_project.id)
    async with db.scoped_session(session_maker) as session:
        row = await repository.get_by_entity_id(session, sample_entity.id)
    assert row is not None
    assert row.markdown_content == markdown_content
    assert row.db_checksum == await file_utils.compute_checksum(markdown_content)
    assert row.last_source == "read_repair"
