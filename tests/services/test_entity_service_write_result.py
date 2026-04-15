"""Tests for EntityWriteResult content variants."""

import sys

import pytest

from basic_memory.file_utils import remove_frontmatter
from basic_memory.schemas import Entity as EntitySchema

skip_on_windows = pytest.mark.skipif(
    sys.platform == "win32",
    reason="formatter command uses POSIX shell redirection",
)


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
@pytest.mark.parametrize("permalink_line", ["permalink:", "permalink: null", 'permalink: ""'])
async def test_create_entity_ignores_empty_frontmatter_permalink(
    entity_service, file_service, permalink_line: str
) -> None:
    result = await entity_service.create_entity_with_content(
        EntitySchema(
            title="Empty Frontmatter Permalink",
            directory="notes",
            note_type="note",
            content=f"---\n{permalink_line}\n---\nCreate body content",
        )
    )

    file_path = file_service.get_entity_path(result.entity)
    file_content, _ = await file_service.read_file(file_path)

    assert result.entity.permalink == "test-project/notes/empty-frontmatter-permalink"
    assert "permalink: test-project/notes/empty-frontmatter-permalink" in file_content
    assert "permalink: None" not in file_content


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


@skip_on_windows
@pytest.mark.asyncio
async def test_create_entity_with_content_returns_persisted_content_after_format_on_save(
    entity_service, file_service, app_config
) -> None:
    app_config.format_on_save = True
    app_config.formatter_command = "sh -c 'echo modified > {file}'"
    file_service.app_config = app_config

    result = await entity_service.create_entity_with_content(
        EntitySchema(
            title="FormattedWriteResult",
            directory="notes",
            note_type="note",
            content="Original body content",
        )
    )

    file_path = file_service.get_entity_path(result.entity)
    file_content, _ = await file_service.read_file(file_path)

    assert file_content == "modified\n"
    assert result.content == file_content
    assert result.search_content == remove_frontmatter(file_content)
    assert result.search_content == "modified"
