"""Tests for the NoteSectionRepository and the section index lifecycle."""

import hashlib
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.index.local_project import (
    LocalProjectIndexRuntimeFactory,
    run_local_project_index_for_project,
)
from basic_memory.models import Entity, NoteContent, NoteSection, Project
from basic_memory.repository.note_section_repository import (
    AcceptedSectionWrite,
    NoteSectionRepository,
    heading_path_digest,
)


def _section(
    heading: str,
    *,
    level: int = 2,
    heading_path: str | None = None,
    duplicate_index: int = 0,
    start_line: int = 1,
    end_line: int = 2,
    start_offset: int = 0,
    end_offset: int = 10,
) -> AcceptedSectionWrite:
    return AcceptedSectionWrite(
        heading=heading,
        level=level,
        heading_path=heading_path or heading,
        duplicate_index=duplicate_index,
        start_line=start_line,
        end_line=end_line,
        start_offset=start_offset,
        end_offset=end_offset,
    )


async def _add_note_content_generation(
    session_maker: async_sessionmaker[AsyncSession],
    entity: Entity,
    *,
    generation: int,
) -> None:
    async with db.scoped_session(session_maker) as session:
        session.add(
            NoteContent(
                entity_id=entity.id,
                project_id=entity.project_id,
                external_id=f"content-{entity.external_id}",
                file_path=entity.file_path,
                markdown_content="# Current\n",
                db_version=generation,
                db_checksum=f"checksum-{generation}",
                file_write_status="synced",
            )
        )


def test_heading_path_digest_is_sha256_hex():
    assert heading_path_digest("Auth/Decisions") == (hashlib.sha256(b"Auth/Decisions").hexdigest())


@pytest.mark.asyncio
async def test_replace_sections_for_current_generation_inserts_rows(
    sample_entity: Entity,
    session_maker: async_sessionmaker[AsyncSession],
):
    """The current generation replaces the complete scanned section set."""
    repository = NoteSectionRepository(project_id=sample_entity.project_id)
    await _add_note_content_generation(session_maker, sample_entity, generation=7)

    writes = [
        _section("Spec", level=1, start_line=1, end_line=6, start_offset=0, end_offset=60),
        _section(
            "Auth",
            heading_path="Spec/Auth",
            start_line=2,
            end_line=6,
            start_offset=7,
            end_offset=60,
        ),
    ]
    async with db.scoped_session(session_maker) as session:
        result = await repository.replace_sections_for_generation(
            session,
            entity_id=sample_entity.id,
            generation=7,
            sections=writes,
        )

    assert result.generation_is_current
    async with db.scoped_session(session_maker) as session:
        sections = await repository.find_by_entity(session, sample_entity.id)

    assert [section.heading_path for section in sections] == ["Spec", "Spec/Auth"]
    spec, auth = sections
    assert spec.project_id == sample_entity.project_id
    assert (spec.level, spec.start_line, spec.end_line) == (1, 1, 6)
    assert (spec.start_offset, spec.end_offset) == (0, 60)
    assert spec.duplicate_index == 0
    assert spec.heading_path_digest == heading_path_digest("Spec")
    assert auth.heading_path_digest == heading_path_digest("Spec/Auth")


@pytest.mark.asyncio
async def test_replace_sections_wipes_and_recreates(
    sample_entity: Entity,
    session_maker: async_sessionmaker[AsyncSession],
):
    """A later replace under the same fence discards every prior row."""
    repository = NoteSectionRepository(project_id=sample_entity.project_id)
    await _add_note_content_generation(session_maker, sample_entity, generation=3)

    async with db.scoped_session(session_maker) as session:
        await repository.replace_sections_for_generation(
            session,
            entity_id=sample_entity.id,
            generation=3,
            sections=[_section("Old")],
        )
    async with db.scoped_session(session_maker) as session:
        result = await repository.replace_sections_for_generation(
            session,
            entity_id=sample_entity.id,
            generation=3,
            sections=[_section("New A"), _section("New B", start_line=4, end_line=5)],
        )

    assert result.generation_is_current
    async with db.scoped_session(session_maker) as session:
        sections = await repository.find_by_entity(session, sample_entity.id)
    assert [section.heading for section in sections] == ["New A", "New B"]


@pytest.mark.asyncio
async def test_replace_sections_empty_set_wipes_stale_rows(
    sample_entity: Entity,
    session_maker: async_sessionmaker[AsyncSession],
):
    """An empty desired set is a legitimate current-generation wipe."""
    repository = NoteSectionRepository(project_id=sample_entity.project_id)
    await _add_note_content_generation(session_maker, sample_entity, generation=5)
    async with db.scoped_session(session_maker) as session:
        await repository.replace_sections_for_generation(
            session,
            entity_id=sample_entity.id,
            generation=5,
            sections=[_section("Stale")],
        )

    async with db.scoped_session(session_maker) as session:
        result = await repository.replace_sections_for_generation(
            session,
            entity_id=sample_entity.id,
            generation=5,
            sections=[],
        )

    assert result.generation_is_current
    async with db.scoped_session(session_maker) as session:
        assert await repository.find_by_entity(session, sample_entity.id) == []


