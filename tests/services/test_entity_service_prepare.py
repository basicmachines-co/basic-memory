"""Parity tests for prepare-first entity write semantics."""

from __future__ import annotations

import pytest

from basic_memory.file_utils import parse_frontmatter
from basic_memory.schemas import Entity as EntitySchema


@pytest.mark.asyncio
async def test_prepare_create_entity_content_matches_create_entity_with_content(
    entity_service,
) -> None:
    schema = EntitySchema(
        title="Prepared Create",
        directory="notes",
        note_type="note",
        content="---\nstatus: draft\npermalink: prepared/create\n---\nCreate body",
    )

    prepared = await entity_service.prepare_create_entity_content(schema)
    result = await entity_service.create_entity_with_content(schema)

    assert prepared.file_path.as_posix() == result.entity.file_path
    assert prepared.markdown_content == result.content
    assert prepared.search_content == result.search_content
    assert prepared.entity_fields["title"] == result.entity.title
    assert prepared.entity_fields["note_type"] == result.entity.note_type
    assert prepared.entity_fields["permalink"] == result.entity.permalink
    assert prepared.entity_fields["entity_metadata"] == result.entity.entity_metadata


@pytest.mark.asyncio
async def test_prepare_create_entity_content_can_skip_storage_existence_check(
    entity_service,
) -> None:
    async def fail_if_called(*args, **kwargs):
        raise AssertionError("file_service.exists should not be called")

    entity_service.file_service.exists = fail_if_called

    prepared = await entity_service.prepare_create_entity_content(
        EntitySchema(
            title="Prepared Create No HEAD",
            directory="notes",
            note_type="note",
            content="Create body",
        ),
        check_storage_exists=False,
    )

    assert prepared.file_path.as_posix() == "notes/Prepared Create No HEAD.md"
    assert prepared.entity_fields["title"] == "Prepared Create No HEAD"


@pytest.mark.asyncio
async def test_prepare_update_entity_content_matches_update_entity_with_content(
    entity_service,
    file_service,
) -> None:
    created = await entity_service.create_entity(
        EntitySchema(
            title="Prepared Update",
            directory="notes",
            note_type="note",
            content="---\nstatus: draft\nowner: alice\n---\nOriginal body",
        )
    )

    existing_content = await file_service.read_file_content(created.file_path)
    update_schema = EntitySchema(
        title="Prepared Update",
        directory="notes",
        note_type="note",
        content="---\nstatus: published\nreviewed_by: bob\n---\nUpdated body",
    )

    prepared = await entity_service.prepare_update_entity_content(
        created,
        update_schema,
        existing_content,
    )
    result = await entity_service.update_entity_with_content(created, update_schema)
    prepared_frontmatter = parse_frontmatter(prepared.markdown_content)

    assert prepared.markdown_content == result.content
    assert prepared.search_content == result.search_content
    assert prepared.entity_fields["title"] == result.entity.title
    assert prepared.entity_fields["note_type"] == result.entity.note_type
    assert prepared.entity_fields["permalink"] == result.entity.permalink
    assert prepared_frontmatter["owner"] == "alice"
    assert prepared_frontmatter["status"] == "published"
    assert prepared_frontmatter["reviewed_by"] == "bob"


@pytest.mark.asyncio
async def test_prepare_edit_entity_content_matches_edit_entity_with_content(
    entity_service,
    file_service,
) -> None:
    created = await entity_service.create_entity(
        EntitySchema(
            title="Prepared Edit",
            directory="notes",
            note_type="note",
            content="Before edit",
        )
    )

    current_content = await file_service.read_file_content(created.file_path)
    prepared = await entity_service.prepare_edit_entity_content(
        created,
        current_content,
        operation="find_replace",
        content="After edit",
        find_text="Before edit",
    )
    result = await entity_service.edit_entity_with_content(
        identifier=created.permalink,
        operation="find_replace",
        content="After edit",
        find_text="Before edit",
    )

    assert prepared.markdown_content == result.content
    assert prepared.search_content == result.search_content
    assert prepared.entity_fields["title"] == result.entity.title
    assert prepared.entity_fields["note_type"] == result.entity.note_type
    assert prepared.entity_fields["permalink"] == result.entity.permalink
