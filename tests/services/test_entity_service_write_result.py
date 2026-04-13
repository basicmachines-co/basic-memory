"""Tests for EntityWriteResult content variants."""

import pytest

from basic_memory.file_utils import remove_frontmatter
from basic_memory.schemas import Entity as EntitySchema


@pytest.mark.asyncio
async def test_create_entity_with_content_returns_full_and_search_content(
    entity_service, file_service
) -> None:
    result = await entity_service.create_entity_with_content(
        EntitySchema(
            title="Create Write Result",
            directory="notes",
            note_type="note",
            content="Create body content",
        )
    )

    file_path = file_service.get_entity_path(result.entity)
    file_content, _ = await file_service.read_file(file_path)

    assert result.content == file_content
    assert result.search_content == remove_frontmatter(file_content)
    assert result.search_content == "Create body content"


@pytest.mark.asyncio
async def test_update_entity_with_content_returns_full_and_search_content(
    entity_service, file_service
) -> None:
    created = await entity_service.create_entity(
        EntitySchema(
            title="Update Write Result",
            directory="notes",
            note_type="note",
            content="Original body content",
        )
    )

    result = await entity_service.update_entity_with_content(
        created,
        EntitySchema(
            title="Update Write Result",
            directory="notes",
            note_type="note",
            content="Updated body content",
        ),
    )

    file_path = file_service.get_entity_path(result.entity)
    file_content, _ = await file_service.read_file(file_path)

    assert result.content == file_content
    assert result.search_content == remove_frontmatter(file_content)
    assert result.search_content == "Updated body content"


@pytest.mark.asyncio
async def test_edit_entity_with_content_returns_full_and_search_content(
    entity_service, file_service
) -> None:
    created = await entity_service.create_entity(
        EntitySchema(
            title="Edit Write Result",
            directory="notes",
            note_type="note",
            content="Original body content",
        )
    )

    result = await entity_service.edit_entity_with_content(
        identifier=created.permalink,
        operation="find_replace",
        content="Edited body content",
        find_text="Original body content",
    )

    file_path = file_service.get_entity_path(result.entity)
    file_content, _ = await file_service.read_file(file_path)

    assert result.content == file_content
    assert result.search_content == remove_frontmatter(file_content)
    assert result.search_content == "Edited body content"