@pytest.mark.asyncio
async def test_replace_sections_for_stale_generation_is_noop(
    sample_entity: Entity,
    session_maker: async_sessionmaker[AsyncSession],
):
    """A stale fence cannot delete the current rows or insert its desired set."""
    repository = NoteSectionRepository(project_id=sample_entity.project_id)
    await _add_note_content_generation(session_maker, sample_entity, generation=8)
    async with db.scoped_session(session_maker) as session:
        await repository.replace_sections_for_generation(
            session,
            entity_id=sample_entity.id,
            generation=8,
            sections=[_section("Current")],
        )

    async with db.scoped_session(session_maker) as session:
        result = await repository.replace_sections_for_generation(
            session,
            entity_id=sample_entity.id,
            generation=7,
            sections=[_section("Stale")],
        )

    assert not result.generation_is_current
    async with db.scoped_session(session_maker) as session:
        sections = await repository.find_by_entity(session, sample_entity.id)
    assert [section.heading for section in sections] == ["Current"]


@pytest.mark.asyncio
async def test_find_by_entity_orders_by_start_line(
    sample_entity: Entity,
    session_maker: async_sessionmaker[AsyncSession],
):
    repository = NoteSectionRepository(project_id=sample_entity.project_id)
    await _add_note_content_generation(session_maker, sample_entity, generation=2)

    async with db.scoped_session(session_maker) as session:
        await repository.replace_sections_for_generation(
            session,
            entity_id=sample_entity.id,
            generation=2,
            sections=[
                _section("Late", start_line=9, end_line=10),
                _section("Early", start_line=1, end_line=8),
                _section("Middle", start_line=4, end_line=8),
            ],
        )

    async with db.scoped_session(session_maker) as session:
        sections = await repository.find_by_entity(session, sample_entity.id)
    assert [section.heading for section in sections] == ["Early", "Middle", "Late"]


@pytest.mark.asyncio
async def test_lookup_by_digest_and_duplicate_index(
    sample_entity: Entity,
    session_maker: async_sessionmaker[AsyncSession],
):
    """The lookup index addresses one span by (entity, path digest, duplicate)."""
    repository = NoteSectionRepository(project_id=sample_entity.project_id)
    await _add_note_content_generation(session_maker, sample_entity, generation=4)

    async with db.scoped_session(session_maker) as session:
        await repository.replace_sections_for_generation(
            session,
            entity_id=sample_entity.id,
            generation=4,
            sections=[
                _section("Auth", heading_path="Spec/Auth", start_line=2, end_line=3),
                _section(
                    "Auth",
                    heading_path="Spec/Auth",
                    duplicate_index=1,
                    start_line=4,
                    end_line=5,
                ),
            ],
        )

    async with db.scoped_session(session_maker) as session:
        query = repository.select().filter(
            NoteSection.entity_id == sample_entity.id,
            NoteSection.heading_path_digest == heading_path_digest("Spec/Auth"),
            NoteSection.duplicate_index == 1,
        )
        result = await repository.execute_query(session, query)
        second_auth = result.scalar_one()

    assert (second_auth.start_line, second_auth.end_line) == (4, 5)


