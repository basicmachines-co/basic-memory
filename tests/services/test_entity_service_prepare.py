"""Parity tests for prepare-first entity write semantics."""

from __future__ import annotations

import pytest

from basic_memory.file_utils import ParseError, parse_frontmatter, remove_frontmatter
from basic_memory.schemas import Entity as EntitySchema
from basic_memory.services.exceptions import EntityAlreadyExistsError


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
async def test_prepare_update_entity_content_can_change_file_path(
    entity_service,
    file_service,
) -> None:
    """Full replacements should carry title/directory renames through prepare state."""
    created = await entity_service.create_entity(
        EntitySchema(
            title="Original Name",
            directory="notes",
            note_type="note",
            content="Original body",
        )
    )

    existing_content = await file_service.read_file_content(created.file_path)
    update_schema = EntitySchema(
        title="Renamed Note",
        directory="journal",
        note_type="note",
        content="Renamed body",
    )

    prepared = await entity_service.prepare_update_entity_content(
        created,
        update_schema,
        existing_content,
    )
    result = await entity_service.update_entity_with_content(created, update_schema)

    assert prepared.file_path.as_posix() == "journal/Renamed Note.md"
    assert result.entity.file_path == "journal/Renamed Note.md"
    assert result.content == prepared.markdown_content
    assert prepared.entity_fields["permalink"] != created.permalink
    assert prepared.entity_fields["permalink"] == result.entity.permalink
    assert not await file_service.exists("notes/Original Name.md")
    assert await file_service.exists("journal/Renamed Note.md")


@pytest.mark.asyncio
async def test_prepare_update_entity_content_preserves_permalink_when_move_updates_disabled(
    entity_service,
    file_service,
) -> None:
    created = await entity_service.create_entity(
        EntitySchema(
            title="Stable Permalink",
            directory="notes",
            note_type="note",
            content="Original body",
        )
    )
    entity_service.app_config.update_permalinks_on_move = False

    existing_content = await file_service.read_file_content(created.file_path)
    update_schema = EntitySchema(
        title="Renamed Stable Permalink",
        directory="journal",
        note_type="note",
        content="Renamed body",
    )

    prepared = await entity_service.prepare_update_entity_content(
        created,
        update_schema,
        existing_content,
    )
    result = await entity_service.update_entity_with_content(created, update_schema)
    prepared_frontmatter = parse_frontmatter(prepared.markdown_content)

    assert prepared.file_path.as_posix() == "journal/Renamed Stable Permalink.md"
    assert result.entity.file_path == "journal/Renamed Stable Permalink.md"
    assert prepared.entity_fields["permalink"] == created.permalink
    assert result.entity.permalink == created.permalink
    assert prepared_frontmatter["permalink"] == created.permalink
    assert not await file_service.exists("notes/Stable Permalink.md")
    assert await file_service.exists("journal/Renamed Stable Permalink.md")


@pytest.mark.asyncio
async def test_update_entity_with_content_rejects_rename_conflicts_before_writing(
    entity_service,
    file_service,
) -> None:
    source = await entity_service.create_entity(
        EntitySchema(
            title="Source Note",
            directory="notes",
            note_type="note",
            content="Source body",
        )
    )
    target = await entity_service.create_entity(
        EntitySchema(
            title="Target Note",
            directory="notes",
            note_type="note",
            content="Target body",
        )
    )

    source_content = await file_service.read_file_content(source.file_path)
    target_content = await file_service.read_file_content(target.file_path)

    with pytest.raises(
        EntityAlreadyExistsError,
        match="file already exists at destination path: notes/Target Note.md",
    ):
        await entity_service.update_entity_with_content(
            source,
            EntitySchema(
                title="Target Note",
                directory="notes",
                note_type="note",
                content="Overwritten body",
            ),
        )

    assert await file_service.read_file_content(source.file_path) == source_content
    assert await file_service.read_file_content(target.file_path) == target_content
    assert await file_service.exists(source.file_path)
    assert await file_service.exists(target.file_path)


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


@pytest.mark.asyncio
async def test_prepare_edit_entity_content_prepend_preserves_valid_frontmatter(
    entity_service,
    file_service,
) -> None:
    created = await entity_service.create_entity(
        EntitySchema(
            title="Prepared Prepend Frontmatter",
            directory="notes",
            note_type="note",
            content="---\nstatus: draft\ntags:\n  - one\n---\nOriginal body",
        )
    )

    current_content = await file_service.read_file_content(created.file_path)
    prepared = await entity_service.prepare_edit_entity_content(
        created,
        current_content,
        operation="prepend",
        content="Prepended line",
    )

    assert parse_frontmatter(prepared.markdown_content) == {
        "title": "Prepared Prepend Frontmatter",
        "type": "note",
        "status": "draft",
        "tags": ["one"],
        "permalink": created.permalink,
    }
    assert remove_frontmatter(prepared.markdown_content) == "Prepended line\nOriginal body"


@pytest.mark.asyncio
async def test_prepare_edit_entity_content_prepend_fails_for_malformed_frontmatter(
    entity_service,
) -> None:
    created = await entity_service.create_entity(
        EntitySchema(
            title="Prepared Prepend Parse Error",
            directory="notes",
            note_type="note",
            content="Original body",
        )
    )

    malformed_content = "---\nstatus: [draft\n---\nOriginal body"

    with pytest.raises(ParseError, match="Invalid YAML in frontmatter"):
        await entity_service.prepare_edit_entity_content(
            created,
            malformed_content,
            operation="prepend",
            content="Prepended line",
        )


@pytest.mark.asyncio
async def test_prepare_edit_entity_content_prepend_without_frontmatter_uses_simple_prepend(
    entity_service,
) -> None:
    created = await entity_service.create_entity(
        EntitySchema(
            title="Prepared Prepend Simple",
            directory="notes",
            note_type="note",
            content="Original body",
        )
    )

    prepared = await entity_service.prepare_edit_entity_content(
        created,
        "Original body",
        operation="prepend",
        content="Prepended line",
    )

    assert prepared.markdown_content == "Prepended line\nOriginal body"
