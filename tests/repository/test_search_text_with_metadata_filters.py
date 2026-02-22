"""Regression coverage for combined text + metadata filtering."""

from datetime import datetime, timezone

import pytest

from basic_memory import db
from basic_memory.models.knowledge import Entity
from basic_memory.repository.search_index_row import SearchIndexRow
from basic_memory.schemas.search import SearchItemType


async def _index_entity(search_repository, session_maker, title: str, status: str) -> Entity:
    slug = "-".join(title.lower().split())
    now = datetime.now(timezone.utc)
    file_path = f"notes/{slug}.md"
    permalink = f"notes/{slug}"

    async with db.scoped_session(session_maker) as session:
        entity = Entity(
            project_id=search_repository.project_id,
            title=title,
            note_type="note",
            permalink=permalink,
            file_path=file_path,
            content_type="text/markdown",
            entity_metadata={"status": status},
            created_at=now,
            updated_at=now,
        )
        session.add(entity)
        await session.flush()

    await search_repository.index_item(
        SearchIndexRow(
            project_id=search_repository.project_id,
            id=entity.id,
            type=SearchItemType.ENTITY.value,
            title=entity.title,
            content_stems="CLI metadata filter regression",
            content_snippet="CLI metadata filter regression",
            permalink=entity.permalink,
            file_path=entity.file_path,
            entity_id=entity.id,
            metadata={"note_type": entity.note_type},
            created_at=entity.created_at,
            updated_at=entity.updated_at,
        )
    )

    return entity


@pytest.mark.asyncio
async def test_search_text_and_metadata_filters_work_together(search_repository, session_maker):
    """Combined text + metadata filters should work without MATCH context errors."""
    active = await _index_entity(search_repository, session_maker, "CLI Active Result", "active")
    await _index_entity(search_repository, session_maker, "CLI Inactive Result", "inactive")

    results = await search_repository.search(
        search_text="CLI",
        metadata_filters={"status": "active"},
    )

    assert {row.id for row in results} == {active.id}