@pytest.mark.asyncio
async def test_replace_sections_is_project_scoped(
    sample_entity: Entity,
    session_maker: async_sessionmaker[AsyncSession],
):
    """A repository cannot claim another project's note_content fence."""
    del sample_entity
    async with db.scoped_session(session_maker) as session:
        other_project = Project(
            name="other-section-project",
            permalink="other-section-project",
            path="/other-section-project",
        )
        session.add(other_project)
        await session.flush()
        other_entity = Entity(
            project_id=other_project.id,
            title="Other",
            note_type="note",
            permalink="other-sectioned",
            file_path="other-sectioned.md",
            content_type="text/markdown",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        session.add(other_entity)
        await session.flush()
        session.add(
            NoteContent(
                entity_id=other_entity.id,
                project_id=other_project.id,
                external_id=f"content-{other_entity.external_id}",
                file_path=other_entity.file_path,
                markdown_content="# Other\n",
                db_version=2,
                db_checksum="other-checksum",
                file_write_status="synced",
            )
        )
        other_project_id = other_project.id
        other_entity_id = other_entity.id

    foreign_repository = NoteSectionRepository(project_id=other_project_id + 1000)
    async with db.scoped_session(session_maker) as session:
        result = await foreign_repository.replace_sections_for_generation(
            session,
            entity_id=other_entity_id,
            generation=2,
            sections=[_section("Hijack")],
        )

    assert not result.generation_is_current
    owning_repository = NoteSectionRepository(project_id=other_project_id)
    async with db.scoped_session(session_maker) as session:
        assert await owning_repository.find_by_entity(session, other_entity_id) == []


@pytest.mark.asyncio
async def test_entity_delete_cascades_section_rows(
    sample_entity: Entity,
    entity_repository,
    session_maker: async_sessionmaker[AsyncSession],
):
    """Sections are removed with their entity (ORM cascade + DB FK)."""
    repository = NoteSectionRepository(project_id=sample_entity.project_id)
    await _add_note_content_generation(session_maker, sample_entity, generation=1)
    async with db.scoped_session(session_maker) as session:
        await repository.replace_sections_for_generation(
            session,
            entity_id=sample_entity.id,
            generation=1,
            sections=[_section("Doomed")],
        )

    async with db.scoped_session(session_maker) as session:
        assert await entity_repository.delete(session, sample_entity.id)

    async with db.scoped_session(session_maker) as session:
        rows = (
            (
                await session.execute(
                    select(NoteSection).where(NoteSection.entity_id == sample_entity.id)
                )
            )
            .scalars()
            .all()
        )
    assert rows == []


@pytest.mark.asyncio
async def test_long_heading_path_row_inserts_under_digest_guard(
    sample_entity: Entity,
    session_maker: async_sessionmaker[AsyncSession],
):
    """Arbitrary heading text persists: only the fixed-width digest is indexed.

    A >3KB path would overflow PostgreSQL's btree index-row limit if the lookup
    index keyed on the raw path (the Observation.permalink incident class).
    """
    repository = NoteSectionRepository(project_id=sample_entity.project_id)
    await _add_note_content_generation(session_maker, sample_entity, generation=6)
    long_heading = "H" * 3200
    long_path = f"Parent/{long_heading}"

    async with db.scoped_session(session_maker) as session:
        result = await repository.replace_sections_for_generation(
            session,
            entity_id=sample_entity.id,
            generation=6,
            sections=[_section(long_heading, heading_path=long_path, start_line=2, end_line=3)],
        )

    assert result.generation_is_current
    async with db.scoped_session(session_maker) as session:
        sections = await repository.find_by_entity(session, sample_entity.id)
    assert sections[0].heading_path == long_path
    assert len(sections[0].heading_path_digest) == 64


# --- Index lifecycle: rows appear on index, rebuild on reindex ---

_SECTIONED_NOTE_V1 = """---
type: note
title: Sectioned
---
# Spec
intro
## Auth
first
## Auth
second
"""

_SECTIONED_NOTE_V2 = """---
type: note
title: Sectioned
---
# Spec
intro
## Ops
runbook
"""


@pytest.mark.asyncio
async def test_project_index_builds_and_rebuilds_section_rows(
    app_config,
    session_maker: async_sessionmaker[AsyncSession],
    test_project,
    project_config,
    entity_repository,
):
    """A full project index populates note_section; reindexing rebuilds it."""
    del app_config
    note_path = Path(project_config.home) / "sectioned.md"
    note_path.parent.mkdir(parents=True, exist_ok=True)
    note_path.write_text(_SECTIONED_NOTE_V1, encoding="utf-8")

    indexed = await run_local_project_index_for_project(
        test_project,
        runtime_factory=LocalProjectIndexRuntimeFactory(batch_size=10),
        force_full=True,
    )
    assert indexed.enqueued_files == 1

    repository = NoteSectionRepository(project_id=test_project.id)
    async with db.scoped_session(session_maker) as session:
        entity = await entity_repository.get_by_file_path(session, "sectioned.md")
        assert entity is not None
        sections = await repository.find_by_entity(session, entity.id)

    assert [(section.heading_path, section.duplicate_index) for section in sections] == [
        ("Spec", 0),
        ("Spec/Auth", 0),
        ("Spec/Auth", 1),
    ]
    spec = sections[0]
    assert spec.level == 1
    assert spec.start_line == 1
    assert spec.start_offset == 0
    assert spec.end_offset > spec.start_offset

    # Reindex after an edit: the derived rows converge to the new body.
    note_path.write_text(_SECTIONED_NOTE_V2, encoding="utf-8")
    await run_local_project_index_for_project(
        test_project,
        runtime_factory=LocalProjectIndexRuntimeFactory(batch_size=10),
        force_full=True,
    )

    async with db.scoped_session(session_maker) as session:
        entity = await entity_repository.get_by_file_path(session, "sectioned.md")
        assert entity is not None
        sections = await repository.find_by_entity(session, entity.id)

    assert [section.heading_path for section in sections] == ["Spec", "Spec/Ops"]
