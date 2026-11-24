"""Frontmatter keys that match frontmatter.Post parameters survive writes and edits (#2148)."""

import pytest

from basic_memory.file_utils import build_post, dump_frontmatter, loads_post
from basic_memory.schemas import Entity as EntitySchema
from basic_memory.services.entity_service import EntityService
from basic_memory.services.file_service import FileService


def test_post_round_trip_keeps_reserved_keys():
    post = loads_post("---\nhandler: webhook\ncontent: summary\n---\n\nbody\n")
    assert post.metadata == {"handler": "webhook", "content": "summary"}
    assert post.content == "body"

    rebuilt = build_post("body", {"handler": "webhook", "content": "summary"})
    assert loads_post(dump_frontmatter(rebuilt)).metadata == post.metadata


@pytest.mark.asyncio
async def test_edit_note_with_reserved_frontmatter_keys(
    entity_service: EntityService, file_service: FileService
):
    entity = await entity_service.create_entity(
        EntitySchema(
            title="Reserved Keys",
            directory="test",
            content="first line",
            note_type="note",
            entity_metadata={"handler": "webhook", "content": "summary"},
        )
    )
    assert entity.permalink is not None

    await entity_service.edit_entity(entity.permalink, "append", "second line")

    text, _ = await file_service.read_file(file_service.get_entity_path(entity))
    post = loads_post(text)
    assert post.metadata["handler"] == "webhook"
    assert post.metadata["content"] == "summary"
    assert "first line" in post.content
    assert "second line" in post.content
